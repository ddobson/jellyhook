"""The parts of Jellyfin a Refresh needs, behind a small interface.

A Refresh depends on `JellyfinLibrary`, not on the Jellyfin client, so it can run against a
stateful in-memory fake in tests. `JellyfinApiLibrary` is the production implementation; it
wraps the shared client from `get_client()`, fetched at the point of use.

The interface reports facts (an episode's season, whether it is on disk, whether Jellyhook's user
has played it). The rules for building an Episode Pool from those facts belong to the Refresh.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from jellyfin_apiclient_python.exceptions import HTTPException as JellyfinHTTPException

from workers import config
from workers.clients.jellyfin import get_client
from workers.errors import TransientError, WebhookWorkerError


class JellyfinLibraryError(WebhookWorkerError):
    """Jellyfin answered in a way Jellyhook cannot use, or cannot be asked as configured."""


class JellyfinItemNotFoundError(JellyfinLibraryError):
    """Jellyfin has no item with the requested ID. Permanent: a retry will not create it."""


class JellyfinNoResponseError(TransientError):
    """Jellyfin returned no body where one was needed.

    jellyfin_apiclient_python returns None, rather than raising, when the server answers 500, so
    this is retried like any other server error.
    """


class PlaylistNotReplacedError(TransientError):
    """Jellyfin accepted a playlist update, but the playlist does not hold the new items.

    Retried, since a 500 the client swallowed looks just like a successful update.
    """


@dataclass(frozen=True)
class Series:
    """A TV series in the Jellyfin library.

    Attributes:
        id (str): The series' Jellyfin item ID.
        name (str): The series title.
    """

    id: str
    name: str


@dataclass(frozen=True)
class Episode:
    """An episode of a series, as Jellyhook's user sees it.

    Attributes:
        id (str): The episode's Jellyfin item ID.
        season (int | None): The season number; 0 is Specials. None when Jellyfin has none.
        on_disk (bool): Whether the episode has a file. Missing and virtual episodes do not.
        played (bool): Whether Jellyhook's user (`JELLYFIN_USER_ID`) has played it.
    """

    id: str
    season: int | None
    on_disk: bool
    played: bool


@dataclass(frozen=True)
class Playlist:
    """A playlist owned by Jellyhook's user.

    Attributes:
        id (str): The playlist's Jellyfin item ID.
        name (str): The playlist name.
    """

    id: str
    name: str


class JellyfinLibrary(Protocol):
    """The Jellyfin operations a Refresh needs, and nothing more.

    Implementations raise the errors the Jellyfin client raises (or ones chained `from` them), so
    `is_transient()` can tell an unreachable Jellyfin from a request that will never succeed.
    """

    def find_series(self, title: str) -> list[Series]:
        """Every series whose title is exactly `title`."""
        ...

    def list_episodes(self, series_id: str) -> list[Episode]:
        """Every episode of a series, specials and missing ones included.

        Raises:
            JellyfinItemNotFoundError: If there is no series with this ID.
        """
        ...

    def find_playlists(self, name: str) -> list[Playlist]:
        """Every playlist of Jellyhook's user whose name is exactly `name`."""
        ...

    def create_playlist(self, name: str) -> str:
        """Create an empty playlist owned by Jellyhook's user, and return its ID."""
        ...

    def replace_playlist_items(self, playlist_id: str, item_ids: Sequence[str]) -> None:
        """Make the playlist hold exactly `item_ids`, in this order, keeping its ID.

        Raises:
            JellyfinItemNotFoundError: If there is no playlist with this ID.
        """
        ...


# Jellyfin's LocationType for an item it knows about but has no playable file for: "Virtual" is
# a missing or not-yet-aired episode, "Offline" a file on storage that is not reachable.
_NOT_ON_DISK = frozenset({"Virtual", "Offline"})

# What Jellyfin answers for a series ID it does not know: 404, or 400 when it is not a GUID
_NOT_FOUND_STATUSES = frozenset({400, 404})


class JellyfinApiLibrary:
    """`JellyfinLibrary` on the real Jellyfin, through the shared client.

    The client comes from `get_client()` on every call, so building this does not connect. All
    lookups are made as `JELLYFIN_USER_ID`, which makes "played" and playlist ownership that
    user's. Endpoints (Jellyfin 10.9 or later):

    - series and playlists: `GET Items` filtered by type, matched on the exact name here, since
      Jellyfin's `SearchTerm` is a fuzzy search;
    - episodes: `GET Shows/{seriesId}/Episodes`;
    - create: `POST Playlists`;
    - replace: `POST Playlists/{playlistId}` with `Ids` (UpdatePlaylist), which swaps the whole
      contents in one call and keeps the ID, checked afterwards with
      `GET Playlists/{playlistId}/Items`.
    """

    def find_series(self, title: str) -> list[Series]:
        """Every series whose title is exactly `title`."""
        return [
            Series(id=item["Id"], name=item["Name"])
            for item in self._items_of_type("Series")
            if item.get("Name") == title
        ]

    def list_episodes(self, series_id: str) -> list[Episode]:
        """Every episode of a series, specials and missing ones included.

        Raises:
            JellyfinItemNotFoundError: If there is no series with this ID.
        """
        try:
            items = self._get_items(
                f"Shows/{series_id}/Episodes",
                {"userId": self._user_id(), "enableImages": "false", "enableUserData": "true"},
            )
        except JellyfinHTTPException as exc:
            if getattr(exc, "status", None) in _NOT_FOUND_STATUSES:
                raise JellyfinItemNotFoundError(
                    f"Jellyfin has no series with ID {series_id!r}"
                ) from exc
            raise
        return [
            Episode(
                id=item["Id"],
                season=item.get("ParentIndexNumber"),
                on_disk=item.get("LocationType") not in _NOT_ON_DISK,
                played=bool((item.get("UserData") or {}).get("Played")),
            )
            for item in items
        ]

    def find_playlists(self, name: str) -> list[Playlist]:
        """Every playlist of Jellyhook's user whose name is exactly `name`."""
        return [
            Playlist(id=item["Id"], name=item["Name"])
            for item in self._items_of_type("Playlist")
            if item.get("Name") == name
        ]

    def create_playlist(self, name: str) -> str:
        """Create an empty video playlist owned by Jellyhook's user, and return its ID."""
        response = self._request(
            "POST",
            "Playlists",
            json={"Name": name, "Ids": [], "UserId": self._user_id(), "MediaType": "Video"},
        )
        if not isinstance(response, dict) or not response.get("Id"):
            raise JellyfinNoResponseError(
                f"Jellyfin did not return the ID of the new playlist {name!r} (got {response!r})"
            )
        return str(response["Id"])

    def replace_playlist_items(self, playlist_id: str, item_ids: Sequence[str]) -> None:
        """Make the playlist hold exactly `item_ids`, in this order, keeping its ID.

        Raises:
            JellyfinItemNotFoundError: If there is no playlist with this ID.
            PlaylistNotReplacedError: If the playlist does not hold `item_ids` afterwards.
        """
        try:
            self._request("POST", f"Playlists/{playlist_id}", json={"Ids": list(item_ids)})
        except JellyfinHTTPException as exc:
            if getattr(exc, "status", None) == 404:
                raise JellyfinItemNotFoundError(
                    f"Jellyfin has no playlist with ID {playlist_id!r} (or is older than 10.9, "
                    "which cannot update a playlist's items)"
                ) from exc
            raise
        held = self._get_items(
            f"Playlists/{playlist_id}/Items",
            {"userId": self._user_id(), "enableImages": "false", "enableUserData": "false"},
        )
        if [_normalize_id(item.get("Id")) for item in held] != [
            _normalize_id(item_id) for item_id in item_ids
        ]:
            raise PlaylistNotReplacedError(
                f"Playlist {playlist_id} holds {len(held)} items after the update, not the "
                f"{len(item_ids)} drawn ones"
            )

    def _items_of_type(self, item_type: str) -> list[dict[str, Any]]:
        """Every item of one type that Jellyhook's user can see, across all libraries."""
        return self._get_items(
            "Items",
            {
                "userId": self._user_id(),
                "includeItemTypes": item_type,
                "recursive": "true",
                "enableImages": "false",
                "enableUserData": "false",
            },
        )

    def _get_items(self, handler: str, params: dict[str, str]) -> list[dict[str, Any]]:
        """The `Items` list of a GET response.

        Raises:
            JellyfinNoResponseError: If Jellyfin returned no body (it does for a 500).
            JellyfinLibraryError: If the body has no `Items` list.
        """
        response = self._request("GET", handler, params=params)
        if response is None:
            raise JellyfinNoResponseError(f"Jellyfin returned nothing for GET {handler}")
        items = response.get("Items") if isinstance(response, dict) else None
        if not isinstance(items, list):
            raise JellyfinLibraryError(f"Jellyfin returned no Items list for GET {handler}")
        return [item for item in items if isinstance(item, dict)]

    @staticmethod
    def _request(
        method: str,
        handler: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        """Send one request through the shared client, connecting first if needed."""
        request: dict[str, Any] = {"type": method, "handler": handler, "params": params or {}}
        if json is not None:
            request["json"] = json
        return get_client().http.request(request)

    @staticmethod
    def _user_id() -> str:
        """Jellyhook's Jellyfin user.

        Raises:
            JellyfinLibraryError: If `JELLYFIN_USER_ID` is not set.
        """
        if not config.JELLYFIN_USER_ID:
            raise JellyfinLibraryError("JELLYFIN_USER_ID is not set, so there is no user to ask as")
        return config.JELLYFIN_USER_ID


def _normalize_id(item_id: Any) -> str:
    """A Jellyfin item ID (a GUID, with or without dashes) in lowercase without dashes."""
    return str(item_id or "").replace("-", "").lower()
