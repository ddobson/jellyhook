import subprocess
import sys
from unittest import mock

import pytest
from jellyfin_apiclient_python.connection_manager import CONNECTION_STATE

from workers.clients import jellyfin
from workers.errors import TransientError, is_transient


@pytest.fixture(autouse=True)
def fresh_client():
    """Every test starts (and ends) with no cached client."""
    jellyfin.reset_client()
    yield
    jellyfin.reset_client()


@pytest.fixture
def fake_library():
    """Replace the Jellyfin library class so no connection is attempted."""
    with mock.patch.object(jellyfin, "JellyfinClient") as cls:
        cls.return_value.authenticate.return_value = {"State": CONNECTION_STATE["SignedIn"]}
        cls.return_value.config.data = {}
        yield cls


def test_importing_the_services_does_not_connect():
    """The whole point: importing services must not touch Jellyfin.

    Runs in a fresh interpreter so the services really are imported for the first time.
    """
    code = (
        "from unittest import mock\n"
        "from workers.clients import jellyfin\n"
        "with mock.patch.object(jellyfin, '_connect') as connect:\n"
        "    import workers.services.metadata_update\n"
        "    import workers.services.playlist_assignment\n"
        "assert not connect.called\n"
    )

    subprocess.run([sys.executable, "-I", "-c", code], check=True, timeout=60)


def test_get_client_connects_once_and_shares_the_client(fake_library):
    first = jellyfin.get_client()
    second = jellyfin.get_client()

    assert first is second
    fake_library.assert_called_once()
    fake_library.return_value.authenticate.assert_called_once()


def test_get_client_sets_an_http_timeout(fake_library):
    client = jellyfin.get_client()

    assert client.config.data["http.timeout"] == jellyfin.HTTP_TIMEOUT_SECONDS


def test_unreachable_jellyfin_is_a_retryable_error_and_not_cached(fake_library):
    fake_library.return_value.authenticate.return_value = {"State": CONNECTION_STATE["Unavailable"]}

    with pytest.raises(jellyfin.JellyfinUnavailableError) as excinfo:
        jellyfin.get_client()

    assert isinstance(excinfo.value, TransientError)
    assert is_transient(excinfo.value)

    # Jellyfin comes back: the next call connects instead of replaying the failure
    fake_library.return_value.authenticate.return_value = {"State": CONNECTION_STATE["SignedIn"]}
    assert jellyfin.get_client() is fake_library.return_value


def test_reset_client_forces_a_reconnect(fake_library):
    jellyfin.get_client()
    jellyfin.reset_client()
    jellyfin.get_client()

    assert fake_library.call_count == 2
