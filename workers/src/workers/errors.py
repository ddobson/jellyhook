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


# HTTP statuses worth retrying. Other 4xx (bad API key, unknown item) will fail again.
_RETRYABLE_STATUSES = frozenset({408, 429})


def _is_retryable_http_error(error: requests.HTTPError) -> bool:
    """Decide whether an HTTP error response is worth retrying.

    Args:
        error (requests.HTTPError): The error, normally from `raise_for_status()`.

    Returns:
        bool: True for 5xx, 408 and 429. True when there is no response to inspect.
    """
    response = error.response
    # Not `if response`: a requests.Response is falsy for 4xx and 5xx statuses
    if response is None:
        return True
    return response.status_code >= 500 or response.status_code in _RETRYABLE_STATUSES


def is_transient(error: BaseException) -> bool:
    """Decide whether a failure is worth retrying.

    Services often wrap low-level errors (`raise PlaylistAssignmentError(...) from exc`), so this
    follows the explicit `__cause__` chain and decides on the first recognisable error in it.
    Wrap with `from exc` to keep a failure classifiable.

    Args:
        error (BaseException): The exception raised while processing a message.

    Returns:
        bool: True for explicit transient errors, network failures, and HTTP 5xx/408/429.
            False for everything else, including other HTTP 4xx responses.
    """
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, requests.HTTPError):  # before RequestException, its parent
            return _is_retryable_http_error(current)
        if isinstance(
            current,
            TransientError | requests.RequestException | ConnectionError | TimeoutError,
        ):
            return True
        current = current.__cause__
    return False
