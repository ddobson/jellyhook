"""Fixtures for the real-RabbitMQ integration suite.

These tests start a RabbitMQ container with Docker, so they are not part of the default `pytest`
run (see `testpaths` in pytest.ini). Run them with `make test_integration` or
`uv run pytest tests/integration`. Without Docker they are skipped, unless
JELLYHOOK_IT_REQUIRED=1, which CI sets so a missing Docker is a failure rather than a silent skip.
"""

import os
import threading
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

import pytest
import support
from support import Broker

from workers.clients import rabbitmq, topology
from workers.results import ProcessingResult

Handler = Callable[[str, bytes], ProcessingResult]


@pytest.fixture(scope="session")
def broker() -> Iterator[Broker]:
    """One real RabbitMQ for the whole run. Tests isolate themselves with unique queue names."""
    if not support.docker_available():
        message = "Docker is not available, so the integration tests cannot run"
        if os.getenv("JELLYHOOK_IT_REQUIRED") == "1":
            pytest.fail(message)
        pytest.skip(message)

    running = support.start_broker()
    yield running
    support.remove_broker(running)


@pytest.fixture
def queue(broker: Broker) -> Iterator[str]:
    """A unique work-queue name. The queue and its retry/dead companions are removed afterwards."""
    name = f"it:{uuid.uuid4().hex[:10]}"
    yield name
    support.delete_queues(
        broker, name, topology.retry_queue_name(name), topology.dead_queue_name(name)
    )


@pytest.fixture
def fast_retry(monkeypatch: pytest.MonkeyPatch) -> int:
    """Shrink the retry delay from 60 s to half a second, for queues declared in this test."""
    monkeypatch.setattr(topology, "RETRY_DELAY_MS", 500)
    return 500


@dataclass
class RunningConsumer:
    """A QueueConsumer running on its own thread, plus what happened to it."""

    consumer: rabbitmq.QueueConsumer
    thread: threading.Thread
    stop_event: threading.Event
    errors: list[BaseException] = field(default_factory=list)

    @property
    def alive(self) -> bool:
        return self.thread.is_alive()


def stop_consumer(running: RunningConsumer, timeout: float = 30) -> None:
    """Ask a consumer to stop and wait for its thread."""
    running.stop_event.set()
    running.thread.join(timeout)


@pytest.fixture
def start_consumer(broker: Broker) -> Iterator[Callable[..., RunningConsumer]]:
    """Factory that starts a consumer against the test broker and stops it at teardown."""
    started: list[RunningConsumer] = []

    def factory(
        queue: str,
        handler: Handler,
        *,
        max_attempts: int = 5,
        grace: float = 10,
        user: str = "guest",
        password: str = "guest",  # noqa: S107
    ) -> RunningConsumer:
        stop_event = threading.Event()
        consumer = rabbitmq.QueueConsumer(
            webhook_id="it",
            queue=queue,
            handler=handler,
            job_slots=threading.BoundedSemaphore(1),
            stop_event=stop_event,
            max_attempts=max_attempts,
            connect=lambda: support.connect(broker, user=user, password=password),
        )
        running = RunningConsumer(consumer, threading.Thread(daemon=True), stop_event)

        def target() -> None:
            try:
                consumer.run(grace)
            except BaseException as e:  # noqa: BLE001 - the test inspects what ended the run
                running.errors.append(e)

        running.thread = threading.Thread(target=target, name=f"it-{queue}", daemon=True)
        running.thread.start()
        started.append(running)
        return running

    yield factory

    for running in started:
        stop_consumer(running)
