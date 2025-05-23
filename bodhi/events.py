from enum import Enum


class LiveTranscriptionEvents(Enum):
    Transcript = "Transcript"
    UtteranceEnd = "UtteranceEnd"
    SpeechStarted = "SpeechStarted"
    Error = "Error"
    Close = "Close"
