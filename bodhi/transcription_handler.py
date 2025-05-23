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
from .utils.exceptions import (
    ConfigurationError,
    ConnectionError,
    StreamingError,
    InvalidURLError,
    EmptyAudioError,
    InvalidAudioFormatError,
    AudioDownloadError,
    FileNotFoundError,
)


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
            logger.debug("No config provided, creating default Config instance")
            config = TranscriptionConfig()
        if not hasattr(config, "model"):
            error_msg = "Config must include 'model' field"
            logger.error(error_msg)
            raise ConfigurationError(error_msg)

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
            print(final_config, "final_config...")
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
            logger.error(error_msg, exc_info=True)
            raise ConnectionError(error_msg)

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
            raise StreamingError(error_msg)

        try:
            from io import BytesIO

            stream = BytesIO(audio_data)
            await AudioProcessor.process_stream(stream, self.ws)
        except Exception as e:
            error_msg = f"Failed to stream audio data: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise StreamingError(error_msg)

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
            raise ConnectionError(error_msg)

        try:
            if not self.ws.closed:
                await self.ws.send('{"eof": 1}')
                logger.debug("Sent EOF signal")
                # Wait for the receive task to complete using gather
                try:
                    result = await asyncio.gather(self.recv_task)
                    await self.ws.close()
                    logger.info("Finished streaming session")
                    return result[0]  # Extract result from gather tuple
                except asyncio.CancelledError:
                    logger.warning("Transcription tasks cancelled")
                    raise
            return []
        except Exception as e:
            error_msg = f"Failed to finish streaming: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise ConnectionError(error_msg)
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
                    raise InvalidURLError(error_msg)

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
                    raise EmptyAudioError(error_msg)

                source = temp_audio.name
            else:
                # Validate local file exists
                if not os.path.exists(source):
                    logger.error(f"Audio file not found: {source}")
                    raise FileNotFoundError(f"Audio file not found: {source}")

            # Validate file format
            logger.debug(f"Validating audio file format: {source}")
            with open(source, "rb") as f:
                header = f.read(4)
                if header != b"RIFF":
                    error_msg = f"Invalid audio file format. Expected WAV file, got file with header: {header}"
                    logger.error(error_msg)
                    if on_error:
                        await on_error(InvalidAudioFormatError(error_msg))
                    raise InvalidAudioFormatError(error_msg)

            wf = wave.open(source, "rb")
            (channels, sample_width, sample_rate, num_samples, _, _) = wf.getparams()
            logger.debug(
                f"Audio parameters: channels={channels}, sample_rate={sample_rate}, num_samples={num_samples}"
            )

            final_config = self._prepare_config(config)
            final_config["sample_rate"] = sample_rate

            ws = await self.websocket_handler.connect()
            await self.websocket_handler.send_config(ws, final_config)

            buffer_size = int(sample_rate * 0.1)  # 100ms chunks
            interval_seconds = 0.1
            logger.debug(
                f"Audio processing parameters: buffer_size={buffer_size}, interval={interval_seconds}s"
            )

            send_task = asyncio.create_task(
                AudioProcessor.process_file(ws, wf, buffer_size, interval_seconds)
            )
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
                logger.warning("Transcription tasks cancelled")
                raise

        except requests.exceptions.RequestException as e:
            error_msg = f"Failed to download audio from URL: {str(e)}"
            logger.error(error_msg, exc_info=True)
            if on_error:
                await on_error(e)
            raise AudioDownloadError(error_msg)
        except Exception as e:
            error_msg = f"Failed to transcribe audio file: {str(e)}"
            logger.error(error_msg, exc_info=True)
            if on_error:
                await on_error(e)
            raise StreamingError(error_msg)
        finally:
            if temp_audio:
                temp_audio.close()
