import requests


class WebhookWorkerError(Exception):
    """Exception raised for errors in the WebhookWorker."""


class TransientError(WebhookWorkerError):
    """A failure that may succeed if the message is tried again later."""


class MediaNotFoundError(TransientError):
    """The media file for a message is not on disk (yet).

    Jellyfin can fire ItemAdded before the file has finished landing, so this is retried.
    """


class ServiceConfigurationError(WebhookWorkerError):
    """A configured service cannot be loaded (unknown name or broken import).

    Retrying cannot fix this; it needs a config or code change, so it must fail loudly.
    """


def is_transient(error: BaseException) -> bool:
    """Decide whether a failure is worth retrying.

    Args:
        error (BaseException): The exception raised while processing a message.

    Returns:
        bool: True for explicit transient errors and for network failures talking to Jellyfin.
    """
    return isinstance(
        error,
        TransientError | requests.RequestException | ConnectionError | TimeoutError,
    )
