"""Audio Processing Module for Bodhi Client"""

import wave
import asyncio
from typing import BinaryIO, Any
from .utils.logger import logger


class AudioProcessor:
    @staticmethod
    async def process_file(
        ws: Any,
        wf: wave.Wave_read,
    ) -> None:
        """Process and stream audio file data.

        Args:
            ws: WebSocket connection
            wf: Wave file object
        """
        try:

            REALTIME_RESOLUTION = 0.02  # 20ms
            byte_rate = wf.getframerate() * wf.getsampwidth() * wf.getnchannels()
            data = wf.readframes(wf.getnframes())
            audio_cursor = 0

            while len(data):
                i = int(byte_rate * REALTIME_RESOLUTION)
                chunk, data = data[:i], data[i:]
                if not ws.closed:
                    await ws.send(chunk)
                    logger.debug(f"Sent {len(chunk)} bytes of audio data")
                audio_cursor += REALTIME_RESOLUTION
                await asyncio.sleep(REALTIME_RESOLUTION)

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
