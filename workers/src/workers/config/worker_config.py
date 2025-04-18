import json
import pathlib
from typing import Any

import yaml

from workers import utils
from workers.parsers import MovieNamingScheme


class WorkerConfig(metaclass=utils.SingletonMeta):
    """A singleton class to manage webhook configurations.

    This class is user-driven via a configuration file.
    """

    config = {}

    def get_enabled_webhooks(self) -> dict:
        """Get a list of enabled webhooks.

        Returns:
            A list of enabled webhook IDs.
        """
        webhooks = self.config.get("webhooks", {})
        return {
            webhook_id: config
            for webhook_id, config in webhooks.items()
            if config.get("enabled", False)
        }

    def get_enabled_services(self, webhook_id: str) -> list[dict[str, Any]]:
        """Get enabled services for a webhook.

        Args:
            webhook_id: The ID of the webhook to get services for.

        Returns:
            A list of enabled service configurations, sorted by priority.
        """
        webhook_config = self.get_webhook_config(webhook_id)

        # If webhook is disabled, return empty list
        if not webhook_config.get("enabled", False):
            return []

        services = webhook_config.get("services", [])

        # Filter enabled services and sort by priority
        enabled_services = [service for service in services if service.get("enabled", True)]

        return sorted(enabled_services, key=lambda s: s.get("priority", 100))

    def get_webhook_config(self, webhook_id: str) -> dict[str, Any]:
        """Get configuration for a specific webhook.

        Args:
            webhook_id: The ID of the webhook to get configuration for.

        Returns:
            The webhook configuration, or an empty dict if not found.
        """
        return self.config.get("webhooks", {}).get(webhook_id, {})

    def get_service_config(self, webhook_id: str, service_name: str) -> dict[str, Any]:
        """Get configuration for a specific service within a webhook.

        Args:
            webhook_id: The ID of the webhook.
            service_name: The name of the service.

        Returns:
            The service configuration, or an empty dict if not found.
        """
        webhook_config = self.get_webhook_config(webhook_id)
        services = webhook_config.get("services", [])

        for service in services:
            if service.get("name") == service_name:
                return service.get("config", {})

        return {}

    def get_general_config(self) -> dict[str, Any]:
        """Get the general configuration.

        Returns:
            The general configuration.
        """
        return self.config.get("general", {})

    def get_naming_scheme(self, item_type: str) -> MovieNamingScheme:
        """Get the naming configuration for the worker.

        Args:
            item_type (str): The type of item (e.g., "movie", "episode").

        Returns:
            The naming configuration.
        """
        general_config = self.get_general_config()
        schemes = general_config.get("naming_schemes", {})
        return schemes.get(item_type, "fallback")

    def _set_worker_config(self, config: dict[str, Any]) -> None:
        """Set the worker configuration."""
        self.config = config

    @classmethod
    def load(cls, config_path: str) -> "WorkerConfig":
        """Load the configuration from the config file."""
        worker = cls()
        jellyhook_config = load_config_file(config_path)
        worker_config = jellyhook_config.get("worker", {})
        worker._set_worker_config(worker_config)
        return worker


def load_config_file(filename: str) -> dict:
    """Load a JSON or YAML configuration file.

    Args:
        filename (str): The path to the configuration file.

    Returns:
        dict: The loaded configuration data.
    """
    default = {}

    try:
        config_path = pathlib.Path(filename)
        if not config_path.exists():
            return default

        with open(config_path, "r") as f:
            if config_path.suffix.lower() in [".yml", ".yaml"]:
                return yaml.safe_load(f)
            return json.load(f)
    except (json.JSONDecodeError, yaml.YAMLError, IOError):
        return default
