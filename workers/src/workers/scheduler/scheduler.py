import threading
from collections.abc import Sequence
from pathlib import Path

from workers.config.random_playlists import (
    RandomPlaylist,
    RandomPlaylistConfigError,
    parse_random_playlists,
)
from workers.config.worker_config import load_config_file
from workers.logger import logger
from workers.scheduler.clock import Clock
from workers.scheduler.publisher import RefreshPublisher
from workers.scheduler.state import StateFile

USAGE = 'usage: scheduler [run "<playlist name>"]'


def run_scheduler(
    args: Sequence[str],
    *,
    config_path: str,
    state_path: Path,
    clock: Clock,
    publisher: RefreshPublisher,
    stop_event: threading.Event,
) -> int:
    """Run the scheduler, or publish one manual Refresh, and return the process exit code.

    With no arguments it keeps the Random Playlists' Schedules until `stop_event` is set. First it
    publishes one catch-up Refresh for each playlist that is overdue: never published, or whose
    latest firing at or before now is later than its recorded publish time. Then it publishes a
    Refresh each time a Schedule fires. Each publish is recorded in the state file only after the
    publisher returns, that is after the broker's confirm. With no Random Playlists configured it
    logs a warning and waits for `stop_event` instead.

    With `run "<playlist name>"` it publishes one Refresh for that playlist and returns. It does
    not read or write the state file, so the Schedule is unaffected.

    The publisher is called only when there is something to publish, so a publisher that connects
    lazily never touches the broker for an invalid config, an unknown name, or an idle scheduler.

    Args:
        args (Sequence[str]): The command-line arguments after the program name: none, or
            `["run", "<playlist name>"]`.
        config_path (str): The path to `jellyhook.yaml`; only `random_playlists:` is read.
        state_path (Path): The JSON state file of playlist name to last publish time.
        clock (Clock): The source of local time, and how to wait for the next firing.
        publisher (RefreshPublisher): Publishes a Refresh and waits for the confirm.
        stop_event (threading.Event): Set (for example by a SIGTERM handler) to stop the
            scheduler. It is checked between firings and wakes `clock.sleep_until`.

    Returns:
        int: 0 after a requested stop or a published manual Refresh. 1 when the Random Playlist
            config is invalid, the manual run names no configured Random Playlist, or a publish
            or state write fails. 2 for unrecognised arguments.
    """
    if args and (len(args) != 2 or args[0] != "run"):
        logger.error(f"Unrecognised arguments {list(args)!r}; {USAGE}")
        return 2

    try:
        playlists = parse_random_playlists(load_config_file(config_path).get("random_playlists"))
    except RandomPlaylistConfigError as exc:
        logger.error(f"Invalid Random Playlist configuration, not starting: {exc}")
        return 1

    try:
        if args:
            return _run_manually(args[1], playlists, publisher)
        if not playlists:
            logger.warning("No Random Playlists are configured; waiting idle until stopped")
            stop_event.wait()
            return 0
        _keep_schedules(playlists, StateFile(state_path), clock, publisher, stop_event)
    except Exception:
        logger.exception("Scheduler stopped: a Refresh could not be published or recorded")
        return 1
    logger.info("Scheduler stopped")
    return 0


def _run_manually(
    name: str, playlists: Sequence[RandomPlaylist], publisher: RefreshPublisher
) -> int:
    if name not in {playlist.name for playlist in playlists}:
        logger.error(f"No Random Playlist named {name!r} in the config")
        return 1
    publisher.publish(name)
    logger.info(f"Published a manual Refresh for Random Playlist {name!r}")
    return 0


def _keep_schedules(
    playlists: Sequence[RandomPlaylist],
    state: StateFile,
    clock: Clock,
    publisher: RefreshPublisher,
    stop_event: threading.Event,
) -> None:
    """Catch up overdue playlists once, then publish on every firing until stopped."""
    last_published = state.last_published()
    now = clock.now()
    for playlist in playlists:
        recorded = last_published.get(playlist.name)
        if recorded is None or playlist.schedule.latest_firing(now) > recorded:
            _publish(playlist, state, clock, publisher, reason="catch-up")

    # Computed from `now` after each wake, so firings missed during one long wait (a suspended
    # host, a slow publish) collapse into a single Refresh
    next_due = {playlist.name: playlist.schedule.next_firing(now) for playlist in playlists}
    for playlist in playlists:
        logger.info(
            f"Next Refresh for Random Playlist {playlist.name!r} at {next_due[playlist.name]}"
        )
    while not stop_event.is_set():
        clock.sleep_until(min(next_due.values()), stop_event)
        if stop_event.is_set():
            return
        now = clock.now()
        for playlist in playlists:
            if next_due[playlist.name] <= now:
                _publish(playlist, state, clock, publisher, reason="scheduled")
                next_due[playlist.name] = playlist.schedule.next_firing(now)


def _publish(
    playlist: RandomPlaylist,
    state: StateFile,
    clock: Clock,
    publisher: RefreshPublisher,
    reason: str,
) -> None:
    """Publish a Refresh and, once it is confirmed, record when."""
    publisher.publish(playlist.name)
    state.record(playlist.name, clock.now())
    logger.info(f"Published a {reason} Refresh for Random Playlist {playlist.name!r}")
