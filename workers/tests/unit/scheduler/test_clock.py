import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from workers.scheduler import SystemClock


@pytest.fixture
def container_tz(monkeypatch):
    """Set the process timezone the way the container's `TZ` does, and restore it afterwards."""

    def set_tz(name: str) -> None:
        monkeypatch.setenv("TZ", name)
        time.tzset()

    yield set_tz
    monkeypatch.undo()
    time.tzset()


@pytest.mark.parametrize("zone", ["America/New_York", "Asia/Tokyo"])
def test_now_is_wall_clock_time_in_the_containers_timezone(container_tz, zone):
    """Schedules are read in local time, so `0 1 * * 0` means 1am where the user lives."""
    container_tz(zone)

    now = SystemClock().now()

    expected = datetime.now(ZoneInfo(zone)).replace(tzinfo=None)
    assert now.tzinfo is None
    assert abs(now - expected) < timedelta(seconds=5)


def test_sleep_until_returns_once_the_deadline_has_passed():
    clock = SystemClock()
    deadline = clock.now() + timedelta(milliseconds=100)

    clock.sleep_until(deadline, threading.Event())

    assert clock.now() >= deadline


def test_sleep_until_returns_promptly_once_stop_is_set():
    """A SIGTERM handler only sets the event; the scheduler must not sleep through it."""
    clock = SystemClock()
    stop = threading.Event()
    threading.Timer(0.05, stop.set).start()
    started = time.monotonic()

    clock.sleep_until(clock.now() + timedelta(hours=1), stop)

    assert time.monotonic() - started < 2
