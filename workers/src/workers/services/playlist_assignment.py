from __future__ import annotations

from typing import Any, Iterable

import requests

from workers.clients.jellyfin import get_client
from workers.errors import WebhookWorkerError, is_transient
from workers.job import Job
from workers.logger import logger
from workers.models.item import Item
from workers.services.service_base import ServiceBase


class PlaylistAssignmentError(WebhookWorkerError):
    """Exception raised for errors in the PlaylistAssignmentService."""


class PlaylistAssignmentService(ServiceBase):
    """Service that adds media items to playlists when rule conditions are met."""

    def __init__(
        self,
        item_id: str,
        message: dict[str, Any],
        rules: list[dict[str, Any]],
        jellyfin_client: Any = None,
    ) -> None:
        """Initialize the playlist assignment service with webhook data and rules."""
        self.item_id = item_id
        self.message = message
        self.rules = rules
        # None until first used: a message with no work to do must not need Jellyfin up
        self._jellyfin_client = jellyfin_client
        self._item_details: dict[str, Any] | None = None

    @property
    def _client(self) -> Any:
        """The Jellyfin client, connected on first use rather than when the service is built."""
        if self._jellyfin_client is None:
            self._jellyfin_client = get_client()
        return self._jellyfin_client

    @property
    def _api(self) -> Any:
        """The Jellyfin API wrapper of the client."""
        return self._client.jellyfin

    def exec(self) -> None:
        """Evaluate rules and add the item to configured playlists."""
        if not self.item_id:
            raise PlaylistAssignmentError("Webhook payload does not contain an ItemId")

        if not self.rules:
            logger.info("PlaylistAssignmentService has no rules configured. Skipping.")
            return

        item_details = self._load_item_details()
        runtime_minutes = Item.from_message(item_details).runtime_minutes
        matches = 0

        for rule in self.rules:
            if not isinstance(rule, dict):
                logger.warning("Ignoring playlist rule because it is not a mapping: {}", rule)
                continue

            if self._rule_matches(rule, item_details, runtime_minutes):
                self._add_to_playlist(rule)
                matches += 1

        if matches == 0:
            logger.info("No playlist rules matched for item {}", self.item_id)
        else:
            logger.info("Applied {} playlist rule(s) for item {}", matches, self.item_id)

    @classmethod
    def from_message(cls, job: Job, service_config: dict[str, Any]) -> PlaylistAssignmentService:
        """Create a PlaylistAssignmentService from a message's job and config.

        This service never touches the media file, so it only reads the job's message and item.
        """
        rules = service_config.get("rules", [])
        if not isinstance(rules, list):
            raise PlaylistAssignmentError(
                "Playlist service configuration must include a list of rules"
            )

        return cls(item_id=job.item.item_id or "", message=job.message, rules=rules)

    def _load_item_details(self) -> dict[str, Any]:
        """Return webhook details enriched with Jellyfin metadata if required."""
        if self._item_details is not None:
            return self._item_details

        details = dict(self.message)
        embedded = details.get("Item")
        if isinstance(embedded, dict):
            details.update(embedded)

        self._item_details = details

        if not self._has_sufficient_metadata(details):
            self._enrich_item_details()

        return self._item_details

    def _has_sufficient_metadata(self, details: dict[str, Any]) -> bool:
        """Return True when the webhook payload already contains required fields."""
        item = Item.from_message(details)
        return (
            item.runtime_minutes is not None
            and bool(item.genres)
            and bool(item.tags)
            and item.year is not None
        )

    def _enrich_item_details(self) -> None:
        """Fetch additional item metadata from Jellyfin when needed.

        Raises:
            PlaylistAssignmentError: If Jellyfin could not be reached (retryable, through its
                cause). Carrying on without the metadata would evaluate every rule against
                missing fields and report "no rule matched" as a success.
        """
        try:
            fetch_fn = getattr(self._api, "get_item", None)
            if not callable(fetch_fn):
                logger.debug("Jellyfin client does not expose get_item; skipping enrichment.")
                return
            fetched = fetch_fn(self.item_id)
        except Exception as exc:
            if is_transient(exc):
                raise PlaylistAssignmentError(
                    f"Jellyfin was unavailable while loading item {self.item_id}"
                ) from exc
            logger.error(
                "Failed to load Jellyfin item {} for playlist rules: {}", self.item_id, exc
            )
            return

        if isinstance(fetched, dict) and self._item_details is not None:
            self._item_details.update(fetched)

    def _rule_matches(
        self,
        rule: dict[str, Any],
        item_details: dict[str, Any],
        runtime_minutes: float | None,
    ) -> bool:
        """Return True if the rule's conditions match the provided item metadata."""
        conditions = rule.get("conditions", {})
        if not isinstance(conditions, dict):
            logger.warning("Playlist rule missing conditions mapping: {}", rule)
            return False

        item = Item.from_message(item_details)
        allowed_types = self._normalize_str_iterable(conditions.get("item_types"))
        if allowed_types and (item.item_type or "").lower() not in allowed_types:
            return False

        runtime_minutes = runtime_minutes or item.runtime_minutes
        if not self._evaluate_runtime(conditions, runtime_minutes):
            return False

        if not self._evaluate_release_year(conditions, item.year):
            return False

        required_genres = self._normalize_str_iterable(conditions.get("required_genres"))
        if required_genres:
            genres = self._normalize_str_iterable(item.genres)
            if not genres or not required_genres.issubset(genres):
                return False

        required_tags = self._normalize_str_iterable(conditions.get("required_tags"))
        if required_tags:
            tags = self._normalize_str_iterable(item.tags)
            if not tags or not required_tags.issubset(tags):
                return False

        excluded_tags = self._normalize_str_iterable(conditions.get("excluded_tags"))
        if excluded_tags:
            tags = self._normalize_str_iterable(item.tags)
            if tags and tags.intersection(excluded_tags):
                return False

        return True

    def _evaluate_runtime(self, conditions: dict[str, Any], runtime_minutes: float | None) -> bool:
        """Validate min/max runtime constraints for the current rule."""
        max_runtime = self._to_float(conditions.get("max_runtime_minutes"))
        min_runtime = self._to_float(conditions.get("min_runtime_minutes"))

        if max_runtime is None and min_runtime is None:
            return True

        if runtime_minutes is None:
            logger.warning(
                "Unable to evaluate runtime-based playlist rule for item {} because runtime is missing",  # noqa: E501
                self.item_id,
            )
            return False

        if max_runtime is not None and runtime_minutes > max_runtime:
            return False

        if min_runtime is not None and runtime_minutes < min_runtime:  # noqa: SIM103
            return False

        return True

    def _evaluate_release_year(self, conditions: dict[str, Any], release_year: int | None) -> bool:
        """Validate min/max release year constraints for the current rule."""
        max_year = self._to_int(conditions.get("max_release_year"))
        min_year = self._to_int(conditions.get("min_release_year"))

        if max_year is None and min_year is None:
            return True

        if release_year is None:
            logger.warning(
                "Unable to evaluate release year rule for item {} because year is missing",
                self.item_id,
            )
            return False

        if max_year is not None and release_year > max_year:
            return False

        if min_year is not None and release_year < min_year:  # noqa: SIM103
            return False

        return True

    def _add_to_playlist(self, rule: dict[str, Any]) -> None:
        """Add the current item to the playlist referenced by the rule."""
        playlist_id = rule.get("playlist_id")
        if not playlist_id:
            logger.warning("Playlist rule does not specify playlist_id: {}", rule)
            return

        try:
            self._post_playlist_items(playlist_id)
        except requests.HTTPError as exc:
            raise PlaylistAssignmentError(
                f"Jellyfin API returned an error while adding item to playlist {playlist_id}"
            ) from exc
        except requests.RequestException as exc:
            raise PlaylistAssignmentError(
                f"Failed to communicate with Jellyfin while adding playlist {playlist_id}"
            ) from exc
        except Exception as exc:  # noqa: BLE001 - capture unexpected client failures
            raise PlaylistAssignmentError(
                f"Unexpected error while adding item to playlist {playlist_id}"
            ) from exc
        else:
            playlist_name = rule.get("playlist_name", playlist_id)
            logger.info("Item {} is in playlist '{}'", self.item_id, playlist_name)

    def _post_playlist_items(self, playlist_id: str) -> None:
        """Add the item to a playlist using the Jellyfin HTTP API, unless it is already there.

        Adding is not idempotent (Jellyfin allows duplicate entries), and a retry or redelivery
        re-runs every service, so membership is checked first.
        """
        endpoint = f"Playlists/{playlist_id}/Items"
        user_id = self._get_user_id()  # Retrieve the user ID from the context or configuration
        if not user_id:
            raise PlaylistAssignmentError("User ID is required to add items to a playlist.")

        http_client = getattr(self._client, "http", None)
        http_request = getattr(http_client, "request", None)
        if not callable(http_request):
            raise PlaylistAssignmentError(
                f"Failed to send request to add item to playlist {playlist_id}"
            )

        existing = http_request({"type": "GET", "handler": endpoint, "params": {"UserId": user_id}})
        if self._playlist_contains_item(existing):
            logger.info("Item {} is already in playlist {}", self.item_id, playlist_id)
            return

        http_request(
            {
                "type": "POST",
                "handler": endpoint,
                "params": {"Ids": self.item_id, "UserId": user_id},
            }
        )

    def _playlist_contains_item(self, response: Any) -> bool:
        """Whether a `GET Playlists/{id}/Items` response already lists the current item.

        Jellyfin item ids are GUIDs that may be written with or without dashes, so they are
        compared in a normalised form. An unexpected response counts as "not there".
        """
        if not isinstance(response, dict):
            return False
        items = response.get("Items")
        if not isinstance(items, list):
            return False
        wanted = self._normalize_item_id(self.item_id)
        return any(
            isinstance(item, dict) and self._normalize_item_id(item.get("Id")) == wanted
            for item in items
        )

    @staticmethod
    def _normalize_item_id(item_id: Any) -> str:
        """Lowercase a Jellyfin item id and drop its dashes."""
        return str(item_id or "").replace("-", "").lower()

    @staticmethod
    def _to_float(value: Any) -> float | None:
        """Attempt to coerce a value into a float, returning None on failure."""
        if value is None or value == "":
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _to_int(value: Any) -> int | None:
        """Attempt to coerce a value into an integer, returning None on failure."""
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _normalize_str_iterable(value: Any) -> set[str]:
        """Convert user-supplied values into a lowercase set of strings."""
        if value is None:
            return set()
        if isinstance(value, str):
            items = [value]
        elif isinstance(value, Iterable):
            items = list(value)
        else:
            items = [value]
        normalized = {str(item).strip().lower() for item in items if str(item).strip()}
        return normalized

    def _get_user_id(self) -> str | None:
        """Retrieve the user ID from the Jellyfin client configuration."""
        user_id: str | None = self._client.config.data.get("auth.user_id")
        if not user_id:
            logger.error(
                "User ID is not configured or available in the Jellyfin client configuration."
            )
        return user_id
