#
# A no-LLM LiveKit agent for testing both Bodhi plugins on the Agent Console /
# a LiveKit room. It greets you with Bodhi TTS, then repeats back whatever you
# say (Bodhi STT -> Bodhi TTS). No OpenAI/LLM key needed.
#
#   pip install "bodhi-api-sdk[livekit]" "livekit-agents[silero]"
#   export BODHI_API_KEY=...
#   export LIVEKIT_URL=...  LIVEKIT_API_KEY=...  LIVEKIT_API_SECRET=...
#   python -m bodhi.examples.livekit_echo_agent dev
#
# Then open LiveKit Cloud -> your project -> Agents -> Launch Console and talk.
# For a local mic/speaker test with no LiveKit server, use `console` instead of
# `dev`.
#
# BODHI_LANG / BODHI_VOICE tune TTS; BODHI_STT_MODEL / BODHI_STT_SR tune STT.
#

import logging
import os

from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    UserInputTranscribedEvent,
    WorkerOptions,
    cli,
)
from livekit.plugins import silero

from livekit.plugins import bodhi

# surface the plugin's own debug traces under the worker's DEBUG logging
logging.getLogger("bodhi.livekit.tts").setLevel(logging.DEBUG)
logging.getLogger("bodhi.echo").setLevel(logging.DEBUG)

LANG = os.getenv("BODHI_LANG", "hi")
VOICE = os.getenv("BODHI_VOICE", "default_female")
STT_MODEL = os.getenv("BODHI_STT_MODEL", "hi-general-v2-8khz")
STT_SR = int(os.getenv("BODHI_STT_SR", "8000"))
GREETING = os.getenv("BODHI_GREETING", "नमस्ते! जो भी आप बोलेंगे मैं उसे दोहरा दूँगी।")


async def entrypoint(ctx: JobContext):
    session = AgentSession(
        stt=bodhi.STT(model=STT_MODEL, sample_rate=STT_SR, aux=True),
        vad=silero.VAD.load(),
        tts=bodhi.TTS(language=LANG, voice=VOICE),
        # no llm: this is a pure STT/TTS echo loop.
    )

    @session.on("user_input_transcribed")
    def _echo(ev: UserInputTranscribedEvent):
        # Repeat each finalised utterance back through Bodhi TTS.
        logging.getLogger("bodhi.echo").info(
            "transcribed final=%s text=%r", ev.is_final, ev.transcript
        )
        if ev.is_final and ev.transcript.strip():
            # Don't let the next utterance cancel this echo mid-synthesis, so
            # each reply plays to completion even when you talk over it.
            session.say(ev.transcript, allow_interruptions=False)

    await session.start(agent=Agent(instructions="echo bot"), room=ctx.room)
    await session.say(GREETING)


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
