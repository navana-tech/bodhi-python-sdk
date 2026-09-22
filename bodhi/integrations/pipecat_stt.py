#
# Bodhi (Navana Tech) Speech-to-Text service for Pipecat.
#
# Written for pipecat-ai 1.4, and verified on 1.0.0, 1.4.0 and 1.8.1. Install with
# `pip install "bodhi-api-sdk[stt]"`; no changes to pipecat itself are needed.
# The file is also self-contained, so it can simply be copied into a project
# that pins an older bodhi-api-sdk.
#

"""Bodhi Speech-to-Text service for Pipecat 1.x.

Streams PCM audio to a Bodhi ASR websocket endpoint (``wss://stt.navana.ai``
by default) and converts Bodhi's ``partial`` / ``complete`` messages into
Pipecat ``InterimTranscriptionFrame`` / ``TranscriptionFrame``.

Bodhi does its own endpointing server-side: every endpoint produces a
``complete`` message, which this service pushes as a finalized
``TranscriptionFrame``. Pipeline VAD (Silero et al.) is still what drives
interruptions and turn taking -- this service does not emit speaking frames.

Verified against pipecat-ai 1.0.0, 1.4.0 and 1.8.1. Runtime settings changes
(a new model or hotword list) need 1.4+, where Pipecat gained the VAD-aware
reconnect hook; on older versions audio and transcripts work as normal.

Protocol notes that shape the implementation:

* Auth is a single request header, ``x-api-key``. The
  ``Sec-WebSocket-Protocol`` form the Bodhi docs also mention is not used here.
* Exactly one config message per connection, sent before any audio. Sending a
  second one is an error, so runtime settings changes reconnect instead.
* The server closes a connection after 15s without a message, hence the
  keepalive silence below.
* Bodhi models are 8 kHz or 16 kHz. Audio is resampled client-side when the
  pipeline runs at some other rate.
"""

import json
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any

# loguru and websockets arrive with pipecat, so check for pipecat first and
# fail with an instruction instead of a bare ModuleNotFoundError.
try:
    import pipecat  # noqa: F401
except ImportError as e:  # pragma: no cover - depends on how the SDK was installed
    raise ImportError(
        'BodhiSTTService needs Pipecat: pip install "bodhi-api-sdk[stt]"'
    ) from e

from loguru import logger
from websockets.asyncio.client import connect as websocket_connect
from websockets.protocol import State

from pipecat.audio.utils import create_stream_resampler
from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    InterimTranscriptionFrame,
    StartFrame,
    TranscriptionFrame,
)
from pipecat.services.settings import STTSettings

try:
    # pipecat >= 1.5 keeps the NOT_GIVEN sentinel in pipecat.utils.types.
    from pipecat.utils.types import NOT_GIVEN, NotGiven, assert_given, is_given
except ImportError:
    # pipecat 1.0 - 1.4.
    from pipecat.services.settings import NOT_GIVEN, is_given  # type: ignore[no-redef]
    from pipecat.services.settings import _NotGiven as NotGiven  # type: ignore[no-redef]

    try:
        from pipecat.services.settings import assert_given  # type: ignore[no-redef]
    except ImportError:
        # pipecat 1.0 - 1.3 had no assert_given; it only unwraps a store-mode
        # field, which by that mode's invariant is never NOT_GIVEN.
        def assert_given(value):  # type: ignore[misc]
            """Return a store-mode settings value, refusing the NOT_GIVEN sentinel."""
            if not is_given(value):
                raise RuntimeError("Store-mode settings field is NOT_GIVEN (invariant violated)")
            return value
from pipecat.services.stt_latency import DEFAULT_TTFS_P99
from pipecat.services.stt_service import WebsocketSTTService
from pipecat.transcriptions.language import Language
from pipecat.utils.time import time_now_iso8601
from pipecat.utils.tracing.service_decorators import traced_stt

BODHI_DEFAULT_URL = "wss://stt.navana.ai"

#: Sample rates Bodhi models are served at. Anything else is resampled.
BODHI_SAMPLE_RATES = (8000, 16000)

#: Bodhi has not been run through pipecat's STT benchmark, so this is the
#: framework default. Measure your deployment and pass ``ttfs_p99_latency``.
BODHI_TTFS_P99: float = DEFAULT_TTFS_P99

# The server drops a connection that has been silent for this long. Send
# silence well before that, and check often enough that a check can't
# straddle it.
_SERVER_IDLE_TIMEOUT = 15.0
_KEEPALIVE_TIMEOUT = 8.0
_KEEPALIVE_INTERVAL = 2.0

# Audio stopping for _KEEPALIVE_TIMEOUT means the utterance is over by any
# measure, but Bodhi is still holding it open. The first keepalive after audio
# stops therefore carries more than the server's longest endpointing threshold
# (1.2s) of silence, which makes the recognizer finalise what it has instead of
# welding it onto whatever is said after the gap. Later keepalives are short,
# since they only need to keep the socket alive and the audio they add is
# billable.
_IDLE_FLUSH_SILENCE = 1.5
_KEEPALIVE_SILENCE = 0.1

# Pipecat asks STT services to drop results below 50% confidence, where the
# provider reports one. Bodhi reports utterance confidence on every final.
_DEFAULT_MIN_CONFIDENCE = 0.5

# Settings that are part of the config message, and so need a reconnect to
# take effect. `language` only tags outgoing frames, so it is not in here.
_WIRE_SETTINGS = frozenset({"model", "hotwords", "parse_number", "endpoint_silence_duration"})


@dataclass
class BodhiHotword:
    """A hotword to bias recognition towards.

    Parameters:
        phrase: The word or phrase to boost.
        score: Boost strength. Bodhi's default applies when omitted; useful
            values are roughly 1.0-3.0, higher being more aggressive.
    """

    phrase: str
    score: float | None = None


@dataclass
class BodhiSTTSettings(STTSettings):
    """Runtime-updatable settings for :class:`BodhiSTTService`.

    Parameters:
        model: Bodhi model name, e.g. ``"hi-general-v2-8khz"``. Required.
        language: Language used to tag transcription frames. Bodhi infers the
            language from the model, so this is presentational only; it
            defaults to the model name's language prefix.
        hotwords: Phrases to bias recognition towards.
        parse_number: Ask Bodhi to normalise numbers, dates and currency in
            the returned text.
        endpoint_silence_duration: Trailing silence in seconds before Bodhi
            finalises an utterance. Not sent unless set, in which case the
            server applies its default of 0.44. Clamped to 0.44-1.2.
    """

    hotwords: list[BodhiHotword] | None | NotGiven = field(default_factory=lambda: NOT_GIVEN)
    parse_number: bool | NotGiven = field(default_factory=lambda: NOT_GIVEN)
    endpoint_silence_duration: float | None | NotGiven = field(default_factory=lambda: NOT_GIVEN)


def language_from_bodhi_model(model: str) -> Language | None:
    """Derive a language from a Bodhi model name.

    Bodhi model names start with a two-letter language code, e.g. ``hi`` in
    ``hi-general-v2-8khz``.

    Args:
        model: The Bodhi model name.

    Returns:
        The matching ``Language``, or ``None`` if the prefix isn't one.
    """
    code = model.split("-", 1)[0].lower()
    try:
        return Language(code)
    except ValueError:
        logger.debug(f"Bodhi model '{model}' has no recognizable language prefix")
        return None


class BodhiSTTService(WebsocketSTTService):
    """Speech-to-Text service using Bodhi's streaming websocket API.

    Example::

        from bodhi.integrations.pipecat_stt import BodhiHotword, BodhiSTTService

        stt = BodhiSTTService(
            api_key=os.getenv("BODHI_API_KEY"),
            model="hi-general-v2-8khz",
            settings=BodhiSTTService.Settings(
                parse_number=True,
                hotwords=[BodhiHotword("बजाज फिनसर्व", 2.0)],
            ),
        )
    """

    Settings = BodhiSTTSettings
    _settings: Settings

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        url: str = BODHI_DEFAULT_URL,
        sample_rate: int | None = None,
        language: Language | str | None = None,
        transaction_id: str | None = None,
        interim_results: bool = True,
        aux: bool = False,
        min_confidence: float = _DEFAULT_MIN_CONFIDENCE,
        settings: Settings | None = None,
        ttfs_p99_latency: float | None = BODHI_TTFS_P99,
        **kwargs,
    ):
        """Initialize the Bodhi STT service.

        Args:
            api_key: Bodhi API key, sent as the ``x-api-key`` header.
            model: Bodhi model name, e.g. ``"hi-general-v2-8khz"``.
            url: Bodhi websocket URL. Defaults to ``wss://stt.navana.ai``.
            sample_rate: Input sample rate in Hz. Defaults to the pipeline's.
            language: Language to tag frames with. Defaults to the model's
                language prefix, and keeps following the model on a later
                model change unless set here.
            transaction_id: Bodhi transaction id (must be a UUID) used to
                correlate this session with Bodhi's logs and billing. A fresh
                one is generated per connection when omitted.
            interim_results: Whether to emit ``InterimTranscriptionFrame``s
                from Bodhi's partials. Turning this off also stops the server
                sending them.
            aux: Ask Bodhi to attach timing/confidence metadata to each
                message. It arrives on the frame's ``result`` field.
            min_confidence: Drop final transcripts whose utterance confidence
                is below this, following Pipecat's guidance for services that
                report confidence. Defaults to 0.5; pass 0 to keep everything.
            settings: Runtime-updatable settings; values here win over the
                equivalent constructor arguments.
            ttfs_p99_latency: P99 latency from speech end to final transcript,
                in seconds, broadcast for downstream turn strategies.
            **kwargs: Additional arguments passed to ``WebsocketSTTService``.
        """
        default_settings = self.Settings(
            model=model,
            language=language if language is not None else language_from_bodhi_model(model),
            hotwords=None,
            parse_number=False,
            endpoint_silence_duration=None,
        )
        if settings is not None:
            default_settings.apply_update(settings)

        # Overridable, but a timeout at or past the server's own 15s idle
        # limit means the connection dies before the silence goes out.
        keepalive_timeout = kwargs.pop("keepalive_timeout", _KEEPALIVE_TIMEOUT)
        keepalive_interval = kwargs.pop("keepalive_interval", _KEEPALIVE_INTERVAL)
        if keepalive_timeout is not None and keepalive_timeout >= _SERVER_IDLE_TIMEOUT:
            logger.warning(
                f"Bodhi closes idle connections after {_SERVER_IDLE_TIMEOUT}s; "
                f"keepalive_timeout={keepalive_timeout} is too late to prevent that"
            )

        super().__init__(
            sample_rate=sample_rate,
            ttfs_p99_latency=ttfs_p99_latency,
            keepalive_timeout=keepalive_timeout,
            keepalive_interval=keepalive_interval,
            settings=default_settings,
            **kwargs,
        )

        self._api_key = api_key
        self._url = url
        self._interim_results = interim_results
        self._aux = aux
        self._min_confidence = min_confidence

        self._fixed_transaction_id = transaction_id
        self._transaction_id = transaction_id or ""

        # When the caller didn't name a language, it stays tied to the model.
        self._language_follows_model = language is None

        self._receive_task = None
        self._config_sent = False

        # Whether the pending utterance has been flushed since audio stopped.
        self._flushed_while_idle = False

        # Rate Bodhi is fed at, and the resampler used when it differs from
        # the pipeline's rate. Both are resolved in start().
        self._bodhi_sample_rate = 0
        self._resampler = None

    def __str__(self):
        return f"{self.name} [{self._transaction_id}]"

    @property
    def transaction_id(self) -> str:
        """The Bodhi transaction id of the current connection, for log correlation."""
        return self._transaction_id

    def can_generate_metrics(self) -> bool:
        """Whether this service can generate performance metrics.

        Returns:
            True.
        """
        return True

    async def start(self, frame: StartFrame):
        """Resolve the audio rate and connect to Bodhi.

        Args:
            frame: The start frame containing initialization parameters.
        """
        await super().start(frame)

        if self.sample_rate in BODHI_SAMPLE_RATES:
            self._bodhi_sample_rate = self.sample_rate
        else:
            self._bodhi_sample_rate = 16000
            self._resampler = create_stream_resampler()
            logger.debug(
                f"{self} pipeline runs at {self.sample_rate} Hz; "
                f"resampling to {self._bodhi_sample_rate} Hz for Bodhi"
            )

        await self._connect()

    async def stop(self, frame: EndFrame):
        """Signal end of audio and disconnect.

        Args:
            frame: The end frame triggering service shutdown.
        """
        # Flush first: pipecat >= 1.5 disconnects inside
        # WebsocketSTTService.stop(), so eof after it would never go out.
        await self._send_eof()
        await super().stop(frame)
        await self._disconnect()

    async def cancel(self, frame: CancelFrame):
        """Disconnect without waiting for a final transcript.

        Args:
            frame: The cancel frame triggering service cancellation.
        """
        await super().cancel(frame)
        await self._disconnect()

    async def _update_settings(self, delta: Settings) -> dict[str, Any]:
        """Apply a settings delta, reconnecting if the config message changed.

        Bodhi accepts one config message per connection, so a changed model,
        hotword list, number parsing or endpointing value only takes effect on
        a new connection. The reconnect is deferred until the user stops
        speaking.

        Args:
            delta: A settings delta.

        Returns:
            Dict mapping changed field names to their previous values.
        """
        changed = await super()._update_settings(delta)
        if not changed:
            return changed

        if "model" in changed and self._language_follows_model and not is_given(delta.language):
            self._settings.language = language_from_bodhi_model(assert_given(self._settings.model))

        if _WIRE_SETTINGS.intersection(changed):
            await self._request_reconnect()

        return changed

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame | None, None]:
        """Send audio to Bodhi.

        Transcripts arrive asynchronously on the receive task, so nothing is
        yielded here.

        Args:
            audio: Raw 16-bit PCM audio bytes at the pipeline's sample rate.

        Yields:
            None.
        """
        await self.start_processing_metrics()

        if self._resampler is not None:
            audio = await self._resampler.resample(
                audio, self.sample_rate, self._bodhi_sample_rate
            )

        await self._send_audio(audio)
        yield None

    async def _connect(self):
        """Connect to Bodhi and start the receive task."""
        await self._connect_websocket()

        await super()._connect()

        if self._websocket and not self._receive_task:
            self._receive_task = self.create_task(self._receive_task_handler(self._report_error))

    async def _disconnect(self):
        """Stop the receive task and close the connection."""
        await super()._disconnect()

        if self._receive_task:
            await self.cancel_task(self._receive_task)
            self._receive_task = None

        await self._disconnect_websocket()

    async def _connect_websocket(self):
        """Open the websocket and send the config message."""
        try:
            if self._websocket and self._websocket.state is State.OPEN:
                return

            logger.debug(f"{self.name} connecting to Bodhi at {self._url}")
            self._config_sent = False
            # Both header styles: the STT endpoint reads x-api-key and the TTS
            # endpoint reads Authorization, and each ignores the other. Sending
            # both keeps this working whichever a deployment accepts.
            headers = {
                "x-api-key": self._api_key,
                "Authorization": f"Bearer {self._api_key}",
            }
            try:
                self._websocket = await websocket_connect(self._url, additional_headers=headers)
            except TypeError:
                # websockets < 14 spells it extra_headers.
                self._websocket = await websocket_connect(self._url, extra_headers=headers)

            await self._send_config()
            await self._call_event_handler("on_connected")
            logger.debug(f"{self} connected to Bodhi")
        except Exception as e:
            self._websocket = None
            self._config_sent = False
            await self.push_error(error_msg=f"{self} unable to connect to Bodhi: {e}", exception=e)

    async def _disconnect_websocket(self):
        """Close the websocket connection."""
        try:
            if self._websocket and self._websocket.state is State.OPEN:
                logger.debug(f"{self} disconnecting from Bodhi")
                await self._websocket.close()
        except Exception as e:
            await self.push_error(error_msg=f"{self} error closing websocket: {e}", exception=e)
        finally:
            self._websocket = None
            self._config_sent = False
            await self._call_event_handler("on_disconnected")

    def _build_config(self) -> dict[str, Any]:
        """Build Bodhi's config message for the current settings."""
        s = self._settings

        config: dict[str, Any] = {
            "transaction_id": self._transaction_id,
            "model": assert_given(s.model),
            "sample_rate": self._bodhi_sample_rate,
            "channels": 1,
            "parse_number": bool(assert_given(s.parse_number)),
            "aux": self._aux,
            "exclude_partial": not self._interim_results,
        }

        hotwords = assert_given(s.hotwords)
        if hotwords:
            config["hotwords"] = [
                {"phrase": hw.phrase, **({} if hw.score is None else {"score": hw.score})}
                for hw in hotwords
            ]

        endpoint_silence_duration = assert_given(s.endpoint_silence_duration)
        if endpoint_silence_duration is not None:
            config["endpoint_silence_duration"] = endpoint_silence_duration

        return {"config": config}

    async def _send_config(self):
        """Send the one config message this connection is allowed."""
        if self._websocket is None:
            return

        self._transaction_id = self._fixed_transaction_id or str(uuid.uuid4())
        config = self._build_config()
        await self._websocket.send(json.dumps(config))
        self._config_sent = True
        logger.debug(f"{self} sent config: {config}")

    async def _send_audio(self, audio: bytes):
        """Send an audio chunk, reconnecting once if the send fails."""
        if not self._config_sent:
            return
        self._flushed_while_idle = False
        await self.send_with_retry(audio, self._report_error)

    async def _send_eof(self):
        """Tell Bodhi no more audio is coming, so it flushes a final transcript."""
        if self._websocket and self._websocket.state is State.OPEN:
            try:
                await self._websocket.send(json.dumps({"eof": 1}))
            except Exception as e:
                logger.warning(f"{self} failed to send eof: {e}")

    def _is_keepalive_ready(self) -> bool:
        """Whether silence can be sent right now.

        Returns:
            True when the websocket is open and the config message has landed.
        """
        return self._config_sent and super()._is_keepalive_ready()

    async def _send_keepalive(self, silence: bytes):
        """Send silence at Bodhi's sample rate to hold the connection open.

        The first one after audio stops is long enough for Bodhi to endpoint,
        so a pending utterance is finalised during the gap rather than merged
        with whatever follows it.

        Args:
            silence: Silence generated at the pipeline's rate, ignored in
                favour of silence sized for Bodhi's rate.
        """
        if self._websocket is None:
            return

        if self._flushed_while_idle:
            seconds = _KEEPALIVE_SILENCE
        else:
            seconds = _IDLE_FLUSH_SILENCE
            self._flushed_while_idle = True

        num_samples = int(self._bodhi_sample_rate * seconds)
        await self._websocket.send(b"\x00" * (num_samples * 2))

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

    @property
    def _frame_language(self) -> Language | None:
        """The language to tag transcription frames with."""
        language = assert_given(self._settings.language)
        if language is None or isinstance(language, Language):
            return language
        try:
            return Language(language)
        except ValueError:
            return None

    def _is_low_confidence(self, content: dict[str, Any]) -> bool:
        """Whether a final should be dropped for low confidence.

        Args:
            content: The Bodhi message.

        Returns:
            True when Bodhi reported a confidence below ``min_confidence``. A
            message without a confidence value is always kept.
        """
        if self._min_confidence <= 0:
            return False

        confidence = (content.get("segment_meta") or {}).get("confidence")
        if confidence is None or confidence >= self._min_confidence:
            return False

        logger.debug(
            f"{self} dropping transcript at confidence {confidence:.2f} "
            f"(below {self._min_confidence}): {content.get('text')!r}"
        )
        return True

    @traced_stt
    async def _handle_transcription(
        self, transcript: str, is_final: bool, language: Language | None = None
    ):
        await self.stop_processing_metrics()

    async def _receive_messages(self):
        """Receive transcripts from Bodhi and push them as frames."""
        async for message in self._get_websocket():
            if isinstance(message, bytes):
                continue

            try:
                content = json.loads(message)
            except json.JSONDecodeError:
                logger.warning(f"{self} received non-JSON message: {message}")
                continue

            if content.get("error"):
                await self.push_error(
                    error_msg=f"{self} Bodhi error {content['error']}: {content.get('message', '')}"
                )
                continue

            text = (content.get("text") or "").strip()
            if not text:
                continue

            message_type = content.get("type")
            language = self._frame_language

            if message_type == "complete":
                if self._is_low_confidence(content):
                    continue
                await self.push_frame(
                    TranscriptionFrame(
                        text,
                        self._user_id,
                        time_now_iso8601(),
                        language,
                        result=content,
                        finalized=True,
                    )
                )
                await self._handle_transcription(
                    transcript=text, is_final=True, language=language
                )
            elif message_type == "partial":
                if not self._interim_results:
                    continue
                await self.push_frame(
                    InterimTranscriptionFrame(
                        text,
                        self._user_id,
                        time_now_iso8601(),
                        language,
                        result=content,
                    )
                )
            else:
                logger.trace(f"{self} ignoring message of type {message_type}: {message}")
