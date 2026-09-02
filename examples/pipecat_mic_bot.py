#
# A live Pipecat bot that transcribes your microphone with Bodhi. Transcripts
# appear in the browser UI (over RTVI) and in this terminal.
#
# It needs no OpenAI or Cartesia keys, because there is no LLM or TTS in the
# pipeline -- it is the smallest thing that exercises a real browser, real VAD
# and real Bodhi:
#
#   pip install "bodhi-sdk[pipecat]" "pipecat-ai[webrtc,silero,runner]"
#   export BODHI_API_KEY=...  BODHI_CUSTOMER_ID=...
#   python examples/pipecat_mic_bot.py
#
# Then open http://localhost:7860/client and click Connect.
#
# To hear a bot answer back, use the official quickstart
# (https://docs.pipecat.ai/pipecat/get-started/quickstart) and replace its
# DeepgramSTTService with the BodhiSTTService built below.
#

import os
import sys
import time
from pathlib import Path

# Run against this checkout rather than an installed bodhi-sdk.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import Frame, InterimTranscriptionFrame, TranscriptionFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineParams
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.runner.run import main
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.transports.base_transport import TransportParams
from pipecat.workers.runner import WorkerRunner

from bodhi.integrations.pipecat_stt import BodhiHotword, BodhiSTTService

MODEL = os.getenv("BODHI_MODEL", "hi-general-v2-8khz")

# Comma-separated, each "phrase" or "phrase:score", e.g. "बजाज फिनसर्व:2.0".
HOTWORDS = [
    BodhiHotword(*(p.split(":")[0], float(p.split(":")[1]) if ":" in p else None))
    for p in filter(None, os.getenv("BODHI_HOTWORDS", "").split(","))
]


class TranscriptPrinter(FrameProcessor):
    """Prints transcription frames as they flow past."""

    def __init__(self):
        super().__init__()
        self._started = time.monotonic()

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """Print transcription frames, then pass everything along."""
        await super().process_frame(frame, direction)

        elapsed = time.monotonic() - self._started
        if isinstance(frame, TranscriptionFrame):
            print(f"[{elapsed:6.2f}s] FINAL   {frame.text}", flush=True)
        elif isinstance(frame, InterimTranscriptionFrame):
            print(f"[{elapsed:6.2f}s] partial {frame.text}", flush=True)

        await self.push_frame(frame, direction)


async def bot(runner_args: RunnerArguments):
    """Run the transcription pipeline for one browser connection."""
    api_key = os.getenv("BODHI_API_KEY")
    customer_id = os.getenv("BODHI_CUSTOMER_ID")
    if not api_key or not customer_id:
        raise SystemExit("set BODHI_API_KEY and BODHI_CUSTOMER_ID first")

    # Whichever leg the client connects on: browser WebRTC, or a websocket.
    def params():
        return TransportParams(
            audio_in_enabled=True,
            # No TTS in this pipeline, so nothing is spoken back. The output
            # leg is still needed: it carries the RTVI messages that put the
            # transcripts on screen.
            audio_out_enabled=True,
            vad_analyzer=SileroVADAnalyzer(),
        )

    transport = await create_transport(runner_args, {"webrtc": params, "websocket": params})

    stt = BodhiSTTService(
        api_key=api_key,
        customer_id=customer_id,
        model=MODEL,
        settings=BodhiSTTService.Settings(hotwords=HOTWORDS or None),
    )

    print(f"transcribing with Bodhi model {MODEL}; speak into the browser tab", flush=True)

    worker = PipelineWorker(
        Pipeline([transport.input(), stt, TranscriptPrinter(), transport.output()]),
        params=PipelineParams(audio_in_sample_rate=16000),
    )

    runner = WorkerRunner(handle_sigint=runner_args.handle_sigint)
    await runner.add_workers(worker)
    await runner.run()


if __name__ == "__main__":
    main()
