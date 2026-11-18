"""The scheduler process, entered through `main()` (what `python -m workers.scheduler` runs).

The scheduler core has its own tests (`test_scheduler.py`). These cover the wiring around it: the
real clock and signal handlers, and the RabbitMQ publisher with the broker connection faked.
"""

import json
import os
import runpy
import signal
import sys
import threading
import time
from datetime import datetime
from unittest import mock

import pika
import pika.exceptions
import pytest
import yaml

from workers.scheduler import __main__ as scheduler_main
from workers.services.refresh import refresh_message

QUEUE = "jellyhook:refresh-test"
NAME = "Bob's Burgers Shuffle"


@pytest.fixture
def settings(tmp_path, restore_signal_handlers):
    """Point the scheduler at a temporary config, state file and Refresh queue.

    The config has one Random Playlist that fires on Sundays at 1am. The scheduler runs on the
    real clock, so a watchdog sends SIGTERM after 10 s: a test whose scheduler wrongly settles
    into waiting for Sunday then fails on its exit code instead of hanging.
    """
    watchdog = threading.Timer(10, os.kill, args=(os.getpid(), signal.SIGTERM))
    watchdog.daemon = True
    watchdog.start()
    config_path = tmp_path / "jellyhook.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "random_playlists": [
                    {"name": NAME, "series": ["Bob's Burgers"], "size": 25, "schedule": "0 1 * * 0"}
                ]
            }
        )
    )
    state_path = tmp_path / "state" / "scheduler-state.json"
    with (
        mock.patch.object(scheduler_main.config, "JELLYHOOK_CONFIG_PATH", str(config_path)),
        mock.patch.object(scheduler_main.config, "SCHEDULER_STATE_PATH", str(state_path)),
        mock.patch.object(scheduler_main.config, "REFRESH_QUEUE", QUEUE),
    ):
        yield state_path
    watchdog.cancel()


def record_publish_now(state_path) -> None:
    """Mark the Random Playlist as just refreshed, so nothing is overdue."""
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps({NAME: datetime.now().astimezone().isoformat()}))


class SignalNotHandledError(Exception):
    """A signal reached the test's fallback handler instead of the scheduler's."""


@pytest.fixture
def restore_signal_handlers():
    """`main()` installs SIGTERM and SIGINT handlers; put pytest's back afterwards.

    Until then a fallback handler stands in, so a scheduler that forgot to install its own fails
    the test (the exception ends the wait) instead of SIGTERM killing pytest.
    """
    previous = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}

    def fallback(signum, frame):
        raise SignalNotHandledError(signal.Signals(signum).name)

    for signum in previous:
        signal.signal(signum, fallback)
    yield
    for signum, handler in previous.items():
        signal.signal(signum, handler)


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT], ids=["SIGTERM", "SIGINT"])
def test_a_signal_stops_the_scheduler_promptly_while_it_waits_for_the_next_schedule(
    settings, restore_signal_handlers, signum
):
    """`docker stop` must not wait out the week until the next Schedule fires."""
    record_publish_now(settings)
    timer = threading.Timer(0.5, os.kill, args=(os.getpid(), signum))

    with mock.patch("workers.clients.rabbitmq.open_connection") as open_connection:
        started = time.monotonic()
        timer.start()
        try:
            exit_code = scheduler_main.main([])
        finally:
            timer.cancel()
        elapsed = time.monotonic() - started

    assert exit_code == 0
    assert elapsed < 5
    open_connection.assert_not_called()  # nothing was due, so the broker was never touched


def test_an_unreachable_broker_exits_non_zero_and_records_nothing(settings):
    """Fail fast: Docker's restart policy retries the catch-up Refresh, not the process."""
    with mock.patch(
        "workers.clients.rabbitmq.open_connection",
        side_effect=pika.exceptions.AMQPConnectionError("connection refused"),
    ) as open_connection:
        exit_code = scheduler_main.main([])

    assert exit_code == 1
    open_connection.assert_called_once()  # the never-refreshed playlist's catch-up was attempted
    assert not settings.exists()  # unconfirmed, so the Refresh is still overdue next start


@pytest.fixture
def connection():
    """A fake broker connection; its channel records what was declared and published."""
    connection = mock.MagicMock(name="connection")
    connection.is_open = True
    connection.channel.return_value.queue_declare.return_value.method.message_count = 0
    return connection


def published(connection) -> list[tuple[str, bytes, pika.BasicProperties]]:
    """Each publish on the fake connection as (routing key, body, properties)."""
    return [
        (call.kwargs["routing_key"], call.kwargs["body"], call.kwargs["properties"])
        for call in connection.channel.return_value.basic_publish.call_args_list
    ]


def declared(connection) -> list[str]:
    return [
        call.kwargs["queue"]
        for call in connection.channel.return_value.queue_declare.call_args_list
    ]


def test_a_manual_run_publishes_a_persistent_json_refresh_on_its_own_connection(
    settings, connection
):
    """`run "<name>"`: one confirmed, persistent Refresh, then the connection is closed."""
    with mock.patch("workers.clients.rabbitmq.open_connection", return_value=connection):
        exit_code = scheduler_main.main(["run", NAME])

    assert exit_code == 0
    ((routing_key, body, properties),) = published(connection)
    assert routing_key == QUEUE
    assert body == refresh_message(NAME)
    assert properties.delivery_mode == pika.DeliveryMode.Persistent.value
    assert properties.content_type == "application/json"
    # Declared the way the worker declares it, so neither side's redeclare is refused
    assert declared(connection) == [QUEUE, f"{QUEUE}.retry", f"{QUEUE}.dead"]
    connection.channel.return_value.confirm_delivery.assert_called_once()
    connection.close.assert_called_once()
    assert not settings.exists()  # a manual run leaves the Schedule alone


@pytest.mark.parametrize(
    "failure",
    [
        pika.exceptions.StreamLostError("Transport indicated EOF"),
        pika.exceptions.NackError([]),
        pika.exceptions.UnroutableError([]),
    ],
    ids=["connection lost", "nacked", "unroutable"],
)
def test_a_refresh_the_broker_does_not_confirm_exits_non_zero_and_records_nothing(
    settings, connection, failure
):
    """Only a confirmed publish counts; anything else ends the process for Docker to restart."""
    connection.channel.return_value.basic_publish.side_effect = failure

    with mock.patch("workers.clients.rabbitmq.open_connection", return_value=connection):
        exit_code = scheduler_main.main([])

    assert exit_code == 1
    assert not settings.exists()
    connection.close.assert_called_once()  # not leaked, even though the publish failed


@pytest.mark.filterwarnings("ignore:.*found in sys.modules:RuntimeWarning")
@pytest.mark.parametrize(
    ("argv", "code"),
    [(["scheduler", "run", "Removed Shuffle"], 1), (["scheduler", "bogus"], 2)],
    ids=["unknown name", "bad arguments"],
)
def test_running_the_module_passes_its_arguments_and_exits_with_the_result(settings, argv, code):
    """`python -m workers.scheduler run "<name>"` reports a failure in its exit status."""
    with (
        mock.patch.object(sys, "argv", argv),
        mock.patch("workers.clients.rabbitmq.open_connection") as open_connection,
        pytest.raises(SystemExit) as exit_info,
    ):
        runpy.run_module("workers.scheduler", run_name="__main__")

    assert exit_info.value.code == code
    open_connection.assert_not_called()
