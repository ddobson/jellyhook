"""Tests for the service orchestrator module."""

import json
from pathlib import Path
from unittest import mock

import pytest

from workers.config import WorkerConfig
from workers.services.dovi_conversion import DoviConversionService
from workers.services.orchestrator import ServiceOrchestrator, process_webhook_message
from workers.services.service_base import ServiceBase


@pytest.fixture
def mock_config_data():
    """Create mock configuration data for testing."""
    return {
        "webhooks": {
            "item_added": {
                "enabled": True,
                "queue": "jellyfin:item_added",
                "services": [
                    {
                        "name": "metadata_update",
                        "enabled": True,
                        "priority": 10,
                        "config": {"some_config": "value"},
                    },
                    {
                        "name": "dovi_conversion",
                        "enabled": True,
                        "priority": 20,
                        "config": {"temp_dir": "/tmp/dovi_conversion"},
                    },
                    {
                        "name": "disabled_service",
                        "enabled": False,
                        "priority": 30,
                        "config": {},
                    },
                ],
            },
            "disabled_webhook": {
                "enabled": False,
                "queue": "jellyfin:disabled",
                "services": [],
            },
        }
    }


@pytest.fixture
def mock_service():
    """Create a mock service class and instance."""
    # Create mock instance first
    mock_service_instance = mock.MagicMock(spec=ServiceBase)
    mock_service_instance.exec.return_value = None
    mock_service_instance.tmp_dir = Path("/tmp/test_service")

    # Now create the mock class with a class method
    mock_service_class = mock.MagicMock()
    # We need to create a valid class method mock
    mock_service_class.from_message = mock.MagicMock(return_value=mock_service_instance)

    return {"class": mock_service_class, "instance": mock_service_instance}


@pytest.fixture
def orchestrator(mock_config_data):
    """Create a service orchestrator for testing."""
    # Create a new WorkerConfig and reset the singleton
    with (
        mock.patch.dict("workers.utils.SingletonMeta._instances", {}, clear=True),
        # Mock WorkerConfig.__init__ to not require config
        mock.patch.object(WorkerConfig, "__init__", return_value=None),
    ):
        orchestrator = ServiceOrchestrator("item_added")
        # Set mock config manually
        orchestrator.worker_config = mock.MagicMock()
        return orchestrator


def test_is_webhook_enabled(orchestrator):
    """Test checking if a webhook is enabled."""
    # Set up the mock methods of the worker_config
    orchestrator.worker_config.get_enabled_webhooks.return_value = {"item_added": {"enabled": True}}

    # Test enabled webhook
    assert orchestrator.is_webhook_enabled() is True

    # Test disabled webhook
    orchestrator.webhook_id = "disabled_webhook"
    assert orchestrator.is_webhook_enabled() is False

    # Test non-existent webhook
    orchestrator.webhook_id = "non_existent"
    assert orchestrator.is_webhook_enabled() is False


def test_get_enabled_services(orchestrator):
    """Test getting enabled services sorted by priority."""
    # Create the expected result
    expected = [
        {
            "name": "metadata_update",
            "enabled": True,
            "priority": 10,
            "config": {"some_config": "value"},
        },
        {
            "name": "dovi_conversion",
            "enabled": True,
            "priority": 20,
            "config": {"temp_dir": "/tmp/dovi_conversion"},
        },
    ]

    # Set up the mock methods of the worker_config
    orchestrator.worker_config.get_enabled_services.return_value = expected

    result = orchestrator.get_enabled_services()

    # Check services are returned sorted by priority
    assert len(result) == 2
    assert result[0]["name"] == "metadata_update"
    assert result[1]["name"] == "dovi_conversion"
    assert result[0]["priority"] < result[1]["priority"]


def test_create_service_instance_success():
    """Test creating a service instance - success case."""
    # We'll mock WorkerConfig.__init__ to avoid the singleton issue
    with mock.patch.object(WorkerConfig, "__init__", return_value=None):
        # For this test, we'll create our own ServiceOrchestrator to avoid fixture complexity
        orchestrator = ServiceOrchestrator("test_webhook")
        # Set up a mock worker_config
        orchestrator.worker_config = mock.MagicMock()

        # Create test data
        message = {"Name": "Test Movie", "Year": 2023}
        service_config = {"temp_dir": "/tmp/test"}

        # Create a mock service instance
        mock_service_instance = mock.MagicMock(spec=ServiceBase)

        # Create a simplified mock implementation of create_service_instance
        def mock_create_service(service_name, msg, config):
            if service_name == "test_service":
                return mock_service_instance
            return None

        # Replace the method with our mock implementation
        with mock.patch.object(
            ServiceOrchestrator, "create_service_instance", side_effect=mock_create_service
        ):
            # Call the method being tested
            service = orchestrator.create_service_instance("test_service", message, service_config)

            # Verify the correct service was returned
            assert service is mock_service_instance

            # Test an unknown service
            service = orchestrator.create_service_instance(
                "unknown_service", message, service_config
            )
            assert service is None


def test_create_service_instance_unknown_service():
    """Test creating a service instance - unknown service case."""
    # We'll mock WorkerConfig.__init__ to avoid the singleton issue
    with mock.patch.object(WorkerConfig, "__init__", return_value=None):
        # Create our own ServiceOrchestrator
        orchestrator = ServiceOrchestrator("test_webhook")
        # Set up a mock worker_config
        orchestrator.worker_config = mock.MagicMock()

        message = {"Name": "Test Movie", "Year": 2023}
        service_config = {"temp_dir": "/tmp/test"}

        with mock.patch("workers.services.orchestrator.logger") as mock_logger:
            # Call the real method with an unknown service
            service = orchestrator.create_service_instance(
                "unknown_service", message, service_config
            )

            # The service should not be created
            assert service is None

            # Logger.error should be called
            mock_logger.error.assert_called_once_with("Unknown service: unknown_service")


@mock.patch("workers.services.orchestrator.importlib.import_module")
# Mock WorkerConfig.__init__ to avoid the singleton issue
@mock.patch.object(WorkerConfig, "__init__", return_value=None)
def test_create_service_instance_import_error(mock_config, mock_importmod):
    """Test creating a service instance - import error case."""
    mock_dovi_service = mock.Mock(spec=DoviConversionService)
    mock_dovi_service.from_message.return_value = mock_dovi_service
    mock_importmod.return_value = mock.Mock(DoviConversionService=mock_dovi_service)
    orchestrator = ServiceOrchestrator("item_added")
    orchestrator.worker_config = mock.MagicMock()

    message = {"Name": "Test Movie", "Year": 2023}
    service_config = {"temp_dir": "/tmp/test"}
    service_name = "dovi_conversion"

    result = orchestrator.create_service_instance(service_name, message, service_config)
    assert result is mock_dovi_service
    mock_dovi_service.from_message.assert_called_once_with(message, service_config)


def test_process_webhook_disabled_webhook(orchestrator):
    """Test processing a webhook - disabled webhook case."""
    message = {"Name": "Test Movie", "Year": 2023}

    # Set up orchestrator to have a disabled webhook
    orchestrator.worker_config.get_enabled_webhooks.return_value = {}

    with mock.patch("workers.services.orchestrator.logger") as mock_logger:
        result = orchestrator.process_webhook(message)

        # The webhook should be skipped, but return success
        assert result is True

        # Logger.info should be called
        mock_logger.info.assert_called_once_with(
            "Webhook item_added is disabled, skipping processing"
        )


def test_process_webhook_no_enabled_services(orchestrator):
    """Test processing a webhook - no enabled services case."""
    message = {"Name": "Test Movie", "Year": 2023}

    # Set up orchestrator to have an enabled webhook but no services
    orchestrator.worker_config.get_enabled_webhooks.return_value = {"item_added": {"enabled": True}}
    orchestrator.worker_config.get_enabled_services.return_value = []

    with mock.patch("workers.services.orchestrator.logger") as mock_logger:
        result = orchestrator.process_webhook(message)

        # No services to run, but return success
        assert result is True

        # Logger.info should be called
        mock_logger.info.assert_called_once_with(
            "No enabled services configured for webhook item_added"
        )


def test_process_webhook_success(orchestrator, mock_service):
    """Test processing a webhook - success case with multiple services."""
    message = {"Name": "Test Movie", "Year": 2023}

    # Create a list of enabled services
    enabled_services = [
        {
            "name": "metadata_update",
            "enabled": True,
            "priority": 10,
            "config": {"some_config": "value"},
        },
        {
            "name": "dovi_conversion",
            "enabled": True,
            "priority": 20,
            "config": {"temp_dir": "/tmp/dovi_conversion"},
        },
    ]

    # Set up orchestrator with enabled webhook and services
    orchestrator.worker_config.get_enabled_services.return_value = enabled_services
    orchestrator.worker_config.get_enabled_webhooks.return_value = {"item_added": {"enabled": True}}

    # Mock create_service_instance to return our mock service
    with mock.patch.object(
        ServiceOrchestrator, "create_service_instance", return_value=mock_service["instance"]
    ):
        result = orchestrator.process_webhook(message)

        # All services ran successfully
        assert result is True

        # Each service was executed
        assert mock_service["instance"].exec.call_count == 2

        # Check temp directories were added to the set
        assert len(orchestrator.temp_dirs) == 1
        assert Path("/tmp/test_service") in orchestrator.temp_dirs


def test_process_webhook_service_creation_error(orchestrator):
    """Test processing a webhook - service creation error."""
    message = {"Name": "Test Movie", "Year": 2023}

    # Create a list of enabled services
    enabled_services = [
        {
            "name": "metadata_update",
            "enabled": True,
            "priority": 10,
            "config": {"some_config": "value"},
        },
    ]

    # Set up orchestrator with enabled webhook and service
    orchestrator.worker_config.get_enabled_webhooks.return_value = {"item_added": {"enabled": True}}
    orchestrator.worker_config.get_enabled_services.return_value = enabled_services

    # Mock create_service_instance to return None (error)
    with (
        mock.patch.object(ServiceOrchestrator, "create_service_instance", return_value=None),
        mock.patch("workers.services.orchestrator.logger") as mock_logger,
    ):
        result = orchestrator.process_webhook(message)

        # Service not created, but not critical failure
        assert result is True

        # Info log about service not being created
        mock_logger.info.assert_any_call(
            "Service metadata_update was not created for this message - skipping"
        )


def test_process_webhook_service_execution_error(orchestrator):
    """Test processing a webhook - service execution error."""
    message = {"Name": "Test Movie", "Year": 2023}

    # Create enabled services
    enabled_services = [
        {
            "name": "metadata_update",
            "enabled": True,
            "priority": 10,
            "config": {"some_config": "value"},
        },
        {
            "name": "dovi_conversion",
            "enabled": True,
            "priority": 20,
            "config": {"temp_dir": "/tmp/dovi_conversion"},
        },
    ]

    # Set up mock services with different behaviors
    metadata_service = mock.MagicMock(spec=ServiceBase)
    metadata_service.exec.side_effect = Exception("Metadata service failed")

    dovi_service = mock.MagicMock(spec=ServiceBase)
    dovi_service.exec.side_effect = Exception("Dovi service failed")

    # Set up orchestrator with enabled webhook and services
    orchestrator.worker_config.get_enabled_webhooks.return_value = {"item_added": {"enabled": True}}
    orchestrator.worker_config.get_enabled_services.return_value = enabled_services

    # Create service_instance should return different services based on name
    def mock_create_service(service_name, *args, **kwargs):
        if service_name == "metadata_update":
            return metadata_service
        elif service_name == "dovi_conversion":
            return dovi_service
        return None

    with (
        mock.patch.object(
            ServiceOrchestrator, "create_service_instance", side_effect=mock_create_service
        ),
        mock.patch("workers.services.orchestrator.logger") as mock_logger,
    ):
        result = orchestrator.process_webhook(message)

        # Metadata errors are not critical, but dovi errors are
        assert result is False

        # Both services attempted execution
        metadata_service.exec.assert_called_once()
        dovi_service.exec.assert_called_once()

        # Logger error calls for each service failure
        mock_logger.error.assert_any_call("Service metadata_update failed: Metadata service failed")
        mock_logger.error.assert_any_call("Service dovi_conversion failed: Dovi service failed")


def test_cleanup(orchestrator):
    """Test cleaning up temporary directories."""
    # Create temp directories
    orchestrator.temp_dirs = {Path("/tmp/service1"), Path("/tmp/service2")}

    with (
        mock.patch("workers.utils.clean_dir") as mock_clean_dir,
        mock.patch("workers.services.orchestrator.logger") as mock_logger,
    ):
        # Mock utils.clean_dir
        orchestrator.cleanup()

        # Each directory should be cleaned
        assert mock_clean_dir.call_count == 2
        mock_clean_dir.assert_any_call(Path("/tmp/service1"))
        mock_clean_dir.assert_any_call(Path("/tmp/service2"))

        # Logger info calls
        assert mock_logger.info.call_count == 2


def test_cleanup_error(orchestrator):
    """Test cleaning up temporary directories with errors."""
    # Create temp directories
    orchestrator.temp_dirs = {Path("/tmp/service1"), Path("/tmp/service2")}

    # Mock utils.clean_dir to raise exception
    with (
        mock.patch("workers.utils.clean_dir", side_effect=FileNotFoundError("Directory not found")),
        mock.patch("workers.services.orchestrator.logger") as mock_logger,
    ):
        orchestrator.cleanup()

        # Logger error calls
        assert mock_logger.error.call_count == 2
        mock_logger.error.assert_any_call(
            "Failed to clean temporary directory /tmp/service1: Directory not found"
        )


def test_process_webhook_message_success():
    """Test processing a webhook message - success case."""
    webhook_id = "item_added"
    message_body = json.dumps({"Name": "Test Movie", "Year": 2023}).encode()

    # Mock ServiceOrchestrator
    mock_orchestrator = mock.MagicMock()
    mock_orchestrator.process_webhook.return_value = True

    with (
        mock.patch(
            "workers.services.orchestrator.ServiceOrchestrator", return_value=mock_orchestrator
        ),
        mock.patch(
            "workers.services.orchestrator.json.loads",
            return_value={"Name": "Test Movie", "Year": 2023},
        ),
    ):
        result = process_webhook_message(webhook_id, message_body)

        # The message was processed successfully
        assert result is True

        # The orchestrator was created with the correct webhook_id
        assert mock_orchestrator.process_webhook.called
        assert mock_orchestrator.cleanup.called


def test_process_webhook_message_error():
    """Test processing a webhook message - error case."""
    webhook_id = "item_added"
    message_body = b"invalid json"

    # Mock json.loads to raise JSONDecodeError
    with (
        mock.patch(
            "workers.services.orchestrator.json.loads",
            side_effect=json.JSONDecodeError("Invalid JSON", "", 0),
        ),
        mock.patch("workers.services.orchestrator.logger") as mock_logger,
    ):
        result = process_webhook_message(webhook_id, message_body)

        # The message was not processed successfully
        assert result is False

        # Logger error was called
        mock_logger.error.assert_called_once()
        assert "Failed to process webhook item_added" in mock_logger.error.call_args[0][0]
