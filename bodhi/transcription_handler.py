# transcription_handler.py
"""Transcription Handler Module for Bodhi Client"""

from csv import Error
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
    AudioDownloadError,
    AuthenticationError,
    BodhiAPIError,
    ConfigurationError,
    ConnectionError,
    EmptyAudioError,
    FileNotFoundError,
    ForbiddenError,
    InvalidAudioFormatError,
    InvalidURLError,
    PaymentRequiredError,
    StreamingError,
)
import json
from bodhi.events import LiveTranscriptionEvents
from . import EOF_SIGNAL
from bodhi.utils.error_utils import make_error_response, BodhiErrors
import sys


class TranscriptionHandler:
    def __init__(self, websocket_handler: Any):
        self.websocket_handler = websocket_handler
        self.ws = None
        self.send_task = None
        self.recv_task = None

    async def _handle_api_error(self, e: Exception):
        """Handle API-related errors and emit appropriate events."""
        error = None
        status_code = None
        if hasattr(e, "status") and isinstance(e.status, int):
            status_code = e.status
        details = getattr(e, "details", None)
        # Map specific status codes
        if status_code == BodhiErrors.Unauthorized:
            message = "Authentication Error"
            error_response = make_error_response(
                message=message,
                code=status_code,
                extra={"details": details} if details else None,
            )
            error = AuthenticationError(json.dumps(error_response))
        elif status_code == BodhiErrors.InsufficientCredit:
            message = "Insufficient Credits"
            error_response = make_error_response(
                message,
                status_code,
                extra={"details": details} if details else None,
            )
            error = PaymentRequiredError(json.dumps(error_response))
        elif status_code == BodhiErrors.InactiveCustomer:
            message = "Forbidden"
            error_response = make_error_response(
                message,
                status_code,
                extra={"details": details} if details else None,
            )
            error = ForbiddenError(json.dumps(error_response))
        else:
            message = "Internal Server Error"
            status_code = status_code or BodhiErrors.InternalServerError
            error_response = make_error_response(
                message=message,
                code=status_code,
                extra={"details": details} if details else None,
            )
            error = ConnectionError(json.dumps(error_response))
        # Use server message if available
        if details and isinstance(details, dict):
            message = details.get("message", message)
        logger.debug(f"API Error: {error_response}")
        await self.websocket_handler.emit(LiveTranscriptionEvents.Error, error)
        raise error

    def _validate_event_bindings(self) -> None:
        logger.info("Validating event bindings...")

        # Check if transcript listener is registered
        if LiveTranscriptionEvents.Transcript not in self.websocket_handler._listeners:
            logger.warning(
                "\n"
                + "*" * 80
                + "\n"
                + "⚠️  WARNING: NO LISTENER REGISTERED FOR 'TRANSCRIPT' EVENT! ⚠️\n"
                + ">> This may result in missed transcription outputs.\n"
                + ">> Make sure to register a listener using `.on(LiveTranscriptionEvents.Transcript, callback)`.\n"
                + "*" * 80
                + "\n"
            )

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
            error_msg = make_error_response(
                message="transcription config must be defined",
                code=BodhiErrors.BadRequest.value,
            )
            raise ConfigurationError(json.dumps(error_msg))

        if not hasattr(config, "model") or config.model is None:
            error_msg = make_error_response(
                message="model is a required argument - transcription config must be defined.",
                code=BodhiErrors.BadRequest.value,
            )
            raise ConfigurationError(json.dumps(error_msg))

        if not hasattr(config, "sample_rate") or config.sample_rate is None:
            error_msg = make_error_response(
                message="sample_rate is a required argument - transcription config must be defined.",
                code=BodhiErrors.BadRequest.value,
            )
            raise ConfigurationError(json.dumps(error_msg))

        self.config = config
        config_instance = TranscriptionConfig(
            model=config.model,
            transaction_id=getattr(config, "transaction_id", str(uuid.uuid4())),
            parse_number=getattr(config, "parse_number"),
            hotwords=getattr(config, "hotwords"),
            aux=getattr(config, "aux"),
            exclude_partial=getattr(config, "exclude_partial"),
            sample_rate=getattr(config, "sample_rate"),
            at_start_lid=getattr(config, "at_start_lid"),
            transliterate=getattr(config, "transliterate"),
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
        final_config = self._prepare_config(config)
        self._validate_event_bindings()  # Validate if listener is registered

        try:
            self.ws = await self.websocket_handler.connect()
            await self.websocket_handler.send_config(self.ws, final_config)
            self.recv_task = asyncio.create_task(
                self.websocket_handler.process_transcription_stream(
                    self.ws,
                )
            )
            logger.info("Started streaming session and processing stream")
        except Exception as e:
            await self._handle_api_error(e)
            return

    async def stream_audio(self, audio_data: bytes) -> None:
        """Stream audio data to the WebSocket connection and process results.

        Args:
            audio_data: Audio data bytes to stream

        Raises:
            StreamingError: If streaming session is not started or connection is closed
        """
        if not self.ws or self.ws.closed:
            error_msg = make_error_response(
                message="WebSocket connection is not established or closed",
                code=BodhiErrors.ClientClosed.value,
            )
            logger.error(error_msg)
            await self.websocket_handler.emit(
                LiveTranscriptionEvents.Error, StreamingError(json.dumps(error_msg))
            )

        try:
            from io import BytesIO

            stream = BytesIO(audio_data)
            await AudioProcessor.process_stream(stream, self.ws)
        except Exception as e:
            error_msg = make_error_response(
                message=f"Failed to stream audio data: {str(e)}",
                code=BodhiErrors.GatewayDown.value,
            )
            logger.error(error_msg)
            await self.websocket_handler.emit(
                LiveTranscriptionEvents.Error, StreamingError(json.dumps(error_msg))
            )
            return

    async def finish_streaming(self) -> None:
        """Finish streaming session and get transcription results.

        Returns:
            List of complete transcribed sentences

        Raises:
            ConnectionError: If streaming session is not started
        """
        if not self.ws:
            error_msg = make_error_response(
                message="No active streaming session",
                code=BodhiErrors.BadRequest.value,
            )
            logger.error(error_msg)
            await self.websocket_handler.emit(
                LiveTranscriptionEvents.Error, ConnectionError(json.dumps(error_msg))
            )

        try:
            if not self.ws.closed:
                await self.ws.send_str(EOF_SIGNAL)
                logger.debug("Sent EOF signal")
                try:
                    await asyncio.gather(self.recv_task)
                    await self.ws.close()
                    logger.info("Finished streaming session")
                    return
                except asyncio.CancelledError:
                    error_response = make_error_response(
                        message="Transcription tasks were cancelled",
                        code=BodhiErrors.ClientClosed.value,
                    )
                    await self.websocket_handler.emit(
                        LiveTranscriptionEvents.Error,
                        ConnectionError(json.dumps(error_response)),
                    )
                    return
            return
        except Exception as e:
            error_msg = make_error_response(
                message=f"Failed to finish streaming: {str(e)}",
                code=BodhiErrors.GatewayDown.value,
            )
            logger.error(error_msg)
            await self.websocket_handler.emit(
                LiveTranscriptionEvents.Error, ConnectionError(json.dumps(error_msg))
            )

        finally:
            self.ws = None

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
            InvalidURLError: If URL is invalid or download fails
            requests.exceptions.RequestException: If network error occurs
        """
        return await self._handle_audio_source(
            source=audio_url,
            is_url=True,
            config=config,
        )

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
            InvalidAudioFormatError: If audio file format is invalid
        """
        return await self._handle_audio_source(
            source=audio_file,
            is_url=False,
            config=config,
        )

    async def _handle_audio_source(
        self,
        source: Any,
        is_url: bool,
        config: Optional[TranscriptionConfig] = None,
    ) -> None:
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
                    error_msg = make_error_response(
                        message=f"Invalid URL format: {source}",
                        code=BodhiErrors.BadRequest.value,
                    )
                    await self.websocket_handler.emit(
                        LiveTranscriptionEvents.Error,
                        InvalidURLError(json.dumps(error_msg)),
                    )
                    return

                temp_audio = tempfile.NamedTemporaryFile(delete=True)
                logger.debug("Downloading audio from URL to temporary file")

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
                    error_msg = make_error_response(
                        message="Downloaded audio file is empty",
                        code=BodhiErrors.BadRequest.value,
                    )
                    await self.websocket_handler.emit(
                        LiveTranscriptionEvents.Error,
                        AudioDownloadError(json.dumps(error_msg)),
                    )
                    return

                source = temp_audio.name
            else:
                # Validate local file exists
                if not os.path.exists(source):
                    error_msg = make_error_response(
                        message=f"Audio file not found: {source}",
                        code=BodhiErrors.BadRequest.value,
                    )
                    await self.websocket_handler.emit(
                        LiveTranscriptionEvents.Error,
                        FileNotFoundError(json.dumps(error_msg)),
                    )
                    return

            # Validate file format
            logger.debug(f"Validating audio file format: {source}")
            with open(source, "rb") as f:
                header = f.read(4)
                if header != b"RIFF":
                    error_msg = make_error_response(
                        message=f"Invalid audio file format. Expected WAV file, got file with header: {header}",
                        code=BodhiErrors.BadRequest.value,
                    )
                    await self.websocket_handler.emit(
                        LiveTranscriptionEvents.Error,
                        InvalidAudioFormatError(json.dumps(error_msg)),
                    )
                    return

            wf = wave.open(source, "rb")
            (channels, sample_width, sample_rate, num_samples, _, _) = wf.getparams()
            logger.debug(
                f"Audio parameters: channels={channels}, sample_rate={sample_rate}, num_samples={num_samples}"
            )

            config.sample_rate = sample_rate
            final_config = self._prepare_config(config)
            self._validate_event_bindings()  # Validate if listener is registered

            ws = await self.websocket_handler.connect()
            await self.websocket_handler.send_config(ws, final_config)

            send_task = asyncio.create_task(AudioProcessor.process_file(ws, wf))
            recv_task = asyncio.create_task(
                self.websocket_handler.process_transcription_stream(ws)
            )

            try:
                await asyncio.gather(send_task, recv_task)
                logger.debug("Transcription completed successfully")
            except asyncio.CancelledError:
                error_response = make_error_response(
                    message="Transcription tasks were cancelled",
                    code=BodhiErrors.ClientClosed.value,
                )
                await self.websocket_handler.emit(
                    LiveTranscriptionEvents.Error,
                    ConnectionError(json.dumps(error_response)),
                )
                return

        except requests.exceptions.RequestException as e:
            error_msg = f"Failed to download audio from URL: {str(e)}"
            logger.error(error_msg)
            error_response = make_error_response(
                message=error_msg, code=BodhiErrors.BadRequest.value
            )

            await self.websocket_handler.emit(
                LiveTranscriptionEvents.Error,
                AudioDownloadError(json.loads(error_response)),
            )
            return
        except Exception as e:
            await self._handle_api_error(e)
            return
        finally:
            if temp_audio:
                temp_audio.close()
