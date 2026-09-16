"""
Test script using bodhi SDK v1.2.0 to verify language_code is working
"""
import asyncio
import os
import wave
import logging
from dotenv import load_dotenv
from bodhi import (
    BodhiClient,
    TranscriptionConfig,
    TranscriptionResponse,
    LiveTranscriptionEvents,
    __version__,
)

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")

API_KEY = os.getenv("BODHI_API_KEY")
URI = "wss://stt.navana.ai"
AUDIO_FILE = os.path.join(os.path.dirname(__file__), "loan.wav")


async def on_transcript(response: TranscriptionResponse):
    """Handle transcript events - show language_code"""
    lang = response.language_code or "N/A"
    rtype = response.type
    text = response.text[:60] if response.text else ""
    print(f"[{rtype:8s}] lang={lang:4s} | {text}")


async def on_utterance_end(response):
    print(f"\n=== UTTERANCE END ===")
    print(f"  start_time: {response.get('start_time')}")
    print(f"  end_time: {response.get('end_time')}")
    print()


async def on_speech_started(response):
    print(f"\n=== SPEECH STARTED at {response} ===\n")


async def on_error(e):
    logging.error(f"Error: {str(e)}")


async def on_close():
    print("\n=== CONNECTION CLOSED ===")


async def main():
    print("=" * 60)
    print(f"BODHI SDK VERSION: {__version__}")
    print("=" * 60)
    
    if __version__ != "1.0.0":
        print(f"WARNING: Expected v1.0.0, got {__version__}")
        print("Run: pip install --upgrade bodhi-api-sdk==1.0.0")
    
    print(f"Audio file: {AUDIO_FILE}")
    print()

    client = BodhiClient(
        api_key=API_KEY,
        uri=URI,
    )

    # Register event listeners
    client.on(LiveTranscriptionEvents.Transcript, on_transcript)
    client.on(LiveTranscriptionEvents.UtteranceEnd, on_utterance_end)
    client.on(LiveTranscriptionEvents.SpeechStarted, on_speech_started)
    client.on(LiveTranscriptionEvents.Error, on_error)
    client.on(LiveTranscriptionEvents.Close, on_close)

    # Config with new params
    config = TranscriptionConfig(
        model="hi-banking-v2-8khz",
        at_start_lid=True,     # Enable language identification
        transliterate=False,   # Disable transliteration
    )

    print("Config:")
    print(f"  model: {config.model}")
    print(f"  at_start_lid: {config.at_start_lid}")
    print(f"  transliterate: {config.transliterate}")
    print()

    # Transcribe
    await client.transcribe_local_file(AUDIO_FILE, config=config)
    print("\nTranscription complete!")


if __name__ == "__main__":
    asyncio.run(main())
