import json
import os
from datetime import datetime
from pathlib import Path

from workers.logger import logger


class StateFile:
    """The scheduler's record of when it last published a Refresh for each Random Playlist.

    The file is a JSON object mapping playlist name to an ISO 8601 time with its UTC offset, so it
    stays unambiguous across DST changes and a change of `TZ`. Times go in and come out as naive
    local wall-clock times, like the scheduler's clock.

    Args:
        path (Path): Where the file lives; on a volume that survives container recreation.
    """

    def __init__(self, path: Path) -> None:
        """Point at the state file; nothing is read until `last_published` is called."""
        self.path = path

    def last_published(self) -> dict[str, datetime]:
        """Read every recorded publish time.

        An unreadable file or entry is logged and left out, so those playlists count as never
        refreshed. The cost is one extra Refresh, where failing would crash-loop the scheduler.

        Returns:
            dict[str, datetime]: Playlist name to last publish time; empty if there is no file.
        """
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text())
        except ValueError as exc:
            logger.warning(f"Ignoring unreadable scheduler state file {self.path}: {exc}")
            return {}
        if not isinstance(raw, dict):
            logger.warning(f"Ignoring scheduler state file {self.path}: expected a JSON object")
            return {}

        entries = {}
        for name, when in raw.items():
            try:
                entries[name] = _from_stored(when)
            except (TypeError, ValueError):
                logger.warning(f"Ignoring unreadable publish time {when!r} for {name!r}")
        return entries

    def record(self, playlist_name: str, published_at: datetime) -> None:
        """Record a confirmed publish, keeping every other entry.

        The file is replaced atomically, so a crash mid-write leaves the previous version.

        Args:
            playlist_name (str): The Random Playlist's name.
            published_at (datetime): When the publish was confirmed, local wall-clock time.
        """
        entries = {name: _to_stored(when) for name, when in self.last_published().items()}
        entries[playlist_name] = _to_stored(published_at)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        scratch = self.path.with_name(f"{self.path.name}.tmp")
        scratch.write_text(json.dumps(entries, indent=2, sort_keys=True))
        os.replace(scratch, self.path)


def _to_stored(when: datetime) -> str:
    return when.astimezone().isoformat()


def _from_stored(when: str) -> datetime:
    return datetime.fromisoformat(when).astimezone().replace(tzinfo=None)
