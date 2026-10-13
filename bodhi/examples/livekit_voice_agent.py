#
# A LiveKit Agents voice bot that listens with Bodhi STT and speaks with Bodhi
# TTS. It is the smallest thing that exercises both Bodhi plugins inside a real
# LiveKit AgentSession (STT -> LLM -> TTS).
#
# Install (plus an LLM plugin; OpenAI is used here, swap freely):
#
#   pip install "bodhi-api-sdk[livekit]" "livekit-agents[silero]" "livekit-plugins-openai"
#   export BODHI_API_KEY=...        # Navana key (STT + TTS)
#   export OPENAI_API_KEY=...       # or whichever LLM you wire below
#
# Run it on your own mic/speakers, no LiveKit server needed:
#
#   python -m bodhi.examples.livekit_voice_agent console
#
# Or connect it to a LiveKit room (then join via the Agents Playground):
#
#   export LIVEKIT_URL=... LIVEKIT_API_KEY=... LIVEKIT_API_SECRET=...
#   python -m bodhi.examples.livekit_voice_agent dev
#
# Point either plugin at another deployment with BODHI_STT_URL / BODHI_TTS_URL.
# BODHI_MODEL / BODHI_LANG / BODHI_VOICE tune recognition and the voice.
#

import os

from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli
from livekit.plugins import openai, silero

from bodhi.integrations.livekit_stt import (
    BODHI_STT_BASE_URL,
    BODHI_STT_DEFAULT_MODEL,
    BodhiSTT,
)
from bodhi.integrations.livekit_tts import BODHI_TTS_BASE_URL, BodhiTTS

STT_URL = os.getenv("BODHI_STT_URL", BODHI_STT_BASE_URL)
TTS_URL = os.getenv("BODHI_TTS_URL", BODHI_TTS_BASE_URL)
MODEL = os.getenv("BODHI_MODEL", BODHI_STT_DEFAULT_MODEL)
LANG = os.getenv("BODHI_LANG", "hi")
VOICE = os.getenv("BODHI_VOICE", "default_female")


async def entrypoint(ctx: JobContext):
    """Start one voice session using Bodhi for both STT and TTS."""
    session = AgentSession(
        stt=BodhiSTT(
            base_url=STT_URL,
            model=MODEL,
            # hi-general models run at 8 kHz; the plugin resamples the mic to it.
            sample_rate=8000,
            aux=True,
        ),
        vad=silero.VAD.load(),
        # Any LiveKit LLM plugin works here; this is the only non-Bodhi piece.
        llm=openai.LLM(model="gpt-4o-mini"),
        tts=BodhiTTS(base_url=TTS_URL, language=LANG, voice=VOICE),
    )

    await session.start(
        agent=Agent(
            instructions=(
                "You are a concise, friendly voice assistant. "
                "Reply in the same language the user speaks."
            )
        ),
        room=ctx.room,
    )
    await session.generate_reply(instructions="Greet the user in one short sentence.")


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
