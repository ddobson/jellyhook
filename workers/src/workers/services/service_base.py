from abc import ABC, abstractmethod
from typing import ClassVar

from workers.job import Job


class ServiceBase(ABC):
    """Abstract base class for services."""

    # True for services that work on the movie file the Job locates. The orchestrator skips them
    # for other item types (episodes, music), which have no such file.
    movies_only: ClassVar[bool] = False

    # True for services that rewrite the media file. The orchestrator holds the Job's per-file
    # lock for the duration of `exec()` so two jobs never rewrite the same file at once.
    modifies_media: ClassVar[bool] = False

    @abstractmethod
    def exec(self) -> None:
        """Execute the service.

        This method should be implemented by subclasses to define the service's behavior.
        """
        pass

    @classmethod
    @abstractmethod
    def from_message(cls, job: Job, service_config: dict) -> "ServiceBase":
        """Create an instance of the service for a message.

        Args:
            job (Job): The message's job. It owns the media file, scratch space and lock, and
                carries the message and parsed item.
            service_config (dict): The configuration for the service.

        Returns:
            ServiceBase: An instance of the service.
        """
        pass
