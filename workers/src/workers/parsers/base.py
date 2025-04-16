from abc import ABC, abstractmethod


class BaseParser(ABC):
    """Abstract base class for parsers."""

    @classmethod
    @abstractmethod
    def parse(cls, filename: str) -> dict | None:
        """Parse a filename and return attributes or None if not matched."""
