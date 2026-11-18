"""Random Playlists from the top-level `random_playlists:` section of `jellyhook.yaml`.

`parse_random_playlists` is the one validation routine for that section. The worker and the
scheduler both call it when they load the config, so the two agree on what is valid.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from cronsim import CronSim, CronSimError


class RandomPlaylistConfigError(ValueError):
    """The `random_playlists:` section of the config is invalid.

    The message names the offending Random Playlist (or its position in the list, when it has no
    usable name) and what is wrong with it.
    """


@dataclass(frozen=True)
class Schedule:
    """The standard 5-field cron expression on which a Random Playlist is refreshed.

    Attributes:
        expression (str): The cron expression, for example `0 1 * * 0`.

    Raises:
        ValueError: If `expression` is not a valid 5-field cron expression.
    """

    expression: str

    def __post_init__(self) -> None:
        """Reject anything but a valid standard 5-field cron expression."""
        if not isinstance(self.expression, str):
            raise ValueError(f"expected a cron expression string, got {self.expression!r}")
        # cronsim also takes a sixth (seconds) field; a Schedule is standard 5-field cron
        if len(self.expression.split()) != 5:
            raise ValueError(
                f"{self.expression!r} is not a 5-field cron expression "
                "(minute hour day-of-month month day-of-week)"
            )
        try:
            CronSim(self.expression, datetime.now())
        except CronSimError as exc:
            raise ValueError(f"{self.expression!r} is not a valid cron expression: {exc}") from exc


@dataclass(frozen=True)
class SeriesByTitle:
    """A series named by its title, resolved against Jellyfin at Refresh time.

    Attributes:
        title (str): The series title, matched exactly.
    """

    title: str


@dataclass(frozen=True)
class SeriesById:
    """A series named by its Jellyfin item ID, which skips the title lookup.

    Attributes:
        id (str): The Jellyfin item ID of the series.
    """

    id: str


SeriesRef = SeriesByTitle | SeriesById


@dataclass(frozen=True)
class RandomPlaylist:
    """A Jellyfin playlist, identified by its name, that a Refresh fills with random episodes.

    Attributes:
        name (str): The playlist name in Jellyfin, and its identity.
        series (tuple[SeriesRef, ...]): The series whose episodes make up the Episode Pool.
        size (int): How many episodes a Refresh draws, at most.
        schedule (Schedule): When the playlist is refreshed.
        include_specials (bool): Whether season 0 is part of the Episode Pool.
        unplayed_only (bool): Whether only episodes unplayed by Jellyhook's user are drawn.
    """

    name: str
    series: tuple[SeriesRef, ...]
    size: int
    schedule: Schedule
    include_specials: bool = False
    unplayed_only: bool = False


_REQUIRED_KEYS = ("name", "series", "size", "schedule")
_OPTIONAL_KEYS = ("include_specials", "unplayed_only")


def parse_random_playlists(section: Any) -> tuple[RandomPlaylist, ...]:
    """Validate the `random_playlists:` section and build the Random Playlists it defines.

    Args:
        section (Any): The value of the top-level `random_playlists` key, as loaded from YAML
            or JSON. `None` (key absent or left empty) means no Random Playlists.

    Returns:
        tuple[RandomPlaylist, ...]: The Random Playlists, in config order.

    Raises:
        RandomPlaylistConfigError: If the section is invalid.
    """
    if section is None:
        return ()
    if not isinstance(section, list):
        raise RandomPlaylistConfigError(
            f"random_playlists must be a list of Random Playlists, got {section!r}"
        )
    playlists = tuple(_parse_entry(index, entry) for index, entry in enumerate(section))
    names = [playlist.name for playlist in playlists]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise RandomPlaylistConfigError(
            f"Random Playlist {duplicates[0]!r}: the name is used more than once; each Random "
            "Playlist needs its own name"
        )
    return playlists


def _parse_entry(index: int, entry: Any) -> RandomPlaylist:
    """Validate one entry of the `random_playlists:` list and build its Random Playlist.

    Args:
        index (int): The entry's position in the list, to name it when its name is unusable.
        entry (Any): The entry, as loaded from YAML or JSON.

    Returns:
        RandomPlaylist: The Random Playlist.

    Raises:
        RandomPlaylistConfigError: If the entry is invalid.
    """
    position = f"random_playlists[{index}]"
    if not isinstance(entry, dict):
        raise RandomPlaylistConfigError(
            f"{position}: expected a mapping of Random Playlist settings, got {entry!r}"
        )
    name = entry.get("name")
    if not isinstance(name, str) or not name.strip():
        raise RandomPlaylistConfigError(
            f"{position}: name must be a non-empty string, got {name!r}"
        )

    try:
        unknown = sorted(str(key) for key in entry.keys() - {*_REQUIRED_KEYS, *_OPTIONAL_KEYS})
        if unknown:
            raise ValueError(f"unknown key(s): {', '.join(unknown)}")
        missing = [key for key in _REQUIRED_KEYS if key not in entry]
        if missing:
            raise ValueError(f"missing required key(s): {', '.join(missing)}")
        return RandomPlaylist(
            name=name,
            series=_parse_series(entry["series"]),
            size=_parse_size(entry["size"]),
            schedule=_parse_schedule(entry["schedule"]),
            include_specials=_parse_flag("include_specials", entry.get("include_specials", False)),
            unplayed_only=_parse_flag("unplayed_only", entry.get("unplayed_only", False)),
        )
    except ValueError as exc:
        raise RandomPlaylistConfigError(f"Random Playlist {name!r}: {exc}") from exc


def _parse_series(value: Any) -> tuple[SeriesRef, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"series must be a non-empty list, got {value!r}")
    return tuple(_parse_series_ref(ref) for ref in value)


def _parse_series_ref(ref: Any) -> SeriesRef:
    """Build a series reference from a title string or an `{id: <series id>}` mapping.

    Args:
        ref (Any): One element of a Random Playlist's `series` list.

    Returns:
        SeriesRef: The series reference.

    Raises:
        ValueError: If `ref` is neither a non-empty title nor exactly `{id: <non-empty string>}`.
    """
    if isinstance(ref, str) and ref.strip():
        return SeriesByTitle(ref)
    if isinstance(ref, dict) and ref.keys() == {"id"}:
        series_id = ref["id"]
        if isinstance(series_id, str) and series_id.strip():
            return SeriesById(series_id)
    raise ValueError(
        f"series element {ref!r} is neither a series title nor {{id: <series id>}} "
        "(quote an ID that YAML would read as a number)"
    )


def _parse_size(value: Any) -> int:
    # bool is an int subclass, and `size: yes` is a typo, not 1
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"size must be a whole number of at least 1, got {value!r}")
    return value


def _parse_schedule(value: Any) -> Schedule:
    try:
        return Schedule(value)
    except ValueError as exc:
        raise ValueError(f"schedule {exc}") from exc


def _parse_flag(key: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be true or false, got {value!r}")
    return value
