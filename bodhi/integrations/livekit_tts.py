#
# Bodhi (Navana Tech) Text-to-Speech service for LiveKit Agents.
#
# Install with `pip install "bodhi-api-sdk[livekit]"`; no changes to
# livekit-agents itself are needed. The file is self-contained, so it can also
# be copied into a project that pins an older bodhi-api-sdk.
#

"""Bodhi Text-to-Speech plugin for LiveKit Agents 1.x.

Exposes :class:`BodhiTTS`, a ``livekit.agents.tts.TTS`` provider backed by
Bodhi's TTS API. It offers both forms LiveKit expects:

* :meth:`BodhiTTS.synthesize` - one HTTP request to ``/tts/bytes`` returning the
  whole clip, wrapped as a ``ChunkedStream``.
* :meth:`BodhiTTS.stream` - a websocket session on ``/v1`` that synthesises each
  input segment as it arrives, wrapped as a ``SynthesizeStream``. This is what a
  voice agent wants, since audio starts coming back before the full text is in.

Protocol notes that shape the implementation (same server as the Pipecat
integration, protocol v2):

* Auth travels as ``Authorization: Bearer <key>``. Some deployments also accept
  ``x-api-key``; both headers are sent so the plugin works against whichever a
  deployment honours (JWT hosts take Bearer, ``bd_`` key hosts take either).
* One ``hello`` per websocket connection sets language, voice and output format,
  and the server answers with ``ready``. Unknown fields in ``hello`` are
  rejected, so a settings change needs a fresh connection.
* Text is sent as ``{"type": "text", "seq": <int>, "target_text": <str>}``.
  Audio comes back as a JSON header ``{"type": "audio", "is_last_chunk": ...}``
  immediately followed by a binary PCM frame. ``{"type": "done"}`` only arrives
  after ``{"type": "end"}`` closes the session.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, replace
from typing import Any

logger = logging.getLogger("bodhi.livekit.tts")

try:
    import livekit.agents  # noqa: F401
except ImportError as e:  # pragma: no cover - depends on how the SDK was installed
    raise ImportError(
        'BodhiTTS needs LiveKit Agents: pip install "bodhi-api-sdk[livekit]"'
    ) from e

import aiohttp

from livekit.agents import (
    APIConnectionError,
    APIConnectOptions,
    APIStatusError,
    APITimeoutError,
    tts,
    utils,
)
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS

#: Default base URL; a deployment on another host overrides it with ``base_url``.
BODHI_TTS_BASE_URL = "https://tts.navana.ai"

#: Sample rates the server will synthesise at.
BODHI_TTS_SAMPLE_RATES = (8000, 16000, 24000)

#: Used when the caller asks for a rate the server does not serve.
BODHI_TTS_DEFAULT_SAMPLE_RATE = 24000

#: Voice every language ships with.
DEFAULT_VOICE = "default_female"

#: Default language (Bodhi serves ten).
DEFAULT_LANGUAGE = "hi"


def _normalize_lang(language: str) -> str:
    """Reduce a BCP-47-ish language tag to Bodhi's two-letter ``lang`` code."""
    return str(language).split("-")[0].lower()


@dataclass
class _TTSOptions:
    api_key: str
    base_url: str
    voice: str
    language: str
    sample_rate: int
    use_fast: bool
    num_step: int | None
    guidance_scale: float | None

    @property
    def output_format(self) -> str:
        return f"{self.sample_rate}:pcm16"

    @property
    def auth_headers(self) -> dict[str, str]:
        # Both styles: JWT hosts read Authorization, bd_ key hosts read either,
        # and each endpoint ignores the header it does not use.
        return {
            "Authorization": f"Bearer {self.api_key}",
            "x-api-key": self.api_key,
        }

    def http_url(self) -> str:
        return f"{self.base_url}/tts/bytes"

    def ws_url(self) -> str:
        # http(s) -> ws(s); the websocket is served on /v1 and refuses any other
        # path (the bare host answers with a 308 to /v1).
        return f"{self.base_url.replace('http', 'ws', 1)}/v1"


class BodhiTTS(tts.TTS):
    """Text-to-Speech for LiveKit Agents backed by Bodhi.

    Example::

        from bodhi.integrations.livekit_tts import BodhiTTS

        tts = BodhiTTS(
            api_key=os.environ["BODHI_API_KEY"],
            language="hi",
            voice="default_female",
        )
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = BODHI_TTS_BASE_URL,
        voice: str = DEFAULT_VOICE,
        language: str = DEFAULT_LANGUAGE,
        sample_rate: int = BODHI_TTS_DEFAULT_SAMPLE_RATE,
        use_fast: bool = False,
        num_step: int | None = None,
        guidance_scale: float | None = None,
        http_session: aiohttp.ClientSession | None = None,
    ) -> None:
        """Create a Bodhi TTS provider.

        Args:
            api_key: Bodhi API key. Falls back to ``$BODHI_API_KEY``. Sent as
                both ``Authorization: Bearer`` and ``x-api-key``.
            base_url: Base URL of the Bodhi TTS deployment, e.g.
                ``https://tts.navana.ai``. ``/tts/bytes`` and ``/v1`` are
                appended for the HTTP and websocket endpoints.
            voice: Voice id for the language, e.g. ``default_female``.
            language: Language code; Bodhi serves ten (``hi``, ``en``, ...).
            sample_rate: Output rate in Hz, one of 8000/16000/24000.
            use_fast: Use the distilled model where one is available.
            num_step: Flow-matching steps, 1-100. Lower is faster and rougher.
            guidance_scale: Classifier-free guidance.
            http_session: An existing aiohttp session to reuse; one is created
                lazily otherwise.
        """
        if sample_rate not in BODHI_TTS_SAMPLE_RATES:
            raise ValueError(
                f"sample_rate must be one of {BODHI_TTS_SAMPLE_RATES}, got {sample_rate}"
            )

        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=True, aligned_transcript=False),
            sample_rate=sample_rate,
            num_channels=1,
        )

        key = api_key or os.environ.get("BODHI_API_KEY")
        if not key:
            raise ValueError(
                "Bodhi API key is required: pass api_key= or set BODHI_API_KEY"
            )

        self._opts = _TTSOptions(
            api_key=key,
            base_url=base_url.rstrip("/"),
            voice=voice,
            language=_normalize_lang(language),
            sample_rate=sample_rate,
            use_fast=use_fast,
            num_step=num_step,
            guidance_scale=guidance_scale,
        )
        self._session = http_session

    def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = utils.http_context.http_session()
        return self._session

    def update_options(
        self,
        *,
        voice: str | None = None,
        language: str | None = None,
        use_fast: bool | None = None,
        num_step: int | None = None,
        guidance_scale: float | None = None,
    ) -> None:
        """Change synthesis options for subsequent requests."""
        if voice is not None:
            self._opts.voice = voice
        if language is not None:
            self._opts.language = _normalize_lang(language)
        if use_fast is not None:
            self._opts.use_fast = use_fast
        if num_step is not None:
            self._opts.num_step = num_step
        if guidance_scale is not None:
            self._opts.guidance_scale = guidance_scale

    def synthesize(
        self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> ChunkedStream:
        return ChunkedStream(tts=self, input_text=text, conn_options=conn_options)

    def stream(
        self, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> SynthesizeStream:
        return SynthesizeStream(tts=self, conn_options=conn_options)


def _hello_frame(opts: _TTSOptions) -> dict[str, Any]:
    """Build the per-connection hello frame; only send knobs that are set."""
    hello: dict[str, Any] = {
        "type": "hello",
        "lang": opts.language,
        "voice": opts.voice or DEFAULT_VOICE,
        "output_format": opts.output_format,
    }
    if opts.use_fast:
        hello["use_fast"] = True
    if opts.num_step is not None:
        hello["num_step"] = opts.num_step
    if opts.guidance_scale is not None:
        hello["guidance_scale"] = opts.guidance_scale
    return hello


class ChunkedStream(tts.ChunkedStream):
    """One-shot synthesis over the HTTP ``/tts/bytes`` endpoint."""

    def __init__(self, *, tts: BodhiTTS, input_text: str, conn_options: APIConnectOptions) -> None:
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._tts: BodhiTTS = tts
        self._opts = replace(tts._opts)

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        body: dict[str, Any] = {
            "text": self._input_text,
            "lang": self._opts.language,
            "voice": self._opts.voice,
            "output_format": self._opts.output_format,
        }
        # Unknown fields are rejected, so only send what is set.
        if self._opts.use_fast:
            body["use_fast"] = True
        if self._opts.num_step is not None:
            body["num_step"] = self._opts.num_step
        if self._opts.guidance_scale is not None:
            body["guidance_scale"] = self._opts.guidance_scale

        try:
            async with self._tts._ensure_session().post(
                self._opts.http_url(),
                headers=self._opts.auth_headers,
                json=body,
                timeout=aiohttp.ClientTimeout(
                    total=30, sock_connect=self._conn_options.timeout
                ),
            ) as resp:
                resp.raise_for_status()

                # The server's own rate wins over the one we asked for.
                sample_rate = int(resp.headers.get("X-Sample-Rate", self._opts.sample_rate))
                output_emitter.initialize(
                    request_id=utils.shortuuid(),
                    sample_rate=sample_rate,
                    num_channels=1,
                    mime_type="audio/pcm",
                )

                async for data, _ in resp.content.iter_chunks():
                    output_emitter.push(data)

                output_emitter.flush()
        except asyncio.TimeoutError:
            raise APITimeoutError() from None
        except aiohttp.ClientResponseError as e:
            raise APIStatusError(
                message=e.message, status_code=e.status, request_id=None, body=None
            ) from None
        except Exception as e:
            raise APIConnectionError() from e


class SynthesizeStream(tts.SynthesizeStream):
    """Streaming synthesis over the websocket ``/v1`` endpoint."""

    def __init__(self, *, tts: BodhiTTS, conn_options: APIConnectOptions) -> None:
        super().__init__(tts=tts, conn_options=conn_options)
        self._tts: BodhiTTS = tts
        self._opts = replace(tts._opts)

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        request_id = utils.shortuuid()
        # The server flushes a full utterance's audio before the next; one
        # segment per connection keeps ordering simple.
        seq = 0

        async def _send_task(ws: aiohttp.ClientWebSocketResponse) -> None:
            nonlocal seq
            buffer: list[str] = []

            async def flush_buffer() -> None:
                nonlocal seq
                text = "".join(buffer).strip()
                buffer.clear()
                if not text:
                    return
                seq += 1
                self._mark_started()
                logger.debug("bodhi-tts send text seq=%d len=%d", seq, len(text))
                await ws.send_str(
                    json.dumps({"type": "text", "seq": seq, "target_text": text})
                )

            async for data in self._input_ch:
                if isinstance(data, self._FlushSentinel):
                    await flush_buffer()
                    continue
                buffer.append(data)

            await flush_buffer()
            logger.debug("bodhi-tts send end (seq total=%d)", seq)
            await ws.send_str(json.dumps({"type": "end"}))

        async def _recv_task(ws: aiohttp.ClientWebSocketResponse) -> None:
            started_segment = False
            total = 0
            while True:
                msg = await ws.receive(timeout=self._conn_options.timeout)
                if msg.type in (
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSING,
                ):
                    logger.debug("bodhi-tts ws closed (pushed=%d bytes)", total)
                    raise APIStatusError(
                        "Bodhi TTS connection closed unexpectedly",
                        request_id=request_id,
                        status_code=ws.close_code or -1,
                        body=None,
                    )

                if msg.type == aiohttp.WSMsgType.BINARY:
                    if not started_segment:
                        output_emitter.start_segment(segment_id=request_id)
                        started_segment = True
                    total += len(msg.data)
                    output_emitter.push(msg.data)
                    continue

                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue

                data = json.loads(msg.data)
                kind = data.get("type")
                if kind == "audio":
                    # Header for the binary frame that follows; nothing to do
                    # until the bytes arrive.
                    continue
                elif kind == "done":
                    logger.debug("bodhi-tts done (pushed=%d bytes)", total)
                    if started_segment:
                        output_emitter.end_segment()
                    output_emitter.end_input()
                    break
                elif kind == "error":
                    raise APIStatusError(
                        message=f"Bodhi TTS error {data.get('code')}: {data.get('message')}",
                        status_code=-1,
                        request_id=request_id,
                        body=None,
                    )

        try:
            logger.debug("bodhi-tts connecting ws %s", self._opts.ws_url())
            async with self._tts._ensure_session().ws_connect(
                self._opts.ws_url(), headers=self._opts.auth_headers
            ) as ws:
                await ws.send_str(json.dumps(_hello_frame(self._opts)))
                ready = json.loads(await ws.receive_str())
                logger.debug("bodhi-tts ready: %s", ready.get("type"))
                if ready.get("type") != "ready":
                    raise APIStatusError(
                        message=f"handshake refused: {ready.get('code')} {ready.get('message')}",
                        status_code=-1,
                        request_id=request_id,
                        body=None,
                    )

                output_emitter.initialize(
                    request_id=request_id,
                    sample_rate=int(ready.get("sample_rate", self._opts.sample_rate)),
                    num_channels=1,
                    mime_type="audio/pcm",
                    stream=True,
                )

                tasks = [
                    asyncio.create_task(_send_task(ws)),
                    asyncio.create_task(_recv_task(ws)),
                ]
                try:
                    await asyncio.gather(*tasks)
                finally:
                    await utils.aio.gracefully_cancel(*tasks)
        except asyncio.TimeoutError:
            logger.warning("bodhi-tts stream timed out")
            raise APITimeoutError() from None
        except aiohttp.ClientResponseError as e:
            logger.warning("bodhi-tts http error %s: %s", e.status, e.message)
            raise APIStatusError(
                message=e.message, status_code=e.status, request_id=None, body=None
            ) from None
        except APIStatusError:
            raise
        except Exception as e:
            logger.warning("bodhi-tts stream failed: %r", e)
            raise APIConnectionError() from e
