import pytest
import requests

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
