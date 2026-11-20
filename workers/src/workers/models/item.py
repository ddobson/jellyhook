from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

TICKS_PER_MINUTE = 10_000_000 * 60


@dataclass(frozen=True)
class Item:
    """A Jellyfin item, normalised once from a raw webhook message.

    The webhook payload is forwarded by the API without a schema, so the same field can arrive in
    different shapes. Services read the Item rather than the raw message so that each field is
    interpreted one way everywhere.
    """

    item_id: str | None = None
    genres: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    item_type: str | None = None
    runtime_minutes: float | None = None
    year: int | None = None

    @property
    def is_movie(self) -> bool:
        """Whether this is a movie, the only type with a file the movie services can locate.

        An item whose type the message does not state counts as a movie, so those services
        still attempt it rather than silently skipping it.
        """
        return self.item_type is None or self.item_type == "Movie"

    @classmethod
    def from_message(cls, message: dict[str, Any]) -> "Item":
        """Build an Item from a Jellyfin webhook message.

        Fields of an embedded `Item` mapping take precedence over the top-level ones. Items fetched
        from the Jellyfin API use the same field names, so one can be merged into the message
        before building.

        Args:
            message: The webhook message as received from the queue.

        Returns:
            The normalised Item.
        """
        embedded = message.get("Item")
        if isinstance(embedded, dict):
            message = {**message, **embedded}

        return cls(
            item_id=message.get("ItemId") or None,
            genres=_list_field(message.get("Genres")),
            tags=_list_field(message.get("Tags")),
            item_type=message.get("ItemType") or message.get("Type"),
            runtime_minutes=_runtime_minutes(message),
            year=_year(message),
        )


def _list_field(raw: Any) -> tuple[str, ...]:
    """Normalise a comma-separated string or a list into stripped, non-empty entries."""
    if raw is None:
        return ()
    if isinstance(raw, str):
        parts: Iterable[Any] = raw.split(",")
    elif isinstance(raw, Iterable):
        parts = raw
    else:
        parts = [raw]
    return tuple(text for part in parts if (text := str(part).strip()))


def _runtime_minutes(message: dict[str, Any]) -> float | None:
    """Convert Jellyfin's runtime ticks (100 ns units) to minutes, if a numeric value is present."""
    ticks = message.get("RunTimeTicks") or message.get("RuntimeTicks")
    if isinstance(ticks, (int, float)):
        return ticks / TICKS_PER_MINUTE
    return None


def _year(message: dict[str, Any]) -> int | None:
    """Read the release year, falling back to the year of the premiere or creation date."""
    for key in ("ProductionYear", "Year"):
        year = _to_int(message.get(key))
        if year is not None:
            return year

    date = message.get("PremiereDate") or message.get("DateCreated")
    if isinstance(date, str):
        return _to_int(date[:4])
    return None


def _to_int(value: Any) -> int | None:
    """Coerce a value to int, or None when it is missing or not a number."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
