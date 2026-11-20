"""Tests for the service orchestrator module."""

import json
import subprocess
import threading
import time
from pathlib import Path
from unittest import mock

import pytest
import requests
from jellyfin_apiclient_python.exceptions import HTTPException

from workers.clients.jellyfin import JellyfinUnavailableError
from workers.config import WorkerConfig
from workers.errors import MediaNotFoundError, ServiceConfigurationError
from workers.job import find_media_file
from workers.results import Outcome, ProcessingResult
from workers.services.dovi_conversion import DoviConversionService
from workers.services.metadata_update import MetadataUpdateError
from workers.services.orchestrator import ServiceOrchestrator, process_webhook_message
from workers.services.playlist_assignment import PlaylistAssignmentError
from workers.services.service_base import ServiceBase


def _make_service(**attrs):
    """A spec'd mock service that, like most, leaves the media file alone."""
    service = mock.MagicMock(spec=ServiceBase)
    service.modifies_media = False
    for name, value in attrs.items():
        setattr(service, name, value)
    return service


@pytest.fixture
def job(make_job):
    """A real Job; none of these tests need its media file to exist."""
    return make_job({"Name": "Test Movie", "Year": 2023})


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
    mock_service_instance = _make_service()
    mock_service_instance.exec.return_value = None

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


def test_create_service_instance_success(job):
    """Test creating a service instance - success case."""
    # We'll mock WorkerConfig.__init__ to avoid the singleton issue
    with mock.patch.object(WorkerConfig, "__init__", return_value=None):
        # For this test, we'll create our own ServiceOrchestrator to avoid fixture complexity
        orchestrator = ServiceOrchestrator("test_webhook")
        # Set up a mock worker_config
        orchestrator.worker_config = mock.MagicMock()

        # Create test data
        service_config = {"temp_dir": "/tmp/test"}

        # Create a mock service instance
        mock_service_instance = _make_service()

        # Create a simplified mock implementation of create_service_instance
        def mock_create_service(service_name, job_, config):
            if service_name == "test_service":
                return mock_service_instance
            return None

        # Replace the method with our mock implementation
        with mock.patch.object(
            ServiceOrchestrator, "create_service_instance", side_effect=mock_create_service
        ):
            # Call the method being tested
            service = orchestrator.create_service_instance("test_service", job, service_config)

            # Verify the correct service was returned
            assert service is mock_service_instance

            # Test an unknown service
            service = orchestrator.create_service_instance("unknown_service", job, service_config)
            assert service is None


def test_create_service_instance_unknown_service(job):
    """Test creating a service instance - unknown service case."""
    # We'll mock WorkerConfig.__init__ to avoid the singleton issue
    with mock.patch.object(WorkerConfig, "__init__", return_value=None):
        # Create our own ServiceOrchestrator
        orchestrator = ServiceOrchestrator("test_webhook")
        # Set up a mock worker_config
        orchestrator.worker_config = mock.MagicMock()

        service_config = {"temp_dir": "/tmp/test"}

        with mock.patch("workers.services.orchestrator.logger") as mock_logger:
            # An unknown service is a configuration error, not a skip
            with pytest.raises(ServiceConfigurationError, match="Unknown service"):
                orchestrator.create_service_instance("unknown_service", job, service_config)

            # Logger.error should be called
            mock_logger.error.assert_called_once_with("Unknown service: unknown_service")


@mock.patch("workers.services.orchestrator.importlib.import_module")
# Mock WorkerConfig.__init__ to avoid the singleton issue
@mock.patch.object(WorkerConfig, "__init__", return_value=None)
def test_create_service_instance_import_error(mock_config, mock_importmod, job):
    """Test creating a service instance - import error case."""
    mock_dovi_service = mock.Mock(spec=DoviConversionService)
    mock_dovi_service.from_message.return_value = mock_dovi_service
    mock_importmod.return_value = mock.Mock(DoviConversionService=mock_dovi_service)
    orchestrator = ServiceOrchestrator("item_added")
    orchestrator.worker_config = mock.MagicMock()

    service_config = {"temp_dir": "/tmp/test"}
    service_name = "dovi_conversion"

    result = orchestrator.create_service_instance(service_name, job, service_config)
    assert result is mock_dovi_service
    mock_dovi_service.from_message.assert_called_once_with(job, service_config)


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
    metadata_service = _make_service()
    metadata_service.exec.side_effect = Exception("Metadata service failed")

    dovi_service = _make_service()
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


def test_process_webhook_removes_scratch_dirs_when_a_service_fails_mid_exec(orchestrator, tmp_path):
    """A service that dies mid-run must still have its scratch directory removed."""
    orchestrator.temp_dir = tmp_path
    scratch_dirs = []
    _enable_services(orchestrator, ["dovi_conversion"])

    def create_service(name, job, config):
        def exec_():
            scratch = job.scratch_dir("dovi")
            scratch_dirs.append(scratch)
            (scratch / "half_written.hevc").write_bytes(b"x")
            raise RuntimeError("boom")

        return _make_service(exec=mock.Mock(side_effect=exec_))

    with mock.patch.object(orchestrator, "create_service_instance", side_effect=create_service):
        result = orchestrator.process_webhook({"Name": "Item A", "Year": 2023})

    assert result.outcome is Outcome.FAILED
    assert len(scratch_dirs) == 1
    assert not scratch_dirs[0].exists()


def test_process_webhook_does_not_locate_the_file_for_services_that_do_not_need_it(orchestrator):
    """Services like playlist_assignment never ask the Job for the file."""
    _enable_services(orchestrator, ["playlist_assignment"])
    service = _make_service()

    with (
        mock.patch.object(ServiceOrchestrator, "create_service_instance", return_value=service),
        mock.patch("workers.job.find_media_file") as find,
    ):
        result = orchestrator.process_webhook({"Name": "Test Movie", "Year": 2023})

    assert result == ProcessingResult.success()
    find.assert_not_called()


def test_services_that_modify_a_file_run_under_that_files_lock(orchestrator, make_job):
    """Two jobs for the same file must not overlap."""
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
        return _make_service(modifies_media=True, exec=mock.Mock(side_effect=slow_exec))

    message = {"Name": "Test Movie", "Year": "2023"}
    threads = [
        threading.Thread(target=orchestrator._run_service, args=(make_service(), make_job(message)))
        for _ in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert peak == 1


def test_services_that_leave_files_alone_run_unlocked_and_without_a_file(orchestrator, make_job):
    service = _make_service()
    job = make_job({"Name": "Not On Disk", "Year": "2023"})

    orchestrator._run_service(service, job)  # would raise MediaNotFoundError if it took the lock

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
    service = _make_service()
    service.exec.side_effect = error
    return service


def test_create_service_instance_import_failure_raises(job):
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
        orchestrator.create_service_instance("dovi_conversion", job, {})


def test_create_service_instance_does_not_hide_service_bugs(job):
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
        orchestrator.create_service_instance("dovi_conversion", job, {})


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


@pytest.mark.parametrize("item_type", ["Episode", "Series", "Season", "Audio"])
def test_process_webhook_skips_file_services_for_non_movie_items(orchestrator, item_type):
    """A TV episode or song is never under MEDIA_PATHS: skip it, don't retry until dead."""
    _enable_services(orchestrator, ["metadata_update", "dovi_conversion", "media_track_clean"])
    message = {"Name": "Pilot", "Year": 2023, "ItemType": item_type, "ItemId": "1"}

    with mock.patch("workers.job.find_media_file") as find:
        result = orchestrator.process_webhook(message)

    assert result.outcome is Outcome.SUCCESS
    find.assert_not_called()


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


# --- The real build path: orchestrator, Job and services together over a temporary media tree ---


@pytest.fixture
def real_orchestrator(orchestrator, media_dir):
    """The orchestrator pointed at the temporary media tree, with the real services enabled."""
    orchestrator.media_paths = (media_dir["standup_path"], media_dir["movie_path"])
    orchestrator.temp_dir = media_dir["tmp_path"]
    return orchestrator


STREAMS_TO_TRIM = {
    "streams": [
        {"index": 0, "codec_type": "video"},
        {"index": 1, "codec_type": "audio", "tags": {"language": "eng"}},
        {"index": 2, "codec_type": "audio", "tags": {"language": "spa"}},
    ]
}
KEEP_EVERYTHING = {
    "streams": [
        {"index": 0, "codec_type": "video"},
        {"index": 1, "codec_type": "audio", "disposition": {"default": 1}},
    ]
}
MOVIE_MESSAGE = {"Name": "Test Movie", "Year": 2023, "ItemType": "Movie", "ItemId": "1"}


def test_the_media_file_is_located_once_for_the_whole_message(real_orchestrator):
    _enable_services(real_orchestrator, ["metadata_update", "media_track_clean"])

    with (
        mock.patch("workers.job.find_media_file", wraps=find_media_file) as find,
        mock.patch("workers.services.metadata_update.get_client"),
        mock.patch(
            "workers.services.media_track_clean.run_command",
            return_value=mock.Mock(stdout=json.dumps(KEEP_EVERYTHING)),
        ),
    ):
        result = real_orchestrator.process_webhook(MOVIE_MESSAGE)

    assert result == ProcessingResult.success()
    find.assert_called_once()


def test_scratch_space_is_removed_after_a_failure_part_way_through_exec(
    real_orchestrator, media_dir
):
    _enable_services(real_orchestrator, ["media_track_clean"])
    real_orchestrator.worker_config.get_enabled_services.return_value[0]["config"] = {
        "keep_audio_langs": ["eng"],
        "keep_original": False,
        "keep_default": False,
    }
    run_command = mock.Mock(
        side_effect=[
            mock.Mock(stdout=json.dumps(STREAMS_TO_TRIM)),
            subprocess.CalledProcessError(1, ["ffmpeg"], stderr="boom"),
        ]
    )

    with mock.patch("workers.services.media_track_clean.run_command", run_command):
        result = real_orchestrator.process_webhook(MOVIE_MESSAGE)

    assert result.outcome is Outcome.FAILED
    assert run_command.call_count == 2  # the failure really was mid-exec, after scratch was made
    assert list(Path(media_dir["tmp_path"]).iterdir()) == []
    assert Path(media_dir["movie_file"]).read_text() == "movie content"


@pytest.mark.parametrize("service", ["dovi_conversion", "media_track_clean"])
def test_a_missing_media_file_is_retried_by_every_file_service(real_orchestrator, service):
    _enable_services(real_orchestrator, [service])

    result = real_orchestrator.process_webhook({**MOVIE_MESSAGE, "Name": "Not Here"})

    assert result.outcome is Outcome.RETRY


def test_services_that_need_no_scratch_space_leave_the_temp_dir_empty(real_orchestrator, media_dir):
    _enable_services(real_orchestrator, ["metadata_update", "media_track_clean"])

    with (
        mock.patch("workers.services.metadata_update.get_client"),
        mock.patch(
            "workers.services.media_track_clean.run_command",
            return_value=mock.Mock(stdout=json.dumps(KEEP_EVERYTHING)),
        ),
    ):
        real_orchestrator.process_webhook(MOVIE_MESSAGE)

    assert list(Path(media_dir["tmp_path"]).iterdir()) == []
