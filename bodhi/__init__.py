"""
Bodhi Python SDK - Streaming Speech Recognition
"""

__version__ = "1.2.0"

EOF_SIGNAL = '{"eof": 1}'

from .transcription_client import BodhiClient
from .transcription_config import TranscriptionConfig, Hotword
from .transcription_response import TranscriptionResponse
from .events import LiveTranscriptionEvents
from bodhi.utils.error_utils import BodhiErrors

__all__ = [
    "BodhiClient",
    "TranscriptionConfig",
    "TranscriptionResponse",
    "Hotword",
    "LiveTranscriptionEvents",
    "EOF_SIGNAL",
    BodhiErrors,
]
