"""Custom exception classes for the Bodhi Python SDK."""


class BodhiError(Exception):
    """Base exception for Bodhi SDK errors."""

    pass


class ConfigurationError(BodhiError):
    """Raised when there is an issue with the configuration."""

    pass


class ConnectionError(BodhiError):
    """Raised when there is an issue with the WebSocket connection."""

    pass


class StreamingError(BodhiError):
    """Raised when there is an issue during audio streaming."""

    pass


class TranscriptionError(BodhiError):
    """Raised when there is an issue during transcription processing."""

    pass


class InvalidAudioFormatError(BodhiError):
    """Raised when the audio file format is invalid."""

    pass


class AudioDownloadError(BodhiError):
    """Raised when there is an issue downloading audio from a URL."""

    pass


class FileNotFoundError(BodhiError):
    """Raised when a local audio file is not found."""

    pass


class InvalidURLError(BodhiError):
    """Raised when a provided URL is invalid."""

    pass


class EmptyAudioError(BodhiError):
    """Raised when a downloaded audio file is empty."""

    pass


class WebSocketError(BodhiError):
    """Raised for general WebSocket related errors."""

    pass


class WebSocketTimeoutError(WebSocketError):
    """Raised when a WebSocket operation times out."""

    pass


class WebSocketConnectionClosedError(WebSocketError):
    """Raised when the WebSocket connection is unexpectedly closed."""

    pass


class InvalidJSONError(WebSocketError):
    """Raised when an invalid JSON response is received."""

    pass


class BodhiAPIError(WebSocketError):
    """Raised when an error is received from the Bodhi API."""

    pass
