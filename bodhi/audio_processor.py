"""Audio Processing Module for Bodhi Client"""

import wave
import asyncio
from typing import BinaryIO, Any
from .utils.logger import logger


class AudioProcessor:
    @staticmethod
    async def process_file(
        ws: Any, wf: wave.Wave_read, buffer_size: int, interval_seconds: float
    ) -> None:
        """Process and stream audio file data.

        Args:
            ws: WebSocket connection
            wf: Wave file object
            buffer_size: Number of frames to read at once
            interval_seconds: Delay between sending frames
        """
        try:
            logger.debug(
                f"Starting audio file processing with buffer size {buffer_size}"
            )
            while True:
                data = wf.readframes(buffer_size)
                if len(data) == 0:
                    break
                if not ws.closed:
                    await ws.send(data)
                    logger.debug(f"Sent {len(data)} bytes of audio data")
                await asyncio.sleep(interval_seconds)

            if not ws.closed:
                await ws.send('{"eof": 1}')
                logger.debug("Sent EOF signal")
        except Exception as e:
            logger.error(f"Error processing audio file: {str(e)}")
            raise

    @staticmethod
    async def process_stream(stream: BinaryIO, ws: Any) -> None:
        """Process and stream generic binary stream data.

        Args:
            stream: Binary input stream
            ws: WebSocket connection
        """
        try:
            data = stream.read()
            if not ws.closed:
                await ws.send(data)
                logger.debug(f"Sent {len(data)} bytes of stream data")

        except Exception as e:
            logger.error(f"Error processing stream: {str(e)}")
            raise
