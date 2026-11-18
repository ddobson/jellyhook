"""Refresh messages: how the worker handles a Refresh of a Random Playlist.

A Refresh message is a JSON object carrying only the Random Playlist's name, `{"name": "..."}`.
The worker looks the definition up in its own config when it processes the message, so a config
change made while a Refresh waits for a retry takes effect once the worker restarts with it.
"""

import json
import random

from workers.clients.jellyfin_library import Episode, JellyfinApiLibrary, JellyfinLibrary
from workers.config import RandomPlaylist, SeriesById, SeriesRef, WorkerConfig
from workers.errors import WebhookWorkerError, is_transient
from workers.logger import logger
from workers.results import ProcessingResult


class RefreshError(WebhookWorkerError):
    """A Refresh cannot be done as configured. Permanent: it needs a config or library change."""


def refresh_message(name: str) -> bytes:
    """Build the body of a Refresh message for a Random Playlist.

    Args:
        name (str): The Random Playlist's name.

    Returns:
        bytes: The message body, UTF-8 encoded JSON.
    """
    return json.dumps({"name": name}).encode()


def process_refresh_message(
    consumer_id: str,
    message_body: bytes,
    *,
    jellyfin: JellyfinLibrary | None = None,
    rng: random.Random | None = None,
) -> ProcessingResult:
    """Process a Refresh message from the Refresh queue.

    Args:
        consumer_id (str): The consumer that received the message, for logs.
        message_body (bytes): The raw message body.
        jellyfin (JellyfinLibrary | None): The Jellyfin to refresh in. Defaults to the real one.
        rng (random.Random | None): The source of randomness for the draw. Defaults to a fresh,
            unseeded one.

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
        refresh(
            playlist,
            jellyfin if jellyfin is not None else JellyfinApiLibrary(),
            rng if rng is not None else random.Random(),
        )
    except Exception as e:
        logger.error(f"Refresh of Random Playlist {name!r} failed: {e}")
        reason = f"Refresh of Random Playlist {name!r} failed: {type(e).__name__}: {e}"
        return (
            ProcessingResult.retry(reason) if is_transient(e) else ProcessingResult.failed(reason)
        )
    return ProcessingResult.success()


def refresh(playlist: RandomPlaylist, jellyfin: JellyfinLibrary, rng: random.Random) -> None:
    """Refresh a Random Playlist: replace its contents with a new draw from its Episode Pool.

    Raise to fail the Refresh; the caller classifies the error with `is_transient()`, so keep
    `raise ... from exc` chains intact.

    Args:
        playlist (RandomPlaylist): The Random Playlist's definition, from the current config.
        jellyfin (JellyfinLibrary): The Jellyfin to refresh it in.
        rng (random.Random): The source of randomness for the draw.
    """
    name = playlist.name
    logger.info(f"Refresh of Random Playlist {name!r} started")
    pool = _episode_pool(playlist, jellyfin)
    if not pool:
        logger.warning(
            f"Refresh of Random Playlist {name!r} skipped: its Episode Pool is empty, so the "
            "playlist is left as it is"
        )
        return
    if len(pool) < playlist.size:
        logger.warning(
            f"Random Playlist {name!r} asks for {playlist.size} episodes but its Episode Pool "
            f"has only {len(pool)}; the playlist gets all {len(pool)}, shuffled"
        )
    drawn = rng.sample(pool, min(playlist.size, len(pool)))
    playlist_id = _playlist_id(name, jellyfin)
    jellyfin.replace_playlist_items(playlist_id, [episode.id for episode in drawn])
    logger.info(
        f"Refresh of Random Playlist {name!r} completed: playlist {playlist_id} now holds "
        f"{len(drawn)} episodes"
    )


def _playlist_id(name: str, jellyfin: JellyfinLibrary) -> str:
    """The ID of Jellyhook's user's playlist named `name`, created if there is none.

    Raises:
        RefreshError: If the user has several playlists with this name, since replacing the
            wrong one's contents would be data loss.
    """
    matches = jellyfin.find_playlists(name)
    if len(matches) > 1:
        ids = ", ".join(playlist.id for playlist in matches)
        raise RefreshError(
            f"{len(matches)} playlists are named {name!r} ({ids}); rename or delete all but one"
        )
    if matches:
        return matches[0].id
    playlist_id = jellyfin.create_playlist(name)
    logger.info(f"Created playlist {playlist_id} for Random Playlist {name!r}")
    return playlist_id


def _episode_pool(playlist: RandomPlaylist, jellyfin: JellyfinLibrary) -> list[Episode]:
    """The episodes a Refresh of `playlist` draws from, each once, in library order.

    The union across the playlist's series of the episodes that are on disk, leaving out
    Specials (season 0) unless `include_specials` is set, and episodes Jellyhook's user has
    played when `unplayed_only` is set.
    """
    pool: dict[str, Episode] = {}
    for series_id in dict.fromkeys(_series_id(ref, jellyfin) for ref in playlist.series):
        for episode in jellyfin.list_episodes(series_id):
            if not episode.on_disk:
                continue
            if episode.season == 0 and not playlist.include_specials:
                continue
            if episode.played and playlist.unplayed_only:
                continue
            pool.setdefault(episode.id, episode)
    return list(pool.values())


def _series_id(ref: SeriesRef, jellyfin: JellyfinLibrary) -> str:
    """The Jellyfin ID of a configured series. An `id` is used as it is, without a lookup."""
    if isinstance(ref, SeriesById):
        return ref.id
    matches = jellyfin.find_series(ref.title)
    if not matches:
        raise RefreshError(f"Jellyfin has no series titled {ref.title!r}")
    if len(matches) > 1:
        ids = ", ".join(series.id for series in matches)
        raise RefreshError(
            f"{len(matches)} series are titled {ref.title!r} ({ids}); name the one you mean "
            "with {id: <series id>}"
        )
    return matches[0].id


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
