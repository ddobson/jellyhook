"""Lazily created, shared Jellyfin API client.

The client used to authenticate when this module was imported. That put a network call in the
import path of every service: tests needed a reachable Jellyfin (or a stub), and a Jellyfin
outage while the worker started crashed it before it could consume anything. Now nothing talks
to Jellyfin until a job actually needs it, and an outage is just a retryable job failure.
"""

import threading

from jellyfin_apiclient_python import JellyfinClient
from jellyfin_apiclient_python.connection_manager import CONNECTION_STATE

from workers import config
from workers.errors import TransientError
from workers.logger import logger

# Seconds to wait on any one Jellyfin HTTP call before giving up (the library's default is
# generous). A hung Jellyfin then fails the job, and the retry queue tries again later.
HTTP_TIMEOUT_SECONDS = 30

_client: JellyfinClient | None = None
_client_lock = threading.Lock()


class JellyfinUnavailableError(TransientError):
    """Jellyfin could not be reached or refused our credentials just now."""


def _connect() -> JellyfinClient:
    """Build a client and sign it in.

    Raises:
        JellyfinUnavailableError: If the server did not sign us in.
    """
    client = JellyfinClient()
    client.config.app(
        config.APP_NAME, config.APP_VERSION, config.APP_DEVICE_NAME, config.APP_DEVICE_ID
    )
    client.config.data["auth.ssl"] = True
    client.config.data["http.timeout"] = HTTP_TIMEOUT_SECONDS
    state = client.authenticate(
        {
            "Servers": [
                {
                    "AccessToken": config.JELLYFIN_API_KEY,
                    "address": f"{config.JELLYFIN_HOST}:{config.JELLYFIN_PORT}",
                    "DateLastAccessed": 0,
                    "UserId": config.JELLYFIN_USER_ID,
                }
            ]
        },
        discover=True,
    )
    # authenticate() reports failure through the returned state rather than by raising
    if state.get("State") != CONNECTION_STATE["SignedIn"]:
        err_msg = f"Jellyfin did not sign us in (state: {state.get('State')})"
        raise JellyfinUnavailableError(err_msg)
    return client


def get_client() -> JellyfinClient:
    """Return the shared client, connecting on first use.

    A failed attempt is not cached, so the next job simply tries again.

    Raises:
        JellyfinUnavailableError: If Jellyfin is unreachable. Retryable.
    """
    global _client  # noqa: PLW0603 - a process-wide lazy singleton
    with _client_lock:
        if _client is None:
            logger.info(f"Connecting to Jellyfin at {config.JELLYFIN_HOST}:{config.JELLYFIN_PORT}")
            _client = _connect()
        return _client


def reset_client() -> None:
    """Forget the shared client so the next `get_client()` reconnects (used by tests)."""
    global _client  # noqa: PLW0603
    with _client_lock:
        _client = None
