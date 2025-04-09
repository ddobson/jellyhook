import pathlib
from abc import ABC, abstractmethod

from workers import config
from workers.errors import WebhookWorkerError


class ServiceBase(ABC):
    """Abstract base class for services."""

    @abstractmethod
    def exec(self) -> None:
        """Execute the service.

        This method should be implemented by subclasses to define the service's behavior.
        """
        pass

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
    def file_from_message(message: dict) -> pathlib.Path:
        """Get the path to the file from a Jellyfin item_added webhook message.

        Args:
            message (dict): The Jellyfin webhook message containing 'Name' and 'Year' keys.

        Returns:
            pathlib.Path: The path to the file.

        Raises:
            WebhookWorkerError: If no file is found or multiple files are found.
        """
        file_types = [".mkv", ".mp4", ".avi"]
        search_result = []

        for base_dir in config.MEDIA_PATHS:
            dirname = f"{base_dir}/{message['Name']} ({message['Year']})".replace(":", " -")
            obj_dir = pathlib.Path(dirname)

            if not obj_dir.exists():
                continue

            patterns = [
                f"{message['Name'].replace(':', '')}*{file_type}" for file_type in file_types
            ]
            files_found = [result for pattern in patterns for result in obj_dir.glob(pattern)]

            if files_found:
                search_result = files_found
                break

        if not search_result:
            err_msg = f"No video found for '{message['Name']}'"
            raise WebhookWorkerError(err_msg)

        if len(search_result) != 1:
            err_msg = f"Found more than one video for '{message['Name']}'"
            raise WebhookWorkerError(err_msg)

        return search_result[0]
