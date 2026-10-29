import pytest
import requests
from jellyfin_apiclient_python.exceptions import HTTPException as JellyfinHTTPException

from workers.errors import (
    MediaNotFoundError,
    ServiceConfigurationError,
    TransientError,
    WebhookWorkerError,
    is_transient,
)


@pytest.mark.parametrize(
    "error",
    [
        TransientError("try later"),
        MediaNotFoundError("No video found"),
        requests.ConnectionError("jellyfin down"),
        requests.Timeout("jellyfin slow"),
        ConnectionResetError("reset"),
        TimeoutError("timed out"),
    ],
)
def test_is_transient_true(error):
    """Failures that can clear up on their own are retried."""
    assert is_transient(error) is True


@pytest.mark.parametrize(
    "error",
    [
        WebhookWorkerError("found more than one video"),
        ServiceConfigurationError("Unknown service: x"),
        ValueError("bad value"),
        KeyError("Name"),
        FileNotFoundError("gone"),
    ],
)
def test_is_transient_false(error):
    """Bugs, bad data and bad config will fail the same way again."""
    assert is_transient(error) is False


def test_media_not_found_is_a_webhook_worker_error():
    """Existing handlers that catch WebhookWorkerError keep working."""
    assert issubclass(MediaNotFoundError, WebhookWorkerError)


def make_http_error(status_code):
    """Build the HTTPError that `raise_for_status()` raises for a status code."""
    response = requests.Response()
    response.status_code = status_code
    return requests.HTTPError(f"{status_code} error", response=response)


@pytest.mark.parametrize("status_code", [500, 502, 503, 504, 408, 429])
def test_is_transient_retries_server_side_http_errors(status_code):
    """The server may recover, or the rate limit may lift."""
    assert is_transient(make_http_error(status_code)) is True


@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 422])
def test_is_transient_does_not_retry_client_http_errors(status_code):
    """A bad API key or unknown item fails identically five times; dead-letter it at once."""
    assert is_transient(make_http_error(status_code)) is False


def test_is_transient_retries_an_http_error_with_no_response():
    assert is_transient(requests.HTTPError("no response attached")) is True


def wrap(outer_type, message, cause):
    """Build `outer_type(message)` chained from `cause` the way `raise ... from` does."""
    outer = outer_type(message)
    outer.__cause__ = cause
    return outer


def test_is_transient_sees_through_a_service_wrapper():
    """playlist_assignment re-raises Jellyfin failures as its own error type, `from exc`."""
    wrapped = wrap(
        WebhookWorkerError, "Failed to communicate with Jellyfin", requests.ConnectionError()
    )

    assert is_transient(wrapped) is True


def test_is_transient_applies_the_status_rule_to_a_wrapped_http_error():
    assert is_transient(wrap(WebhookWorkerError, "x", make_http_error(503))) is True
    assert is_transient(wrap(WebhookWorkerError, "x", make_http_error(401))) is False


def test_is_transient_wrapper_around_an_ordinary_bug_stays_permanent():
    assert is_transient(wrap(WebhookWorkerError, "x", ValueError("bad value"))) is False


def test_is_transient_follows_multiple_levels():
    inner = wrap(WebhookWorkerError, "inner", TimeoutError("slow"))

    assert is_transient(wrap(WebhookWorkerError, "outer", inner)) is True


def test_is_transient_survives_a_cause_cycle():
    first = WebhookWorkerError("first")
    second = WebhookWorkerError("second")
    first.__cause__ = second
    second.__cause__ = first

    assert is_transient(first) is False


def raised_while_handling(outer, inner):
    """Raise `outer` inside an `except` for `inner`, as jellyfin_apiclient_python does."""
    try:
        try:
            raise inner
        except type(inner):
            raise outer  # noqa: B904 - reproducing the client library's behavior
    except type(outer) as exc:
        return exc


def test_is_transient_follows_the_implicit_context():
    """The Jellyfin client re-raises network errors without `from`, leaving only __context__."""
    error = raised_while_handling(WebhookWorkerError("wrapped"), requests.ConnectionError())

    assert error.__cause__ is None
    assert is_transient(error) is True


def test_is_transient_ignores_a_context_suppressed_with_from_none():
    try:
        try:
            raise requests.ConnectionError()
        except requests.ConnectionError:
            raise WebhookWorkerError("unrelated") from None
    except WebhookWorkerError as exc:
        error = exc

    assert is_transient(error) is False


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("ServerUnreachable", True),
        ("ReadTimeout", True),
        (503, True),
        (502, True),
        (429, True),
        (408, True),
        ("Unauthorized", False),
        ("AccessRestricted", False),
        ("MissingSchema", False),
        (404, False),
        (400, False),
    ],
)
def test_is_transient_classifies_jellyfin_client_errors(status, expected):
    """The status alone decides, even with no requests error chained underneath."""
    assert is_transient(JellyfinHTTPException(status, "detail")) is expected


def test_is_transient_sees_a_jellyfin_outage_through_a_service_wrapper():
    """metadata_update wraps the client error `from e`; the outage must still be retried."""
    client_error = raised_while_handling(
        JellyfinHTTPException("ServerUnreachable", "down"), requests.ConnectionError()
    )

    assert is_transient(wrap(WebhookWorkerError, "Failed to update metadata", client_error))
