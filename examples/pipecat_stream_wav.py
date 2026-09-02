#
# Stream a WAV file through BodhiSTTService in a real Pipecat pipeline and
# print the transcripts as they arrive.
#
#   pip install "bodhi-sdk[pipecat]"
#   export BODHI_API_KEY=...  BODHI_CUSTOMER_ID=...
#   python examples/pipecat_stream_wav.py call.wav --model hi-general-v2-8khz
#
# Audio is fed in real time (100 ms at a time), so partials and finals land
# with the same timing a live call would see.
#

import argparse
import asyncio
import os
import sys
import time
import wave
from pathlib import Path

# Run against this checkout rather than an installed bodhi-sdk.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipecat.frames.frames import (
    EndFrame,
    Frame,
    InputAudioRawFrame,
    InterimTranscriptionFrame,
    TranscriptionFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineParams
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.workers.runner import WorkerRunner

from bodhi.integrations.pipecat_stt import BodhiHotword, BodhiSTTService

CHUNK_SECONDS = 0.1


class TranscriptPrinter(FrameProcessor):
    """Prints transcription frames as the pipeline produces them."""

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


def read_wav(path: Path) -> tuple[bytes, int]:
    """Read a mono 16-bit WAV file.

    Args:
        path: Path to the WAV file.

    Returns:
        Its PCM bytes and sample rate.
    """
    with wave.open(str(path), "rb") as wav:
        if wav.getsampwidth() != 2 or wav.getnchannels() != 1:
            raise SystemExit(
                f"{path} is {wav.getnchannels()}ch/{wav.getsampwidth() * 8}-bit; "
                "convert it first: ffmpeg -i in.wav -ac 1 -ar 8000 -sample_fmt s16 out.wav"
            )
        return wav.readframes(wav.getnframes()), wav.getframerate()


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wav", type=Path, help="mono 16-bit WAV file to stream")
    parser.add_argument("--model", default=os.getenv("BODHI_MODEL", "hi-general-v2-8khz"))
    parser.add_argument("--url", default=os.getenv("BODHI_URL", "wss://bodhi.navana.ai"))
    parser.add_argument("--api-key", default=os.getenv("BODHI_API_KEY"))
    parser.add_argument("--customer-id", default=os.getenv("BODHI_CUSTOMER_ID"))
    parser.add_argument("--hotword", action="append", default=[], metavar="PHRASE[:SCORE]")
    parser.add_argument("--parse-number", action="store_true", help="normalise numbers and dates")
    parser.add_argument("--no-partials", action="store_true", help="finals only")
    args = parser.parse_args()

    if not args.api_key or not args.customer_id:
        raise SystemExit("set BODHI_API_KEY and BODHI_CUSTOMER_ID (or pass --api-key/--customer-id)")

    hotwords = []
    for spec in args.hotword:
        phrase, _, score = spec.partition(":")
        hotwords.append(BodhiHotword(phrase, float(score) if score else None))

    audio, sample_rate = read_wav(args.wav)
    print(
        f"streaming {len(audio) / 2 / sample_rate:.1f}s of {sample_rate} Hz audio "
        f"to {args.url} as {args.model}",
        flush=True,
    )

    stt = BodhiSTTService(
        api_key=args.api_key,
        customer_id=args.customer_id,
        model=args.model,
        url=args.url,
        interim_results=not args.no_partials,
        settings=BodhiSTTService.Settings(
            parse_number=args.parse_number,
            hotwords=hotwords or None,
        ),
    )

    worker = PipelineWorker(
        Pipeline([stt, TranscriptPrinter()]),
        params=PipelineParams(audio_in_sample_rate=sample_rate),
        cancel_on_idle_timeout=False,
    )

    async def feed_audio():
        # Let the pipeline start and the websocket handshake finish.
        await asyncio.sleep(0.5)
        chunk_bytes = int(sample_rate * CHUNK_SECONDS) * 2
        for offset in range(0, len(audio), chunk_bytes):
            await worker.queue_frame(
                InputAudioRawFrame(
                    audio=audio[offset : offset + chunk_bytes],
                    sample_rate=sample_rate,
                    num_channels=1,
                )
            )
            await asyncio.sleep(CHUNK_SECONDS)
        # Give Bodhi a moment to endpoint the last utterance.
        await asyncio.sleep(1.5)
        await worker.queue_frame(EndFrame())
        print(f"transaction_id: {stt.transaction_id}", flush=True)

    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)
    await asyncio.gather(runner.run(), feed_audio())


if __name__ == "__main__":
    asyncio.run(main())
