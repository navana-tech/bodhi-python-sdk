# tts_client.py
"""Bodhi Text-to-Speech client.

Two ways to synthesize, both returning raw audio with no container:

* :meth:`BodhiTTSClient.synthesize` — one HTTP request, the whole clip at once.
  Simplest, and right for anything that is not a live conversation.
* :meth:`BodhiTTSClient.stream` — a websocket, audio yielded chunk by chunk as
  the server produces it. First audio arrives in roughly the time the HTTP call
  takes to return *anything*, which is what a voice agent needs.

For a Pipecat pipeline use :class:`bodhi.integrations.pipecat_tts.BodhiTTSService`
instead — it speaks this same protocol, but hands audio to Pipecat as frames so
barge-in can cut an utterance short.
"""

import json
import os
import struct
import wave
from typing import Any, AsyncGenerator, Dict, Optional, Sequence, Union

import aiohttp

from .utils.exceptions import BodhiAPIError, ConfigurationError, WebSocketError
from .utils.logger import logger

#: Streaming endpoint. The server serves the websocket on ``/v1`` and refuses
#: any other path; the bare host answers with a redirect, which not every
#: client follows, so address it directly.
BODHI_TTS_STREAM_URL = "wss://tts.navana.ai/v1"

#: One-shot synthesis endpoint.
BODHI_TTS_HTTP_URL = "https://tts.navana.ai/tts/bytes"

DEFAULT_VOICE = "default_female"
DEFAULT_LANGUAGE = "hi"
DEFAULT_SAMPLE_RATE = 24000
DEFAULT_ENCODING = "pcm16"

#: Rates the server will synthesize at.
SAMPLE_RATES = (8000, 16000, 24000)

#: ``pcm16`` is 16-bit signed PCM, ``mulaw`` is 8-bit G.711 (telephony),
#: ``float32`` is 32-bit float.
ENCODINGS = ("pcm16", "mulaw", "float32")

_SAMPLE_WIDTHS = {"pcm16": 2, "mulaw": 1, "float32": 4}


class TTSAudio:
    """Synthesized audio, plus what is needed to interpret the bytes.

    Attributes:
        audio: Raw audio with no header — see :meth:`save`.
        sample_rate: Rate the server actually used, which is authoritative.
        encoding: One of :data:`ENCODINGS`.
        duration_ms: Audio duration in milliseconds, when the server reported it.
    """

    __slots__ = ("audio", "sample_rate", "encoding", "duration_ms")

    def __init__(self, audio: bytes, sample_rate: int, encoding: str,
                 duration_ms: Optional[int] = None):
        self.audio = audio
        self.sample_rate = sample_rate
        self.encoding = encoding
        self.duration_ms = duration_ms

    def __len__(self) -> int:
        return len(self.audio)

    def __repr__(self) -> str:
        return (f"TTSAudio({len(self.audio)} bytes, {self.sample_rate} Hz, "
                f"{self.encoding}, {self.duration_ms} ms)")

    def save(self, path: str) -> str:
        """Write the audio to a playable WAV file.

        Raw PCM will not play in anything until it has a header; this adds one.

        Args:
            path: Where to write the ``.wav``.

        Returns:
            The path written.

        Raises:
            ValueError: For ``float32``, which WAV cannot carry this way — ask
                for ``pcm16`` or ``mulaw`` if you want a file.
        """
        if self.encoding == "float32":
            raise ValueError(
                "float32 audio cannot be saved as WAV; synthesize with "
                "encoding='pcm16' (or 'mulaw' for telephony) to save a file"
            )
        if self.encoding == "mulaw":
            # Python's wave module handles neither reading nor writing u-law,
            # so the header goes out by hand: WAVE_FORMAT_MULAW (7), 8-bit,
            # plus the 'fact' chunk non-PCM formats are supposed to carry.
            # Players and ffmpeg read the result; Python's wave will not.
            self._write_mulaw_wav(path)
            return path
        with wave.open(path, "wb") as f:
            f.setnchannels(1)
            f.setsampwidth(_SAMPLE_WIDTHS[self.encoding])
            f.setframerate(self.sample_rate)
            f.writeframes(self.audio)
        return path

    def _write_mulaw_wav(self, path: str) -> None:
        n = len(self.audio)
        pad = n % 2
        with open(path, "wb") as f:
            f.write(b"RIFF")
            f.write(struct.pack("<I", 4 + 26 + 12 + 8 + n + pad))
            f.write(b"WAVE")
            f.write(b"fmt ")
            f.write(struct.pack("<IHHIIHHH", 18, 7, 1, self.sample_rate,
                                self.sample_rate, 1, 8, 0))
            f.write(b"fact")
            f.write(struct.pack("<II", 4, n))
            f.write(b"data")
            f.write(struct.pack("<I", n))
            f.write(self.audio)
            if pad:
                f.write(b"\x00")


class BodhiTTSClient:
    """Client for Bodhi text-to-speech.

    Example:
        ::

            client = BodhiTTSClient(api_key=os.environ["BODHI_API_KEY"])

            speech = await client.synthesize("नमस्ते, यह बोधि की आवाज़ है।")
            speech.save("hello.wav")

            async for chunk in client.stream("नमस्ते, यह बोधि की आवाज़ है।"):
                play(chunk)
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        url: Optional[str] = None,
        http_url: Optional[str] = None,
    ):
        """Initialize the TTS client.

        Args:
            api_key: Bodhi API key. Falls back to ``$BODHI_API_KEY``.
            url: Streaming websocket URL. Defaults to :data:`BODHI_TTS_STREAM_URL`.
            http_url: One-shot synthesis URL. Defaults to :data:`BODHI_TTS_HTTP_URL`.

        Raises:
            ConfigurationError: If no API key is given or found.
        """
        self.api_key = api_key or os.environ.get("BODHI_API_KEY")
        if not self.api_key:
            raise ConfigurationError(
                "API key is required: pass api_key= or set BODHI_API_KEY"
            )
        self.url = url or BODHI_TTS_STREAM_URL
        self.http_url = http_url or BODHI_TTS_HTTP_URL

    @property
    def _headers(self) -> Dict[str, str]:
        # Both styles: the streaming endpoint reads Authorization, the HTTP one
        # reads X-API-Key first, and each ignores the other.
        return {
            "X-API-Key": self.api_key,
            "Authorization": f"Bearer {self.api_key}",
        }

    @staticmethod
    def _check(text: Union[str, Sequence[str]], sample_rate: int, encoding: str) -> None:
        if sample_rate not in SAMPLE_RATES:
            raise ConfigurationError(
                f"sample_rate must be one of {SAMPLE_RATES}, got {sample_rate}"
            )
        if encoding not in ENCODINGS:
            raise ConfigurationError(
                f"encoding must be one of {ENCODINGS}, got {encoding!r}"
            )
        texts = [text] if isinstance(text, str) else list(text)
        if not texts or any(not t or not t.strip() for t in texts):
            # The server rejects this too; failing here names the argument.
            raise ConfigurationError("text must be a non-empty string")

    async def synthesize(
        self,
        text: str,
        *,
        lang: str = DEFAULT_LANGUAGE,
        voice: str = DEFAULT_VOICE,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        encoding: str = DEFAULT_ENCODING,
        speed: Optional[float] = None,
        num_step: Optional[int] = None,
        guidance_scale: Optional[float] = None,
        use_fast: bool = False,
        g2p_overrides: Optional[Dict[str, Any]] = None,
        timeout: float = 120.0,
    ) -> TTSAudio:
        """Synthesize a whole clip in one request.

        Args:
            text: Text to speak, in the language's own script.
            lang: Language code, e.g. ``hi``, ``en``, ``ta``.
            voice: Voice id. A cloned ``cv_`` voice is only valid for the
                language it was cloned under.
            sample_rate: One of :data:`SAMPLE_RATES`.
            encoding: One of :data:`ENCODINGS`. ``8000`` + ``mulaw`` for telephony.
            speed: Playback rate, ``0.25``–``4.0``. Streaming does not support this.
            num_step: Flow-matching steps, ``1``–``100``. Higher is slower, better.
            guidance_scale: Classifier-free guidance. Defaults per voice.
            use_fast: Use the distilled model where one is available.
            g2p_overrides: Per-request pronunciation overrides.
            timeout: Seconds to wait for the whole clip.

        Returns:
            The audio, with the rate and encoding the server actually used.

        Raises:
            ConfigurationError: If an argument is out of range or text is empty.
            BodhiAPIError: If the server rejects the request.
        """
        self._check(text, sample_rate, encoding)

        body: Dict[str, Any] = {
            "text": text,
            "lang": lang,
            "voice": voice,
            "output_format": f"{sample_rate}:{encoding}",
        }
        # Unknown fields are rejected, so only send what was actually set.
        if speed is not None:
            body["speed"] = speed
        if num_step is not None:
            body["num_step"] = num_step
        if guidance_scale is not None:
            body["guidance_scale"] = guidance_scale
        if use_fast:
            body["use_fast"] = True
        if g2p_overrides is not None:
            body["g2p_overrides"] = g2p_overrides

        async with aiohttp.ClientSession() as session:
            async with session.post(
                self.http_url,
                json=body,
                headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as response:
                payload = await response.read()
                if response.status != 200:
                    raise BodhiAPIError(
                        f"synthesis failed ({response.status}): "
                        f"{payload.decode('utf-8', 'replace')[:200]}",
                        code=response.status,
                    )
                # The server's own rate wins over the one we asked for.
                rate = int(response.headers.get("X-Sample-Rate", sample_rate))
                duration = response.headers.get("X-Audio-Duration-Ms")
                return TTSAudio(
                    audio=payload,
                    sample_rate=rate,
                    encoding=response.headers.get("X-Encoding", encoding),
                    duration_ms=int(duration) if duration else None,
                )

    async def stream(
        self,
        text: Union[str, Sequence[str]],
        *,
        lang: str = DEFAULT_LANGUAGE,
        voice: str = DEFAULT_VOICE,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        encoding: str = DEFAULT_ENCODING,
        num_step: Optional[int] = None,
        guidance_scale: Optional[float] = None,
        use_fast: bool = False,
        timeout: float = 60.0,
    ) -> AsyncGenerator[bytes, None]:
        """Synthesize over a websocket, yielding audio as it is produced.

        Pass a sequence of strings to speak several utterances down one
        connection — each is sent as its own text frame and their audio arrives
        in order, which is how you feed sentences from an LLM as they appear.

        Args:
            text: A string, or a sequence of them.
            lang: Language code.
            voice: Voice id.
            sample_rate: One of :data:`SAMPLE_RATES`.
            encoding: One of :data:`ENCODINGS`.
            num_step: Flow-matching steps, ``1``–``100``.
            guidance_scale: Classifier-free guidance.
            use_fast: Use the distilled model where one is available.
            timeout: Seconds to wait for each message from the server.

        Yields:
            Audio chunks, in order. ``speed`` is deliberately absent: the
            streaming handshake rejects it.

        Raises:
            ConfigurationError: If an argument is out of range or text is empty.
            WebSocketError: If the handshake is refused or the server errors.
        """
        self._check(text, sample_rate, encoding)
        texts = [text] if isinstance(text, str) else list(text)

        hello: Dict[str, Any] = {
            "type": "hello",
            "lang": lang,
            "voice": voice,
            "output_format": f"{sample_rate}:{encoding}",
        }
        if num_step is not None:
            hello["num_step"] = num_step
        if guidance_scale is not None:
            hello["guidance_scale"] = guidance_scale
        if use_fast:
            hello["use_fast"] = True

        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(
                self.url, headers=self._headers, heartbeat=None
            ) as ws:
                await ws.send_str(json.dumps(hello))

                ready = json.loads((await ws.receive_str(timeout=timeout)))
                if ready.get("type") != "ready":
                    raise WebSocketError(
                        f"handshake refused: {ready.get('code')} "
                        f"{ready.get('message')}",
                        code=ready.get("code"),
                    )
                logger.debug(f"Bodhi TTS ready: {ready.get('session_id')}")

                for seq, chunk_text in enumerate(texts):
                    await ws.send_str(json.dumps(
                        {"type": "text", "seq": seq, "target_text": chunk_text}
                    ))
                await ws.send_str(json.dumps({"type": "end"}))

                async for message in ws:
                    if message.type == aiohttp.WSMsgType.BINARY:
                        yield message.data
                        continue
                    if message.type in (aiohttp.WSMsgType.CLOSED,
                                        aiohttp.WSMsgType.ERROR):
                        raise WebSocketError("connection closed before 'done'")

                    frame = json.loads(message.data)
                    kind = frame.get("type")
                    if kind == "error":
                        raise WebSocketError(
                            f"Bodhi TTS error {frame.get('code')}: "
                            f"{frame.get('message')}",
                            code=frame.get("code"),
                        )
                    if kind == "done":
                        return
                    # "audio" headers describe the binary frame that follows,
                    # which arrives next; nothing to do with them here.

    async def stream_to_file(
        self, text: Union[str, Sequence[str]], path: str, **kwargs: Any
    ) -> TTSAudio:
        """Stream, collecting the audio into a playable WAV file.

        Args:
            text: A string, or a sequence of them.
            path: Where to write the ``.wav``.
            **kwargs: Passed to :meth:`stream`.

        Returns:
            The collected audio, already written to ``path``.
        """
        chunks = [chunk async for chunk in self.stream(text, **kwargs)]
        raw = b"".join(chunks)
        rate = kwargs.get("sample_rate", DEFAULT_SAMPLE_RATE)
        encoding = kwargs.get("encoding", DEFAULT_ENCODING)
        # Streaming carries no duration header, but the rate and width are known.
        audio = TTSAudio(
            audio=raw,
            sample_rate=rate,
            encoding=encoding,
            duration_ms=round(len(raw) / _SAMPLE_WIDTHS[encoding] / rate * 1000),
        )
        audio.save(path)
        return audio
