#
# Smoke test for bodhi.integrations.pipecat_stt and pipecat_tts against fake
# Bodhi servers.
#
# Needs no credentials and no network. Run it to check the integration still
# fits a given pipecat version:
#
#   pip install "pipecat-ai==1.4.0"
#   python tests/pipecat_smoke_test.py
#

import asyncio
import json
import sys
from pathlib import Path

from websockets.asyncio.server import serve

# Run against this checkout rather than an installed bodhi-api-sdk.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipecat.frames.frames import (
    ErrorFrame,
    InputAudioRawFrame,
    InterimTranscriptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    STTUpdateSettingsFrame,
    TextFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    TTSSpeakFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
)
from pipecat.pipeline.task import PipelineParams
from pipecat.tests.utils import SleepFrame, run_test
from pipecat.transcriptions.language import Language

from bodhi.integrations.pipecat_stt import BodhiHotword, BodhiSTTService
from bodhi.integrations.pipecat_tts import BodhiTTSService

CHUNK = b"\x11\x00" * 1600  # 100ms of 16kHz PCM16
TTS_CHUNK = b"\x22\x00" * 1200  # 50ms of 24kHz PCM16


def new_state():
    return {"headers": {}, "connections": 0, "configs": [], "audio_bytes": 0, "silence": 0, "eof": False}


def fake_bodhi(state, *, transcribe_on_audio=False):
    """A stand-in for Bodhi: records what it was sent, replies with transcripts."""
    state["pending_transcript"] = transcribe_on_audio

    async def handler(ws):
        state["connections"] += 1
        state["headers"] = dict(ws.request.headers)
        async for message in ws:
            if isinstance(message, bytes):
                state["audio_bytes"] += len(message)
                if set(message) == {0}:
                    state["silence"] += 1
                elif state["pending_transcript"]:
                    state["pending_transcript"] = False
                    await ws.send(json.dumps({"text": "नमस", "type": "partial", "segment_id": 0}))
                    await ws.send(json.dumps({"text": "नमस्ते", "type": "complete", "segment_id": 0}))
            else:
                data = json.loads(message)
                if "config" in data:
                    state["configs"].append(data["config"])
                    if not transcribe_on_audio:
                        await ws.send(json.dumps({"text": "ok", "type": "complete"}))

                elif "eof" in data:
                    state["eof"] = True

    return handler


async def test_transcription(pipeline_rate: int):
    """Config message, audio streaming, resampling and transcript frames."""
    state = new_state()
    async with serve(fake_bodhi(state, transcribe_on_audio=True), "127.0.0.1", 0) as server:
        stt = BodhiSTTService(
            api_key="test-key",
            model="hi-general-v2-8khz",
            url=f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}",
            settings=BodhiSTTService.Settings(
                parse_number=True,
                endpoint_silence_duration=0.6,
                hotwords=[BodhiHotword("बजाज फिनसर्व", 2.0), BodhiHotword("सुविधा")],
            ),
        )
        frames = [
            InputAudioRawFrame(audio=CHUNK, sample_rate=pipeline_rate, num_channels=1),
            InputAudioRawFrame(audio=CHUNK, sample_rate=pipeline_rate, num_channels=1),
            SleepFrame(sleep=0.5),
        ]
        down, _ = await run_test(
            stt,
            frames_to_send=frames,
            pipeline_params=PipelineParams(audio_in_sample_rate=pipeline_rate),
        )

    finals = [f for f in down if isinstance(f, TranscriptionFrame)]
    interims = [f for f in down if isinstance(f, InterimTranscriptionFrame)]
    config = state["configs"][0]

    assert state["headers"]["x-api-key"] == "test-key"
    assert config["model"] == "hi-general-v2-8khz"
    assert config["sample_rate"] == (pipeline_rate if pipeline_rate in (8000, 16000) else 16000)
    assert config["channels"] == 1
    assert config["parse_number"] is True
    assert config["exclude_partial"] is False
    assert config["endpoint_silence_duration"] == 0.6
    assert config["hotwords"] == [{"phrase": "बजाज फिनसर्व", "score": 2.0}, {"phrase": "सुविधा"}]
    assert state["audio_bytes"] > 0
    assert state["eof"], "eof was not sent on EndFrame"
    assert [f.text for f in interims] == ["नमस"]
    assert [f.text for f in finals] == ["नमस्ते"]
    assert finals[0].finalized and finals[0].result and finals[0].language is Language.HI
    print(f"transcription @ {pipeline_rate} Hz: OK ({state['audio_bytes']} bytes sent)")


async def test_confidence_filter():
    """Finals below min_confidence are dropped, as Pipecat asks."""
    state = new_state()

    async def handler(ws):
        state["connections"] += 1
        async for message in ws:
            if isinstance(message, bytes):
                continue
            data = json.loads(message)
            if "config" not in data:
                continue
            state["configs"].append(data["config"])
            for text, confidence in (("भरोसेमंद", 0.91), ("कचरा", 0.12)):
                await ws.send(
                    json.dumps(
                        {
                            "text": text,
                            "type": "complete",
                            "segment_meta": {"confidence": confidence},
                        }
                    )
                )

    async with serve(handler, "127.0.0.1", 0) as server:
        stt = BodhiSTTService(
            api_key="test-key",
            model="hi-general-v2-8khz",
            url=f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}",
            interim_results=False,
        )
        frames = [
            InputAudioRawFrame(audio=CHUNK, sample_rate=16000, num_channels=1),
            SleepFrame(sleep=0.5),
        ]
        down, _ = await run_test(
            stt, frames_to_send=frames, pipeline_params=PipelineParams(audio_in_sample_rate=16000)
        )

    texts = [f.text for f in down if isinstance(f, TranscriptionFrame)]
    assert texts == ["भरोसेमंद"], f"expected the low-confidence final to be dropped, got {texts}"
    print("confidence filter: OK (kept 0.91, dropped 0.12)")


async def test_keepalive_and_settings_update():
    """Idle keepalive, and reconnecting with a new config when settings change."""
    state = new_state()
    async with serve(fake_bodhi(state), "127.0.0.1", 0) as server:
        stt = BodhiSTTService(
            api_key="test-key",
            model="en-general",
            url=f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}",
            keepalive_timeout=0.3,
            keepalive_interval=0.2,
        )
        # Applying a settings change needs the VAD-aware reconnect that arrived
        # in pipecat 1.4; older versions stream fine but can't switch model.
        supports_runtime_settings = hasattr(stt, "_request_reconnect")
        frames = [
            InputAudioRawFrame(audio=CHUNK, sample_rate=16000, num_channels=1),
            SleepFrame(sleep=1.0),  # idle long enough for keepalive silence
            STTUpdateSettingsFrame(
                delta=BodhiSTTService.Settings(
                    model="hi-general-v2-8khz", hotwords=[BodhiHotword("सुविधा", 2.0)]
                )
            ),
            SleepFrame(sleep=0.5),  # reconnect and resend config
        ]
        down, _ = await run_test(
            stt, frames_to_send=frames, pipeline_params=PipelineParams(audio_in_sample_rate=16000)
        )

    finals = [f for f in down if isinstance(f, TranscriptionFrame)]

    assert state["silence"] >= 1, "no keepalive silence was sent while idle"

    if not supports_runtime_settings:
        print(
            f"keepalive: OK ({state['silence']} silence chunks); "
            "runtime settings update: SKIPPED (needs pipecat 1.4+)"
        )
        return

    assert state["connections"] == 2, "settings change did not reconnect"
    assert state["configs"][1]["model"] == "hi-general-v2-8khz"
    assert state["configs"][1]["hotwords"] == [{"phrase": "सुविधा", "score": 2.0}]
    assert state["configs"][0]["transaction_id"] != state["configs"][1]["transaction_id"]
    # Language follows the model when the caller didn't name one.
    assert [f.language for f in finals] == [Language.EN, Language.HI]
    print(f"keepalive + settings update: OK ({state['silence']} silence chunks, 2 configs)")


def new_tts_state():
    return {"headers": {}, "connections": 0, "hellos": [], "texts": [], "end": False}


def fake_bodhi_tts(state, *, chunks=2, error=None, fatal_error=None):
    """A stand-in for Bodhi TTS: hello/ready, then audio headers + binary chunks.

    Mirrors the wire protocol the service expects -- every binary frame is
    preceded by its JSON header, and only the last header of a text frame
    carries ``is_last_chunk``. ``fatal_error`` is the message of a fatal error
    sent for the first text frame, the way the server rejects a bad
    ``g2p_overrides_json``: after ``ready``, not instead of it.
    """

    async def handler(ws):
        state["connections"] += 1
        state["headers"] = dict(ws.request.headers)
        async for message in ws:
            data = json.loads(message)
            kind = data.get("type")

            if kind == "hello":
                state["hellos"].append(data)
                rate = int(data["output_format"].split(":")[0])
                await ws.send(
                    json.dumps(
                        {
                            "type": "ready",
                            "session_id": f"ws-fake-{state['connections']}",
                            "protocol_version": 2,
                            "sample_rate": rate,
                            "encoding": "pcm16",
                        }
                    )
                )

            elif kind == "text":
                state["texts"].append(data)
                if fatal_error is not None:
                    await ws.send(
                        json.dumps(
                            {
                                "type": "error",
                                "code": "bad_request",
                                "message": fatal_error,
                                "fatal": True,
                                "seq": data["seq"],
                            }
                        )
                    )
                    # The real server closes with 1008 after a fatal error.
                    await ws.close(code=1008, reason="bad_request")
                    return
                if error is not None:
                    await ws.send(
                        json.dumps(
                            {"type": "error", "code": error, "message": "no such voice", "fatal": False}
                        )
                    )
                    continue
                for index in range(chunks):
                    await ws.send(
                        json.dumps(
                            {
                                "type": "audio",
                                "seq": data["seq"],
                                "chunk_index": index,
                                "chunk_total": chunks,
                                "is_last_chunk": index == chunks - 1,
                                "inference_ms": 5,
                            }
                        )
                    )
                    await ws.send(TTS_CHUNK)

            elif kind == "end":
                state["end"] = True
                await ws.send(json.dumps({"type": "done", "seq": data.get("seq")}))

    return handler


async def test_tts_synthesis(out_rate: int):
    """Hello fields, auth headers, text frames and the audio/started/stopped frames."""
    state = new_tts_state()
    async with serve(fake_bodhi_tts(state), "127.0.0.1", 0) as server:
        tts = BodhiTTSService(
            api_key="test-key",
            url=f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}",
            voice="default_male",
            language=Language.HI,
            settings=BodhiTTSService.Settings(use_fast=True, num_step=6),
        )
        frames = [TTSSpeakFrame("नमस्ते"), SleepFrame(sleep=0.5)]
        down, up = await run_test(
            tts,
            frames_to_send=frames,
            pipeline_params=PipelineParams(audio_out_sample_rate=out_rate),
        )

    audio = [f for f in down if isinstance(f, TTSAudioRawFrame)]
    started = [f for f in down if isinstance(f, TTSStartedFrame)]
    stopped = [f for f in down if isinstance(f, TTSStoppedFrame)]
    errors = [f for f in down + up if isinstance(f, ErrorFrame)]
    hello = state["hellos"][0]

    # Both header styles, so one client works against STT and TTS alike.
    assert state["headers"]["x-api-key"] == "test-key"
    assert state["headers"]["authorization"] == "Bearer test-key"
    assert hello["lang"] == "hi"
    assert hello["voice"] == "default_male"
    assert hello["output_format"] == f"{out_rate}:pcm16"
    assert hello["use_fast"] is True
    assert hello["num_step"] == 6
    # Unknown fields are rejected by the server, so unset knobs must stay out.
    assert "guidance_scale" not in hello
    assert [t["target_text"] for t in state["texts"]] == ["नमस्ते"]
    assert state["texts"][0]["seq"] == 1, "text frames are numbered from 1"
    assert not errors, f"unexpected error frames: {errors}"
    assert len(started) == 1, f"expected one TTSStartedFrame, got {len(started)}"
    assert len(audio) == 2, f"expected both audio chunks, got {len(audio)}"
    assert all(f.sample_rate == out_rate for f in audio)
    assert b"".join(f.audio for f in audio) == TTS_CHUNK * 2
    assert stopped, "is_last_chunk did not end the utterance"
    print(f"tts synthesis @ {out_rate} Hz: OK ({len(audio)} chunks, {len(b''.join(f.audio for f in audio))} bytes)")


async def test_tts_multi_sentence_turn():
    """One streamed LLM response is one TTS context: one started, one stopped frame.

    Pipecat sends each sentence as its own text frame (own seq) under a shared
    context_id. A stop frame per seq made Pipecat treat every sentence as the end
    of the bot's turn (BotStoppedSpeaking per sentence, context torn down, the
    assistant turn committed in pieces).
    """
    state = new_tts_state()
    sentences = ["जी, धन्यवाद।", "यह आपकी ईएमआई का reminder है।", "क्या आप यह कर पाएंगे?"]
    async with serve(fake_bodhi_tts(state), "127.0.0.1", 0) as server:
        tts = BodhiTTSService(
            api_key="test-key",
            url=f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}",
        )
        frames = (
            [LLMFullResponseStartFrame()]
            + [TextFrame(s + " ") for s in sentences]
            + [LLMFullResponseEndFrame(), SleepFrame(sleep=0.5)]
        )
        down, up = await run_test(
            tts,
            frames_to_send=frames,
            pipeline_params=PipelineParams(audio_out_sample_rate=24000),
        )

    audio = [f for f in down if isinstance(f, TTSAudioRawFrame)]
    started = [f for f in down if isinstance(f, TTSStartedFrame)]
    stopped = [f for f in down if isinstance(f, TTSStoppedFrame)]
    errors = [f for f in down + up if isinstance(f, ErrorFrame)]

    assert [t["seq"] for t in state["texts"]] == [1, 2, 3], "one text frame per sentence"
    assert not errors, f"unexpected error frames: {errors}"
    assert len(started) == 1, f"expected one TTSStartedFrame, got {len(started)}"
    assert len(stopped) == 1, f"expected one TTSStoppedFrame for the turn, got {len(stopped)}"
    assert len(audio) == 6, f"expected audio for every sentence, got {len(audio)} chunks"
    assert {f.context_id for f in audio} == {started[0].context_id} == {stopped[0].context_id}
    print(f"tts multi-sentence turn: OK ({len(sentences)} sentences, 1 started, 1 stopped)")


async def test_tts_back_to_back_speak_frames():
    """Each TTSSpeakFrame gets its own context, and audio is routed by seq.

    Routing by the active context instead sent every chunk to the first context
    and left the others completing with no audio, which Pipecat counts towards
    marking the service unusable.
    """
    state = new_tts_state()
    async with serve(fake_bodhi_tts(state), "127.0.0.1", 0) as server:
        tts = BodhiTTSService(
            api_key="test-key",
            url=f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}",
        )
        frames = [TTSSpeakFrame("एक।"), TTSSpeakFrame("दो।"), TTSSpeakFrame("तीन।"), SleepFrame(sleep=0.5)]
        down, up = await run_test(
            tts,
            frames_to_send=frames,
            pipeline_params=PipelineParams(audio_out_sample_rate=24000),
        )

    started = [f.context_id for f in down if isinstance(f, TTSStartedFrame)]
    stopped = [f.context_id for f in down if isinstance(f, TTSStoppedFrame)]
    errors = [f for f in down + up if isinstance(f, ErrorFrame)]
    chunks_by_context: dict[str, int] = {}
    for f in down:
        if isinstance(f, TTSAudioRawFrame):
            chunks_by_context[f.context_id] = chunks_by_context.get(f.context_id, 0) + 1

    assert not errors, f"unexpected error frames: {errors}"
    assert len(started) == 3 and started == stopped, f"started {started} vs stopped {stopped}"
    assert chunks_by_context == {c: 2 for c in started}, f"audio misrouted: {chunks_by_context}"
    print("tts back-to-back speak frames: OK (3 contexts, 2 chunks each)")


async def test_tts_error_is_reported():
    """A server error frame surfaces as an ErrorFrame rather than a hang."""
    state = new_tts_state()
    async with serve(fake_bodhi_tts(state, error="bad_voice"), "127.0.0.1", 0) as server:
        tts = BodhiTTSService(
            api_key="test-key",
            url=f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}",
            voice="nonexistent",
        )
        frames = [TTSSpeakFrame("नमस्ते"), SleepFrame(sleep=0.5)]
        down, up = await run_test(
            tts,
            frames_to_send=frames,
            pipeline_params=PipelineParams(audio_out_sample_rate=24000),
        )

    errors = [f for f in down + up if isinstance(f, ErrorFrame)]
    assert errors, "server error was swallowed"
    assert "bad_voice" in str(errors[0]), f"error lost its code: {errors[0]}"
    assert not [f for f in down if isinstance(f, TTSAudioRawFrame)], "audio despite an error"
    print("tts error reporting: OK (bad_voice surfaced)")


async def test_tts_g2p_overrides():
    """The override map rides the hello as a JSON string, and only when set."""
    overrides = {"NACH": "n a ː t ʃ", "KUMAR": {"tokens": "k u m a ː r", "lang": "en"}}
    state = new_tts_state()
    async with serve(fake_bodhi_tts(state), "127.0.0.1", 0) as server:
        url = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
        tts = BodhiTTSService(
            api_key="test-key",
            url=url,
            settings=BodhiTTSService.Settings(g2p_overrides=overrides),
        )
        down, up = await run_test(
            tts,
            frames_to_send=[TTSSpeakFrame("नमस्ते"), SleepFrame(sleep=0.5)],
            pipeline_params=PipelineParams(audio_out_sample_rate=24000),
        )
        plain = BodhiTTSService(api_key="test-key", url=url)
        await run_test(
            plain,
            frames_to_send=[TTSSpeakFrame("नमस्ते"), SleepFrame(sleep=0.5)],
            pipeline_params=PipelineParams(audio_out_sample_rate=24000),
        )

    with_map, without_map = state["hellos"]
    assert json.loads(with_map["g2p_overrides_json"]) == overrides, with_map
    assert "g2p_overrides" not in with_map, "the object form is for /tts/bytes only"
    assert "g2p_overrides_json" not in without_map, "unset knobs must stay out of hello"
    assert not [f for f in down + up if isinstance(f, ErrorFrame)]
    assert [f for f in down if isinstance(f, TTSAudioRawFrame)], "no audio with overrides set"

    for bad in ({"NACH": ""}, {"NACH": 3}, {"": "n a ː t ʃ"}, ["NACH"], {"NACH": {"lang": "en"}}):
        try:
            BodhiTTSService(api_key="k", url=url, settings=BodhiTTSService.Settings(g2p_overrides=bad))
        except ValueError:
            continue
        raise AssertionError(f"malformed g2p_overrides accepted: {bad!r}")
    print("tts g2p overrides: OK (hello carries the map as a string; malformed maps refused)")


async def test_tts_invalid_g2p_is_fatal():
    """A server-side g2p rejection stops the pipeline instead of muting it.

    The server answers ``ready`` and only rejects the map on the first text
    frame, with ``fatal: true``. Reconnecting would repeat the same hello, so
    the service reports it once as a permanent error (processor unusable on
    Pipecat 1.8+, a fatal ErrorFrame before that) and stops reconnecting: a
    second sentence must not open a second connection.
    """
    message = "invalid g2p_overrides: G2P override for 'NACH' contains tokens not in the vocabulary: ['naach']"
    state = new_tts_state()
    async with serve(fake_bodhi_tts(state, fatal_error=message), "127.0.0.1", 0) as server:
        tts = BodhiTTSService(
            api_key="test-key",
            url=f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}",
            settings=BodhiTTSService.Settings(g2p_overrides={"NACH": "naach"}),
        )
        down, up = await run_test(
            tts,
            frames_to_send=[
                TTSSpeakFrame("नमस्ते"),
                SleepFrame(sleep=0.5),
                TTSSpeakFrame("फिर से"),
                SleepFrame(sleep=0.5),
            ],
            pipeline_params=PipelineParams(audio_out_sample_rate=24000),
        )

    errors = [f for f in down + up if isinstance(f, ErrorFrame)]
    rejection = [f for f in errors if "invalid g2p_overrides" in str(f.error)]
    assert rejection, f"g2p rejection was swallowed: {errors}"
    assert "NACH" in rejection[0].error, rejection[0]
    permanent = rejection[0].fatal or (hasattr(tts, "is_usable") and not tts.is_usable)
    assert permanent, f"g2p rejection was reported as recoverable: {rejection[0]}"
    assert not [f for f in down if isinstance(f, TTSAudioRawFrame)], "audio despite the rejection"
    assert state["connections"] == 1, f"reconnected into the same bad hello: {state['connections']}"
    assert len(state["texts"]) == 1, "second sentence was sent after the rejection"
    print("tts invalid g2p overrides: OK (reported once as permanent, no reconnect)")


async def test_tts_g2p_rejection_guards():
    """The SDK's own guards, independent of what Pipecat does with an unusable service.

    On Pipecat 1.8+ the base classes already skip an unusable service and refuse
    to reconnect it, so the end-to-end case above passes without exercising the
    SDK side. Drive the service directly: once a rejection is recorded, connecting
    is a no-op (no hello reaches the server) and run_tts yields an error and a stop
    frame without touching the socket; replacing the map clears the rejection.
    """
    state = new_tts_state()
    async with serve(fake_bodhi_tts(state), "127.0.0.1", 0) as server:
        tts = BodhiTTSService(
            api_key="test-key",
            url=f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}",
            settings=BodhiTTSService.Settings(g2p_overrides={"NACH": "naach"}),
        )
        tts._g2p_rejected = "invalid g2p_overrides: test"
        await tts._connect_websocket()
        assert tts._websocket is None and not state["hellos"], "connected despite a rejection"

        frames = [f async for f in tts.run_tts("नमस्ते", "ctx-1")]
        assert any(isinstance(f, ErrorFrame) for f in frames), frames
        assert any(isinstance(f, TTSStoppedFrame) for f in frames), frames
        assert not state["texts"] and state["connections"] == 0, "run_tts touched the socket"

        tts._g2p_rejected = None
        # The output rate is normally learned from StartFrame; the service is
        # driven directly here, so set it the way the pipeline would.
        tts._output_sample_rate = 24000
        await tts._connect_websocket()
        assert len(state["hellos"]) == 1, "a cleared rejection must connect again"
        await tts._disconnect_websocket()
    print("tts g2p rejection guards: OK (no hello, no text while rejected)")


async def main():
    await test_transcription(16000)
    await test_transcription(24000)
    await test_confidence_filter()
    await test_keepalive_and_settings_update()
    await test_tts_synthesis(24000)
    await test_tts_synthesis(8000)
    await test_tts_multi_sentence_turn()
    await test_tts_back_to_back_speak_frames()
    await test_tts_error_is_reported()
    await test_tts_g2p_overrides()
    await test_tts_invalid_g2p_is_fatal()
    await test_tts_g2p_rejection_guards()
    print("all checks passed")


if __name__ == "__main__":
    asyncio.run(main())
