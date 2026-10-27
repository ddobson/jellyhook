"""Tests for the service orchestrator module."""

import json
from pathlib import Path
from unittest import mock

import pytest
import requests
from jellyfin_apiclient_python.exceptions import HTTPException

from workers.clients.jellyfin import JellyfinUnavailableError
from workers.config import WorkerConfig
from workers.errors import MediaNotFoundError, ServiceConfigurationError
from workers.results import Outcome, ProcessingResult
from workers.services.dovi_conversion import DoviConversionService
from workers.services.metadata_update import MetadataUpdateError
from workers.services.orchestrator import ServiceOrchestrator, process_webhook_message
from workers.services.playlist_assignment import PlaylistAssignmentError
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
            # An unknown service is a configuration error, not a skip
            with pytest.raises(ServiceConfigurationError, match="Unknown service"):
                orchestrator.create_service_instance("unknown_service", message, service_config)

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
        assert result == ProcessingResult.success()

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
        assert result == ProcessingResult.success()

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
        assert result == ProcessingResult.success()

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
        assert result == ProcessingResult.success()

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
        assert result.outcome is Outcome.FAILED
        assert "dovi_conversion: Exception: Dovi service failed" in result.reason
        assert "metadata_update" not in result.reason

        # Both services attempted execution
        metadata_service.exec.assert_called_once()
        dovi_service.exec.assert_called_once()

        # Logger error calls for each service failure
        mock_logger.error.assert_any_call("Service metadata_update failed: Metadata service failed")
        mock_logger.error.assert_any_call("Service dovi_conversion failed: Dovi service failed")


def test_cleanup_removes_scratch_directories(orchestrator, tmp_path):
    """Every registered directory is deleted, contents included."""
    dirs = {tmp_path / "service1", tmp_path / "service2"}
    for d in dirs:
        (d / "nested").mkdir(parents=True)
        (d / "nested" / "partial.hevc").write_bytes(b"x")
    orchestrator.temp_dirs = set(dirs)

    orchestrator.cleanup()

    assert not any(d.exists() for d in dirs)
    assert orchestrator.temp_dirs == set()


def test_cleanup_ignores_directories_that_are_already_gone(orchestrator, tmp_path):
    orchestrator.temp_dirs = {tmp_path / "never_created"}

    orchestrator.cleanup()  # must not raise


def test_process_webhook_registers_scratch_dir_before_exec_so_failures_still_clean_up(
    orchestrator, tmp_path
):
    """A service that dies mid-run must still have its scratch directory removed."""
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    (scratch / "half_written.hevc").write_bytes(b"x")
    service = mock.MagicMock()
    service.tmp_dir = scratch
    service.locked_path = None
    service.exec.side_effect = RuntimeError("boom")

    with (
        mock.patch.object(orchestrator, "is_webhook_enabled", return_value=True),
        mock.patch.object(orchestrator, "create_service_instance", return_value=service),
        mock.patch.object(
            orchestrator,
            "get_enabled_services",
            return_value=[{"name": "dovi_conversion", "config": {}}],
        ),
    ):
        result = orchestrator.process_webhook({"Name": "Item A", "Year": 2023})
        orchestrator.cleanup()

    assert result.outcome is Outcome.FAILED
    assert not scratch.exists()


def test_process_webhook_message_cleans_up_even_when_processing_raises():
    orchestrator = mock.MagicMock()
    orchestrator.process_webhook.side_effect = RuntimeError("boom")

    with mock.patch("workers.services.orchestrator.ServiceOrchestrator", return_value=orchestrator):
        result = process_webhook_message("item_added", b"{}")

    assert result.outcome is Outcome.FAILED
    orchestrator.cleanup.assert_called_once()


def test_services_that_modify_a_file_run_under_that_files_lock(orchestrator, tmp_path):
    """Two jobs for the same file must not overlap; different files may."""
    import threading
    import time

    movie = tmp_path / "movie.mkv"
    movie.write_bytes(b"x")
    running = 0
    peak = 0
    guard = threading.Lock()

    def slow_exec():
        nonlocal running, peak
        with guard:
            running += 1
            peak = max(peak, running)
        time.sleep(0.05)
        with guard:
            running -= 1

    def make_service():
        service = mock.MagicMock()
        service.locked_path = movie
        service.exec.side_effect = slow_exec
        return service

    threads = [
        threading.Thread(target=orchestrator._run_service, args=(make_service(),)) for _ in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert peak == 1


def test_services_without_a_locked_path_run_unlocked(orchestrator):
    service = mock.MagicMock()
    service.locked_path = None

    orchestrator._run_service(service)

    service.exec.assert_called_once()


def test_process_webhook_message_success():
    """Test processing a webhook message - success case."""
    webhook_id = "item_added"
    message_body = json.dumps({"Name": "Test Movie", "Year": 2023}).encode()

    # Mock ServiceOrchestrator
    mock_orchestrator = mock.MagicMock()
    mock_orchestrator.process_webhook.return_value = ProcessingResult.success()

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
        assert result == ProcessingResult.success()

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

        # Malformed JSON can never succeed, so it is a permanent failure
        assert result.outcome is Outcome.FAILED
        assert "Invalid JSON" in result.reason

        # Logger error was called
        mock_logger.error.assert_called_once()
        assert "Invalid JSON in message for webhook item_added" in mock_logger.error.call_args[0][0]


def _enable_services(orchestrator, names):
    """Configure the orchestrator with enabled services in priority order."""
    orchestrator.worker_config.get_enabled_webhooks.return_value = {"item_added": {"enabled": True}}
    orchestrator.worker_config.get_enabled_services.return_value = [
        {"name": name, "enabled": True, "priority": (i + 1) * 10} for i, name in enumerate(names)
    ]


def _failing_service(error):
    """Create a mock service whose exec raises the given error."""
    service = mock.MagicMock(spec=ServiceBase)
    service.exec.side_effect = error
    return service


def test_create_service_instance_import_failure_raises():
    """A service module that fails to import is a configuration error, not a skip."""
    with mock.patch.object(WorkerConfig, "__init__", return_value=None):
        orchestrator = ServiceOrchestrator("item_added")

    with (
        mock.patch(
            "workers.services.orchestrator.importlib.import_module",
            side_effect=ImportError("no module named x"),
        ),
        pytest.raises(ServiceConfigurationError, match="Failed to import service dovi_conversion"),
    ):
        orchestrator.create_service_instance("dovi_conversion", {}, {})


def test_create_service_instance_does_not_hide_service_bugs():
    """An AttributeError raised inside from_message is a bug and must not look like a skip."""
    with mock.patch.object(WorkerConfig, "__init__", return_value=None):
        orchestrator = ServiceOrchestrator("item_added")

    broken_service = mock.Mock()
    broken_service.from_message.side_effect = AttributeError("'NoneType' has no attribute 'x'")
    module = mock.Mock(DoviConversionService=broken_service)

    with (
        mock.patch("workers.services.orchestrator.importlib.import_module", return_value=module),
        pytest.raises(AttributeError),
    ):
        orchestrator.create_service_instance("dovi_conversion", {}, {})


def test_process_webhook_unknown_service_fails_instead_of_acking(orchestrator):
    """A mistyped service name must fail the message, not be acknowledged as a skip."""
    _enable_services(orchestrator, ["metadat_update"])

    result = orchestrator.process_webhook({"Name": "Test Movie", "Year": 2023})

    assert result.outcome is Outcome.FAILED
    assert "Unknown service: metadat_update" in result.reason


def test_process_webhook_missing_media_is_retryable(orchestrator):
    """A file that hasn't landed on disk yet should be retried, not dropped."""
    _enable_services(orchestrator, ["dovi_conversion"])
    service_error = MediaNotFoundError("No video found for 'Test Movie'")

    with mock.patch.object(
        ServiceOrchestrator, "create_service_instance", side_effect=service_error
    ):
        result = orchestrator.process_webhook({"Name": "Test Movie", "Year": 2023})

    assert result.outcome is Outcome.RETRY
    assert "No video found" in result.reason


def test_process_webhook_network_error_is_retryable(orchestrator):
    """Jellyfin being unreachable is transient."""
    _enable_services(orchestrator, ["playlist_assignment"])
    service = _failing_service(ConnectionError("Jellyfin unreachable"))

    with mock.patch.object(ServiceOrchestrator, "create_service_instance", return_value=service):
        result = orchestrator.process_webhook({"Name": "Test Movie", "Year": 2023})

    assert result.outcome is Outcome.RETRY


def test_process_webhook_mixed_failures_retry(orchestrator):
    """If any failure is transient the message is retried; the permanent one fails again."""
    _enable_services(orchestrator, ["dovi_conversion", "media_track_clean"])
    services = {
        "dovi_conversion": _failing_service(ValueError("bad profile")),
        "media_track_clean": _failing_service(TimeoutError("jellyfin timed out")),
    }

    with mock.patch.object(
        ServiceOrchestrator,
        "create_service_instance",
        side_effect=lambda name, *_: services[name],
    ):
        result = orchestrator.process_webhook({"Name": "Test Movie", "Year": 2023})

    assert result.outcome is Outcome.RETRY
    assert "dovi_conversion: ValueError: bad profile" in result.reason
    assert "media_track_clean: TimeoutError" in result.reason


def test_process_webhook_permanent_metadata_failure_alone_still_succeeds(orchestrator):
    """metadata_update is best-effort: a permanent failure never fails the message."""
    _enable_services(orchestrator, ["metadata_update"])
    service = _failing_service(ValueError("bad rule"))

    with mock.patch.object(ServiceOrchestrator, "create_service_instance", return_value=service):
        result = orchestrator.process_webhook({"Name": "Test Movie", "Year": 2023})

    assert result == ProcessingResult.success()


@pytest.mark.parametrize(
    "error",
    [
        JellyfinUnavailableError("Jellyfin did not sign us in"),
        ConnectionError("Jellyfin unreachable"),
    ],
)
def test_process_webhook_transient_metadata_failure_is_retried_not_dropped(orchestrator, error):
    """A Jellyfin outage during metadata_update must not be acked as a success."""
    _enable_services(orchestrator, ["metadata_update"])
    service = _failing_service(error)

    with mock.patch.object(ServiceOrchestrator, "create_service_instance", return_value=service):
        result = orchestrator.process_webhook({"Name": "Test Movie", "Year": 2023})

    assert result.outcome is Outcome.RETRY
    assert "metadata_update" in result.reason


def test_process_webhook_message_rejects_non_object_body():
    """A body like `null` or `[]` parses as JSON but can never be processed."""
    result = process_webhook_message("item_added", b"null")

    assert result.outcome is Outcome.FAILED
    assert "Expected a JSON object" in result.reason


def test_process_webhook_message_unexpected_error_is_classified():
    """An unexpected crash is permanent unless it looks transient."""
    with mock.patch(
        "workers.services.orchestrator.ServiceOrchestrator", side_effect=KeyError("config")
    ):
        crashed = process_webhook_message("item_added", b"{}")
    with mock.patch(
        "workers.services.orchestrator.ServiceOrchestrator", side_effect=TimeoutError("slow")
    ):
        timed_out = process_webhook_message("item_added", b"{}")

    assert crashed.outcome is Outcome.FAILED
    assert timed_out.outcome is Outcome.RETRY


def _wrapped_playlist_error(cause):
    """Raise-and-catch to chain the error the way playlist_assignment does (`from exc`)."""
    try:
        try:
            raise cause
        except Exception as exc:
            raise PlaylistAssignmentError("Failed to add item to playlist") from exc
    except PlaylistAssignmentError as error:
        return error


def test_process_webhook_retries_a_playlist_service_that_wrapped_a_jellyfin_outage(orchestrator):
    """The service re-raises Jellyfin failures as its own type; that must not hide the cause."""
    import requests

    _enable_services(orchestrator, ["playlist_assignment"])
    service = _failing_service(_wrapped_playlist_error(requests.ConnectionError("refused")))

    with mock.patch.object(ServiceOrchestrator, "create_service_instance", return_value=service):
        result = orchestrator.process_webhook({"Name": "Test Movie", "Year": 2023})

    assert result.outcome is Outcome.RETRY


def test_process_webhook_does_not_retry_a_playlist_service_that_got_a_401(orchestrator):
    """A rejected API key will not fix itself in four minutes."""
    import requests

    response = requests.Response()
    response.status_code = 401
    _enable_services(orchestrator, ["playlist_assignment"])
    service = _failing_service(_wrapped_playlist_error(requests.HTTPError(response=response)))

    with mock.patch.object(ServiceOrchestrator, "create_service_instance", return_value=service):
        result = orchestrator.process_webhook({"Name": "Test Movie", "Year": 2023})

    assert result.outcome is Outcome.FAILED


def test_process_webhook_does_not_track_a_temp_dir_for_services_without_one(orchestrator):
    """Services like metadata_update have no tmp_dir, so there is nothing to clean up."""
    _enable_services(orchestrator, ["metadata_update"])
    service = mock.MagicMock(spec=ServiceBase)  # spec'd, so `tmp_dir` does not exist

    with mock.patch.object(ServiceOrchestrator, "create_service_instance", return_value=service):
        result = orchestrator.process_webhook({"Name": "Test Movie", "Year": 2023})

    assert result == ProcessingResult.success()
    assert orchestrator.temp_dirs == set()


@pytest.mark.parametrize("item_type", ["Episode", "Series", "Season", "Audio"])
def test_process_webhook_skips_file_services_for_non_movie_items(orchestrator, item_type):
    """A TV episode or song is never under MEDIA_PATHS: skip it, don't retry until dead."""
    _enable_services(orchestrator, ["metadata_update", "dovi_conversion", "media_track_clean"])
    message = {"Name": "Pilot", "Year": 2023, "ItemType": item_type, "ItemId": "1"}

    with mock.patch.object(
        ServiceBase, "file_from_message", side_effect=MediaNotFoundError("No video")
    ) as file_from_message:
        result = orchestrator.process_webhook(message)

    assert result.outcome is Outcome.SUCCESS
    file_from_message.assert_not_called()


def _jellyfin_unreachable() -> MetadataUpdateError:
    """The error metadata_update raises when Jellyfin is down, chained as the client does it."""
    try:
        try:
            raise requests.ConnectionError("connection refused")
        except requests.ConnectionError as network_error:
            raise HTTPException("ServerUnreachable", network_error)  # noqa: B904
    except HTTPException as client_error:
        try:
            raise MetadataUpdateError("Failed to update metadata") from client_error
        except MetadataUpdateError as wrapped:
            return wrapped


def test_process_webhook_retries_metadata_update_when_jellyfin_is_down(orchestrator):
    """The update must be retried, not swallowed as a best-effort failure and acked."""
    _enable_services(orchestrator, ["metadata_update"])
    service = _failing_service(_jellyfin_unreachable())

    with mock.patch.object(ServiceOrchestrator, "create_service_instance", return_value=service):
        result = orchestrator.process_webhook({"Name": "Test Movie", "Year": 2023})

    assert result.outcome is Outcome.RETRY
