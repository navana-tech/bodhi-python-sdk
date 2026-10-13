#
# Bodhi (Navana Tech) Speech-to-Text service for LiveKit Agents.
#
# Install with `pip install "bodhi-api-sdk[livekit]"`; no changes to
# livekit-agents itself are needed. The file is self-contained, so it can also
# be copied into a project that pins an older bodhi-api-sdk.
#

"""Bodhi Speech-to-Text plugin for LiveKit Agents 1.x.

Exposes :class:`BodhiSTT`, a ``livekit.agents.stt.STT`` provider backed by
Bodhi's streaming ASR websocket. Partial results arrive as
``INTERIM_TRANSCRIPT`` events and endpointed finals as ``FINAL_TRANSCRIPT``;
``stream()`` is the real path a voice agent uses, with ``_recognize_impl``
provided so one-shot ``recognize()`` works too.

Protocol notes that shape the implementation (same server as the Pipecat
integration):

* Auth travels as ``x-api-key: <key>`` on the upgrade; ``Authorization: Bearer``
  is also sent so JWT deployments work, and each endpoint ignores the header it
  does not use.
* The websocket is served on the bare host (no path). The first message is a
  single ``{"config": {...}}`` frame; audio follows as raw little-endian PCM16
  binary frames, and ``{"eof": 1}`` tells the server to flush a final.
* Responses are JSON ``{"type": "partial" | "complete", "text": ...}``. A
  ``complete`` is an endpointed final. Confidence, when requested with
  ``aux=True``, is at ``segment_meta.confidence``.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from dataclasses import dataclass, field
from typing import Any

try:
    import livekit.agents  # noqa: F401
except ImportError as e:  # pragma: no cover - depends on how the SDK was installed
    raise ImportError(
        'BodhiSTT needs LiveKit Agents: pip install "bodhi-api-sdk[livekit]"'
    ) from e

import aiohttp

from livekit import rtc
from livekit.agents import (
    APIConnectionError,
    APIConnectOptions,
    APIStatusError,
    APITimeoutError,
    stt,
    utils,
)
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN, NotGivenOr
from livekit.agents.utils import AudioBuffer, is_given

#: Default base URL; a deployment on another host overrides it with ``base_url``.
BODHI_STT_BASE_URL = "wss://stt.navana.ai"

#: Bodhi models run at a fixed input rate; the base class resamples the
#: pipeline's audio to this before it is sent.
BODHI_STT_DEFAULT_SAMPLE_RATE = 16000

#: Default model. This is deployment-specific - pass ``model=`` for the model a
#: given Bodhi instance serves.
BODHI_STT_DEFAULT_MODEL = "hi-general-v2-8khz"

#: Drop finals below this utterance confidence; 0 keeps everything. Only applies
#: when ``aux=True``, since confidence rides on the aux metadata.
DEFAULT_MIN_CONFIDENCE = 0.5


@dataclass
class BodhiHotword:
    """A hotword to bias recognition towards.

    Attributes:
        phrase: The word or phrase to boost.
        score: Boost strength; Bodhi's default applies when omitted.
    """

    phrase: str
    score: float | None = None


def _language_from_model(model: str) -> str:
    """Derive a two-letter language code from a Bodhi model name's prefix."""
    return model.split("-", 1)[0].lower()


@dataclass
class _STTOptions:
    api_key: str
    base_url: str
    model: str
    language: str
    sample_rate: int
    parse_number: bool
    aux: bool
    interim_results: bool
    min_confidence: float
    hotwords: list[BodhiHotword] | None
    endpoint_silence_duration: float | None

    @property
    def auth_headers(self) -> dict[str, str]:
        # STT hosts read x-api-key; Authorization is sent too for JWT hosts.
        return {
            "x-api-key": self.api_key,
            "Authorization": f"Bearer {self.api_key}",
        }

    def build_config(self) -> dict[str, Any]:
        config: dict[str, Any] = {
            "transaction_id": str(uuid.uuid4()),
            "model": self.model,
            "sample_rate": self.sample_rate,
            "channels": 1,
            "parse_number": self.parse_number,
            "aux": self.aux,
            # The server emits partials unless told not to.
            "exclude_partial": not self.interim_results,
        }
        if self.hotwords:
            config["hotwords"] = [
                {"phrase": hw.phrase, **({} if hw.score is None else {"score": hw.score})}
                for hw in self.hotwords
            ]
        if self.endpoint_silence_duration is not None:
            config["endpoint_silence_duration"] = self.endpoint_silence_duration
        return {"config": config}


class BodhiSTT(stt.STT):
    """Speech-to-Text for LiveKit Agents backed by Bodhi.

    Example::

        from bodhi.integrations.livekit_stt import BodhiSTT, BodhiHotword

        stt = BodhiSTT(
            api_key=os.environ["BODHI_API_KEY"],
            model="hi-general-v2-8khz",
            hotwords=[BodhiHotword("बजाज फिनसर्व", 2.0)],
        )
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = BODHI_STT_BASE_URL,
        model: str = BODHI_STT_DEFAULT_MODEL,
        language: str | None = None,
        sample_rate: int = BODHI_STT_DEFAULT_SAMPLE_RATE,
        interim_results: bool = True,
        parse_number: bool = False,
        aux: bool = False,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
        hotwords: list[BodhiHotword] | None = None,
        endpoint_silence_duration: float | None = None,
        http_session: aiohttp.ClientSession | None = None,
    ) -> None:
        """Create a Bodhi STT provider.

        Args:
            api_key: Bodhi API key. Falls back to ``$BODHI_API_KEY``. Sent as
                both ``x-api-key`` and ``Authorization: Bearer``.
            base_url: Websocket base URL of the Bodhi STT deployment, e.g.
                ``wss://stt.navana.ai``. The socket is served on the bare host.
            model: Bodhi model name, e.g. ``hi-general-v2-8khz`` - this is
                deployment-specific.
            language: Language code to tag transcripts with. Defaults to the
                model name's two-letter prefix.
            sample_rate: Rate in Hz the audio is sent to Bodhi at; the pipeline's
                audio is resampled to this automatically.
            interim_results: Emit ``INTERIM_TRANSCRIPT`` events for partials.
            parse_number: Ask Bodhi to normalise numbers, dates and currency.
            aux: Attach timing/confidence metadata to each result. Required for
                ``min_confidence`` to have anything to filter on.
            min_confidence: Drop finals below this confidence (needs ``aux``);
                0 keeps everything.
            hotwords: Phrases to bias recognition towards.
            endpoint_silence_duration: Trailing silence in seconds before Bodhi
                finalises an utterance (server clamps ~0.44-1.2).
            http_session: An existing aiohttp session to reuse; one is created
                lazily otherwise.
        """
        super().__init__(
            capabilities=stt.STTCapabilities(
                streaming=True, interim_results=interim_results
            )
        )

        key = api_key or os.environ.get("BODHI_API_KEY")
        if not key:
            raise ValueError(
                "Bodhi API key is required: pass api_key= or set BODHI_API_KEY"
            )

        self._opts = _STTOptions(
            api_key=key,
            base_url=base_url.rstrip("/"),
            model=model,
            language=(language or _language_from_model(model)),
            sample_rate=sample_rate,
            parse_number=parse_number,
            aux=aux,
            interim_results=interim_results,
            min_confidence=min_confidence,
            hotwords=hotwords,
            endpoint_silence_duration=endpoint_silence_duration,
        )
        self._session = http_session

    def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = utils.http_context.http_session()
        return self._session

    def update_options(
        self,
        *,
        model: str | None = None,
        language: str | None = None,
        hotwords: list[BodhiHotword] | None = None,
        endpoint_silence_duration: float | None = None,
    ) -> None:
        """Change recognition options for subsequent streams."""
        if model is not None:
            self._opts.model = model
            self._opts.language = language or _language_from_model(model)
        if language is not None:
            self._opts.language = language
        if hotwords is not None:
            self._opts.hotwords = hotwords
        if endpoint_silence_duration is not None:
            self._opts.endpoint_silence_duration = endpoint_silence_duration

    def _speech_data(self, content: dict[str, Any]) -> stt.SpeechData:
        meta = content.get("segment_meta") or {}
        confidence = meta.get("confidence")
        return stt.SpeechData(
            language=self._opts.language,
            text=(content.get("text") or "").strip(),
            confidence=float(confidence) if confidence is not None else 1.0,
        )

    def _below_min_confidence(self, content: dict[str, Any]) -> bool:
        if self._opts.min_confidence <= 0:
            return False
        confidence = (content.get("segment_meta") or {}).get("confidence")
        return confidence is not None and confidence < self._opts.min_confidence

    async def _recognize_impl(
        self,
        buffer: AudioBuffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> stt.SpeechEvent:
        """One-shot recognition: stream the whole buffer, return the final."""
        frame = rtc.combine_audio_frames(buffer)
        # Resample the buffer to Bodhi's rate if the caller's differs.
        pcm = frame.data.tobytes()
        if frame.sample_rate != self._opts.sample_rate:
            resampler = rtc.AudioResampler(frame.sample_rate, self._opts.sample_rate)
            out = bytearray()
            for f in resampler.push(frame):
                out += f.data.tobytes()
            for f in resampler.flush():
                out += f.data.tobytes()
            pcm = bytes(out)

        request_id = str(uuid.uuid4())
        finals: list[stt.SpeechData] = []
        try:
            async with self._ensure_session().ws_connect(
                self._opts.base_url,
                headers=self._opts.auth_headers,
                timeout=aiohttp.ClientWSTimeout(ws_close=conn_options.timeout),
            ) as ws:
                await ws.send_str(json.dumps(self._opts.build_config()))
                # Send in ~1s chunks so the server can stream through it.
                step = self._opts.sample_rate * 2  # 1s of PCM16
                for i in range(0, len(pcm), step):
                    await ws.send_bytes(pcm[i : i + step])
                await ws.send_str(json.dumps({"eof": 1}))

                async for msg in ws:
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        continue
                    content = json.loads(msg.data)
                    if content.get("error"):
                        raise APIStatusError(
                            message=f"Bodhi error {content['error']}: {content.get('message', '')}",
                            status_code=-1,
                            request_id=request_id,
                            body=None,
                        )
                    if content.get("type") == "complete":
                        if self._below_min_confidence(content):
                            continue
                        data = self._speech_data(content)
                        if data.text:
                            finals.append(data)
        except (APIStatusError, APITimeoutError, APIConnectionError):
            raise
        except TimeoutError:
            raise APITimeoutError() from None
        except Exception as e:
            raise APIConnectionError() from e

        if not finals:
            finals = [stt.SpeechData(language=self._opts.language, text="")]
        return stt.SpeechEvent(
            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
            request_id=request_id,
            alternatives=finals,
        )

    def stream(
        self,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> SpeechStream:
        return SpeechStream(stt=self, opts=self._opts, conn_options=conn_options)


class SpeechStream(stt.SpeechStream):
    """A live Bodhi recognition session."""

    def __init__(
        self,
        *,
        stt: BodhiSTT,
        opts: _STTOptions,
        conn_options: APIConnectOptions,
    ) -> None:
        super().__init__(stt=stt, conn_options=conn_options, sample_rate=opts.sample_rate)
        self._bodhi = stt
        self._opts = opts
        self._speaking = False

    async def _run(self) -> None:
        request_id = str(uuid.uuid4())
        closing = False

        async def _send_task(ws: aiohttp.ClientWebSocketResponse) -> None:
            nonlocal closing
            # Frames reach _input_ch already resampled to the Bodhi rate; rebuffer
            # into ~100ms chunks to keep the message rate sane.
            samples_100ms = self._opts.sample_rate // 10
            bstream = utils.audio.AudioByteStream(
                sample_rate=self._opts.sample_rate,
                num_channels=1,
                samples_per_channel=samples_100ms,
            )
            async for data in self._input_ch:
                frames: list[rtc.AudioFrame] = []
                if isinstance(data, rtc.AudioFrame):
                    frames.extend(bstream.write(data.data.tobytes()))
                elif isinstance(data, self._FlushSentinel):
                    frames.extend(bstream.flush())
                for frame in frames:
                    await ws.send_bytes(frame.data.tobytes())
            closing = True
            await ws.send_str(json.dumps({"eof": 1}))

        async def _recv_task(ws: aiohttp.ClientWebSocketResponse) -> None:
            while True:
                msg = await ws.receive()
                if msg.type in (
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSING,
                ):
                    if closing:
                        return
                    raise APIStatusError(
                        "Bodhi STT connection closed unexpectedly",
                        status_code=ws.close_code or -1,
                        request_id=request_id,
                        body=None,
                    )
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue

                content = json.loads(msg.data)
                if content.get("error"):
                    raise APIStatusError(
                        message=f"Bodhi error {content['error']}: {content.get('message', '')}",
                        status_code=-1,
                        request_id=request_id,
                        body=None,
                    )

                text = (content.get("text") or "").strip()
                if not text:
                    continue
                kind = content.get("type")

                if not self._speaking:
                    self._speaking = True
                    self._event_ch.send_nowait(
                        stt.SpeechEvent(type=stt.SpeechEventType.START_OF_SPEECH)
                    )

                if kind == "complete":
                    if self._bodhi._below_min_confidence(content):
                        continue
                    self._event_ch.send_nowait(
                        stt.SpeechEvent(
                            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                            request_id=request_id,
                            alternatives=[self._bodhi._speech_data(content)],
                        )
                    )
                    if self._speaking:
                        self._speaking = False
                        self._event_ch.send_nowait(
                            stt.SpeechEvent(type=stt.SpeechEventType.END_OF_SPEECH)
                        )
                elif kind == "partial":
                    if not self._opts.interim_results:
                        continue
                    self._event_ch.send_nowait(
                        stt.SpeechEvent(
                            type=stt.SpeechEventType.INTERIM_TRANSCRIPT,
                            request_id=request_id,
                            alternatives=[self._bodhi._speech_data(content)],
                        )
                    )

        try:
            async with self._bodhi._ensure_session().ws_connect(
                self._opts.base_url, headers=self._opts.auth_headers
            ) as ws:
                await ws.send_str(json.dumps(self._opts.build_config()))
                tasks = [
                    asyncio.create_task(_send_task(ws)),
                    asyncio.create_task(_recv_task(ws)),
                ]
                try:
                    await asyncio.gather(*tasks)
                finally:
                    await utils.aio.gracefully_cancel(*tasks)
        except (APIStatusError, APITimeoutError, APIConnectionError):
            raise
        except TimeoutError:
            raise APITimeoutError() from None
        except Exception as e:
            raise APIConnectionError() from e
