import pathlib
from urllib.parse import urljoin, urlparse, urlunparse

import requests

from workers import utils
from workers.config import JELLYFIN_API_KEY, JELLYFIN_PORT, STANDUP_PATH
from workers.logger import logger
from workers.movie import Movie


class GenreModificationError(utils.WebhookWorkerError):
    """Exception raised for errors in the GenreModificationService."""


class GenreModificationService:
    """Service to modify genres for stand-up comedy media."""

    def __init__(self, movie: Movie, jellyfin_url: str, api_key: str, item_id: str) -> None:
        """Initialize the GenreModificationService.

        Args:
            movie (Movie): The movie to modify genres for.
            jellyfin_url (str): The Jellyfin server URL.
            api_key (str): The Jellyfin API key.
            item_id (str): The Jellyfin item ID.

        Returns:
            None
        """
        self.movie = movie
        self.jellyfin_url = jellyfin_url
        self.api_key = api_key
        self.item_id = item_id

    def exec(self) -> None:
        """Execute the genre modification process.

        Returns:
            None
        """
        logger.info(f"Beginning genre modification for {self.movie.full_title}...")

        # Check if this is a stand-up comedy file
        if not self.is_standup_file():
            logger.info(f"Not a stand-up comedy file: {self.movie.full_path}. Skipping.")
            return

        # Update the genres
        self.update_genres()
        logger.info(f"Genre modification complete for '{self.movie.full_title}'")

    def is_standup_file(self) -> bool:
        """Check if the file is in the stand-up directory.

        Returns:
            bool: True if the file is in the stand-up directory, False otherwise.
        """
        standup_path = pathlib.Path(STANDUP_PATH)
        file_path = pathlib.Path(self.movie.full_path)

        return str(file_path).startswith(str(standup_path))

    def update_genres(self) -> None:
        """Update the genres for the media item.

        Returns:
            None
        """
        headers = {"X-Emby-Token": self.api_key, "Content-Type": "application/json"}

        # Create request URL
        api_url = urljoin(self.jellyfin_url, f"/Items/{self.item_id}")

        # Prepare new genres data
        data = {"Genres": ["Stand-Up"]}

        # Send request to Jellyfin API
        try:
            response = requests.post(api_url, headers=headers, json=data)
            response.raise_for_status()
            logger.info(f"Successfully updated genres for {self.movie.full_title}")
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to update genres: {e}")
            raise GenreModificationError(
                f"Failed to update genres for '{self.movie.full_title}'"
            ) from e

    @classmethod
    def from_message(cls, message: dict) -> "GenreModificationService":
        """Create a GenreModificationService from a Jellyfin webhook message.

        Args:
            message (dict): The Jellyfin webhook message.

        Returns:
            GenreModificationService: The initialized GenreModificationService.
        """
        movie_file = utils.file_from_message(message)
        movie = Movie.from_file(movie_file)

        # Extract necessary information from the webhook
        jellyfin_url = message.get("ServerUrl", "")

        # Ensure URL has a protocol
        if not jellyfin_url.startswith(("http://", "https://")):
            jellyfin_url = f"http://{jellyfin_url}"

        # Ensure URL has the correct port
        parsed_url = urlparse(jellyfin_url)

        # If no port is specified, add the default port
        if not parsed_url.port:
            netloc = f"{parsed_url.netloc}:{JELLYFIN_PORT}"
            jellyfin_url = urlunparse(
                (
                    parsed_url.scheme,
                    netloc,
                    parsed_url.path,
                    parsed_url.params,
                    parsed_url.query,
                    parsed_url.fragment,
                )
            )

        api_key = JELLYFIN_API_KEY
        item_id = message.get("ItemId", "")

        return cls(movie, jellyfin_url, api_key, item_id)
