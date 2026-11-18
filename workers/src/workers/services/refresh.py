"""Refresh messages: how the worker handles a Refresh of a Random Playlist.

A Refresh message is a JSON object carrying only the Random Playlist's name, `{"name": "..."}`.
The worker looks the definition up in its own config when it processes the message, so a config
change made while a Refresh waits for a retry takes effect once the worker restarts with it.
"""

import json

from workers.config import RandomPlaylist, WorkerConfig
from workers.errors import is_transient
from workers.logger import logger
from workers.results import ProcessingResult


def refresh_message(name: str) -> bytes:
    """Build the body of a Refresh message for a Random Playlist.

    Args:
        name (str): The Random Playlist's name.

    Returns:
        bytes: The message body, UTF-8 encoded JSON.
    """
    return json.dumps({"name": name}).encode()


def process_refresh_message(consumer_id: str, message_body: bytes) -> ProcessingResult:
    """Process a Refresh message from the Refresh queue.

    Args:
        consumer_id (str): The consumer that received the message, for logs.
        message_body (bytes): The raw message body.

    Returns:
        ProcessingResult: Success once the Refresh is done, or a failure. A name that is not in
            the config is a permanent failure, since retrying cannot bring it back.
    """
    try:
        name = _playlist_name(message_body)
    except ValueError as e:
        logger.error(f"Invalid Refresh message on {consumer_id}: {e}")
        return ProcessingResult.failed(f"Invalid Refresh message: {e}")

    playlist = _find_random_playlist(name)
    if playlist is None:
        reason = f"Random Playlist {name!r} is not in the config, so it cannot be refreshed"
        logger.error(reason)
        return ProcessingResult.failed(reason)

    try:
        refresh(playlist)
    except Exception as e:
        logger.error(f"Refresh of Random Playlist {name!r} failed: {e}")
        reason = f"Refresh of Random Playlist {name!r} failed: {type(e).__name__}: {e}"
        return (
            ProcessingResult.retry(reason) if is_transient(e) else ProcessingResult.failed(reason)
        )
    return ProcessingResult.success()


def refresh(playlist: RandomPlaylist) -> None:
    """Refresh a Random Playlist: replace its contents with a new draw from its Episode Pool.

    A placeholder until the Refresh itself is built (#38). Raise to fail the Refresh; the caller
    classifies the error with `is_transient()`, so keep `raise ... from exc` chains intact.

    Args:
        playlist (RandomPlaylist): The Random Playlist's definition, from the current config.
    """
    logger.warning(f"Refresh of Random Playlist {playlist.name!r} is not implemented yet")


def _playlist_name(message_body: bytes) -> str:
    """Read the Random Playlist name from a Refresh message body.

    Raises:
        ValueError: If the body is not a JSON object with a non-empty string `name`.
    """
    message = json.loads(message_body)
    if not isinstance(message, dict):
        raise ValueError(f"expected a JSON object, got {type(message).__name__}")
    name = message.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError(f"expected a Random Playlist name, got {name!r}")
    return name


def _find_random_playlist(name: str) -> RandomPlaylist | None:
    """The configured Random Playlist with exactly this name, if there is one."""
    for playlist in WorkerConfig().get_random_playlists():
        if playlist.name == name:
            return playlist
    return None
