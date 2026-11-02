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
  text frame it belongs to and ``is_last_chunk``, which marks the end of that
  one text frame. There is no context or flush concept on the wire: Pipecat
  sends one text frame per sentence under a shared ``context_id`` and then
  calls ``flush_audio``, so the service maps ``seq`` back to the context it
  was sent for and pushes a single ``TTSStoppedFrame`` once every ``seq`` of
  a flushed context has finished. ``done`` only arrives after ``end`` closes
  the session.
* The server closes a session after ``idle_timeout_s`` (180s on the deployment
  this was written against), so a long-idle agent reconnects on its next turn.
* Pronunciation overrides travel in ``hello`` as ``g2p_overrides_json``, the
  override map serialised to a string (the hello schema takes scalars only).
  They apply to every text frame of the session and cannot change mid-session,
  so a new map reconnects like any other setting. The server validates the map
  on the first text frame, not on hello: a bad one answers ``ready`` and then
  fails every sentence with a fatal ``invalid g2p_overrides`` error. The
  service reports that once as a permanent error and stops sending that
  hello, instead of reconnecting into the same error for every sentence.
  On Pipecat 1.8+ the processor is marked unusable and the ``PipelineTask``'s
  ``processor_unusable_policy`` decides what happens next: the default,
  ``CONTINUE``, keeps the pipeline running with a silent TTS, ``CANCEL``
  stops it. On older Pipecat the fatal ``ErrorFrame`` cancels the pipeline.
  Replacing ``g2p_overrides`` clears the rejection and reconnects.
"""

import inspect
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
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
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

#: The streaming endpoint. The server serves the websocket on ``/v1`` and
#: refuses any other path; the bare host answers with a 308 to ``/v1``, so
#: connecting directly costs one round trip less.
BODHI_TTS_DEFAULT_URL = "wss://tts.navana.ai/v1"

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


#: A server-side rejection of the hello's pronunciation map. The server only
#: checks the map when it tokenizes the first text frame, so this arrives as a
#: fatal per-text error rather than a refused handshake.
G2P_ERROR_PREFIX = "invalid g2p_overrides"

# Pipecat >= 1.8 marks a processor unusable through push_error(...,
# force_treat_as_permanent=True) and lets the PipelineTask's
# processor_unusable_policy decide what happens; earlier versions only have the
# ErrorFrame.fatal flag, which cancels the pipeline outright.
_PUSH_ERROR_PERMANENT = (
    "force_treat_as_permanent" in inspect.signature(FrameProcessor.push_error).parameters
)


def validate_g2p_overrides(overrides: Any) -> dict[str, Any] | None:
    """Check the shape of a pronunciation override map before it is sent.

    The map is ``{"WORD": "<phonemes>"}`` or ``{"WORD": {"tokens": "<phonemes>",
    "lang": "en"}}``; phonemes are space-separated IPA tokens, as the platform's
    Pronunciation page generates them. Only the shape is checked here: whether
    every token is in the model's vocabulary is known only to the server.

    Args:
        overrides: The map to check. ``None`` and ``{}`` mean no overrides.

    Returns:
        The map, or ``None`` when empty.

    Raises:
        ValueError: If the map is not a dict of non-empty strings to a non-empty
            phoneme string or a ``{"tokens", "lang"}`` object.
    """
    if overrides is None:
        return None
    if not isinstance(overrides, dict):
        raise ValueError(f"g2p_overrides must be a dict, got {type(overrides).__name__}")
    if not overrides:
        return None
    for word, entry in overrides.items():
        if not isinstance(word, str) or not word.strip():
            raise ValueError(f"g2p_overrides keys must be non-empty words, got {word!r}")
        if isinstance(entry, str):
            tokens = entry
        elif isinstance(entry, dict):
            tokens = entry.get("tokens")
            lang = entry.get("lang")
            if lang is not None and not isinstance(lang, str):
                raise ValueError(f"g2p_overrides[{word!r}].lang must be a string, got {lang!r}")
            extra = set(entry) - {"tokens", "lang"}
            if extra:
                raise ValueError(f"g2p_overrides[{word!r}] has unknown fields {sorted(extra)}")
        else:
            raise ValueError(
                f"g2p_overrides[{word!r}] must be a phoneme string or "
                f'{{"tokens": ..., "lang": ...}}, got {type(entry).__name__}'
            )
        if not isinstance(tokens, str) or not tokens.strip():
            raise ValueError(f"g2p_overrides[{word!r}] needs a non-empty phoneme string")
    # A copy, so a caller mutating their dict later does not change a session
    # or request already built from it.
    return {w: dict(e) if isinstance(e, dict) else e for w, e in overrides.items()}


@dataclass
class BodhiTTSSettings(TTSSettings):
    """Runtime-updatable settings for :class:`BodhiTTSService`.

    Parameters:
        voice: Voice id for the language, e.g. ``default_female``.
        language: Language to synthesise in; Bodhi serves ten.
        use_fast: Use the distilled model where one is available.
        num_step: Flow-matching steps, 1-100. Lower is faster and rougher.
        guidance_scale: Classifier-free guidance.
        g2p_overrides: Pronunciation overrides for the session, as
            ``{"WORD": "<phonemes>"}`` or ``{"WORD": {"tokens": "<phonemes>",
            "lang": "en"}}`` to render that word in another language's accent.
            Phonemes are space-separated IPA tokens; copy them from the
            platform's Pronunciation page (Generate Phoneme) rather than
            writing them by hand. Words match case-insensitively. Applies to
            every sentence of the session; changing it reconnects.
    """

    use_fast: bool | NotGiven = field(default_factory=lambda: NOT_GIVEN)
    num_step: int | None | NotGiven = field(default_factory=lambda: NOT_GIVEN)
    guidance_scale: float | None | NotGiven = field(default_factory=lambda: NOT_GIVEN)
    g2p_overrides: dict[str, Any] | None | NotGiven = field(default_factory=lambda: NOT_GIVEN)


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
            url: Bodhi TTS websocket URL. Defaults to ``wss://tts.navana.ai/v1``.
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
            g2p_overrides=None,
        )
        if settings is not None:
            default_settings.apply_update(settings)
        validate_g2p_overrides(assert_given(default_settings.g2p_overrides))

        super().__init__(
            # Let the base open the audio context and push TTSStartedFrame; the
            # matching stop frame is pushed from flush_audio once every seq of
            # the context has finished.
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
        self._chunk_seq: int | None = None
        self._chunk_is_last = False
        # seq -> context_id for every text frame still synthesising, and the
        # contexts Pipecat has flushed but which still have seqs in flight.
        self._context_by_seq: dict[int, str] = {}
        self._closing_contexts: set[str] = set()
        # The server's message when it rejected g2p_overrides; set once and
        # cleared when the setting changes, so run_tts stops reconnecting
        # into the same bad hello.
        self._g2p_rejected: str | None = None

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

        Raises:
            ValueError: If ``delta.g2p_overrides`` is malformed; nothing is
                applied and the session stays up.
        """
        overrides = getattr(delta, "g2p_overrides", NOT_GIVEN)
        if not isinstance(overrides, NotGiven):
            validate_g2p_overrides(overrides)
        changed = await super()._update_settings(delta)
        if "g2p_overrides" in changed and self._g2p_rejected is not None:
            # A different map may be valid: forget the rejection and, on
            # Pipecat 1.8+, take the processor back out of the unusable state
            # the rejection put it in, so the reconnect below gets to try it.
            self._g2p_rejected = None
            if hasattr(self, "set_usable"):
                await self.set_usable(True)
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
        overrides = validate_g2p_overrides(assert_given(s.g2p_overrides))
        if overrides:
            # The hello schema takes scalars only, so the map goes as a string.
            hello["g2p_overrides_json"] = json.dumps(overrides, ensure_ascii=False)

        return hello

    async def _connect_websocket(self):
        """Open the websocket and complete the hello/ready handshake.

        Does nothing while the server has rejected ``g2p_overrides``: every
        reconnect, whether from ``run_tts`` or from the base class's own
        retry loop, would send the same hello and fail the same way.
        """
        if self._g2p_rejected is not None:
            logger.error(f"{self} not connecting: {self._g2p_rejected}")
            self._websocket = None
            return
        try:
            if self._websocket and self._websocket.state is State.OPEN:
                return

            logger.debug(f"{self.name} connecting to Bodhi TTS at {self._url}")
            # Both header styles: this endpoint reads Authorization while the
            # STT endpoint reads x-api-key, and each ignores the other.
            self._websocket = await websocket_connect(
                self._url,
                additional_headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "x-api-key": self._api_key,
                },
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
            self._chunk_seq = None
            self._chunk_is_last = False
            self._context_by_seq.clear()
            self._closing_contexts.clear()
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

    def _context_for_seq(self, seq: int | None) -> str | None:
        """Resolve the context a server ``seq`` was sent for.

        Falls back to the active context for a header without a known ``seq``,
        so a server that stops echoing it still plays audio.
        """
        if seq is not None and seq in self._context_by_seq:
            return self._context_by_seq[seq]
        return self.get_active_audio_context_id()

    async def _finish_seq(self, seq: int | None):
        """Mark ``seq`` done and close its context if nothing else is pending."""
        if seq is None:
            return
        context_id = self._context_by_seq.pop(seq, None)
        if context_id is not None:
            await self._maybe_close_context(context_id)

    async def _maybe_close_context(self, context_id: str):
        """Push the stop frame once a flushed context has no seq in flight."""
        if context_id not in self._closing_contexts:
            return
        if context_id in self._context_by_seq.values():
            return
        self._closing_contexts.discard(context_id)
        if not self.audio_context_available(context_id):
            # Already closed by Pipecat (interrupted, or timed out waiting).
            return
        await self.append_to_audio_context(context_id, TTSStoppedFrame(context_id=context_id))
        await self.remove_audio_context(context_id)

    async def flush_audio(self, context_id: str | None = None):
        """Finish ``context_id`` once every seq sent under it has been received.

        Pipecat calls this after the last sentence of a response has gone to
        ``run_tts``. Bodhi has no flush command, so the stop frame is pushed
        here if the audio has already arrived, or from the receive task when
        the last pending seq's ``is_last_chunk`` lands.

        Args:
            context_id: The context to finish. Defaults to the active one.
        """
        context_id = context_id or self.get_active_audio_context_id()
        if not context_id:
            return
        self._closing_contexts.add(context_id)
        await self._maybe_close_context(context_id)

    async def on_audio_context_interrupted(self, context_id: str):
        """Drop the bookkeeping for a context Pipecat has cancelled."""
        self._closing_contexts.discard(context_id)
        for seq in [s for s, c in self._context_by_seq.items() if c == context_id]:
            del self._context_by_seq[seq]

    async def _receive_messages(self):
        """Receive audio and push it into the context its seq was sent for."""
        async for message in self._get_websocket():
            if isinstance(message, (bytes, bytearray)):
                await self.stop_ttfb_metrics()

                seq, is_last = self._chunk_seq, self._chunk_is_last
                self._chunk_seq, self._chunk_is_last = None, False

                context_id = self._context_for_seq(seq)
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

                if is_last:
                    await self._finish_seq(seq)
                continue

            try:
                content = json.loads(message)
            except json.JSONDecodeError:
                logger.warning(f"{self} received non-JSON message: {message}")
                continue

            kind = content.get("type")
            if kind == "audio":
                # Header for the binary frame that follows it.
                self._chunk_seq = content.get("seq")
                self._chunk_is_last = bool(content.get("is_last_chunk"))
            elif kind == "error":
                error_msg = (
                    f"{self} Bodhi TTS error {content.get('code')}: {content.get('message')}"
                )
                rejected = content.get("fatal") and str(content.get("message") or "").startswith(
                    G2P_ERROR_PREFIX
                )
                if rejected:
                    await self._report_g2p_rejection(error_msg)
                else:
                    await self.push_error(error_msg=error_msg)
                # A per-text error names its seq; that text will produce no
                # audio, so stop waiting on it.
                await self._finish_seq(content.get("seq"))
            elif kind == "done":
                logger.debug(f"{self} session finished: {content}")
            else:
                logger.trace(f"{self} ignoring message: {message}")

    async def _report_g2p_rejection(self, error_msg: str):
        """Report a rejected ``g2p_overrides`` map once, as a permanent error.

        The server validates the map on the first text frame, so every
        reconnect would send the same hello and fail the same way, which from
        the outside is a bot that connects fine and never speaks. Remember the
        rejection so ``_connect_websocket`` stops sending that hello, and
        report it the way the installed Pipecat expects a processor to say it
        can no longer do its job: ``push_error(force_treat_as_permanent=True)``
        on 1.8+, where the task's ``processor_unusable_policy`` decides between
        continuing (the default: the pipeline runs on with a silent TTS),
        ending and cancelling; a fatal ``ErrorFrame`` (always a cancel) before
        that.
        """
        if self._g2p_rejected is not None:
            return
        self._g2p_rejected = f"{error_msg} (fix the g2p_overrides setting)"
        if _PUSH_ERROR_PERMANENT:
            await self.push_error(error_msg=self._g2p_rejected, force_treat_as_permanent=True)
        else:
            await self.push_frame(
                ErrorFrame(error=self._g2p_rejected, fatal=True), FrameDirection.UPSTREAM
            )

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
            if self._g2p_rejected is not None:
                # Already reported; a reconnect would fail the same way.
                yield ErrorFrame(error=self._g2p_rejected)
                yield TTSStoppedFrame(context_id=context_id)
                return

            if not self._websocket or self._websocket.state is State.CLOSED:
                await self._connect()

            try:
                await self.start_ttfb_metrics()
                self._seq += 1
                self._context_by_seq[self._seq] = context_id
                await self._get_websocket().send(
                    json.dumps({"type": "text", "seq": self._seq, "target_text": text})
                )
                await self.start_tts_usage_metrics(text)
            except Exception as e:
                self._context_by_seq.pop(self._seq, None)
                yield ErrorFrame(error=f"{self} failed to send text: {e}")
                yield TTSStoppedFrame(context_id=context_id)
                await self._disconnect()
                await self._connect()
                return

            yield None
        except Exception as e:
            yield ErrorFrame(error=f"{self} unknown error: {e}")
