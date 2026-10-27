from dataclasses import dataclass
from enum import StrEnum


class Outcome(StrEnum):
    """How processing a message ended, independent of what the queue does about it."""

    SUCCESS = "success"
    RETRY = "retry"  # Failed in a way that may clear up (file not landed yet, Jellyfin down)
    FAILED = "failed"  # Failed in a way retrying cannot fix


@dataclass(frozen=True)
class ProcessingResult:
    """The result of processing one message.

    Attributes:
        outcome (Outcome): How processing ended.
        reason (str): Human-readable failure description, stored on dead-lettered messages.
    """

    outcome: Outcome
    reason: str = ""

    @classmethod
    def success(cls) -> "ProcessingResult":
        """Build a successful result."""
        return cls(Outcome.SUCCESS)

    @classmethod
    def retry(cls, reason: str) -> "ProcessingResult":
        """Build a result for a failure worth retrying."""
        return cls(Outcome.RETRY, reason)

    @classmethod
    def failed(cls, reason: str) -> "ProcessingResult":
        """Build a result for a failure that retrying cannot fix."""
        return cls(Outcome.FAILED, reason)
