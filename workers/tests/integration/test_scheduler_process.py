"""The scheduler process (`python -m workers.scheduler`) against a real broker."""

import json
import os
import pathlib
import signal
import subprocess
import sys
from collections.abc import Callable, Iterator

import pika
import pytest
import support
from pika.exceptions import ChannelClosedByBroker
from support import Broker

from workers.clients import topology
from workers.services.refresh import refresh_message

pytestmark = pytest.mark.integration

NAME = "Bob's Burgers Shuffle"


@pytest.fixture
def state_file(tmp_path: pathlib.Path) -> pathlib.Path:
    """Where the scheduler records its publishes; it does not exist yet."""
    return tmp_path / "state" / "scheduler-state.json"


@pytest.fixture
def scheduler(
    broker: Broker, queue: str, tmp_path: pathlib.Path, state_file: pathlib.Path
) -> Iterator[Callable[..., subprocess.Popen]]:
    """Factory that starts `python -m workers.scheduler` against the test broker.

    The config has one Random Playlist, firing on Sundays at 1am. There is no state file, so the
    long-running scheduler owes it a catch-up Refresh as soon as it starts. Output goes to
    `scheduler.log` in the test's temporary directory.
    """
    config_file = tmp_path / "jellyhook.yaml"
    config_file.write_text(
        "random_playlists:\n"
        f'  - name: "{NAME}"\n'
        '    series: ["Bob\'s Burgers"]\n'
        "    size: 25\n"
        "    schedule: '0 1 * * 0'\n"
    )
    env = {
        **os.environ,
        "RABBITMQ_HOST": "127.0.0.1",
        "RABBITMQ_PORT": str(broker.port),
        "JELLYHOOK_CONFIG_PATH": str(config_file),
        "SCHEDULER_STATE_PATH": str(state_file),
        "REFRESH_QUEUE": queue,
    }
    log = (tmp_path / "scheduler.log").open("w")
    started: list[subprocess.Popen] = []

    def start(*args: str) -> subprocess.Popen:
        process = subprocess.Popen(  # noqa: S603 - our own interpreter and module
            [sys.executable, "-m", "workers.scheduler", *args],
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        started.append(process)
        return process

    yield start

    for process in started:
        if process.poll() is None:
            process.kill()
            process.wait()
    log.close()


def reap(process: subprocess.Popen, tmp_path: pathlib.Path, timeout: float = 30) -> int:
    """Wait for the scheduler to exit, failing the test with its output if it does not."""
    try:
        return process.wait(timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        output = (tmp_path / "scheduler.log").read_text()
        pytest.fail(f"scheduler did not exit in {timeout}s:\n{output}")


def declared_with(broker: Broker, queue: str, arguments: dict | None) -> bool:
    """True if `queue` exists and redeclaring it durable with exactly `arguments` is accepted."""
    try:
        with support.connect(broker) as connection:
            channel = connection.channel()
            channel.queue_declare(queue=queue, passive=True)
            channel.queue_declare(queue=queue, durable=True, arguments=arguments)
    except ChannelClosedByBroker:
        return False
    return True


def assert_one_persistent_json_refresh(broker: Broker, queue: str) -> None:
    ((properties, body),) = support.take_all(broker, queue)
    assert json.loads(body) == {"name": NAME}
    assert body == refresh_message(NAME)  # exactly what the worker's Refresh handler reads
    assert properties.delivery_mode == pika.DeliveryMode.Persistent.value
    assert properties.content_type == "application/json"


def test_a_manual_run_lands_on_the_refresh_queue_as_a_persistent_json_message(
    broker, queue, scheduler, state_file, tmp_path
):
    """`run "<name>"` publishes one confirmed Refresh, exits 0 and leaves the Schedule alone."""
    process = scheduler("run", NAME)

    assert reap(process, tmp_path) == 0

    assert_one_persistent_json_refresh(broker, queue)
    assert not state_file.exists()
    # The companions are declared as the worker declares them, so a worker starting later
    # redeclares the whole topology without PRECONDITION_FAILED
    assert declared_with(
        broker,
        topology.retry_queue_name(queue),
        {
            "x-message-ttl": topology.RETRY_DELAY_MS,
            "x-dead-letter-exchange": "",
            "x-dead-letter-routing-key": queue,
        },
    )
    assert declared_with(broker, topology.dead_queue_name(queue), None)


def test_the_scheduler_publishes_a_catch_up_refresh_records_it_and_stops_on_sigterm(
    broker, queue, scheduler, state_file, tmp_path
):
    """A never-refreshed playlist is published at startup; SIGTERM then ends the wait for Sunday."""
    process = scheduler()

    assert support.wait_until(state_file.exists, timeout=30)
    process.send_signal(signal.SIGTERM)

    assert reap(process, tmp_path, timeout=10) == 0
    assert_one_persistent_json_refresh(broker, queue)
    assert list(json.loads(state_file.read_text())) == [NAME]


def test_the_scheduler_exits_non_zero_when_the_broker_is_down(
    broker, queue, scheduler, state_file, tmp_path
):
    """Fail fast: with no broker to confirm the catch-up, Docker's restart policy takes over."""
    support.stop_broker(broker)
    try:
        process = scheduler()
        assert reap(process, tmp_path, timeout=60) == 1
        assert not state_file.exists()  # still overdue, so the next start publishes it
    finally:
        support.start_stopped_broker(broker)
