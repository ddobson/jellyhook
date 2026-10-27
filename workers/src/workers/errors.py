import requests
from jellyfin_apiclient_python.exceptions import HTTPException as JellyfinHTTPException


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
    return _is_retryable_status(response.status_code)


def _is_retryable_status(status_code: int) -> bool:
    """True for 5xx, 408 and 429."""
    return status_code >= 500 or status_code in _RETRYABLE_STATUSES


# Statuses jellyfin_apiclient_python reports by name when the server could not be reached
_RETRYABLE_JELLYFIN_STATUSES = frozenset({"ServerUnreachable", "ReadTimeout"})


def _is_retryable_jellyfin_error(error: JellyfinHTTPException) -> bool:
    """Decide whether a jellyfin_apiclient_python HTTPException is worth retrying.

    The client raises `HTTPException(status, error)` where `status` is either a name
    ("ServerUnreachable", "Unauthorized", ...) or the numeric HTTP status.

    Args:
        error (JellyfinHTTPException): The error raised by the Jellyfin client.

    Returns:
        bool: True when Jellyfin was unreachable or slow, or answered 5xx/408/429.
    """
    status = getattr(error, "status", None)
    if isinstance(status, int):
        return _is_retryable_status(status)
    return status in _RETRYABLE_JELLYFIN_STATUSES


def _next_in_chain(error: BaseException) -> BaseException | None:
    """The exception `error` was raised from, or raised while handling.

    `raise ... from exc` sets `__cause__`. A plain `raise` inside an `except` block only sets
    `__context__`, which is how jellyfin_apiclient_python reports network failures, so that is
    followed too unless it was suppressed with `from None`.
    """
    if error.__cause__ is not None:
        return error.__cause__
    if error.__suppress_context__:
        return None
    return error.__context__


def is_transient(error: BaseException) -> bool:
    """Decide whether a failure is worth retrying.

    Services often wrap low-level errors (`raise PlaylistAssignmentError(...) from exc`), so this
    follows the exception chain (`__cause__`, else `__context__` unless suppressed) and decides
    on the first recognisable error in it. Wrap with `from exc` to keep a failure classifiable.

    Args:
        error (BaseException): The exception raised while processing a message.

    Returns:
        bool: True for explicit transient errors, network failures, HTTP 5xx/408/429, and
            Jellyfin client errors for an unreachable or slow server.
            False for everything else, including other HTTP 4xx responses.
    """
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, requests.HTTPError):  # before RequestException, its parent
            return _is_retryable_http_error(current)
        if isinstance(current, JellyfinHTTPException):
            return _is_retryable_jellyfin_error(current)
        if isinstance(
            current,
            TransientError | requests.RequestException | ConnectionError | TimeoutError,
        ):
            return True
        current = _next_in_chain(current)
    return False
