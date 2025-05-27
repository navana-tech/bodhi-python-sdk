# transcription_client.py
"""Bodhi Streaming Speech Recognition Client"""


import os
from bodhi.transcription_handler import TranscriptionHandler
from bodhi.websocket_handler import WebSocketHandler
from typing import Optional, Callable, List
from .transcription_config import TranscriptionConfig
from .transcription_response import TranscriptionResponse
from .utils.logger import logger
from .utils.exceptions import ConfigurationError, ConnectionError, StreamingError
from .events import LiveTranscriptionEvents
import uuid


chunk_duration_ms = 100


class BodhiClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        customer_id: Optional[str] = None,
        uri: Optional[str] = None,
    ):
        """Initialize Bodhi client.

        Args:
            api_key: API key for authentication
            customer_id: Customer ID for authentication
            uri: WebSocket URI for the service
        """
        self.api_key = api_key or os.environ.get("API_KEY")
        if not self.api_key:
            logger.error("API key not provided and not found in environment")
            raise ConfigurationError("API key is required")

        self.customer_id = customer_id or os.environ.get("CUSTOMER_ID")
        if not self.customer_id:
            logger.error("Customer ID not provided and not found in environment")
            raise ConfigurationError("Customer ID is required")

        try:
            uuid.UUID(self.customer_id)
        except ValueError:
            raise ConfigurationError("Customer ID must be a valid UUID.")

        self.websocket_url = uri or "wss://bodhi.navana.ai"
        self.websocket_handler = WebSocketHandler(
            self.api_key, self.customer_id, self.websocket_url
        )
        self.transcription_handler = TranscriptionHandler(self.websocket_handler)
        # Remove callback attributes
        # self.on_transcription: Optional[Callable[[TranscriptionResponse], None]] = None
        # self.on_error: Optional[Callable[[Exception], None]] = None

        logger.info(f"Initialized Bodhi client with URI: {self.websocket_url}")

    async def start_connection(
        self, config: Optional[TranscriptionConfig] = None
    ) -> None:
        """Start a streaming transcription session.

        Args:
            config: Configuration object for transcription

        Raises:
            ConnectionError: If configuration is incorrect or session cannot be started
        """
        # Pass no callbacks to transcription handler
        return await self.transcription_handler.start_streaming_session(config)

    async def send_audio_stream(self, audio_data: bytes) -> None:
        """Stream audio data for transcription.

        Args:
            audio_data: Audio data bytes to stream

        Raises:
            StreamingError: If streaming session is not active or data cannot be sent
        """
        await self.transcription_handler.stream_audio(audio_data)

    async def close_connection(self) -> List[str]:
        """Finish streaming transcription and get results.

        Returns:
            List of complete transcribed sentences

        Raises:
            ConnectionError: If streaming session cannot be properly closed
        """
        return await self.transcription_handler.finish_streaming()

    # Remove callback registration methods
    # def register_transcription_callback(
    #     self, callback: Callable[[TranscriptionResponse], None]
    # ) -> None:
    #     """Register callback for transcription results.

    #     Args:
    #         callback: Function to handle transcription results
    #     """
    #     self.on_transcription = callback
    #     logger.debug("Registered transcription callback")

    # def register_error_handler(self, callback: Callable[[Exception], None]) -> None:
    #     """Register callback for error handling.

    #     Args: a
    #         callback: Function to handle errors
    #     """
    #     self.on_error = callback
    #     logger.debug("Registered error handler")

    async def transcribe_local_file(
        self,
        audio_file: str,
        config: Optional[TranscriptionConfig] = None,
    ) -> List[str]:
        """Transcribe local audio file.

        Args:
            audio_file: Path to audio file
            config: Configuration object

        Returns:
            List of complete transcribed sentences

        Raises:
            FileNotFoundError: If audio file does not exist
            Error: If audio file format is invalid
        """
        # Pass no callbacks
        return await self.transcription_handler.transcribe_local_file(
            audio_file, config
        )

    async def transcribe_remote_url(
        self,
        audio_url: str,
        config: Optional[TranscriptionConfig] = None,
    ) -> List[str]:
        """Transcribe audio from URL.

        Args:
            audio_url: URL of audio file
            config: Configuration object

        Returns:
            List of complete transcribed sentences

        Raises:
            Error: If URL is invalid or download fails
            requests.exceptions.RequestException: If network error occurs
        """
        # Pass no callbacks
        return await self.transcription_handler.transcribe_remote_url(audio_url, config)

    def on(self, event: LiveTranscriptionEvents, listener: Callable) -> None:
        """Register an event listener.

        Args:
            event: The event to listen for.
            listener: The function to call when the event is emitted.
        """
        self.websocket_handler.on(event, listener)

    def off(self, event: LiveTranscriptionEvents, listener: Callable) -> None:
        """Remove an event listener.

        Args:
            event: The event to remove the listener from.
            listener: The listener function to remove.
        """
        self.websocket_handler.off(event, listener)
