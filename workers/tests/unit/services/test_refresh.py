"""Refresh tests, entering through the Refresh message handling (Seam A).

Each test loads a config, sets up an in-memory Jellyfin (the `jellyfin` fixture), processes a
Refresh message against it, and asserts on what Jellyfin holds afterwards.
"""

import random
from typing import Any
from unittest import mock

import pytest
import requests
import yaml
from jellyfin_apiclient_python.exceptions import HTTPException as JellyfinHTTPException

from workers.clients.jellyfin import JellyfinUnavailableError
from workers.config import WorkerConfig
from workers.logger import logger
from workers.results import Outcome, ProcessingResult
from workers.services.refresh import process_refresh_message, refresh_message

NAME = "Bob's Burgers Shuffle"


@pytest.fixture
def configure(tmp_path):
    """Load a config with the given Random Playlists into a fresh WorkerConfig singleton."""
    with mock.patch.dict("workers.utils.SingletonMeta._instances", {}, clear=True):

        def load(*playlists: dict[str, Any]) -> None:
            path = tmp_path / "jellyhook.yaml"
            path.write_text(yaml.safe_dump({"random_playlists": list(playlists)}))
            WorkerConfig.load(str(path))

        yield load


@pytest.fixture
def logs():
    """The (level, message) of every log line written during the test."""
    lines: list[tuple[str, str]] = []
    sink = logger.add(lambda m: lines.append((m.record["level"].name, m.record["message"])))
    yield lines
    logger.remove(sink)


def random_playlist(**settings: Any) -> dict[str, Any]:
    """A Random Playlist config entry, with defaults for the settings a test does not care about."""
    return {
        "name": NAME,
        "series": ["Bob's Burgers"],
        "size": 25,
        "schedule": "0 1 * * 0",
        **settings,
    }


def run_refresh(jellyfin, name: str = NAME, seed: int = 1) -> ProcessingResult:
    """Process a Refresh message for `name` against the fake Jellyfin."""
    return process_refresh_message(
        "refresh", refresh_message(name), jellyfin=jellyfin, rng=random.Random(seed)
    )


def test_a_refresh_creates_the_playlist_with_size_distinct_episodes_of_its_series(
    jellyfin, configure
):
    """Stories 8, 10 and 21: the first Refresh creates the playlist and fills it."""
    configure(random_playlist(size=25))
    bobs = jellyfin.add_series("Bob's Burgers")
    jellyfin.add_episodes(bobs, season=1, count=40)
    futurama = jellyfin.add_series("Futurama")
    jellyfin.add_episodes(futurama, season=1, count=40)

    result = run_refresh(jellyfin)

    assert result.outcome is Outcome.SUCCESS
    items = jellyfin.playlist_named(NAME).items
    assert len(items) == 25
    assert len(set(items)) == 25
    assert {jellyfin.series_of(item) for item in items} == {bobs}


def test_a_series_named_by_id_is_drawn_from_without_a_title_lookup(jellyfin, configure):
    """Story 4: an ID picks one of two series that share a title."""
    us_office = jellyfin.add_series("The Office")
    jellyfin.add_episodes(us_office, season=1, count=10)
    uk_office = jellyfin.add_series("The Office")
    jellyfin.add_episodes(uk_office, season=1, count=10)
    configure(random_playlist(series=[{"id": uk_office}], size=5))

    result = run_refresh(jellyfin)

    assert result.outcome is Outcome.SUCCESS
    items = jellyfin.playlist_named(NAME).items
    assert len(items) == 5
    assert {jellyfin.series_of(item) for item in items} == {uk_office}


def test_specials_are_left_out_of_the_episode_pool_by_default(jellyfin, configure):
    """Story 11: season 0 is not drawn unless the playlist asks for it."""
    bobs = jellyfin.add_series("Bob's Burgers")
    regular = jellyfin.add_episodes(bobs, season=1, count=5)
    jellyfin.add_episodes(bobs, season=0, count=5)
    configure(random_playlist(size=10))

    run_refresh(jellyfin)

    assert sorted(jellyfin.playlist_named(NAME).items) == sorted(regular)


def test_include_specials_adds_season_0_to_the_episode_pool(jellyfin, configure):
    """Story 12."""
    bobs = jellyfin.add_series("Bob's Burgers")
    regular = jellyfin.add_episodes(bobs, season=1, count=5)
    specials = jellyfin.add_episodes(bobs, season=0, count=5)
    configure(random_playlist(size=10, include_specials=True))

    run_refresh(jellyfin)

    assert sorted(jellyfin.playlist_named(NAME).items) == sorted(regular + specials)


def test_missing_and_virtual_episodes_are_never_drawn(jellyfin, configure):
    """Story 13: every playlist entry plays."""
    bobs = jellyfin.add_series("Bob's Burgers")
    on_disk = jellyfin.add_episodes(bobs, season=1, count=5)
    jellyfin.add_episodes(bobs, season=2, count=5, on_disk=False)
    jellyfin.add_episodes(bobs, season=0, count=2, on_disk=False)
    configure(random_playlist(size=10, include_specials=True))

    run_refresh(jellyfin)

    assert sorted(jellyfin.playlist_named(NAME).items) == sorted(on_disk)


def test_unplayed_only_draws_only_episodes_jellyhooks_user_has_not_played(jellyfin, configure):
    """Stories 14 and 15: the played state is the one Jellyfin reports for Jellyhook's user."""
    bobs = jellyfin.add_series("Bob's Burgers")
    unplayed = jellyfin.add_episodes(bobs, season=1, count=5)
    jellyfin.add_episodes(bobs, season=2, count=5, played=True)
    configure(random_playlist(size=10, unplayed_only=True))

    run_refresh(jellyfin)

    assert sorted(jellyfin.playlist_named(NAME).items) == sorted(unplayed)


def test_played_episodes_are_drawn_unless_unplayed_only_is_set(jellyfin, configure):
    bobs = jellyfin.add_series("Bob's Burgers")
    unplayed = jellyfin.add_episodes(bobs, season=1, count=5)
    played = jellyfin.add_episodes(bobs, season=2, count=5, played=True)
    configure(random_playlist(size=10))

    run_refresh(jellyfin)

    assert sorted(jellyfin.playlist_named(NAME).items) == sorted(unplayed + played)


def test_the_episode_pool_is_the_union_of_every_listed_series(jellyfin, configure):
    """Story 7: a mixed playlist, with each episode at most once even if a series is listed twice."""
    bobs = jellyfin.add_series("Bob's Burgers")
    bobs_episodes = jellyfin.add_episodes(bobs, season=1, count=3)
    futurama = jellyfin.add_series("Futurama")
    futurama_episodes = jellyfin.add_episodes(futurama, season=1, count=3)
    jellyfin.add_episodes(jellyfin.add_series("The Simpsons"), season=1, count=3)
    configure(random_playlist(series=["Bob's Burgers", {"id": futurama}, {"id": bobs}], size=10))

    run_refresh(jellyfin)

    items = jellyfin.playlist_named(NAME).items
    assert sorted(items) == sorted(bobs_episodes + futurama_episodes)


def test_the_playlist_holds_the_draw_in_random_order(jellyfin, configure):
    """Story 9: the playlist plays like a shuffle, not in episode order."""
    bobs = jellyfin.add_series("Bob's Burgers")
    episodes = jellyfin.add_episodes(bobs, season=1, count=20)
    configure(random_playlist(size=20))

    run_refresh(jellyfin)

    items = jellyfin.playlist_named(NAME).items
    assert sorted(items) == sorted(episodes)
    assert items != episodes


def test_each_refresh_is_an_independent_draw(jellyfin, configure):
    """Story 18: a new draw each time, with no history to manage."""
    jellyfin.add_episodes(jellyfin.add_series("Bob's Burgers"), season=1, count=100)
    configure(random_playlist(size=10))

    run_refresh(jellyfin, seed=1)
    first = jellyfin.playlist_named(NAME).items
    run_refresh(jellyfin, seed=2)
    second = jellyfin.playlist_named(NAME).items

    assert first != second


def test_a_pool_smaller_than_size_fills_the_playlist_with_all_of_it_and_warns(
    jellyfin, configure, logs
):
    """Story 16: a short series still gets a playlist, every episode in shuffled order."""
    episodes = jellyfin.add_episodes(jellyfin.add_series("Bob's Burgers"), season=1, count=10)
    configure(random_playlist(size=25))

    result = run_refresh(jellyfin)

    assert result.outcome is Outcome.SUCCESS
    items = jellyfin.playlist_named(NAME).items
    assert sorted(items) == sorted(episodes)
    assert items != episodes
    warnings = [message for level, message in logs if level == "WARNING"]
    assert len(warnings) == 1
    assert NAME in warnings[0]
    assert "25" in warnings[0]
    assert "10" in warnings[0]


def test_an_empty_pool_leaves_the_playlist_untouched_and_warns(jellyfin, configure, logs):
    """Story 17: a library mid-rescan or an all-watched series never blanks the playlist."""
    bobs = jellyfin.add_series("Bob's Burgers")
    watched = jellyfin.add_episodes(bobs, season=1, count=10, played=True)
    playlist_id = jellyfin.add_playlist(NAME, watched[:3])
    configure(random_playlist(unplayed_only=True))

    result = run_refresh(jellyfin)

    assert result.outcome is Outcome.SUCCESS
    assert jellyfin.playlist_named(NAME).id == playlist_id
    assert jellyfin.playlist(playlist_id).items == watched[:3]
    warnings = [message for level, message in logs if level == "WARNING"]
    assert len(warnings) == 1
    assert NAME in warnings[0]
    assert "skipped" in warnings[0]


def test_an_empty_pool_does_not_create_the_playlist(jellyfin, configure):
    jellyfin.add_series("Bob's Burgers")
    configure(random_playlist())

    result = run_refresh(jellyfin)

    assert result.outcome is Outcome.SUCCESS
    assert jellyfin.playlists_named(NAME) == []


def test_an_existing_playlist_with_the_name_is_adopted_and_refreshed_in_place(jellyfin, configure):
    """Stories 22 and 24: the playlist keeps its ID, so client pins and favourites survive."""
    bobs = jellyfin.add_series("Bob's Burgers")
    jellyfin.add_episodes(bobs, season=0, count=5)
    jellyfin.add_episodes(bobs, season=1, count=40)
    futurama = jellyfin.add_episodes(jellyfin.add_series("Futurama"), season=1, count=5)
    playlist_id = jellyfin.add_playlist(NAME, futurama)
    configure(random_playlist(size=25))

    result = run_refresh(jellyfin)

    assert result.outcome is Outcome.SUCCESS
    playlist = jellyfin.playlist_named(NAME)
    assert playlist.id == playlist_id
    assert len(playlist.items) == 25
    assert len(set(playlist.items)) == 25
    assert {jellyfin.series_of(item) for item in playlist.items} == {bobs}
    assert all(jellyfin.episode(item).season != 0 for item in playlist.items)


def test_playlists_whose_names_only_resemble_the_configured_name_are_left_alone(
    jellyfin, configure
):
    episodes = jellyfin.add_episodes(jellyfin.add_series("Bob's Burgers"), season=1, count=5)
    lookalikes = [
        jellyfin.add_playlist(name, episodes[:1])
        for name in ("bob's burgers shuffle", f"{NAME} (old)", f" {NAME}")
    ]
    configure(random_playlist(size=5))

    run_refresh(jellyfin)

    assert sorted(jellyfin.playlist_named(NAME).items) == sorted(episodes)
    assert [jellyfin.playlist(lookalike).items for lookalike in lookalikes] == [episodes[:1]] * 3


def test_several_playlists_with_the_name_fail_permanently_and_are_left_alone(jellyfin, configure):
    """Story 23: Jellyhook never overwrites the wrong playlist."""
    episodes = jellyfin.add_episodes(jellyfin.add_series("Bob's Burgers"), season=1, count=10)
    first = jellyfin.add_playlist(NAME, episodes[:2])
    second = jellyfin.add_playlist(NAME, episodes[2:4])
    configure(random_playlist())

    result = run_refresh(jellyfin)

    assert result.outcome is Outcome.FAILED
    assert f"2 playlists are named {NAME!r}" in result.reason
    assert jellyfin.playlist(first).items == episodes[:2]
    assert jellyfin.playlist(second).items == episodes[2:4]


def test_running_a_refresh_twice_leaves_one_valid_playlist(jellyfin, configure):
    """Story 32: a redelivered Refresh message is harmless."""
    bobs = jellyfin.add_series("Bob's Burgers")
    jellyfin.add_episodes(bobs, season=1, count=40)
    configure(random_playlist(size=25))

    run_refresh(jellyfin, seed=1)
    playlist_id = jellyfin.playlist_named(NAME).id
    result = run_refresh(jellyfin, seed=1)

    assert result.outcome is Outcome.SUCCESS
    playlist = jellyfin.playlist_named(NAME)
    assert playlist.id == playlist_id
    assert len(set(playlist.items)) == 25
    assert len(playlist.items) == 25


def test_a_refresh_that_failed_partway_is_put_right_by_the_retry(jellyfin, configure):
    """A playlist created just before Jellyfin went away is adopted and filled next time."""
    jellyfin.add_episodes(jellyfin.add_series("Bob's Burgers"), season=1, count=40)
    configure(random_playlist(size=25))
    jellyfin.fail_with(
        requests.ConnectionError("Jellyfin went away"), operation="replace_playlist_items"
    )

    first = run_refresh(jellyfin)
    jellyfin.recover()
    second = run_refresh(jellyfin)

    assert first.outcome is Outcome.RETRY
    assert second.outcome is Outcome.SUCCESS
    playlist = jellyfin.playlist_named(NAME)
    assert len(set(playlist.items)) == 25


@pytest.mark.parametrize(
    "error",
    [
        requests.ConnectionError("Connection refused"),
        JellyfinHTTPException("ServerUnreachable", "Connection refused"),
        JellyfinHTTPException("ReadTimeout", "Read timed out"),
        JellyfinHTTPException(503, "Service Unavailable"),
        JellyfinUnavailableError("Jellyfin did not sign us in"),
    ],
)
def test_a_refresh_is_retried_when_jellyfin_is_unreachable_or_erroring(jellyfin, configure, error):
    """Story 30: a Jellyfin restart at 1am does not cost a week."""
    jellyfin.add_episodes(jellyfin.add_series("Bob's Burgers"), season=1, count=40)
    configure(random_playlist())
    jellyfin.fail_with(error)

    result = run_refresh(jellyfin)

    assert result.outcome is Outcome.RETRY
    assert type(error).__name__ in result.reason


@pytest.mark.parametrize(
    "error",
    [JellyfinHTTPException("Unauthorized", "401"), JellyfinHTTPException(400, "Bad Request")],
)
def test_a_refresh_is_dead_lettered_when_jellyfin_refuses_the_request(jellyfin, configure, error):
    """Story 31: a request that will fail again goes to .dead with its reason."""
    jellyfin.add_episodes(jellyfin.add_series("Bob's Burgers"), season=1, count=40)
    configure(random_playlist())
    jellyfin.fail_with(error)

    result = run_refresh(jellyfin)

    assert result.outcome is Outcome.FAILED
    assert NAME in result.reason
    assert "HTTPException" in result.reason


def test_a_refresh_for_a_name_not_in_the_config_fails_permanently(jellyfin, configure):
    """Story 37: a removed playlist is dead-lettered, not retried forever."""
    jellyfin.add_episodes(jellyfin.add_series("Bob's Burgers"), season=1, count=40)
    configure(random_playlist())

    result = run_refresh(jellyfin, name="Removed Shuffle")

    assert result.outcome is Outcome.FAILED
    assert "'Removed Shuffle'" in result.reason
    assert "not in the config" in result.reason
    assert jellyfin.playlists_named("Removed Shuffle") == []


@pytest.mark.parametrize(
    "body",
    [b"not json", b"\xff", b'["Bob\'s Burgers Shuffle"]', b"{}", b'{"name": ""}', b'{"name": 3}'],
)
def test_a_refresh_message_without_a_playlist_name_fails_permanently(jellyfin, configure, body):
    """A body that names no Random Playlist will not name one on a retry either."""
    jellyfin.add_episodes(jellyfin.add_series("Bob's Burgers"), season=1, count=40)
    configure(random_playlist())

    result = process_refresh_message("refresh", body, jellyfin=jellyfin, rng=random.Random(1))

    assert result.outcome is Outcome.FAILED
    assert result.reason.startswith("Invalid Refresh message")
    assert jellyfin.playlists_named(NAME) == []


def test_a_refresh_logs_that_it_started_and_completed(jellyfin, configure, logs):
    """Story 48: the logs say what happened."""
    jellyfin.add_episodes(jellyfin.add_series("Bob's Burgers"), season=1, count=40)
    configure(random_playlist(size=25))

    run_refresh(jellyfin)

    messages = [message for level, message in logs if level == "INFO"]
    playlist_id = jellyfin.playlist_named(NAME).id
    assert f"Refresh of Random Playlist {NAME!r} started" in messages
    assert any(
        message.startswith(f"Refresh of Random Playlist {NAME!r} completed")
        and playlist_id in message
        and "25 episodes" in message
        for message in messages
    )


def test_a_series_title_that_matches_no_series_fails_permanently(jellyfin, configure):
    """Story 5: a typo fails loudly instead of leaving a stale or empty playlist."""
    jellyfin.add_episodes(jellyfin.add_series("Bob's Burgers"), season=1, count=10)
    playlist_id = jellyfin.add_playlist(NAME, ["episode-2"])
    configure(random_playlist(series=["Bob's Burgers", "Bobs Burgers"]))

    result = run_refresh(jellyfin)

    assert result.outcome is Outcome.FAILED
    assert "'Bobs Burgers'" in result.reason
    assert "no series" in result.reason
    assert jellyfin.playlist(playlist_id).items == ["episode-2"]


def test_a_series_title_that_matches_several_series_fails_permanently(jellyfin, configure):
    """Story 6: Jellyhook never guesses between the UK and US versions of The Office."""
    jellyfin.add_episodes(jellyfin.add_series("The Office"), season=1, count=10)
    jellyfin.add_episodes(jellyfin.add_series("The Office"), season=1, count=10)
    configure(random_playlist(series=["The Office"]))

    result = run_refresh(jellyfin)

    assert result.outcome is Outcome.FAILED
    assert "'The Office'" in result.reason
    assert "2 series" in result.reason
    assert "{id: <series id>}" in result.reason
    assert jellyfin.playlists_named(NAME) == []


def test_a_series_id_that_is_not_in_jellyfin_fails_permanently(jellyfin, configure):
    """An ID cannot start matching a series on a retry."""
    configure(random_playlist(series=[{"id": "no-such-series"}]))

    result = run_refresh(jellyfin)

    assert result.outcome is Outcome.FAILED
    assert "'no-such-series'" in result.reason
    assert jellyfin.playlists_named(NAME) == []
