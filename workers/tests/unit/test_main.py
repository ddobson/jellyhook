import os
import signal
import threading
from unittest import mock

import pytest

from workers import main as worker_main
from workers.services.orchestrator import process_webhook_message


@pytest.fixture
def worker_config():
    """A loaded worker config with two enabled webhooks."""
    config = mock.MagicMock()
    config.get_enabled_webhooks.return_value = {
        "item_added": {"queue": "jellyfin:item_added"},
        "item_removed": {"queue": "jellyfin:item_removed"},
    }
    return config


def test_main_builds_a_consumer_per_enabled_webhook_and_supervises_them(worker_config):
    """Each webhook gets its own consumer; they share one job-slot pool and one stop event."""
    with (
        mock.patch.object(worker_main.config.WorkerConfig, "load", return_value=worker_config),
        mock.patch.object(worker_main, "install_signal_handlers") as install,
        mock.patch.object(worker_main.rabbitmq, "QueueConsumer") as consumer_class,
        mock.patch.object(worker_main, "supervise", return_value=0) as supervise,
        mock.patch.object(worker_main.config, "MAX_CONCURRENT_JOBS", 2),
        mock.patch.object(worker_main.config, "SHUTDOWN_GRACE_SECONDS", 12.5),
    ):
        exit_code = worker_main.main()

    assert exit_code == 0
    assert consumer_class.call_count == 2
    first, second = (call.kwargs for call in consumer_class.call_args_list)
    assert (first["webhook_id"], first["queue"]) == ("item_added", "jellyfin:item_added")
    assert (second["webhook_id"], second["queue"]) == ("item_removed", "jellyfin:item_removed")
    assert first["handler"] is process_webhook_message
    assert first["job_slots"] is second["job_slots"]
    assert first["stop_event"] is second["stop_event"]

    install.assert_called_once_with(first["stop_event"])
    consumers, stop_event, grace = supervise.call_args.args
    assert len(consumers) == 2
    assert stop_event is first["stop_event"]
    assert grace == 12.5


def test_main_returns_the_supervisors_failure_code(worker_config):
    """A crashed consumer must reach the process exit code so Docker restarts the worker."""
    with (
        mock.patch.object(worker_main.config.WorkerConfig, "load", return_value=worker_config),
        mock.patch.object(worker_main, "install_signal_handlers"),
        mock.patch.object(worker_main.rabbitmq, "QueueConsumer"),
        mock.patch.object(worker_main, "supervise", return_value=1),
    ):
        assert worker_main.main() == 1


def test_main_with_no_enabled_webhooks_exits_non_zero():
    """Nothing to consume is a misconfiguration, and a silent exit 0 hid it."""
    config = mock.MagicMock()
    config.get_enabled_webhooks.return_value = {}

    with (
        mock.patch.object(worker_main.config.WorkerConfig, "load", return_value=config),
        mock.patch.object(worker_main, "supervise") as supervise,
        mock.patch.object(worker_main, "logger") as logger,
    ):
        assert worker_main.main() == 1

    supervise.assert_not_called()
    assert logger.warning.call_count >= 1


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
def test_signal_handlers_request_a_graceful_stop(signum):
    """Docker stop (SIGTERM) and ctrl-c (SIGINT) set the event instead of killing the process."""
    stop_event = threading.Event()
    previous = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
    try:
        worker_main.install_signal_handlers(stop_event)
        assert not stop_event.is_set()

        os.kill(os.getpid(), signum)

        assert stop_event.wait(timeout=1)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
