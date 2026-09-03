#
# Smoke test for bodhi.integrations.pipecat_stt against a fake Bodhi server.
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

# Run against this checkout rather than an installed bodhi-sdk.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipecat.frames.frames import (
    InputAudioRawFrame,
    InterimTranscriptionFrame,
    STTUpdateSettingsFrame,
    TranscriptionFrame,
)
from pipecat.pipeline.task import PipelineParams
from pipecat.tests.utils import SleepFrame, run_test
from pipecat.transcriptions.language import Language

from bodhi.integrations.pipecat_stt import BodhiHotword, BodhiSTTService

CUSTOMER_ID = "00000000-0000-0000-0000-000000000001"
CHUNK = b"\x11\x00" * 1600  # 100ms of 16kHz PCM16


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
            customer_id=CUSTOMER_ID,
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
    assert state["headers"]["x-customer-id"] == CUSTOMER_ID
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
            customer_id=CUSTOMER_ID,
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
            customer_id=CUSTOMER_ID,
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


async def main():
    await test_transcription(16000)
    await test_transcription(24000)
    await test_confidence_filter()
    await test_keepalive_and_settings_update()
    print("all checks passed")


if __name__ == "__main__":
    asyncio.run(main())
