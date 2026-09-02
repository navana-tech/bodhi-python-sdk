"""
Bodhi API Response Data Structures
"""

from typing import List, Optional
from dataclasses import dataclass


@dataclass
class Word:
    word: str
    confidence: float


@dataclass
class SegmentMeta:
    tokens: List[str]
    timestamps: List[float]
    start_time: float
    confidence: float
    words: List[Word]


@dataclass
class TranscriptionResponse:
    call_id: str
    segment_id: int
    eos: bool
    type: str
    text: str
    segment_meta: SegmentMeta
    language_code: Optional[str] = None
