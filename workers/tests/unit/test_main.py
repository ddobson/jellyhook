import os
import runpy
import signal
import sys
import threading
import types
from unittest import mock

import pytest

from workers import main as worker_main
from workers.config import RandomPlaylist, Schedule, SeriesByTitle
from workers.services.orchestrator import process_webhook_message
from workers.services.refresh import process_refresh_message

WEBHOOKS = {
    "item_added": {"queue": "jellyfin:item_added"},
    "item_removed": {"queue": "jellyfin:item_removed"},
}
RANDOM_PLAYLISTS = (
    RandomPlaylist(
        name="Bob's Burgers Shuffle",
        series=(SeriesByTitle("Bob's Burgers"),),
        size=25,
        schedule=Schedule("0 1 * * 0"),
    ),
)


def loaded_config(webhooks=None, random_playlists=()):
    """A loaded worker config with the given enabled webhooks and Random Playlists."""
    config = mock.MagicMock()
    config.get_enabled_webhooks.return_value = webhooks or {}
    config.get_random_playlists.return_value = random_playlists
    return config


@pytest.fixture
def worker_config():
    """A loaded worker config with two enabled webhooks and no Random Playlists."""
    return loaded_config(WEBHOOKS)


def run_main(worker_config):
    """Run `main()` with a config, returning its exit code and the consumers it supervised."""
    with (
        mock.patch.object(worker_main.config.WorkerConfig, "load", return_value=worker_config),
        mock.patch.object(worker_main, "install_signal_handlers"),
        mock.patch.object(worker_main.rabbitmq, "QueueConsumer") as consumer_class,
        mock.patch.object(worker_main, "supervise", return_value=0) as supervise,
        mock.patch.object(worker_main.config, "REFRESH_QUEUE", "jellyhook:refresh-test"),
    ):
        exit_code = worker_main.main()
    consumers = [call.kwargs for call in consumer_class.call_args_list]
    return exit_code, consumers, supervise


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


def test_main_with_neither_webhooks_nor_random_playlists_exits_non_zero():
    """Story 41: nothing to consume is a misconfiguration, and a silent exit 0 hid it."""
    config = loaded_config()

    with (
        mock.patch.object(worker_main.config.WorkerConfig, "load", return_value=config),
        mock.patch.object(worker_main, "supervise") as supervise,
        mock.patch.object(worker_main, "logger") as logger,
    ):
        assert worker_main.main() == 1

    supervise.assert_not_called()
    assert logger.warning.call_count >= 1


def test_main_with_only_webhooks_does_not_consume_the_refresh_queue():
    """Without Random Playlists there is nothing to Refresh, so the Refresh queue is left alone."""
    exit_code, consumers, _ = run_main(loaded_config(WEBHOOKS))

    assert exit_code == 0
    assert [c["queue"] for c in consumers] == ["jellyfin:item_added", "jellyfin:item_removed"]


def test_main_with_webhooks_and_random_playlists_consumes_all_their_queues_on_shared_slots():
    """Refreshes share the Webhooks' job slots and stop event (ADR 0001)."""
    exit_code, consumers, supervise = run_main(
        loaded_config(WEBHOOKS, random_playlists=RANDOM_PLAYLISTS)
    )

    assert exit_code == 0
    assert [c["queue"] for c in consumers] == [
        "jellyfin:item_added",
        "jellyfin:item_removed",
        "jellyhook:refresh-test",
    ]
    assert consumers[-1]["handler"] is process_refresh_message
    assert len({id(c["job_slots"]) for c in consumers}) == 1
    assert len({id(c["stop_event"]) for c in consumers}) == 1
    assert len(supervise.call_args.args[0]) == 3


def test_main_with_only_random_playlists_consumes_the_refresh_queue():
    """Story 40: Random Playlists alone are enough to run; no Webhook has to be enabled."""
    exit_code, consumers, supervise = run_main(loaded_config(random_playlists=RANDOM_PLAYLISTS))

    assert exit_code == 0
    assert [(c["queue"], c["handler"]) for c in consumers] == [
        ("jellyhook:refresh-test", process_refresh_message)
    ]
    supervise.assert_called_once()


@mock.patch.dict("workers.utils.SingletonMeta._instances", {}, clear=True)
def test_main_refuses_to_start_on_invalid_random_playlists(tmp_path):
    """Story 36: the worker rejects the Random Playlist config the scheduler would reject."""
    path = tmp_path / "jellyhook.yaml"
    path.write_text(
        "worker:\n"
        "  webhooks:\n"
        "    item_added: {enabled: true, queue: 'jellyfin:item_added', services: []}\n"
        "random_playlists:\n"
        "  - {name: Shuffle, series: [], size: 25, schedule: '0 1 * * 0'}\n"
    )

    with (
        mock.patch.object(worker_main.config, "JELLYHOOK_CONFIG_PATH", str(path)),
        mock.patch.object(worker_main.rabbitmq, "QueueConsumer") as consumer_class,
        mock.patch.object(worker_main, "supervise") as supervise,
        mock.patch.object(worker_main, "logger") as logger,
    ):
        assert worker_main.main() == 1

    consumer_class.assert_not_called()
    supervise.assert_not_called()
    logged = " ".join(str(call.args[0]) for call in logger.error.call_args_list)
    assert "'Shuffle'" in logged
    assert "series" in logged


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


@pytest.fixture
def entrypoint(worker_config):
    """Patch everything `python -m workers.main` touches, so running it has no side effects."""
    with (
        mock.patch.object(worker_main.config.WorkerConfig, "load", return_value=worker_config),
        mock.patch("workers.supervisor.supervise", return_value=3) as supervise,
        mock.patch("workers.clients.rabbitmq.QueueConsumer"),
        mock.patch("signal.signal"),  # don't replace pytest's signal handlers
    ):
        yield supervise


@pytest.mark.filterwarnings("ignore:.*found in sys.modules:RuntimeWarning")
def test_running_the_module_exits_with_the_supervisors_code(entrypoint):
    """`python -m workers.main` must turn a failed consumer into a non-zero exit status."""
    with (
        mock.patch.object(worker_main.config, "DEBUG", False),
        pytest.raises(SystemExit) as exit_info,
    ):
        runpy.run_module("workers.main", run_name="__main__")

    assert exit_info.value.code == 3
    entrypoint.assert_called_once()


@pytest.mark.filterwarnings("ignore:.*found in sys.modules:RuntimeWarning")
def test_running_the_module_in_debug_mode_opens_the_debugger_port(entrypoint):
    fake_debugpy = types.SimpleNamespace(listen=mock.MagicMock())

    with (
        mock.patch.object(worker_main.config, "DEBUG", True),
        mock.patch.dict(sys.modules, {"debugpy": fake_debugpy}),
        pytest.raises(SystemExit),
    ):
        runpy.run_module("workers.main", run_name="__main__")

    fake_debugpy.listen.assert_called_once_with(("0.0.0.0", 5678))
