import asyncio
import os
import logging
from dotenv import load_dotenv
from bodhi import (
    BodhiClient,
    TranscriptionConfig,
    TranscriptionResponse,
    LiveTranscriptionEvents,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)

# Load credentials from .env file
load_dotenv()
API_KEY = os.getenv("BODHI_API_KEY")


async def on_transcript(response: TranscriptionResponse):
    logging.info(f"Transcript: {response.text}")


async def on_utterance_end(response: TranscriptionResponse):
    logging.info(f"UtteranceEnd: {response}")


async def on_speech_started(response: TranscriptionResponse):
    logging.info(f"SpeechStarted: {response}")


async def on_error(e: Exception):
    logging.error(f"Error: {str(e)}")


async def on_close():
    logging.info("WebSocket connection closed.")


async def main():
    if not API_KEY:
        logging.error("Please set the BODHI_API_KEY environment variable")
        raise ValueError("Please set the BODHI_API_KEY environment variable")

    client = BodhiClient(api_key=API_KEY)

    # Register event listeners
    client.on(LiveTranscriptionEvents.Transcript, on_transcript)
    client.on(LiveTranscriptionEvents.UtteranceEnd, on_utterance_end)
    client.on(LiveTranscriptionEvents.SpeechStarted, on_speech_started)
    client.on(LiveTranscriptionEvents.Error, on_error)
    client.on(LiveTranscriptionEvents.Close, on_close)

    # Example configuration
    config = TranscriptionConfig(
        model="hi-banking-v2-8khz",
        at_start_lid=False,    # Enable language identification at start (default: False)
        transliterate=False,   # Enable transliteration output (default: False)
    )

    # Example with remote URL
    audio_url = "https://stt.navana.ai/audios/loan.wav"  # Replace with your audio URL

    try:
        # For remote URL transcription, events will be emitted during the transcribe_remote_url call
        await client.transcribe_remote_url(audio_url, config=config)
        logging.info("Remote URL transcription finished.")
    except Exception as e:
        logging.error(f"Error during transcription: {str(e)}")


if __name__ == "__main__":
    asyncio.run(main())
