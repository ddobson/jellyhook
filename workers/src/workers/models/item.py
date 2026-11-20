from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Item:
    """A Jellyfin item, normalised once from a raw webhook message.

    The webhook payload is forwarded by the API without a schema, so the same field can arrive in
    different shapes. Services read the Item rather than the raw message so that each field is
    interpreted one way everywhere.
    """

    genres: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    item_type: str | None = None

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

        Args:
            message: The webhook message as received from the queue.

        Returns:
            The normalised Item.
        """
        return cls(
            genres=_list_field(message.get("Genres")),
            tags=_list_field(message.get("Tags")),
            item_type=message.get("ItemType") or message.get("Type"),
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
