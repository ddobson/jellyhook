"""The scheduler, driven through `run_scheduler`.

Every test runs against a fake clock, a fake publisher, a temporary state file and a temporary
config file, and asserts on what was published, what the state file records and the exit code.
"""

import json
import threading
import time
from datetime import datetime, timedelta
from unittest import mock

import pytest
import yaml

from workers.scheduler import (
    SystemClock,
    run_scheduler,
    scheduler as scheduler_module,
)


class FakeClock:
    """A clock that jumps straight to each deadline, and requests a stop at `stop_at`."""

    def __init__(self, start: datetime, stop_at: datetime):
        """Start the clock at `start`."""
        self.current = start
        self.stop_at = stop_at

    def now(self) -> datetime:
        return self.current

    def sleep_until(self, deadline: datetime, stop: threading.Event) -> None:
        if deadline >= self.stop_at:
            self.current = self.stop_at
            stop.set()
            return
        self.current = deadline


class FakePublisher:
    """Records each confirmed publish with the clock time it happened at."""

    def __init__(self, clock: FakeClock):
        """Read publish times from `clock`."""
        self.clock = clock
        self.published: list[tuple[str, datetime]] = []

    def publish(self, playlist_name: str) -> None:
        self.published.append((playlist_name, self.clock.now()))


def playlist(name: str, schedule: str = "0 1 * * 0") -> dict:
    return {"name": name, "series": ["Bob's Burgers"], "size": 25, "schedule": schedule}


@pytest.fixture
def config_path(tmp_path):
    def write(*playlists: dict) -> str:
        path = tmp_path / "jellyhook.yaml"
        path.write_text(yaml.safe_dump({"random_playlists": list(playlists)}))
        return str(path)

    return write


@pytest.fixture
def state_path(tmp_path):
    return tmp_path / "state" / "scheduler-state.json"


def write_state(state_path, entries: dict[str, datetime]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps({name: when.astimezone().isoformat() for name, when in entries.items()})
    )


def read_state(state_path) -> dict[str, datetime]:
    raw = json.loads(state_path.read_text())
    return {
        name: datetime.fromisoformat(when).astimezone().replace(tzinfo=None)
        for name, when in raw.items()
    }


# A Sunday; `0 1 * * 0` fires at 1am on it
SUNDAY = datetime(2026, 11, 15)


def test_publishes_a_refresh_at_each_firing_time(config_path, state_path):
    """`0 1 * * 0` publishes every Sunday at 1am, and nothing in between."""
    write_state(state_path, {"Shuffle": datetime(2026, 11, 8, 1, 0, 5)})
    clock = FakeClock(start=SUNDAY, stop_at=SUNDAY + timedelta(days=14, hours=12))
    publisher = FakePublisher(clock)

    exit_code = run_scheduler(
        [],
        config_path=config_path(playlist("Shuffle")),
        state_path=state_path,
        clock=clock,
        publisher=publisher,
        stop_event=threading.Event(),
    )

    assert exit_code == 0
    assert publisher.published == [
        ("Shuffle", datetime(2026, 11, 15, 1, 0)),
        ("Shuffle", datetime(2026, 11, 22, 1, 0)),
        ("Shuffle", datetime(2026, 11, 29, 1, 0)),
    ]


def run(config, state_path, clock, publisher, args=(), stop_event=None):
    return run_scheduler(
        list(args),
        config_path=config,
        state_path=state_path,
        clock=clock,
        publisher=publisher,
        stop_event=stop_event or threading.Event(),
    )


def test_records_each_publish_time_in_the_state_file(config_path, state_path):
    write_state(state_path, {"Shuffle": datetime(2026, 11, 8, 1, 0, 5)})
    clock = FakeClock(start=SUNDAY, stop_at=SUNDAY + timedelta(days=7, hours=12))

    run(config_path(playlist("Shuffle")), state_path, clock, FakePublisher(clock))

    assert read_state(state_path) == {"Shuffle": datetime(2026, 11, 22, 1, 0)}


class UnconfirmedPublisher:
    """A broker that never confirms: every publish fails."""

    def publish(self, playlist_name: str) -> None:
        raise ConnectionError("connection to the broker was lost")


def test_a_publish_without_a_confirm_records_nothing_and_exits_non_zero(config_path, state_path):
    """The state file is written only after a confirmed publish, so the Refresh is not lost."""
    write_state(state_path, {"Shuffle": datetime(2026, 11, 8, 1, 0, 5)})
    clock = FakeClock(start=SUNDAY, stop_at=SUNDAY + timedelta(days=7))

    exit_code = run(config_path(playlist("Shuffle")), state_path, clock, UnconfirmedPublisher())

    assert exit_code == 1
    assert read_state(state_path) == {"Shuffle": datetime(2026, 11, 8, 1, 0, 5)}


def test_an_overdue_playlist_gets_exactly_one_catch_up_at_startup(config_path, state_path):
    """Two missed Sundays cost one catch-up Refresh, not a burst; the Schedule then carries on."""
    write_state(state_path, {"Shuffle": datetime(2026, 11, 1, 1, 0, 5)})
    start = datetime(2026, 11, 15, 9, 30)
    clock = FakeClock(start=start, stop_at=datetime(2026, 11, 22, 12, 0))
    publisher = FakePublisher(clock)

    run(config_path(playlist("Shuffle")), state_path, clock, publisher)

    assert publisher.published == [
        ("Shuffle", start),
        ("Shuffle", datetime(2026, 11, 22, 1, 0)),
    ]


def test_a_never_refreshed_playlist_is_published_at_startup(config_path, state_path):
    """A new Random Playlist appears without waiting for its first Schedule."""
    start = datetime(2026, 11, 17, 14, 0)
    clock = FakeClock(start=start, stop_at=datetime(2026, 11, 20))
    publisher = FakePublisher(clock)

    run(config_path(playlist("Shuffle")), state_path, clock, publisher)

    assert publisher.published == [("Shuffle", start)]
    assert read_state(state_path) == {"Shuffle": start}


def test_a_restart_does_not_reshuffle_an_up_to_date_playlist(config_path, state_path):
    write_state(state_path, {"Shuffle": datetime(2026, 11, 15, 1, 0, 5)})
    clock = FakeClock(start=datetime(2026, 11, 15, 9, 30), stop_at=datetime(2026, 11, 22, 0, 59))
    publisher = FakePublisher(clock)

    run(config_path(playlist("Shuffle")), state_path, clock, publisher)

    assert publisher.published == []
    assert read_state(state_path) == {"Shuffle": datetime(2026, 11, 15, 1, 0, 5)}


def test_a_firing_due_at_the_startup_minute_is_caught_up_once(config_path, state_path):
    """Starting exactly on a firing publishes it at startup, and not again from the loop."""
    write_state(state_path, {"Shuffle": datetime(2026, 11, 8, 1, 0, 5)})
    start = datetime(2026, 11, 15, 1, 0)
    clock = FakeClock(start=start, stop_at=datetime(2026, 11, 22, 0, 59))
    publisher = FakePublisher(clock)

    run(config_path(playlist("Shuffle")), state_path, clock, publisher)

    assert publisher.published == [("Shuffle", start)]


def test_each_playlist_is_caught_up_and_scheduled_on_its_own(config_path, state_path):
    write_state(
        state_path,
        {"Weekly": datetime(2026, 11, 15, 1, 0, 5), "Nightly": datetime(2026, 11, 16, 3, 0, 1)},
    )
    start = datetime(2026, 11, 17, 12, 0)
    clock = FakeClock(start=start, stop_at=datetime(2026, 11, 18, 12, 0))
    publisher = FakePublisher(clock)

    run(
        config_path(playlist("Weekly"), playlist("Nightly", schedule="0 3 * * *")),
        state_path,
        clock,
        publisher,
    )

    assert publisher.published == [
        ("Nightly", start),
        ("Nightly", datetime(2026, 11, 18, 3, 0)),
    ]


def test_state_entries_for_removed_playlists_are_ignored(config_path, state_path):
    """A name no longer in the config is neither published nor a reason to fail."""
    write_state(state_path, {"Old Shuffle": datetime(2026, 11, 1, 1, 0)})
    start = datetime(2026, 11, 17, 14, 0)
    clock = FakeClock(start=start, stop_at=datetime(2026, 11, 20))
    publisher = FakePublisher(clock)

    exit_code = run(config_path(playlist("Shuffle")), state_path, clock, publisher)

    assert exit_code == 0
    assert publisher.published == [("Shuffle", start)]


@pytest.mark.parametrize(
    "contents",
    ["{not json", '["Shuffle"]', '{"Shuffle": "last tuesday"}'],
    ids=["invalid JSON", "not a mapping", "bad time"],
)
def test_an_unreadable_state_file_counts_as_never_refreshed(config_path, state_path, contents):
    """One extra Refresh is harmless; a scheduler that crash-loops on its own file is not."""
    state_path.parent.mkdir(parents=True)
    state_path.write_text(contents)
    start = datetime(2026, 11, 17, 14, 0)
    clock = FakeClock(start=start, stop_at=datetime(2026, 11, 20))
    publisher = FakePublisher(clock)

    exit_code = run(config_path(playlist("Shuffle")), state_path, clock, publisher)

    assert exit_code == 0
    assert publisher.published == [("Shuffle", start)]
    assert read_state(state_path) == {"Shuffle": start}


def test_each_published_refresh_is_logged(config_path, state_path):
    write_state(state_path, {"Shuffle": datetime(2026, 11, 8, 1, 0, 5)})
    clock = FakeClock(start=SUNDAY, stop_at=SUNDAY + timedelta(days=7, hours=12))

    with mock.patch.object(scheduler_module, "logger") as logger:
        run(config_path(playlist("Shuffle")), state_path, clock, FakePublisher(clock))

    logged = [str(call.args[0]) for call in logger.info.call_args_list]
    assert len([line for line in logged if line.startswith("Published")]) == 2
    assert all("'Shuffle'" in line for line in logged if line.startswith("Published"))


def test_a_manual_run_publishes_once_without_touching_the_state_file(config_path, state_path):
    """`run "<name>"` reshuffles on demand; the next scheduled Refresh still happens on time."""
    write_state(state_path, {"Shuffle": datetime(2026, 11, 8, 1, 0, 5)})
    before = state_path.read_text()
    clock = FakeClock(start=datetime(2026, 11, 17, 14, 0), stop_at=datetime(2026, 12, 31))
    publisher = FakePublisher(clock)

    exit_code = run(
        config_path(playlist("Shuffle"), playlist("Other")),
        state_path,
        clock,
        publisher,
        args=["run", "Shuffle"],
    )

    assert exit_code == 0
    assert publisher.published == [("Shuffle", datetime(2026, 11, 17, 14, 0))]
    assert state_path.read_text() == before


def test_a_manual_run_does_not_create_the_state_file(config_path, state_path):
    clock = FakeClock(start=datetime(2026, 11, 17, 14, 0), stop_at=datetime(2026, 12, 31))

    run(
        config_path(playlist("Shuffle")),
        state_path,
        clock,
        FakePublisher(clock),
        ["run", "Shuffle"],
    )

    assert not state_path.exists()


def test_a_manual_run_for_an_unknown_name_exits_non_zero(config_path, state_path):
    clock = FakeClock(start=datetime(2026, 11, 17, 14, 0), stop_at=datetime(2026, 12, 31))
    publisher = FakePublisher(clock)

    exit_code = run(
        config_path(playlist("Shuffle")), state_path, clock, publisher, ["run", "Shufle"]
    )

    assert exit_code == 1
    assert publisher.published == []


def test_a_manual_run_that_is_not_confirmed_exits_non_zero(config_path, state_path):
    clock = FakeClock(start=datetime(2026, 11, 17, 14, 0), stop_at=datetime(2026, 12, 31))

    exit_code = run(
        config_path(playlist("Shuffle")),
        state_path,
        clock,
        UnconfirmedPublisher(),
        ["run", "Shuffle"],
    )

    assert exit_code == 1


def test_with_no_random_playlists_it_warns_and_waits_idle_until_stopped(config_path, state_path):
    """Exiting would make Docker restart it over and over for a user who skips the feature."""
    clock = FakeClock(start=datetime(2026, 11, 17, 14, 0), stop_at=datetime(2026, 12, 31))
    publisher = FakePublisher(clock)
    stop = threading.Event()
    threading.Timer(0.05, stop.set).start()

    with mock.patch.object(scheduler_module, "logger") as logger:
        exit_code = run(config_path(), state_path, clock, publisher, stop_event=stop)

    assert stop.is_set(), "returned before it was asked to stop"
    assert exit_code == 0
    assert publisher.published == []
    assert logger.warning.call_count == 1
    assert not state_path.exists()


def test_a_stop_while_waiting_for_the_next_firing_exits_zero_promptly(config_path, state_path):
    """With the real clock, a stop request (SIGTERM in production) ends the wait."""
    clock = SystemClock()
    write_state(state_path, {"Yearly": clock.now()})
    stop = threading.Event()
    threading.Timer(0.05, stop.set).start()
    started = time.monotonic()

    exit_code = run(
        config_path(playlist("Yearly", schedule="0 1 1 1 *")),
        state_path,
        clock,
        UnconfirmedPublisher(),
        stop_event=stop,
    )

    assert exit_code == 0
    assert time.monotonic() - started < 2


@pytest.mark.parametrize(
    "playlists",
    [
        pytest.param([playlist("Shuffle", schedule="every sunday")], id="unparseable schedule"),
        pytest.param([playlist("Shuffle", schedule="0 0 1 * * 0")], id="six-field schedule"),
        pytest.param([playlist("Shuffle"), playlist("Shuffle")], id="duplicate names"),
        pytest.param([{**playlist("Shuffle"), "size": 0}], id="size below 1"),
        pytest.param([{**playlist("Shuffle"), "series": []}], id="empty series"),
    ],
)
@pytest.mark.parametrize("args", [[], ["run", "Shuffle"]], ids=["scheduled", "manual"])
def test_refuses_to_start_on_invalid_config(config_path, state_path, playlists, args):
    clock = FakeClock(start=datetime(2026, 11, 17, 14, 0), stop_at=datetime(2026, 12, 31))
    publisher = FakePublisher(clock)

    with mock.patch.object(scheduler_module, "logger") as logger:
        exit_code = run(config_path(*playlists), state_path, clock, publisher, args)

    assert exit_code == 1
    assert publisher.published == []
    assert not state_path.exists()
    assert logger.error.call_count == 1


@pytest.mark.parametrize("args", [["run"], ["run", "a", "b"], ["refresh", "Shuffle"]])
def test_unrecognised_arguments_exit_with_a_usage_error(config_path, state_path, args):
    clock = FakeClock(start=datetime(2026, 11, 17, 14, 0), stop_at=datetime(2026, 12, 31))
    publisher = FakePublisher(clock)

    exit_code = run(config_path(playlist("Shuffle")), state_path, clock, publisher, args)

    assert exit_code == 2
    assert publisher.published == []
