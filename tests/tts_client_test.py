#
# Smoke test for bodhi.tts_client against a fake Bodhi TTS server.
#
# Needs no credentials and no network:
#
#   python tests/tts_client_test.py
#

import asyncio
import json
import struct
import sys
import tempfile
import wave
from pathlib import Path

from aiohttp import web

# Run against this checkout rather than an installed bodhi-api-sdk.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bodhi import BodhiTTSClient, TTSAudio
from bodhi.utils.exceptions import BodhiAPIError, ConfigurationError, WebSocketError

CHUNK = b"\x22\x00" * 1200  # 50ms of 24kHz PCM16


def fake_server(state, *, chunks=2, http_status=200, ws_error=None):
    """Serves both entry points: POST /tts/bytes and the websocket on /v1."""

    async def bytes_handler(request):
        state["headers"] = dict(request.headers)
        state["body"] = await request.json()
        if http_status != 200:
            return web.Response(status=http_status, body=b'{"error": "nope"}')
        audio = CHUNK * chunks
        return web.Response(
            body=audio,
            content_type="application/octet-stream",
            headers={
                "X-Sample-Rate": "24000",
                "X-Encoding": "pcm16",
                "X-Audio-Duration-Ms": str(round(len(audio) / 2 / 24000 * 1000)),
            },
        )

    async def ws_handler(request):
        state["ws_headers"] = dict(request.headers)
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        async for message in ws:
            frame = json.loads(message.data)
            kind = frame.get("type")
            if kind == "hello":
                state["hellos"].append(frame)
                await ws.send_str(json.dumps({
                    "type": "ready", "session_id": "ws-fake", "protocol_version": 2,
                    "sample_rate": int(frame["output_format"].split(":")[0]),
                    "encoding": frame["output_format"].split(":")[1],
                }))
            elif kind == "text":
                state["texts"].append(frame)
                if ws_error:
                    await ws.send_str(json.dumps({
                        "type": "error", "code": ws_error,
                        "message": "no such voice", "fatal": True}))
                    continue
                for index in range(chunks):
                    await ws.send_str(json.dumps({
                        "type": "audio", "seq": frame["seq"], "chunk_index": index,
                        "chunk_total": chunks, "is_last_chunk": index == chunks - 1}))
                    await ws.send_bytes(CHUNK)
            elif kind == "end":
                state["end"] = True
                await ws.send_str(json.dumps({"type": "done"}))
        return ws

    app = web.Application()
    app.router.add_post("/tts/bytes", bytes_handler)
    app.router.add_get("/v1", ws_handler)
    return app


class Server:
    """Runs the fake app and hands back a client pointed at it."""

    def __init__(self, **kwargs):
        self.state = {"hellos": [], "texts": [], "end": False}
        self.kwargs = kwargs

    async def __aenter__(self):
        self.runner = web.AppRunner(fake_server(self.state, **self.kwargs))
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await self.site.start()
        port = self.runner.addresses[0][1]
        return BodhiTTSClient(
            api_key="test-key",
            url=f"ws://127.0.0.1:{port}/v1",
            http_url=f"http://127.0.0.1:{port}/tts/bytes",
        )

    async def __aexit__(self, *exc):
        await self.runner.cleanup()


async def test_synthesize():
    """Body fields, auth headers, and the response's own rate winning."""
    async with Server() as client:
        audio = await client.synthesize(
            "नमस्ते", lang="hi", voice="default_male", sample_rate=24000,
            speed=1.2, num_step=12, guidance_scale=2.5, use_fast=True,
        )
    assert isinstance(audio, TTSAudio)
    assert audio.audio == CHUNK * 2
    assert audio.sample_rate == 24000 and audio.encoding == "pcm16"
    assert audio.duration_ms == 100
    print(f"synthesize: OK ({len(audio)} bytes, {audio.duration_ms} ms)")


async def test_synthesize_body():
    srv = Server()
    async with srv as client:
        await client.synthesize("नमस्ते", lang="hi", voice="v", num_step=12)
    body, headers = srv.state["body"], srv.state["headers"]
    assert body["text"] == "नमस्ते" and body["lang"] == "hi" and body["voice"] == "v"
    assert body["output_format"] == "24000:pcm16"
    assert body["num_step"] == 12
    # Unknown fields are rejected by the server, so unset knobs must stay out.
    assert "speed" not in body and "guidance_scale" not in body and "use_fast" not in body
    assert headers["X-API-Key"] == "test-key"
    assert headers["Authorization"] == "Bearer test-key"
    print("synthesize body + auth headers: OK")


async def test_stream():
    """Chunks arrive in order, and one connection can carry several utterances."""
    srv = Server()
    async with srv as client:
        chunks = [c async for c in client.stream("नमस्ते", num_step=12)]
        multi = [c async for c in client.stream(["एक", "दो", "तीन"])]
    assert chunks == [CHUNK, CHUNK], f"expected 2 chunks, got {len(chunks)}"
    assert len(multi) == 6, f"expected 3 texts x 2 chunks, got {len(multi)}"
    assert [t["target_text"] for t in srv.state["texts"]] == ["नमस्ते", "एक", "दो", "तीन"]
    assert [t["seq"] for t in srv.state["texts"][1:]] == [0, 1, 2], "texts are numbered in order"
    assert srv.state["hellos"][0]["num_step"] == 12
    assert "speed" not in srv.state["hellos"][0], "speed is rejected by the hello"
    assert srv.state["ws_headers"]["X-API-Key"] == "test-key"
    assert srv.state["end"], "end frame was not sent"
    print(f"stream: OK ({len(chunks)} chunks, {len(multi)} across 3 texts)")


async def test_save_roundtrip():
    """A saved pcm16 file is a real WAV; mulaw gets a hand-written header."""
    async with Server() as client:
        audio = await client.synthesize("नमस्ते")
    with tempfile.TemporaryDirectory() as tmp:
        path = f"{tmp}/out.wav"
        audio.save(path)
        with wave.open(path) as f:
            assert f.getframerate() == 24000 and f.getsampwidth() == 2
            assert f.getnframes() == len(audio.audio) // 2

        mulaw = TTSAudio(b"\xff" * 8000, 8000, "mulaw")
        mulaw.save(f"{tmp}/tel.wav")
        raw = Path(f"{tmp}/tel.wav").read_bytes()
        assert raw[:4] == b"RIFF" and raw[8:12] == b"WAVE"
        fmt = struct.unpack("<HHII", raw[20:32])
        assert fmt[0] == 7, "mulaw must be WAVE_FORMAT_MULAW (7)"
        assert fmt[1] == 1 and fmt[2] == 8000
        assert len(raw) == 8000 + 58, "header + payload"

        try:
            TTSAudio(b"\x00" * 16, 24000, "float32").save(f"{tmp}/f.wav")
            raise AssertionError("float32 should refuse to save")
        except ValueError:
            pass
    print("save(): OK (pcm16 WAV, mulaw format 7, float32 refused)")


async def test_errors():
    """Bad arguments fail before the network; server errors surface as exceptions."""
    async with Server() as client:
        for kwargs in ({"sample_rate": 44100}, {"encoding": "opus"}):
            try:
                await client.synthesize("नमस्ते", **kwargs)
                raise AssertionError(f"{kwargs} should have raised")
            except ConfigurationError:
                pass
        for text in ("", "   ", []):
            try:
                await client.synthesize(text) if isinstance(text, str) else \
                    [c async for c in client.stream(text)]
                raise AssertionError(f"{text!r} should have raised")
            except ConfigurationError:
                pass

    async with Server(http_status=400) as client:
        try:
            await client.synthesize("नमस्ते")
            raise AssertionError("HTTP 400 should have raised")
        except BodhiAPIError as e:
            assert e.code == 400

    async with Server(ws_error="unknown_voice") as client:
        try:
            [c async for c in client.stream("नमस्ते")]
            raise AssertionError("ws error should have raised")
        except WebSocketError as e:
            assert "unknown_voice" in str(e)

    try:
        BodhiTTSClient(api_key="")
        raise AssertionError("missing api key should have raised")
    except ConfigurationError:
        pass
    print("errors: OK (bad args, HTTP 400, ws error, missing key)")


async def main():
    await test_synthesize()
    await test_synthesize_body()
    await test_stream()
    await test_save_roundtrip()
    await test_errors()
    print("all checks passed")


if __name__ == "__main__":
    asyncio.run(main())
