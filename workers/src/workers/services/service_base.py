import glob
import pathlib
from abc import ABC, abstractmethod
from typing import ClassVar

from workers import config
from workers.errors import MediaNotFoundError, WebhookWorkerError


class ServiceBase(ABC):
    """Abstract base class for services."""

    # True for services that work on the movie file located by `file_from_message`. The
    # orchestrator skips them for other item types (episodes, music), which have no such file.
    movies_only: ClassVar[bool] = False

    @abstractmethod
    def exec(self) -> None:
        """Execute the service.

        This method should be implemented by subclasses to define the service's behavior.
        """
        pass

    @property
    def locked_path(self) -> pathlib.Path | None:
        """The media file this service modifies, or None if it leaves files alone.

        The orchestrator holds a per-file lock for the duration of `exec()` so two jobs never
        rewrite the same file at once. Services that change a file on disk must override this.
        """
        return None

    @classmethod
    @abstractmethod
    def from_message(cls, message: dict, service_config: dict) -> "ServiceBase":
        """Create an instance of the service from a message.

        Args:
            message (dict): The message to create the service from.
            service_config (dict): The configuration for the service.

        Returns:
            ServiceBase: An instance of the service.
        """
        pass

    @staticmethod
    def is_movie_message(message: dict) -> bool:
        """Whether a message is about an item `file_from_message` can locate.

        Only movies (including stand-up, which Jellyfin also types as Movie) live in a
        "<Name> (<Year>)" folder under `MEDIA_PATHS`. Episodes, series, music and the like never
        will, so looking for them would only retry until the message is dead-lettered.

        Args:
            message (dict): The Jellyfin webhook message.

        Returns:
            bool: True for movies, and for messages that do not say what type the item is.
        """
        item_type = message.get("ItemType")
        return item_type is None or item_type == "Movie"

    @staticmethod
    def file_from_message(message: dict) -> pathlib.Path:
        """Get the path to the file from a Jellyfin item_added webhook message.

        Args:
            message (dict): The Jellyfin webhook message containing 'Name' and 'Year' keys.

        Returns:
            pathlib.Path: The path to the file.

        Raises:
            MediaNotFoundError: If no file is found (yet). Retryable: Jellyfin can announce an
                item before the file has finished landing.
            WebhookWorkerError: If multiple files are found.
        """
        file_types = [".mkv", ".mp4", ".avi"]
        search_result = []

        for base_dir in config.MEDIA_PATHS:
            dirname = f"{base_dir}/{message['Name']} ({message['Year']})".replace(":", " -")
            obj_dir = pathlib.Path(dirname)

            if not obj_dir.exists():
                continue

            # Escaped: titles such as "[REC]" would otherwise be read as glob syntax
            name_pattern = glob.escape(message["Name"].replace(":", ""))
            patterns = [f"{name_pattern}*{file_type}" for file_type in file_types]
            files_found = [result for pattern in patterns for result in obj_dir.glob(pattern)]

            if files_found:
                search_result = files_found
                break

        if not search_result:
            err_msg = f"No video found for '{message['Name']}'"
            raise MediaNotFoundError(err_msg)

        if len(search_result) != 1:
            err_msg = f"Found more than one video for '{message['Name']}'"
            raise WebhookWorkerError(err_msg)

        return search_result[0]
