import itertools
from collections.abc import Sequence
from dataclasses import dataclass, field

import pytest

from workers.clients.jellyfin_library import (
    Episode,
    JellyfinItemNotFoundError,
    Playlist,
    Series,
)


@dataclass
class FakePlaylist:
    """A playlist held by the fake: its ID, its name and the item IDs it holds, in order."""

    id: str
    name: str
    items: list[str] = field(default_factory=list)


class FakeJellyfin:
    """A stateful, in-memory stand-in for the parts of Jellyfin a Refresh uses.

    It holds series, their episodes (season, on disk or not, played or not) and Jellyhook's
    user's playlists, and implements `JellyfinLibrary` against them. Tests set it up, run a
    Refresh, and assert on what it holds afterwards.

    `fail_with` makes it raise like an unreachable or erroring Jellyfin would.
    """

    def __init__(self) -> None:
        """Start with an empty library and no playlists."""
        self._ids = itertools.count(1)
        self._series: dict[str, Series] = {}
        self._episodes: dict[str, list[Episode]] = {}
        self._series_of: dict[str, str] = {}
        self._playlists: dict[str, FakePlaylist] = {}
        self._failures: dict[str | None, BaseException] = {}

    # Setting up the library

    def add_series(self, name: str) -> str:
        """Add a series and return its ID."""
        series_id = f"series-{next(self._ids)}"
        self._series[series_id] = Series(id=series_id, name=name)
        self._episodes[series_id] = []
        return series_id

    def add_episodes(
        self,
        series_id: str,
        *,
        season: int | None,
        count: int,
        on_disk: bool = True,
        played: bool = False,
    ) -> list[str]:
        """Add `count` episodes to a season of a series and return their IDs, in order."""
        ids = []
        for _ in range(count):
            episode_id = f"episode-{next(self._ids)}"
            self._episodes[series_id].append(
                Episode(id=episode_id, season=season, on_disk=on_disk, played=played)
            )
            self._series_of[episode_id] = series_id
            ids.append(episode_id)
        return ids

    def add_playlist(self, name: str, items: Sequence[str] = ()) -> str:
        """Add a playlist of Jellyhook's user and return its ID."""
        playlist_id = f"playlist-{next(self._ids)}"
        self._playlists[playlist_id] = FakePlaylist(playlist_id, name, list(items))
        return playlist_id

    def fail_with(self, error: BaseException, *, operation: str | None = None) -> None:
        """Make one operation (by method name), or every operation, raise `error`."""
        self._failures[operation] = error

    def recover(self) -> None:
        """Stop raising the errors set with `fail_with`."""
        self._failures.clear()

    # Inspecting the outcome

    def playlists_named(self, name: str) -> list[FakePlaylist]:
        """Every playlist with exactly this name."""
        return [playlist for playlist in self._playlists.values() if playlist.name == name]

    def playlist_named(self, name: str) -> FakePlaylist:
        """The one playlist with exactly this name; fails the test if there is not exactly one."""
        playlists = self.playlists_named(name)
        assert len(playlists) == 1, f"expected one playlist named {name!r}, found {playlists}"
        return playlists[0]

    def playlist(self, playlist_id: str) -> FakePlaylist:
        """The playlist with this ID."""
        return self._playlists[playlist_id]

    def series_of(self, episode_id: str) -> str:
        """The ID of the series an episode belongs to."""
        return self._series_of[episode_id]

    def episode(self, episode_id: str) -> Episode:
        """The episode with this ID."""
        series_id = self._series_of[episode_id]
        return next(e for e in self._episodes[series_id] if e.id == episode_id)

    # JellyfinLibrary

    def find_series(self, title: str) -> list[Series]:
        self._maybe_fail("find_series")
        return [series for series in self._series.values() if series.name == title]

    def list_episodes(self, series_id: str) -> list[Episode]:
        self._maybe_fail("list_episodes")
        if series_id not in self._episodes:
            raise JellyfinItemNotFoundError(f"Jellyfin has no series with ID {series_id!r}")
        return list(self._episodes[series_id])

    def find_playlists(self, name: str) -> list[Playlist]:
        self._maybe_fail("find_playlists")
        return [Playlist(id=p.id, name=p.name) for p in self.playlists_named(name)]

    def create_playlist(self, name: str) -> str:
        self._maybe_fail("create_playlist")
        return self.add_playlist(name)

    def replace_playlist_items(self, playlist_id: str, item_ids: Sequence[str]) -> None:
        self._maybe_fail("replace_playlist_items")
        if playlist_id not in self._playlists:
            raise JellyfinItemNotFoundError(f"Jellyfin has no playlist with ID {playlist_id!r}")
        unknown = [item_id for item_id in item_ids if item_id not in self._series_of]
        assert not unknown, f"items that are not episodes in the library: {unknown}"
        self._playlists[playlist_id].items = list(item_ids)

    def _maybe_fail(self, operation: str) -> None:
        error = self._failures.get(operation) or self._failures.get(None)
        if error is not None:
            raise error


@pytest.fixture
def jellyfin() -> FakeJellyfin:
    """An empty in-memory Jellyfin."""
    return FakeJellyfin()
