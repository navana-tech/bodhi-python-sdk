"""
Bodhi Client Configuration
"""

import uuid
from typing import Optional, List, Dict, Union
from dataclasses import dataclass
from bodhi.utils.exceptions import StreamingError


@dataclass
class Hotword:
    phrase: str
    score: Optional[float] = None


class TranscriptionConfig:
    def __init__(
        self,
        model: str,
        transaction_id: Optional[str] = None,
        parse_number: Optional[bool] = None,
        hotwords: Optional[List[Hotword]] = None,
        aux: Optional[bool] = None,
        exclude_partial: Optional[bool] = None,
        sample_rate: Optional[int] = None,
    ):
        self.model = model
        self.transaction_id = transaction_id or str(uuid.uuid4())
        self.parse_number = parse_number
        self.hotwords = hotwords
        self.aux = aux
        self.exclude_partial = exclude_partial
        self.sample_rate = sample_rate

    def to_dict(self):
        """Convert Config instance to dictionary with JSON-serializable values"""
        config_dict = {
            "model": self.model,
            "transaction_id": self.transaction_id,
            "parse_number": self.parse_number,
            "aux": self.aux,
            "exclude_partial": self.exclude_partial,
            "sample_rate": self.sample_rate if self.sample_rate else 8000,
        }
        if self.hotwords:
            config_dict["hotwords"] = [
                {"phrase": hw.phrase, "score": hw.score} for hw in self.hotwords
            ]
        return {k: v for k, v in config_dict.items() if v is not None}
