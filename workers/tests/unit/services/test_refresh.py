from unittest import mock

import pytest
import requests

from workers.config import WorkerConfig
from workers.results import Outcome
from workers.services import refresh
from workers.services.refresh import process_refresh_message, refresh_message

CONFIG = """
random_playlists:
  - name: Bob's Burgers Shuffle
    series: ["Bob's Burgers"]
    size: 25
    schedule: "0 1 * * 0"
"""


@pytest.fixture(autouse=True)
def loaded_config(tmp_path):
    """Load a config with one Random Playlist into a fresh WorkerConfig singleton."""
    path = tmp_path / "jellyhook.yaml"
    path.write_text(CONFIG)
    with mock.patch.dict("workers.utils.SingletonMeta._instances", {}, clear=True):
        yield WorkerConfig.load(str(path))


def test_a_refresh_for_a_name_not_in_the_config_fails_permanently_with_a_readable_reason():
    """Story 37: a removed playlist is dead-lettered, not retried forever."""
    result = process_refresh_message("refresh", refresh_message("Removed Shuffle"))

    assert result.outcome is Outcome.FAILED
    assert "'Removed Shuffle'" in result.reason
    assert "not in the config" in result.reason


@pytest.mark.parametrize(
    "body",
    [b"not json", b"\xff", b'["Bob\'s Burgers Shuffle"]', b"{}", b'{"name": ""}', b'{"name": 3}'],
)
def test_a_refresh_message_without_a_playlist_name_fails_permanently(body):
    """A body that names no Random Playlist will not name one on a retry either."""
    with mock.patch.object(refresh, "refresh") as step:
        result = process_refresh_message("refresh", body)

    assert result.outcome is Outcome.FAILED
    assert result.reason.startswith("Invalid Refresh message")
    step.assert_not_called()


def test_a_refresh_for_a_configured_name_runs_the_refresh_on_its_definition():
    """Story 38: the definition comes from the config at processing time, not the message."""
    with mock.patch.object(refresh, "refresh") as step:
        result = process_refresh_message("refresh", refresh_message("Bob's Burgers Shuffle"))

    assert result.outcome is Outcome.SUCCESS
    ((playlist,), _) = step.call_args
    assert playlist.name == "Bob's Burgers Shuffle"
    assert playlist.size == 25


@pytest.mark.parametrize(
    ("error", "outcome"),
    [
        (requests.ConnectionError("Jellyfin is down"), Outcome.RETRY),
        (ValueError("bad playlist"), Outcome.FAILED),
    ],
)
def test_a_failing_refresh_is_classified_like_any_other_job(error, outcome):
    """Jellyfin being unreachable is retried; anything else goes to .dead, with its reason."""
    with mock.patch.object(refresh, "refresh", side_effect=error):
        result = process_refresh_message("refresh", refresh_message("Bob's Burgers Shuffle"))

    assert result.outcome is outcome
    assert type(error).__name__ in result.reason
    assert str(error) in result.reason
