import pathlib
import re
from typing import Any

from jellyfin_apiclient_python.exceptions import HTTPException

from workers.clients.jellyfin import client
from workers.errors import WebhookWorkerError
from workers.logger import logger
from workers.movie import Movie
from workers.services.service_base import ServiceBase


class MetadataUpdateError(WebhookWorkerError):
    """Exception raised for errors in the MetadataUpdateService."""


class MetadataUpdateService(ServiceBase):
    """Service to modify metadata (genres and tags) for media based on configured rules."""

    def __init__(
        self,
        movie: Movie,
        item_id: str,
        path_rules: list[str] = None,
        pattern_rules: list[str] = None,
        original_genres: list[str] = None,
        original_tags: list[str] = None,
        item_data: dict[str, Any] = None,
    ) -> None:
        """Initialize the MetadataUpdateService.

        Args:
            movie (Movie): The movie to modify metadata for.
            item_id (str): The Jellyfin item ID.
            path_rules (List[str], optional): List of path-based rules. Defaults to [].
            pattern_rules (List[str], optional): List of pattern-based rules. Defaults to [].
            original_genres (List[str], optional): The original genres from the media.
            original_tags (List[str], optional): The original tags from the media.
            item_data (Dict[str, Any], optional): Additional item metadata from webhook.

        Returns:
            None
        """
        self.movie = movie
        self.item_id = item_id
        self.path_rules = path_rules or []
        self.pattern_rules = pattern_rules or []
        self.original_genres = original_genres or []
        self.original_tags = original_tags or []
        self.item_data = item_data or {}
        self.matching_rules = []

    def exec(self) -> None:
        """Execute the metadata update process.

        Returns:
            None
        """
        logger.info(f"Beginning metadata update for {self.movie.full_title}...")

        # Find all matching rules
        self.find_matching_rules()

        if not self.matching_rules:
            logger.info(f"No metadata matching rules found for: {self.movie.full_path}. Skipping.")
            return

        # Update the metadata
        self.update_metadata()
        logger.info(f"Success! Metadata update complete for '{self.movie.full_title}'")

    def find_matching_rules(self) -> None:
        """Find all rules that match this media item.

        Updates self.matching_rules with all matching rules.
        """
        # First check path-based rules
        file_path = pathlib.Path(self.movie.full_path)

        for path_rule in self.path_rules:
            rule_path = pathlib.Path(path_rule["path"])
            if str(file_path).startswith(str(rule_path)):
                self.matching_rules.append(path_rule)
                logger.info(f"Matched path rule: {rule_path}")

        # Then check pattern-based rules
        for pattern_rule in self.pattern_rules:
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

    def calculate_new_genres(self) -> list[str]:
        """Calculate the new genres based on matching rules and original genres.

        Returns:
            List[str]: The new genres to apply
        """
        new_genres = self.original_genres.copy()

        for rule in self.matching_rules:
            genre_config = rule.get("genres", {})
            if not genre_config:
                continue

            new_rule_genres = genre_config.get("new_genres", [])
            replace_existing = genre_config.get("replace_existing", False)

            if replace_existing:
                # This rule completely replaces existing genres
                new_genres = new_rule_genres
            else:
                # This rule adds new genres to existing ones
                for genre in new_rule_genres:
                    if genre not in new_genres:
                        new_genres.append(genre)

        return new_genres

    def calculate_new_tags(self) -> list[str]:
        """Calculate the new tags based on matching rules and original tags.

        Returns:
            List[str]: The new tags to apply
        """
        new_tags = self.original_tags.copy()

        for rule in self.matching_rules:
            tag_config = rule.get("tags", {})
            if not tag_config:
                continue

            new_rule_tags = tag_config.get("new_tags", [])
            replace_existing = tag_config.get("replace_existing", False)

            if replace_existing:
                # This rule completely replaces existing tags
                new_tags = new_rule_tags
            else:
                # This rule adds new tags to existing ones
                for tag in new_rule_tags:
                    if tag not in new_tags:
                        new_tags.append(tag)

        return new_tags

    def update_metadata(self) -> None:
        """Update the metadata (genres and tags) for the media item.

        Returns:
            None
        """
        # Calculate new genres and tags
        new_genres = self.calculate_new_genres()
        new_tags = self.calculate_new_tags()

        # If no changes needed, skip
        if sorted(new_genres) == sorted(self.original_genres) and sorted(new_tags) == sorted(
            self.original_tags
        ):
            logger.info(f"Metadata already correct for {self.movie.full_title}, skipping update")
            return

        # Prepare data for the request
        data = {}

        if sorted(new_genres) != sorted(self.original_genres):
            data["Genres"] = new_genres
            logger.info(f"Updating genres for {self.movie.full_title}")
            logger.info(f"New genres: {new_genres}")

        if sorted(new_tags) != sorted(self.original_tags):
            data["Tags"] = new_tags
            logger.info(f"Updating tags for {self.movie.full_title}")
            logger.info(f"New tags: {new_tags}")

        # Send request to Jellyfin API if there are changes to make
        if data:
            try:
                client.jellyfin.update_item(self.item_id, data)
                logger.info(f"Successfully updated metadata for {self.movie.full_title}")
            except HTTPException as e:
                logger.error(f"Failed to update metadata: {e}")
                raise MetadataUpdateError(
                    f"Failed to update metadata for '{self.movie.full_title}'"
                ) from e

    @classmethod
    def from_message(cls, message: dict, service_config: dict[str, Any]) -> "MetadataUpdateService":
        """Create a MetadataUpdateService from a Jellyfin webhook message.

        Args:
            message (dict): The Jellyfin webhook message.
            service_config (dict): The configuration for the metadata update service.

        Returns:
            MetadataUpdateService: The initialized MetadataUpdateService.
        """
        movie_file = cls.file_from_message(message)
        movie = Movie.from_file(movie_file)

        item_id = message.get("ItemId", "")
        path_rules = service_config.get("paths", [])
        pattern_rules = service_config.get("patterns", [])

        # Extract original genres
        original_genres = []
        if "Genres" in message:
            genres_str = message.get("Genres", "")
            if genres_str:
                original_genres = [g.strip() for g in genres_str.split(",")]

        # Extract original tags
        original_tags = []
        if "Tags" in message:
            tags_str = message.get("Tags", "")
            if tags_str:
                original_tags = [t.strip() for t in tags_str.split(",")]

        return cls(
            movie=movie,
            item_id=item_id,
            path_rules=path_rules,
            pattern_rules=pattern_rules,
            original_genres=original_genres,
            original_tags=original_tags,
            item_data=message,
        )
