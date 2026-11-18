"""The production JellyfinLibrary, against a stand-in for the Jellyfin HTTP API.

`JellyfinHttp` answers the requests the adapter sends the way the Jellyfin server does, after
jellyfin_apiclient_python has turned the response into a return value or an exception: a JSON
body is returned, an empty body (204) and a swallowed 500 both come back as None, and other error
statuses raise its HTTPException.
"""

from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest
from jellyfin_apiclient_python.exceptions import HTTPException as JellyfinHTTPException

from workers.clients import jellyfin_library
from workers.clients.jellyfin_library import (
    Episode,
    JellyfinApiLibrary,
    JellyfinItemNotFoundError,
    Playlist,
    Series,
)
from workers.errors import is_transient

USER_ID = "user-1"


class JellyfinHttp:
    """Answers `client.http.request(...)` like a Jellyfin 10.9+ server, for the adapter's calls."""

    def __init__(self) -> None:
        """Start with an empty library and no playlists."""
        self.items: list[dict[str, Any]] = []
        self.episodes: dict[str, list[dict[str, Any]]] = {}
        self.playlist_items: dict[str, list[str]] = {}
        self.ignore_updates = False
        self.answer_none = False
        self.requests: list[dict[str, Any]] = []

    def request(self, data: dict[str, Any]) -> Any:
        self.requests.append(data)
        if self.answer_none:
            return None
        method, handler = data["type"], data["handler"]
        params = data.get("params") or {}
        body = data.get("json") or {}
        parts = handler.split("/")
        if method == "GET" and handler == "Items":
            assert params["userId"] == USER_ID
            wanted = params["includeItemTypes"]
            return {"Items": [item for item in self.items if item["Type"] == wanted]}
        if method == "GET" and parts[0] == "Shows" and parts[2] == "Episodes":
            assert params["userId"] == USER_ID
            if parts[1] not in self.episodes:
                raise JellyfinHTTPException(404, "Not Found")
            return {"Items": self.episodes[parts[1]]}
        if method == "POST" and handler == "Playlists":
            assert body["UserId"] == USER_ID
            playlist_id = f"playlist{len(self.playlist_items) + 1}"
            self.playlist_items[playlist_id] = list(body["Ids"])
            self.items.append({"Id": playlist_id, "Name": body["Name"], "Type": "Playlist"})
            return {"Id": playlist_id}
        if method == "POST" and parts[0] == "Playlists" and len(parts) == 2:
            if parts[1] not in self.playlist_items:
                raise JellyfinHTTPException(404, "Not Found")
            if not self.ignore_updates:
                self.playlist_items[parts[1]] = list(body["Ids"])
            return None  # 204 No Content
        if method == "GET" and parts[0] == "Playlists" and parts[2] == "Items":
            return {
                "Items": [
                    {"Id": item_id, "PlaylistItemId": f"entry-{i}"}
                    for i, item_id in enumerate(self.playlist_items[parts[1]])
                ]
            }
        raise AssertionError(f"unexpected request {method} {handler}")


@pytest.fixture
def http():
    """The stand-in Jellyfin, behind a patched `get_client()`."""
    server = JellyfinHttp()
    client = SimpleNamespace(http=SimpleNamespace(request=server.request))
    with (
        mock.patch.object(jellyfin_library, "get_client", return_value=client),
        mock.patch.object(jellyfin_library.config, "JELLYFIN_USER_ID", USER_ID),
    ):
        yield server


def test_find_series_returns_only_exact_title_matches(http):
    http.items = [
        {"Id": "a", "Name": "The Office", "Type": "Series"},
        {"Id": "b", "Name": "The Office", "Type": "Series"},
        {"Id": "c", "Name": "the office", "Type": "Series"},
        {"Id": "d", "Name": "The Office Christmas Special", "Type": "Series"},
        {"Id": "e", "Name": "The Office", "Type": "Playlist"},
    ]

    assert JellyfinApiLibrary().find_series("The Office") == [
        Series("a", "The Office"),
        Series("b", "The Office"),
    ]


def test_list_episodes_reports_season_presence_on_disk_and_played_state(http):
    http.episodes["s1"] = [
        {
            "Id": "e1",
            "ParentIndexNumber": 1,
            "LocationType": "FileSystem",
            "UserData": {"Played": True},
        },
        {
            "Id": "e2",
            "ParentIndexNumber": 0,
            "LocationType": "FileSystem",
            "UserData": {"Played": False},
        },
        {"Id": "e3", "ParentIndexNumber": 2, "LocationType": "Virtual", "UserData": {}},
        {"Id": "e4", "LocationType": "Offline"},
    ]

    assert JellyfinApiLibrary().list_episodes("s1") == [
        Episode("e1", season=1, on_disk=True, played=True),
        Episode("e2", season=0, on_disk=True, played=False),
        Episode("e3", season=2, on_disk=False, played=False),
        Episode("e4", season=None, on_disk=False, played=False),
    ]


def test_list_episodes_of_an_unknown_series_is_a_permanent_not_found_error(http):
    with pytest.raises(JellyfinItemNotFoundError, match="'nope'") as raised:
        JellyfinApiLibrary().list_episodes("nope")

    assert not is_transient(raised.value)


def test_find_playlists_returns_only_exact_name_matches(http):
    http.items = [
        {"Id": "p1", "Name": "Shuffle", "Type": "Playlist"},
        {"Id": "p2", "Name": "Shuffle (old)", "Type": "Playlist"},
        {"Id": "s1", "Name": "Shuffle", "Type": "Series"},
    ]

    assert JellyfinApiLibrary().find_playlists("Shuffle") == [Playlist("p1", "Shuffle")]


def test_create_playlist_creates_an_empty_video_playlist_for_jellyhooks_user(http):
    library = JellyfinApiLibrary()

    playlist_id = library.create_playlist("Shuffle")

    assert library.find_playlists("Shuffle") == [Playlist(playlist_id, "Shuffle")]
    assert http.playlist_items[playlist_id] == []
    (create,) = [r for r in http.requests if r["handler"] == "Playlists"]
    assert create["json"]["MediaType"] == "Video"


def test_replace_playlist_items_updates_the_playlist_in_place(http):
    library = JellyfinApiLibrary()
    playlist_id = library.create_playlist("Shuffle")
    library.replace_playlist_items(playlist_id, ["old1", "old2"])

    library.replace_playlist_items(playlist_id, ["e3", "e1", "e2"])

    assert http.playlist_items == {playlist_id: ["e3", "e1", "e2"]}
    assert library.find_playlists("Shuffle") == [Playlist(playlist_id, "Shuffle")]


def test_replace_playlist_items_accepts_ids_written_another_way(http):
    """Jellyfin item IDs are GUIDs, with or without dashes and in either case."""
    library = JellyfinApiLibrary()
    playlist_id = library.create_playlist("Shuffle")
    http.ignore_updates = True
    http.playlist_items[playlist_id] = ["0123abcd00000000000000000000beef"]

    library.replace_playlist_items(playlist_id, ["0123ABCD-0000-0000-0000-00000000BEEF"])


def test_a_replace_that_did_not_take_effect_is_a_retryable_error(http):
    """The update answers 204, and so does a 500 the client swallows, so the result is checked."""
    library = JellyfinApiLibrary()
    playlist_id = library.create_playlist("Shuffle")
    http.ignore_updates = True

    with pytest.raises(Exception, match=playlist_id) as raised:
        library.replace_playlist_items(playlist_id, ["e1"])

    assert is_transient(raised.value)


def test_replacing_the_items_of_an_unknown_playlist_is_a_permanent_error(http):
    with pytest.raises(JellyfinItemNotFoundError, match="'nope'") as raised:
        JellyfinApiLibrary().replace_playlist_items("nope", ["e1"])

    assert not is_transient(raised.value)


@pytest.mark.parametrize(
    "call",
    [
        lambda library: library.find_series("The Office"),
        lambda library: library.list_episodes("s1"),
        lambda library: library.find_playlists("Shuffle"),
        lambda library: library.create_playlist("Shuffle"),
    ],
)
def test_a_response_with_no_body_where_one_is_needed_is_a_retryable_error(http, call):
    """jellyfin_apiclient_python returns None for a 500 instead of raising."""
    http.answer_none = True

    with pytest.raises(Exception) as raised:
        call(JellyfinApiLibrary())

    assert is_transient(raised.value)


def test_jellyfin_being_unreachable_is_a_retryable_error(http):
    http.request = mock.Mock(side_effect=JellyfinHTTPException("ServerUnreachable", "refused"))
    client = SimpleNamespace(http=http)

    with (
        mock.patch.object(jellyfin_library, "get_client", return_value=client),
        pytest.raises(JellyfinHTTPException) as raised,
    ):
        JellyfinApiLibrary().find_series("The Office")

    assert is_transient(raised.value)


def test_a_missing_jellyfin_user_id_is_a_permanent_error(http):
    with (
        mock.patch.object(jellyfin_library.config, "JELLYFIN_USER_ID", ""),
        pytest.raises(Exception, match="JELLYFIN_USER_ID") as raised,
    ):
        JellyfinApiLibrary().find_series("The Office")

    assert not is_transient(raised.value)
