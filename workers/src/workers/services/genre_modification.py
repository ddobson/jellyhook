import pathlib
import re
from typing import Any, Dict, List
from urllib.parse import urljoin, urlparse, urlunparse

import requests

from workers.config import GENRE_MAPPINGS, JELLYFIN_API_KEY, JELLYFIN_PORT
from workers.errors import WebhookWorkerError
from workers.logger import logger
from workers.movie import Movie
from workers.services.service_base import ServiceBase


class GenreModificationError(WebhookWorkerError):
    """Exception raised for errors in the GenreModificationService."""


class GenreModificationService(ServiceBase):
    """Service to modify genres for media based on configured rules."""

    def __init__(
        self,
        movie: Movie,
        jellyfin_url: str,
        api_key: str,
        item_id: str,
        original_genres: List[str] = None,
        item_data: Dict[str, Any] = None,
    ) -> None:
        """Initialize the GenreModificationService.

        Args:
            movie (Movie): The movie to modify genres for.
            jellyfin_url (str): The Jellyfin server URL.
            api_key (str): The Jellyfin API key.
            item_id (str): The Jellyfin item ID.
            original_genres (List[str], optional): The original genres from the media.
            item_data (Dict[str, Any], optional): Additional item metadata from webhook.

        Returns:
            None
        """
        self.movie = movie
        self.jellyfin_url = jellyfin_url
        self.api_key = api_key
        self.item_id = item_id
        self.original_genres = original_genres or []
        self.item_data = item_data or {}
        self.matching_rules = []

    def exec(self) -> None:
        """Execute the genre modification process.

        Returns:
            None
        """
        logger.info(f"Beginning genre modification for {self.movie.full_title}...")

        # Find all matching rules
        self.find_matching_rules()

        if not self.matching_rules:
            logger.info(f"No genre matching rules found for: {self.movie.full_path}. Skipping.")
            return

        # Update the genres
        self.update_genres()
        logger.info(f"Genre modification complete for '{self.movie.full_title}'")

    def find_matching_rules(self) -> None:
        """Find all rules that match this media item.

        Updates self.matching_rules with all matching rules.
        """
        # First check path-based rules
        file_path = pathlib.Path(self.movie.full_path)

        for path_rule in GENRE_MAPPINGS.get("paths", []):
            rule_path = pathlib.Path(path_rule["path"])
            if str(file_path).startswith(str(rule_path)):
                self.matching_rules.append(path_rule)
                logger.info(f"Matched path rule: {rule_path}")

        # Then check pattern-based rules
        for pattern_rule in GENRE_MAPPINGS.get("rules", []):
            field_name = pattern_rule.get("match_field", "Name")
            pattern = pattern_rule.get("match_pattern", "")
            case_insensitive = pattern_rule.get("case_insensitive", True)

            # Get the value to match against
            field_value = self.item_data.get(field_name, "")

            # Compile regex pattern
            flags = re.IGNORECASE if case_insensitive else 0
            regex = re.compile(pattern, flags)

            # Check for match
            if regex.search(field_value):
                self.matching_rules.append(pattern_rule)
                logger.info(f"Matched pattern rule: {pattern} against {field_name}")

    def calculate_new_genres(self) -> List[str]:
        """Calculate the new genres based on matching rules and original genres.

        Returns:
            List[str]: The new genres to apply
        """
        new_genres = self.original_genres.copy()

        for rule in self.matching_rules:
            rule_genres = rule.get("genres", [])
            replace_existing = rule.get("replace_existing", False)

            if replace_existing:
                # This rule completely replaces existing genres
                new_genres = rule_genres
            else:
                # This rule adds new genres to existing ones
                for genre in rule_genres:
                    if genre not in new_genres:
                        new_genres.append(genre)

        return new_genres

    def update_genres(self) -> None:
        """Update the genres for the media item.

        Returns:
            None
        """
        headers = {"Authorization": self.api_key, "Content-Type": "application/json"}

        # Calculate new genres
        new_genres = self.calculate_new_genres()

        # If no changes needed, skip
        if sorted(new_genres) == sorted(self.original_genres):
            logger.info(f"Genres already correct for {self.movie.full_title}, skipping update")
            return

        # Create request URL - using the Jellyfin API endpoint
        api_url = urljoin(self.jellyfin_url, f"/Items/{self.item_id}")

        # Prepare data for the request - don't include item ID in body
        data = {"Genres": new_genres}

        # Send request to Jellyfin API
        try:
            response = requests.post(api_url, headers=headers, json=data)
            response.raise_for_status()
            logger.info(f"Successfully updated genres for {self.movie.full_title}")
            logger.info(f"New genres: {new_genres}")
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
        movie_file = cls.file_from_message(message)
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

        # Extract original genres
        original_genres = []
        if "Genres" in message:
            genres_str = message.get("Genres", "")
            if genres_str:
                original_genres = [g.strip() for g in genres_str.split(",")]

        return cls(
            movie=movie,
            jellyfin_url=jellyfin_url,
            api_key=api_key,
            item_id=item_id,
            original_genres=original_genres,
            item_data=message,
        )
