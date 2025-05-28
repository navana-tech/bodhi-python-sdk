# transcription_handler.py
"""Transcription Handler Module for Bodhi Client"""

import os
import wave
import asyncio
from typing import Any, Callable, List, Optional
import uuid
import requests
import tempfile
from .utils.logger import logger
from .transcription_config import TranscriptionConfig
from .audio_processor import AudioProcessor
from bodhi.utils.exceptions import (
    ConfigurationError,
    ConnectionError,
    StreamingError,
    AuthenticationError,
    PaymentRequiredError,
    ForbiddenError,
)
from bodhi.events import LiveTranscriptionEvents


class TranscriptionHandler:
    def __init__(self, websocket_handler: Any):
        self.websocket_handler = websocket_handler
        self.ws = None
        self.send_task = None
        self.recv_task = None

    def _prepare_config(self, config: Optional[TranscriptionConfig] = None) -> dict:
        """Prepare configuration dictionary from TranscriptionConfig instance.

        Args:
            config: Configuration object

        Returns:
            Dictionary containing configuration parameters

        Raises:
            ConfigurationError: If configuration is invalid
        """
        if config is None:
            error_msg = (
                "model is a required argument - transcription config must be defined."
            )
            raise ConfigurationError(error_msg)

        if not hasattr(config, "model") or config.model is None:
            error_msg = (
                "model is a required argument - transcription config must be defined."
            )
            raise ConfigurationError(error_msg)

        if not hasattr(config, "sample_rate") or config.sample_rate is None:
            error_msg = "sample_rate is a required argument - transcription config must be defined."
            logger.error(error_msg)
            raise ConfigurationError(error_msg)

        self.config = config
        config_instance = TranscriptionConfig(
            model=config.model,
            transaction_id=getattr(config, "transaction_id", str(uuid.uuid4())),
            parse_number=getattr(config, "parse_number"),
            hotwords=getattr(config, "hotwords"),
            aux=getattr(config, "aux"),
            exclude_partial=getattr(config, "exclude_partial"),
            sample_rate=getattr(config, "sample_rate"),
        )

        final_config = {}
        config_dict = config_instance.to_dict()
        if config_dict:
            final_config.update(config_dict)
        logger.debug(f"Final configuration: {final_config}")
        return final_config

    async def start_streaming_session(
        self,
        config: Optional[TranscriptionConfig] = None,
    ) -> None:
        """Start a streaming transcription session.

        Args:
            config: Configuration object

        Raises:
            ConnectionError: If configuration is incorrect
        """
        try:
            final_config = self._prepare_config(config)

            self.ws = await self.websocket_handler.connect()
            await self.websocket_handler.send_config(self.ws, final_config)
            # Pass the callbacks from BodhiClient to WebSocketHandler
            self.recv_task = asyncio.create_task(
                self.websocket_handler.process_transcription_stream(
                    self.ws,
                )
            )
            logger.info("Started streaming session and processing stream")

        except Exception as e:
            error_msg = f"Failed to start streaming session: {str(e)}"
            error = ConnectionError(error_msg)
            logger.error(error_msg)
            await self.websocket_handler.emit(LiveTranscriptionEvents.Error, error)
            return

    async def stream_audio(self, audio_data: bytes) -> List[str]:
        """Stream audio data to the WebSocket connection and process results.

        Args:
            audio_data: Audio data bytes to stream

        Raises:
            StreamingError: If streaming session is not started or connection is closed
        """
        if not self.ws or self.ws.closed:
            error_msg = "WebSocket connection is not established or closed"
            logger.error(error_msg)
            error = StreamingError(error_msg)
            await self.websocket_handler.emit(LiveTranscriptionEvents.Error, error)
            return

        try:
            from io import BytesIO

            stream = BytesIO(audio_data)
            await AudioProcessor.process_stream(stream, self.ws)
        except Exception as e:
            error_msg = f"Failed to stream audio data: {str(e)}"
            logger.error(error_msg)
            error = StreamingError(error_msg)
            await self.websocket_handler.emit(LiveTranscriptionEvents.Error, error)
            return

    async def finish_streaming(self) -> List[str]:
        """Finish streaming session and get transcription results.

        Returns:
            List of complete transcribed sentences

        Raises:
            ConnectionError: If streaming session is not started
        """
        if not self.ws:
            error_msg = "No active streaming session"
            logger.error(error_msg)
            error = ConnectionError(error_msg)
            await self.websocket_handler.emit(LiveTranscriptionEvents.Error, error)
            return []

        try:
            if not self.ws.closed:
                await self.ws.send('{"eof": 1}')
                logger.debug("Sent EOF signal")
                try:
                    result = await asyncio.gather(self.recv_task)
                    await self.ws.close()
                    logger.info("Finished streaming session")
                    return result[0]  # Extract result from gather tuple
                except asyncio.CancelledError:
                    error = ConnectionError("Transcription tasks cancelled")
                    await self.websocket_handler.emit(
                        LiveTranscriptionEvents.Error, error
                    )
                    return []
            return []
        except Exception as e:
            error_msg = f"Failed to finish streaming: {str(e)}"
            logger.error(error_msg)
            error = ConnectionError(error_msg)
            await self.websocket_handler.emit(LiveTranscriptionEvents.Error, error)
            return []
        finally:
            self.ws = None

    async def transcribe_remote_url(
        self,
        audio_url: str,
        config: Optional[TranscriptionConfig] = None,
        on_transcription: Optional[Callable[[Any], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
    ) -> List[str]:
        """Transcribe audio from URL.

        Args:
            audio_url: URL of audio file
            config: Configuration object
            on_transcription: Callback for transcription results
            on_error: Callback for error handling

        Returns:
            List of complete transcribed sentences

        Raises:
            InvalidURLError: If URL is invalid or download fails
            requests.exceptions.RequestException: If network error occurs
        """
        return await self._handle_audio_source(
            source=audio_url,
            is_url=True,
            config=config,
            on_transcription=on_transcription,
            on_error=on_error,
        )

    async def transcribe_local_file(
        self,
        audio_file: str,
        config: Optional[TranscriptionConfig] = None,
        on_transcription: Optional[Callable[[Any], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
    ) -> List[str]:
        """Transcribe local audio file.

        Args:
            audio_file: Path to audio file
            config: Configuration object
            on_transcription: Callback for transcription results
            on_error: Callback for error handling

        Returns:
            List of complete transcribed sentences

        Raises:
            FileNotFoundError: If audio file does not exist
            InvalidAudioFormatError: If audio file format is invalid
        """
        return await self._handle_audio_source(
            source=audio_file,
            is_url=False,
            config=config,
            on_transcription=on_transcription,
            on_error=on_error,
        )

    async def _handle_audio_source(
        self,
        source: Any,
        is_url: bool,
        config: Optional[TranscriptionConfig] = None,
        on_transcription: Optional[Callable[[Any], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
    ) -> List[str]:
        """Handle audio source (URL or local file) for transcription.

        Args:
            source: Audio source (URL or file path)
            is_url: Whether source is a URL
            config: Configuration object
            on_transcription: Callback for transcription results
            on_error: Callback for error handling

        Returns:
            List of complete transcribed sentences

        Raises:
            StreamingError: If source is invalid or transcription fails
        """
        temp_audio = None
        try:
            if is_url:
                # Validate URL format
                if not source.startswith(("http://", "https://")):
                    error_msg = f"Invalid URL format: {source}"
                    logger.error(error_msg)
                    error = InvalidURLError(error_msg)
                    await self.websocket_handler.emit(
                        LiveTranscriptionEvents.Error, error
                    )
                    return []

                temp_audio = tempfile.NamedTemporaryFile(delete=True)
                logger.debug(f"Downloading audio from URL to temporary file")

                # Set timeout for the request
                response = requests.get(source, stream=True, timeout=30)
                response.raise_for_status()

                total_size = 0
                for chunk in response.iter_content(chunk_size=8192):
                    if not chunk:
                        break
                    temp_audio.write(chunk)
                    total_size += len(chunk)

                temp_audio.flush()
                logger.debug(f"Downloaded {total_size} bytes of audio data")

                # Verify downloaded file is not empty
                if total_size == 0:
                    error_msg = "Downloaded audio file is empty"
                    logger.error(error_msg)
                    error = EmptyAudioError(error_msg)
                    await self.websocket_handler.emit(
                        LiveTranscriptionEvents.Error, error
                    )
                    return []

                source = temp_audio.name
            else:
                # Validate local file exists
                if not os.path.exists(source):
                    error_msg = f"Audio file not found: {source}"
                    logger.error(error_msg)
                    error = FileNotFoundError(error_msg)
                    await self.websocket_handler.emit(
                        LiveTranscriptionEvents.Error, error
                    )
                    return []

            # Validate file format
            logger.debug(f"Validating audio file format: {source}")
            with open(source, "rb") as f:
                header = f.read(4)
                if header != b"RIFF":
                    error_msg = f"Invalid audio file format. Expected WAV file, got file with header: {header}"
                    logger.error(error_msg)
                    error = InvalidAudioFormatError(error_msg)
                    await self.websocket_handler.emit(
                        LiveTranscriptionEvents.Error, error
                    )
                    return []

            wf = wave.open(source, "rb")
            (channels, sample_width, sample_rate, num_samples, _, _) = wf.getparams()
            logger.debug(
                f"Audio parameters: channels={channels}, sample_rate={sample_rate}, num_samples={num_samples}"
            )

            config.sample_rate = sample_rate
            final_config = self._prepare_config(config)

            ws = await self.websocket_handler.connect()
            await self.websocket_handler.send_config(ws, final_config)

            send_task = asyncio.create_task(AudioProcessor.process_file(ws, wf))
            recv_task = asyncio.create_task(
                self.websocket_handler.process_transcription_stream(ws)
            )

            try:
                result = await asyncio.gather(send_task, recv_task)
                logger.info("Transcription completed successfully")
                return result[
                    1
                ]  # Return complete_sentences from process_transcription_stream
            except asyncio.CancelledError:
                error = ConnectionError("Transcription tasks cancelled")
                await self.websocket_handler.emit(LiveTranscriptionEvents.Error, error)
                return []

        except requests.exceptions.RequestException as e:
            error_msg = f"Failed to download audio from URL: {str(e)}"
            logger.error(error_msg)
            error = AudioDownloadError(error_msg)
            await self.websocket_handler.emit(LiveTranscriptionEvents.Error, error)
            return []
        except Exception as e:
            error_msg = f"Failed to transcribe audio file: {str(e)}"
            error = None
            if e.status_code == 401:
                error_msg = "Authentication failed: Invalid API Key or Customer ID."
                error = AuthenticationError(error_msg)
            elif e.status_code == 402:
                error_msg = "Payment required: Please check your subscription."
                error = PaymentRequiredError(error_msg)
            elif e.status_code == 403:
                error_msg = (
                    "Forbidden: You do not have permission to access this resource."
                )
                error = ForbiddenError(error_msg)
            else:
                error = StreamingError(error_msg)
            logger.error(error_msg)
            await self.websocket_handler.emit(LiveTranscriptionEvents.Error, error)
            return []
        finally:
            if temp_audio:
                temp_audio.close()
