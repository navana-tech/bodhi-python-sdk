"""Custom exception classes for the Bodhi Python SDK."""


class BodhiError(Exception):
    """Base exception for Bodhi SDK errors."""

    def __init__(
        self, message="An unknown Bodhi error occurred.", code=None, *args, **kwargs
    ):
        self.code = code
        super().__init__(message, *args, **kwargs)


class ConfigurationError(BodhiError):
    """Raised when there is an issue with the configuration."""


class ConnectionError(BodhiError):
    """Raised when there is an issue with the WebSocket connection."""


class StreamingError(BodhiError):
    """Raised when there is an issue during audio streaming."""


class TranscriptionError(BodhiError):
    """Raised when there is an issue during transcription processing."""


class InvalidAudioFormatError(BodhiError):
    """Raised when the audio file format is invalid."""


class AudioDownloadError(BodhiError):
    """Raised when there is an issue downloading audio from a URL."""


class FileNotFoundError(BodhiError):
    """Raised when a local audio file is not found."""


class InvalidURLError(BodhiError):
    """Raised when a provided URL is invalid."""


class EmptyAudioError(BodhiError):
    """Raised when a downloaded audio file is empty."""


class WebSocketError(BodhiError):
    """Raised for general WebSocket related errors."""


class WebSocketTimeoutError(WebSocketError):
    """Raised when a WebSocket operation times out."""


class WebSocketConnectionClosedError(WebSocketError):
    """Raised when the WebSocket connection is unexpectedly closed."""


class InvalidJSONError(WebSocketError):
    """Raised when an invalid JSON response is received."""


class BodhiAPIError(WebSocketError):
    """Raised when an error is received from the Bodhi API."""


class InvalidTransactionIDError(ConfigurationError):
    """Raised when an invalid transaction ID is provided."""


class MissingModelError(ConfigurationError):
    """Raised when the model is missing from configuration."""


class ModelNotAvailableError(ConfigurationError):
    """Raised when the specified model is not available."""


class MissingTransactionIDError(ConfigurationError):
    """Raised when transaction_id is missing from configuration."""


class AuthenticationError(BodhiAPIError):
    """Raised when authentication fails (HTTP 401)."""


class PaymentRequiredError(BodhiAPIError):
    """Raised when payment is required (HTTP 402)."""


class ForbiddenError(BodhiAPIError):
    """Raised when access is forbidden (HTTP 403)."""
