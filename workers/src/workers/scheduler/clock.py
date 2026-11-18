import threading
from datetime import datetime
from typing import Protocol

# How long one wait lasts before the wall clock is read again; it bounds how late a firing can be
# after the system clock is set forward (NTP, a resumed host) or a DST change
MAX_WAIT_SECONDS = 60.0


class Clock(Protocol):
    """Where the scheduler gets the time from, and how it waits for the next firing.

    Times are naive wall-clock datetimes in the container's local timezone (`TZ`), which is how
    Schedules are read.
    """

    def now(self) -> datetime:
        """The current local wall-clock time."""

    def sleep_until(self, deadline: datetime, stop: threading.Event) -> None:
        """Return once `deadline` has passed, or sooner once `stop` is set."""


class SystemClock:
    """The real clock: local wall-clock time, honouring `TZ`."""

    def now(self) -> datetime:
        """The current local wall-clock time.

        Returns:
            datetime: A naive datetime in the process's local timezone.
        """
        return datetime.now()

    def sleep_until(self, deadline: datetime, stop: threading.Event) -> None:
        """Wait until the wall clock reaches `deadline`, or until `stop` is set.

        Args:
            deadline (datetime): The local wall-clock time to wait for.
            stop (threading.Event): Ends the wait early when set.
        """
        while not stop.is_set():
            remaining = (deadline - self.now()).total_seconds()
            if remaining <= 0:
                return
            stop.wait(min(remaining, MAX_WAIT_SECONDS))
