"""Custom exception classes for the Bodhi Python SDK."""


class BodhiError(Exception):
    """Base exception for Bodhi SDK errors."""

    def __init__(self, message="An unknown Bodhi error occurred.", *args, **kwargs):
        super().__init__(f"{message}", *args, **kwargs)


class ConfigurationError(BodhiError):
    """Raised when there is an issue with the configuration."""

    def __init__(self, message="Invalid configuration.", *args, **kwargs):
        super().__init__(f"{message}", *args, **kwargs)


class ConnectionError(BodhiError):
    """Raised when there is an issue with the WebSocket connection."""

    def __init__(self, message="Connection failed.", *args, **kwargs):
        super().__init__(f"CONNECTION_ERROR: {message}", *args, **kwargs)


class StreamingError(BodhiError):
    """Raised when there is an issue during audio streaming."""

    def __init__(self, message="Audio streaming error.", *args, **kwargs):
        super().__init__(f"STREAMING_ERROR: {message}", *args, **kwargs)


class TranscriptionError(BodhiError):
    """Raised when there is an issue during transcription processing."""

    def __init__(self, message="Transcription error.", *args, **kwargs):
        super().__init__(f"TRANSCRIPTION_ERROR: {message}", *args, **kwargs)


class InvalidAudioFormatError(BodhiError):
    """Raised when the audio file format is invalid."""

    def __init__(self, message="Invalid audio format.", *args, **kwargs):
        super().__init__(f"INVALID_AUDIO_FORMAT_ERROR: {message}", *args, **kwargs)


class AudioDownloadError(BodhiError):
    """Raised when there is an issue downloading audio from a URL."""

    def __init__(self, message="Audio download failed.", *args, **kwargs):
        super().__init__(f"AUDIO_DOWNLOAD_ERROR: {message}", *args, **kwargs)


class FileNotFoundError(BodhiError):
    """Raised when a local audio file is not found."""

    def __init__(self, message="File not found.", *args, **kwargs):
        super().__init__(f"FILE_NOT_FOUND_ERROR: {message}", *args, **kwargs)


class InvalidURLError(BodhiError):
    """Raised when a provided URL is invalid."""

    def __init__(self, message="Invalid URL.", *args, **kwargs):
        super().__init__(f"INVALID_URL_ERROR: {message}", *args, **kwargs)


class EmptyAudioError(BodhiError):
    """Raised when a downloaded audio file is empty."""

    def __init__(self, message="Empty audio file.", *args, **kwargs):
        super().__init__(f"EMPTY_AUDIO_ERROR: {message}", *args, **kwargs)


class WebSocketError(BodhiError):
    """Raised for general WebSocket related errors."""

    def __init__(self, message="WebSocket error.", *args, **kwargs):
        super().__init__(f"{message}", *args, **kwargs)


class WebSocketTimeoutError(WebSocketError):
    """Raised when a WebSocket operation times out."""

    def __init__(self, message="WebSocket operation timed out.", *args, **kwargs):
        super().__init__(f"WEBSOCKET_TIMEOUT_ERROR: {message}", *args, **kwargs)


class WebSocketConnectionClosedError(WebSocketError):
    """Raised when the WebSocket connection is unexpectedly closed."""

    def __init__(
        self, message="WebSocket connection closed unexpectedly.", *args, **kwargs
    ):
        super().__init__(
            f"WEBSOCKET_CONNECTION_CLOSED_ERROR: {message}", *args, **kwargs
        )


class InvalidJSONError(WebSocketError):
    """Raised when an invalid JSON response is received."""

    def __init__(self, message="Invalid JSON response.", *args, **kwargs):
        super().__init__(f"INVALID_JSON_ERROR: {message}", *args, **kwargs)


class BodhiAPIError(WebSocketError):
    """Raised when an error is received from the Bodhi API."""

    def __init__(self, message="Bodhi API error.", *args, **kwargs):
        super().__init__(f"BODHI_API_ERROR: {message}", *args, **kwargs)


class InvalidTransactionIDError(ConfigurationError):
    """Raised when an invalid transaction ID is provided."""

    def __init__(self, message="Invalid transaction ID.", *args, **kwargs):
        super().__init__(f"INVALID_TRANSACTION_ID_ERROR: {message}", *args, **kwargs)


class MissingModelError(ConfigurationError):
    """Raised when the model is missing from configuration."""

    def __init__(self, message="Model is missing.", *args, **kwargs):
        super().__init__(f"MISSING_MODEL_ERROR: {message}", *args, **kwargs)


class ModelNotAvailableError(ConfigurationError):
    """Raised when the specified model is not available."""

    def __init__(self, message="Model not available.", *args, **kwargs):
        super().__init__(f"MODEL_NOT_AVAILABLE_ERROR: {message}", *args, **kwargs)


class MissingTransactionIDError(ConfigurationError):
    """Raised when transaction_id is missing from configuration."""

    def __init__(self, message="Transaction ID is missing.", *args, **kwargs):
        super().__init__(f"MISSING_TRANSACTION_ID_ERROR: {message}", *args, **kwargs)


class AuthenticationError(BodhiAPIError):
    """Raised when authentication fails (HTTP 401)."""

    def __init__(self, message="Authentication failed.", *args, **kwargs):
        super().__init__(f"AUTHENTICATION_ERROR: {message}", *args, **kwargs)


class PaymentRequiredError(BodhiAPIError):
    """Raised when payment is required (HTTP 402)."""

    def __init__(self, message="Payment required.", *args, **kwargs):
        super().__init__(f"PAYMENT_REQUIRED_ERROR: {message}", *args, **kwargs)


class ForbiddenError(BodhiAPIError):
    """Raised when access is forbidden (HTTP 403)."""

    def __init__(self, message="Forbidden access.", *args, **kwargs):
        super().__init__(f"FORBIDDEN_ERROR: {message}", *args, **kwargs)
