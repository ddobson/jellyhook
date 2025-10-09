"""Service orchestration module.

This module provides a class for orchestrating the execution of services
based on configuration for a specific webhook.

Service registration quick reference:
1. Implement a `ServiceBase` subclass with both `exec()` and `from_message()`
   factory methods so the orchestrator can build and run it.
    - Use `ServiceBase.file_from_message()` helpers and follow the patterns in
      `metadata_update.py` or `dovi_conversion.py` as guides.
2. Register the service name in the maps below:
    - `SERVICE_MODULES` maps the new service's config name to its module path.
    - `SERVICE_CLASSES` maps the same name to the concrete class identifier in that module.
    - The name must match the `service["name"]` value supplied in configuration.
3. Add the service to the worker configuration YAML/JSON file.
    - JSON / YAML schema target (`worker.webhooks[<id>].services`):
    - Include `enabled`, optional `priority` (lower values run first), and a `config` block.
    - At runtime `WorkerConfig.get_enabled_services()` filters the list and passes the
      `config` dict into `from_message()` so the service can read its options.
4. (Optional) Expose the class from `workers.services.__init__` if other modules import it
   directly; the orchestrator only depends on the registry above.
"""

import importlib
import json
from pathlib import Path
from typing import Any

from workers import utils
from workers.config import WorkerConfig
from workers.logger import logger
from workers.services.service_base import ServiceBase

# Service name to module/class mappings
SERVICE_MODULES = {
    "metadata_update": "workers.services.metadata_update",
    "dovi_conversion": "workers.services.dovi_conversion",
    "media_track_clean": "workers.services.media_track_clean",
    "playlist_assignment": "workers.services.playlist_assignment",
}

SERVICE_CLASSES = {
    "metadata_update": "MetadataUpdateService",
    "dovi_conversion": "DoviConversionService",
    "media_track_clean": "MediaTrackCleanService",
    "playlist_assignment": "PlaylistAssignmentService",
}


class ServiceOrchestrator:
    """Service orchestrator for webhook handling.

    This class is responsible for managing the execution of services
    configured for a specific webhook.
    """

    def __init__(self, webhook_id: str) -> None:
        """Initialize the service orchestrator.

        Args:
            webhook_id: The ID of the webhook to orchestrate services for.
        """
        self.webhook_id = webhook_id
        self.temp_dirs: set[Path] = set()
        self.worker_config = WorkerConfig()

    def is_webhook_enabled(self) -> bool:
        """Check if the webhook is enabled in configuration.

        Returns:
            True if the webhook is enabled, False otherwise.
        """
        return self.webhook_id in self.worker_config.get_enabled_webhooks()

    def get_enabled_services(self) -> list[dict[str, Any]]:
        """Get the enabled services for this webhook.

        Returns:
            A list of enabled service configurations, sorted by priority.
        """
        enabled_services = self.worker_config.get_enabled_services(self.webhook_id)
        return sorted(enabled_services, key=lambda s: s.get("priority", 100))

    def create_service_instance(
        self, service_name: str, message: dict[str, Any], service_config: dict[str, Any]
    ) -> ServiceBase | None:
        """Create an instance of a service.

        Args:
            service_name (str): The name of the service to create.
            message (dict[str, Any]): The webhook message to process.
            service_config (dict[str, Any]): The configuration for the service.

        Returns:
            An instance of the service, or None if the service could not be created.
        """
        try:
            if service_name not in SERVICE_MODULES:
                logger.error(f"Unknown service: {service_name}")
                return None

            # Import the module
            module_path = SERVICE_MODULES[service_name]
            module = importlib.import_module(module_path)

            # Get the service class
            class_name = SERVICE_CLASSES[service_name]
            service_class: type[ServiceBase] = getattr(module, class_name)

            # Create the service instance
            return service_class.from_message(message, service_config)

        except (ImportError, AttributeError) as e:
            logger.error(f"Failed to import service {service_name}: {e}")
            return None

    def process_webhook(self, message: dict[str, Any]) -> bool:
        """Process a webhook message with configured services.

        Args:
            message: The webhook message to process.

        Returns:
            True if all services completed successfully, False otherwise.
        """
        # Skip processing if this webhook is disabled in config
        if not self.is_webhook_enabled():
            logger.info(f"Webhook {self.webhook_id} is disabled, skipping processing")
            return True

        # Get the enabled services for this webhook
        enabled_services = self.get_enabled_services()
        if not enabled_services:
            logger.info(f"No enabled services configured for webhook {self.webhook_id}")
            return True

        # Process each service in priority order
        completed = True
        for enabled_service in enabled_services:
            service_name = enabled_service.get("name")
            service_config = enabled_service.get("config", {})
            logger.info(f"Processing service {service_name} for webhook {self.webhook_id}")

            try:
                # Dynamically import and instantiate the service
                service = self.create_service_instance(service_name, message, service_config)
                if not service:
                    # This could be an error or a deliberate skip based on service configuration
                    logger.info(
                        f"Service {service_name} was not created for this message - skipping"
                    )
                    continue

                # Execute the service
                service.exec()

                # Track temporary directory for cleanup if available
                if hasattr(service, "tmp_dir"):
                    self.temp_dirs.add(service.tmp_dir)

            except Exception as e:
                logger.error(f"Service {service_name} failed: {e}")

                # Only mark the webhook as failed if this is a critical service
                # In this implementation, we're keeping the existing behavior where
                # metadata failures don't fail the entire process
                if service_name != "metadata_update":
                    completed = False

        return completed

    def cleanup(self) -> None:
        """Clean up temporary directories created by services."""
        for tmp_dir in self.temp_dirs:
            try:
                logger.info(f"Cleaning temporary files for '{tmp_dir}'...")
                utils.clean_dir(tmp_dir)
            except (FileNotFoundError, PermissionError) as e:
                logger.error(f"Failed to clean temporary directory {tmp_dir}: {e}")


def process_webhook_message(webhook_id: str, message_body: bytes) -> bool:
    """Process a webhook message.

    This function is a convenience wrapper around the ServiceOrchestrator
    for handling webhook messages.

    Args:
        webhook_id: The ID of the webhook.
        message_body: The raw message body.

    Returns:
        True if the message was successfully processed, False otherwise.
    """
    try:
        # Parse the message
        message = json.loads(message_body)

        # Create the orchestrator
        orchestrator = ServiceOrchestrator(webhook_id)

        # Process the webhook
        result = orchestrator.process_webhook(message)

        # Clean up
        orchestrator.cleanup()

        return result
    except Exception as e:
        logger.error(f"Failed to process webhook {webhook_id}: {e}")
        return False
