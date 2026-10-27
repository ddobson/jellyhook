"""Helpers for integration tests that run against a real RabbitMQ broker in Docker.

The broker is driven through the `docker` CLI so the suite needs no extra Python dependency, and
so tests can do what operators do: stop it, kill it, restart it. Everything here is a plain
function over a small immutable `Broker` record.
"""

import logging
import os
import shutil
import socket
import subprocess
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass

import pika
from pika.adapters.blocking_connection import BlockingConnection
from pika.exceptions import ChannelClosedByBroker

# Same major version the compose file runs. The management plugin is not needed: queue depth
# comes from AMQP and unacknowledged counts from rabbitmqctl.
IMAGE = os.getenv("JELLYHOOK_IT_RABBITMQ_IMAGE", "rabbitmq:4.0.5-alpine")


@dataclass(frozen=True)
class Broker:
    """A running RabbitMQ container: its Docker name and the host port mapped to AMQP."""

    container: str
    port: int


def docker(*args: str, check: bool = True, timeout: float = 120) -> subprocess.CompletedProcess:
    """Run a docker CLI command and capture its output."""
    return subprocess.run(  # noqa: S603 - fixed argument list
        ["docker", *args],  # noqa: S607
        capture_output=True,
        text=True,
        check=check,
        timeout=timeout,
    )


def docker_available() -> bool:
    """True if the docker CLI exists and its daemon answers."""
    if shutil.which("docker") is None:
        return False
    try:
        return docker("info", check=False, timeout=20).returncode == 0
    except subprocess.TimeoutExpired:
        return False


def free_port() -> int:
    """Ask the OS for a TCP port nobody is using right now."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def wait_until(condition: Callable[[], bool], timeout: float = 20, interval: float = 0.05) -> bool:
    """Poll until `condition()` is truthy. Returns False if the timeout passes first."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(interval)
    return bool(condition())


def connect(
    broker: Broker,
    *,
    user: str = "guest",
    password: str = "guest",  # noqa: S107
    heartbeat: int = 60,
) -> BlockingConnection:
    """Open a connection to the test broker (one quick attempt)."""
    return BlockingConnection(
        pika.ConnectionParameters(
            host="127.0.0.1",
            port=broker.port,
            credentials=pika.PlainCredentials(user, password),
            heartbeat=heartbeat,
            socket_timeout=5,
            connection_attempts=1,
        )
    )


def _accepts_connections(broker: Broker) -> bool:
    # pika logs every failed attempt at ERROR; while polling a starting broker that is just noise
    pika_logger = logging.getLogger("pika")
    previous = pika_logger.level
    pika_logger.setLevel(logging.CRITICAL)
    try:
        connect(broker).close()
    except Exception:  # noqa: BLE001 - anything means "not ready yet"
        return False
    finally:
        pika_logger.setLevel(previous)
    return True


def wait_ready(broker: Broker, timeout: float = 90) -> None:
    """Block until the broker accepts AMQP connections."""
    if not wait_until(lambda: _accepts_connections(broker), timeout=timeout, interval=0.5):
        logs = docker("logs", "--tail", "30", broker.container, check=False).stdout
        raise RuntimeError(f"RabbitMQ did not become ready in {timeout}s:\n{logs}")


def start_broker() -> Broker:
    """Start a throwaway RabbitMQ container on a free port and wait for it."""
    name = f"jellyhook-it-{uuid.uuid4().hex[:8]}"
    port = free_port()
    docker("run", "-d", "--name", name, "-p", f"127.0.0.1:{port}:5672", IMAGE)
    broker = Broker(container=name, port=port)
    wait_ready(broker)
    return broker


def remove_broker(broker: Broker) -> None:
    """Delete the container and its data."""
    docker("rm", "-f", "-v", broker.container, check=False)


def stop_broker(broker: Broker) -> None:
    """Gracefully stop the container (an operator running `docker stop`)."""
    docker("stop", "-t", "10", broker.container)


def kill_broker(broker: Broker) -> None:
    """SIGKILL the container: the broker gets no chance to shut down cleanly."""
    docker("kill", broker.container)


def start_stopped_broker(broker: Broker) -> None:
    """Start a stopped container again and wait until it accepts connections."""
    docker("start", broker.container)
    wait_ready(broker)


def restart_broker(broker: Broker) -> None:
    """`docker restart`, then wait until the broker is back."""
    docker("restart", "-t", "10", broker.container)
    wait_ready(broker)


def establish_queue(broker: Broker, queue: str) -> None:
    """Declare a durable queue, then restart the broker cleanly so the queue is safely on disk.

    RabbitMQ writes queue declarations to its metadata store lazily. A queue created in the same
    instant as a SIGKILL can vanish, taking its messages with it, which is a property of the
    broker and not of a confirmed publish. Real deployments are never in that window (the worker
    declares its queues at startup, long before traffic), so tests that kill the broker must
    establish the queue first or they test the wrong thing and fail at random.
    """
    with connect(broker) as connection:
        connection.channel().queue_declare(queue=queue, durable=True)
    restart_broker(broker)


def rabbitmqctl(broker: Broker, *args: str) -> str:
    """Run rabbitmqctl inside the container and return its output."""
    # As the rabbitmq user: a root exec can create a root-owned Erlang cookie the broker cannot read
    return docker("exec", "-u", "rabbitmq", broker.container, "rabbitmqctl", *args).stdout


def publish(
    broker: Broker,
    queue: str,
    body: bytes | str,
    *,
    headers: dict | None = None,
    persistent: bool = True,
) -> None:
    """Publish one confirmed message to a durable queue, declaring it as the API does."""
    with connect(broker) as connection:
        channel = connection.channel()
        channel.confirm_delivery()
        channel.queue_declare(queue=queue, durable=True)
        channel.basic_publish(
            exchange="",
            routing_key=queue,
            body=body,
            properties=pika.BasicProperties(
                delivery_mode=pika.DeliveryMode.Persistent if persistent else None,
                headers=headers,
            ),
            mandatory=True,
        )


def ready_count(broker: Broker, queue: str) -> int:
    """Messages waiting (not delivered) on a queue. 0 if the queue does not exist."""
    try:
        with connect(broker) as connection:
            channel = connection.channel()
            return int(channel.queue_declare(queue=queue, passive=True).method.message_count)
    except ChannelClosedByBroker:
        return 0


def unacked_count(broker: Broker, queue: str) -> int:
    """Messages delivered to a consumer but not yet acknowledged."""
    output = rabbitmqctl(broker, "list_queues", "name", "messages_unacknowledged", "--quiet")
    for line in output.splitlines():
        name, _, count = line.rpartition("\t")
        if name == queue:
            return int(count)
    return 0


def take_all(broker: Broker, queue: str) -> list[tuple[pika.BasicProperties, bytes]]:
    """Remove and return every message currently on a queue, with its properties."""
    taken = []
    with connect(broker) as connection:
        channel = connection.channel()
        while True:
            method, properties, body = channel.basic_get(queue=queue, auto_ack=True)
            if method is None:
                return taken
            taken.append((properties, body))


def delete_queues(broker: Broker, *queues: str) -> None:
    """Remove queues (ignoring ones that are not there)."""
    with connect(broker) as connection:
        for queue in queues:
            try:
                connection.channel().queue_delete(queue=queue)
            except ChannelClosedByBroker:
                continue
