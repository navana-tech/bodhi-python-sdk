#
# Bodhi (Navana Tech) Text-to-Speech service for Pipecat.
#
# Install with `pip install "bodhi-api-sdk[tts]"`; no changes to pipecat itself
# are needed. The file is self-contained, so it can also be copied into a
# project that pins an older bodhi-api-sdk.
#

"""Bodhi Text-to-Speech service for Pipecat 1.x.

Streams synthesized audio from Bodhi's TTS websocket API and pushes it into a
Pipecat pipeline as ``TTSAudioRawFrame``s.

Built on ``InterruptibleTTSService``, which is Pipecat's base class for
providers that cannot cancel synthesis mid-utterance: when the user barges in,
it reconnects the socket rather than asking the server to stop.

Protocol notes that shape the implementation:

* Auth on the websocket is ``Authorization: Bearer <key>``. Unlike the Bodhi
  STT endpoint, ``x-api-key`` is rejected on the upgrade; the API key can also
  travel inside the ``hello`` frame as ``auth_token``.
* One ``hello`` per connection sets the language, voice and output format, and
  the server answers with ``ready``. Unknown fields in ``hello`` are rejected
  outright, so settings changes reconnect.
* Each audio chunk is preceded by a JSON header carrying the ``seq`` of the
  text frame it belongs to and ``is_last_chunk``, which is what marks the end
  of an utterance. ``done`` only arrives after ``end`` closes the session.
* The server closes a session after ``idle_timeout_s`` (180s on the deployment
  this was written against), so a long-idle agent reconnects on its next turn.
"""

import json
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any

# loguru and websockets arrive with pipecat, so check for pipecat first and
# fail with an instruction instead of a bare ModuleNotFoundError.
try:
    import pipecat  # noqa: F401
except ImportError as e:  # pragma: no cover - depends on how the SDK was installed
    raise ImportError(
        'BodhiTTSService needs Pipecat: pip install "bodhi-api-sdk[tts]"'
    ) from e

from loguru import logger
from websockets.asyncio.client import connect as websocket_connect
from websockets.protocol import State

from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    ErrorFrame,
    Frame,
    StartFrame,
    TTSAudioRawFrame,
    TTSStoppedFrame,
)
from pipecat.services.tts_service import InterruptibleTTSService
from pipecat.transcriptions.language import Language
from pipecat.utils.tracing.service_decorators import traced_tts

try:
    from pipecat.utils.types import NOT_GIVEN, NotGiven, assert_given
except ImportError:  # pipecat 1.0 - 1.4
    from pipecat.services.settings import NOT_GIVEN  # type: ignore[no-redef]
    from pipecat.services.settings import _NotGiven as NotGiven  # type: ignore[no-redef]

    try:
        from pipecat.services.settings import assert_given  # type: ignore[no-redef]
    except ImportError:

        def assert_given(value):  # type: ignore[misc]
            """Return a store-mode settings value, refusing the NOT_GIVEN sentinel."""
            if isinstance(value, NotGiven):
                raise RuntimeError("Store-mode settings field is NOT_GIVEN (invariant violated)")
            return value


from pipecat.services.settings import TTSSettings

BODHI_TTS_DEFAULT_URL = "wss://tts.navana.ai"

#: Sample rates the server will synthesise at.
BODHI_TTS_SAMPLE_RATES = (8000, 16000, 24000)

#: Used when the pipeline asks for a rate the server does not serve.
BODHI_TTS_DEFAULT_SAMPLE_RATE = 24000

#: Voices every language ships with.
DEFAULT_VOICE = "default_female"


def language_to_bodhi_language(language: Language) -> str:
    """Convert a Pipecat language to a Bodhi ``lang`` code.

    Args:
        language: The language to convert.

    Returns:
        The two-letter code Bodhi expects, e.g. ``hi``.
    """
    return str(language.value).split("-")[0].lower()


@dataclass
class BodhiTTSSettings(TTSSettings):
    """Runtime-updatable settings for :class:`BodhiTTSService`.

    Parameters:
        voice: Voice id for the language, e.g. ``default_female``.
        language: Language to synthesise in; Bodhi serves ten.
        use_fast: Use the distilled model where one is available.
        num_step: Flow-matching steps, 1-100. Lower is faster and rougher.
        guidance_scale: Classifier-free guidance.
    """

    use_fast: bool | NotGiven = field(default_factory=lambda: NOT_GIVEN)
    num_step: int | None | NotGiven = field(default_factory=lambda: NOT_GIVEN)
    guidance_scale: float | None | NotGiven = field(default_factory=lambda: NOT_GIVEN)


class BodhiTTSService(InterruptibleTTSService):
    """Text-to-Speech service using Bodhi's streaming websocket API.

    Example::

        from bodhi.integrations.pipecat_tts import BodhiTTSService

        tts = BodhiTTSService(
            api_key=os.environ["BODHI_API_KEY"],
            language=Language.HI,
            voice="default_female",
        )
    """

    Settings = BodhiTTSSettings
    _settings: Settings

    def __init__(
        self,
        *,
        api_key: str,
        url: str = BODHI_TTS_DEFAULT_URL,
        voice: str = DEFAULT_VOICE,
        language: Language | str = Language.HI,
        sample_rate: int | None = None,
        settings: Settings | None = None,
        **kwargs,
    ):
        """Initialize the Bodhi TTS service.

        Args:
            api_key: Bodhi API key, sent as ``Authorization: Bearer``.
            url: Bodhi TTS websocket URL. Defaults to ``wss://tts.navana.ai``.
            voice: Voice id, e.g. ``default_female`` or ``default_male``.
            language: Language to synthesise in. Defaults to Hindi.
            sample_rate: Output rate in Hz. Defaults to the pipeline's, falling
                back to 24000 when the pipeline asks for a rate Bodhi does not
                serve.
            settings: Runtime-updatable settings; values here win over the
                equivalent constructor arguments.
            **kwargs: Additional arguments passed to ``InterruptibleTTSService``.
        """
        default_settings = self.Settings(
            model=None,
            voice=voice,
            language=language,
            use_fast=False,
            num_step=None,
            guidance_scale=None,
        )
        if settings is not None:
            default_settings.apply_update(settings)

        super().__init__(
            # Let the base open the audio context and push TTSStartedFrame; the
            # matching stop frame is pushed from is_last_chunk below.
            push_start_frame=True,
            sample_rate=sample_rate,
            settings=default_settings,
            **kwargs,
        )

        self._api_key = api_key
        self._url = url
        self._receive_task = None
        self._session_id: str | None = None
        self._seq = 0
        # Set from the JSON header that precedes each binary chunk.
        self._chunk_is_last = False

    def __str__(self):
        # Called from the base constructor, before __init__ sets its attributes.
        return f"{self.name} [{getattr(self, '_session_id', None)}]"

    def can_generate_metrics(self) -> bool:
        """Whether this service can generate performance metrics.

        Returns:
            True.
        """
        return True

    def language_to_service_language(self, language: Language) -> str | None:
        """Convert a Pipecat language to Bodhi's ``lang`` code.

        Args:
            language: The language to convert.

        Returns:
            The Bodhi language code.
        """
        return language_to_bodhi_language(language)

    async def start(self, frame: StartFrame):
        """Resolve the output rate and open the session.

        Args:
            frame: The start frame containing initialization parameters.
        """
        await super().start(frame)

        if self.sample_rate not in BODHI_TTS_SAMPLE_RATES:
            logger.debug(
                f"{self} pipeline asked for {self.sample_rate} Hz, which Bodhi does not "
                f"serve; synthesising at {BODHI_TTS_DEFAULT_SAMPLE_RATE} Hz"
            )
            self._output_sample_rate = BODHI_TTS_DEFAULT_SAMPLE_RATE
        else:
            self._output_sample_rate = self.sample_rate

        await self._connect()

    async def stop(self, frame: EndFrame):
        """Close the session cleanly.

        Args:
            frame: The end frame triggering service shutdown.
        """
        await self._send_end()
        await super().stop(frame)
        await self._disconnect()

    async def cancel(self, frame: CancelFrame):
        """Drop the session without waiting for pending audio.

        Args:
            frame: The cancel frame triggering service cancellation.
        """
        await super().cancel(frame)
        await self._disconnect()

    async def _update_settings(self, delta: Settings) -> dict[str, Any]:
        """Apply a settings delta, reconnecting so the new ``hello`` takes effect.

        Args:
            delta: A settings delta.

        Returns:
            Dict mapping changed field names to their previous values.
        """
        changed = await super()._update_settings(delta)
        if changed:
            await self._disconnect()
            await self._connect()
        return changed

    async def _connect(self):
        """Open the session and start the receive task."""
        await self._connect_websocket()

        await super()._connect()

        if self._websocket and not self._receive_task:
            self._receive_task = self.create_task(self._receive_task_handler(self._report_error))

    async def _disconnect(self):
        """Stop the receive task and close the session."""
        await super()._disconnect()

        if self._receive_task:
            await self.cancel_task(self._receive_task)
            self._receive_task = None

        await self._disconnect_websocket()

    def _build_hello(self) -> dict[str, Any]:
        """Build the hello frame for the current settings."""
        s = self._settings
        language = assert_given(s.language)
        if isinstance(language, Language):
            language = language_to_bodhi_language(language)

        hello: dict[str, Any] = {
            "type": "hello",
            "lang": language,
            "voice": assert_given(s.voice) or DEFAULT_VOICE,
            "output_format": f"{self._output_sample_rate}:pcm16",
        }

        # Unknown fields are rejected, so only send the knobs that are set.
        if assert_given(s.use_fast):
            hello["use_fast"] = True
        num_step = assert_given(s.num_step)
        if num_step is not None:
            hello["num_step"] = num_step
        guidance_scale = assert_given(s.guidance_scale)
        if guidance_scale is not None:
            hello["guidance_scale"] = guidance_scale

        return hello

    async def _connect_websocket(self):
        """Open the websocket and complete the hello/ready handshake."""
        try:
            if self._websocket and self._websocket.state is State.OPEN:
                return

            logger.debug(f"{self.name} connecting to Bodhi TTS at {self._url}")
            self._websocket = await websocket_connect(
                self._url, additional_headers={"Authorization": f"Bearer {self._api_key}"}
            )

            hello = self._build_hello()
            await self._websocket.send(json.dumps(hello))
            logger.debug(f"{self.name} sent hello: {hello}")

            ready = json.loads(await self._websocket.recv())
            if ready.get("type") != "ready":
                raise RuntimeError(
                    f"handshake refused: {ready.get('code')} {ready.get('message')}"
                )

            self._session_id = ready.get("session_id")
            self._seq = 0
            self._chunk_is_last = False
            await self._call_event_handler("on_connected")
            logger.debug(f"{self} ready at {ready.get('sample_rate')} Hz {ready.get('encoding')}")
        except Exception as e:
            self._websocket = None
            await self.push_error(error_msg=f"{self} unable to connect to Bodhi TTS: {e}", exception=e)

    async def _disconnect_websocket(self):
        """Close the websocket connection."""
        try:
            if self._websocket and self._websocket.state is State.OPEN:
                logger.debug(f"{self} disconnecting from Bodhi TTS")
                await self._websocket.close()
        except Exception as e:
            await self.push_error(error_msg=f"{self} error closing websocket: {e}", exception=e)
        finally:
            self._websocket = None
            self._session_id = None
            await self._call_event_handler("on_disconnected")

    def _get_websocket(self):
        """Return the current websocket connection.

        Returns:
            The websocket connection.

        Raises:
            Exception: If the websocket is not connected.
        """
        if self._websocket:
            return self._websocket
        raise Exception(f"{self} websocket not connected")

    async def _send_end(self):
        """Tell the server this session is finished."""
        if self._websocket and self._websocket.state is State.OPEN:
            try:
                await self._websocket.send(json.dumps({"type": "end"}))
            except Exception as e:
                logger.warning(f"{self} failed to send end: {e}")

    async def _receive_messages(self):
        """Receive audio and push it into the active audio context."""
        async for message in self._get_websocket():
            if isinstance(message, (bytes, bytearray)):
                await self.stop_ttfb_metrics()

                context_id = self.get_active_audio_context_id()
                if context_id is None:
                    # Audio for an utterance that has already been interrupted.
                    continue

                frame = TTSAudioRawFrame(
                    audio=bytes(message),
                    sample_rate=self._output_sample_rate,
                    num_channels=1,
                    context_id=context_id,
                )
                await self.append_to_audio_context(context_id, frame)

                if self._chunk_is_last:
                    self._chunk_is_last = False
                    await self.append_to_audio_context(
                        context_id, TTSStoppedFrame(context_id=context_id)
                    )
                continue

            try:
                content = json.loads(message)
            except json.JSONDecodeError:
                logger.warning(f"{self} received non-JSON message: {message}")
                continue

            kind = content.get("type")
            if kind == "audio":
                # Header for the binary frame that follows it.
                self._chunk_is_last = bool(content.get("is_last_chunk"))
            elif kind == "error":
                await self.push_error(
                    error_msg=f"{self} Bodhi TTS error {content.get('code')}: "
                    f"{content.get('message')}"
                )
            elif kind == "done":
                logger.debug(f"{self} session finished: {content}")
            else:
                logger.trace(f"{self} ignoring message: {message}")

    @traced_tts
    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame | None, None]:
        """Synthesize text, streaming the audio back through the receive task.

        Args:
            text: The text to synthesize.
            context_id: Identifier for this TTS context.

        Yields:
            None; audio arrives asynchronously on the receive task.
        """
        try:
            if not self._websocket or self._websocket.state is State.CLOSED:
                await self._connect()

            try:
                await self.start_ttfb_metrics()
                self._seq += 1
                await self._get_websocket().send(
                    json.dumps({"type": "text", "seq": self._seq, "target_text": text})
                )
                await self.start_tts_usage_metrics(text)
            except Exception as e:
                yield ErrorFrame(error=f"{self} failed to send text: {e}")
                yield TTSStoppedFrame(context_id=context_id)
                await self._disconnect()
                await self._connect()
                return

            yield None
        except Exception as e:
            yield ErrorFrame(error=f"{self} unknown error: {e}")
