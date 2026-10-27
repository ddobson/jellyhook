"""QueueConsumer against a real RabbitMQ: the behaviors the unit tests can only mock."""

import json
import re
import threading
import time

import pika
import pytest
import support
from pika.exceptions import AMQPError

from workers.clients import topology
from workers.results import ProcessingResult

pytestmark = pytest.mark.integration


def recorder(*results: ProcessingResult, default: ProcessingResult | None = None):
    """A handler that returns the given results in turn, then `default`, and records its calls."""
    calls: list[bytes] = []
    remaining = list(results)

    def handler(webhook_id: str, body: bytes) -> ProcessingResult:
        calls.append(body)
        if remaining:
            return remaining.pop(0)
        return default or ProcessingResult.success()

    handler.calls = calls  # type: ignore[attr-defined]
    return handler


def settled(broker, queue: str) -> bool:
    """Nothing waiting on the work queue and nothing delivered but unacknowledged."""
    return support.ready_count(broker, queue) == 0 and support.unacked_count(broker, queue) == 0


def test_a_successful_message_is_acked_and_leaves_nothing_behind(broker, queue, start_consumer):
    handler = recorder()
    support.publish(broker, queue, b'{"Name": "Item A"}')

    start_consumer(queue, handler)

    assert support.wait_until(lambda: handler.calls)
    assert support.wait_until(lambda: settled(broker, queue))
    assert handler.calls == [b'{"Name": "Item A"}']
    assert support.ready_count(broker, topology.retry_queue_name(queue)) == 0
    assert support.ready_count(broker, topology.dead_queue_name(queue)) == 0


def test_a_transient_failure_is_retried_after_the_delay_then_succeeds(
    broker, queue, start_consumer, fast_retry
):
    handler = recorder(ProcessingResult.retry("MediaNotFoundError: not on disk yet"))
    support.publish(broker, queue, b"item")

    start_consumer(queue, handler)

    assert support.wait_until(lambda: len(handler.calls) == 2, timeout=20)
    assert support.wait_until(lambda: settled(broker, queue))
    assert support.ready_count(broker, topology.dead_queue_name(queue)) == 0


def test_retries_are_capped_then_dead_lettered_with_the_reason(
    broker, queue, start_consumer, fast_retry
):
    handler = recorder(default=ProcessingResult.retry("MediaNotFoundError: never lands"))
    support.publish(broker, queue, b'{"Name": "Item A"}')

    start_consumer(queue, handler, max_attempts=3)

    dead = topology.dead_queue_name(queue)
    assert support.wait_until(lambda: support.ready_count(broker, dead) == 1, timeout=30)
    ((properties, body),) = support.take_all(broker, dead)
    assert len(handler.calls) == 3
    assert body == b'{"Name": "Item A"}'
    assert properties.headers[topology.ATTEMPTS_HEADER] == 3
    assert "never lands" in properties.headers[topology.FAILURE_REASON_HEADER]
    assert properties.delivery_mode == pika.DeliveryMode.Persistent.value
    # The broker's own death bookkeeping from the trips through .retry is not carried along
    assert not any(key.startswith("x-death") for key in properties.headers)
    assert support.ready_count(broker, topology.retry_queue_name(queue)) == 0


def test_a_permanent_failure_goes_straight_to_the_dead_letter_queue(broker, queue, start_consumer):
    handler = recorder(default=ProcessingResult.failed("ServiceConfigurationError: unknown"))
    support.publish(broker, queue, b"item")

    start_consumer(queue, handler)

    dead = topology.dead_queue_name(queue)
    assert support.wait_until(lambda: support.ready_count(broker, dead) == 1)
    ((properties, _),) = support.take_all(broker, dead)
    assert len(handler.calls) == 1
    assert properties.headers[topology.ATTEMPTS_HEADER] == 1
    assert support.wait_until(lambda: settled(broker, queue))


@pytest.mark.parametrize(
    ("error", "lands_on"),
    [(ConnectionError("jellyfin down"), "retry"), (ValueError("bad payload"), "dead")],
)
def test_a_handler_that_raises_is_classified_not_lost(
    broker, queue, start_consumer, error, lands_on
):
    def handler(webhook_id: str, body: bytes) -> ProcessingResult:
        raise error

    support.publish(broker, queue, b"item")
    start_consumer(queue, handler)

    target = (
        topology.retry_queue_name(queue) if lands_on == "retry" else topology.dead_queue_name(queue)
    )
    assert support.wait_until(lambda: support.ready_count(broker, target) == 1)
    assert support.wait_until(lambda: settled(broker, queue))


def test_a_burst_is_processed_one_at_a_time_in_order_with_nothing_dead(
    broker, queue, start_consumer
):
    """A library scan: 50 events at once. One job runs, one message is unacknowledged."""
    total = 50
    seen: list[int] = []
    running_now = 0
    peak = 0
    lock = threading.Lock()

    def handler(webhook_id: str, body: bytes) -> ProcessingResult:
        nonlocal running_now, peak
        with lock:
            running_now += 1
            peak = max(peak, running_now)
        time.sleep(0.02)
        seen.append(json.loads(body)["n"])
        with lock:
            running_now -= 1
        return ProcessingResult.success()

    for n in range(total):
        support.publish(broker, queue, json.dumps({"n": n}))

    start_consumer(queue, handler)

    max_unacked = 0
    deadline = time.monotonic() + 60
    while len(seen) < total and time.monotonic() < deadline:
        max_unacked = max(max_unacked, support.unacked_count(broker, queue))
        time.sleep(0.1)

    assert seen == list(range(total))  # all of them, in publish order
    assert peak == 1
    assert max_unacked == 1  # prefetch 1: one in the consumer's hands, never more
    assert support.wait_until(lambda: settled(broker, queue))
    assert support.ready_count(broker, topology.dead_queue_name(queue)) == 0


def test_a_job_abandoned_at_shutdown_is_redelivered_not_lost(broker, queue, start_consumer):
    """Grace expires with the job still running: the message must come back for the next worker."""
    release = threading.Event()
    started = threading.Event()

    def stuck(webhook_id: str, body: bytes) -> ProcessingResult:
        started.set()
        release.wait(30)
        return ProcessingResult.success()

    support.publish(broker, queue, b"item")
    first = start_consumer(queue, stuck, grace=0.5)
    assert started.wait(20)

    first.stop_event.set()  # the first worker is told to stop, with a half second of grace
    first.thread.join(20)
    assert not first.alive and not first.errors
    release.set()

    second_handler = recorder()
    start_consumer(queue, second_handler)

    assert support.wait_until(lambda: second_handler.calls, timeout=20)
    assert second_handler.calls == [b"item"]
    assert support.wait_until(lambda: settled(broker, queue))


def test_shutdown_lets_a_job_that_fits_in_the_grace_finish_and_ack(broker, queue, start_consumer):
    finished = threading.Event()
    started = threading.Event()

    def short(webhook_id: str, body: bytes) -> ProcessingResult:
        started.set()
        time.sleep(1.5)
        finished.set()
        return ProcessingResult.success()

    support.publish(broker, queue, b"item")
    running = start_consumer(queue, short, grace=15)
    assert started.wait(20)

    running.stop_event.set()
    running.thread.join(30)

    assert finished.is_set()
    assert not running.errors
    assert settled(broker, queue)  # acked during the drain: nothing left to redeliver


def test_the_work_queue_can_be_declared_by_the_api_and_the_worker_alike(
    broker, queue, start_consumer
):
    """The API declares `durable=True` with no arguments. The worker must accept that queue."""
    support.publish(broker, queue, b"item")  # declares it the way the API does
    handler = recorder()

    running = start_consumer(queue, handler)

    assert support.wait_until(lambda: handler.calls)
    assert not running.errors


def test_a_work_queue_with_dead_letter_arguments_breaks_the_worker(broker, queue, start_consumer):
    """Why retries are done by the worker: adding arguments to the queue fails the redeclare."""
    with support.connect(broker) as connection:
        connection.channel().queue_declare(
            queue=queue, durable=True, arguments={"x-dead-letter-exchange": ""}
        )

    running = start_consumer(queue, recorder())

    running.thread.join(20)
    assert not running.alive
    assert running.errors
    assert "PRECONDITION_FAILED" in str(running.errors[0])


def _permissions(queue: str, *, with_default_exchange: bool) -> tuple[str, str, str]:
    """Configure / write / read patterns for a user that may only touch one webhook's queues."""
    names = f"{re.escape(queue)}(\\.retry|\\.dead)?"
    write = f"^(amq\\.default|{names})$" if with_default_exchange else f"^{names}$"
    return f"^{names}$", write, f"^{names}$"


def _restricted_user(broker, queue: str, *, with_default_exchange: bool) -> str:
    user = f"worker-{queue.split(':')[1]}"
    support.rabbitmqctl(broker, "add_user", user, "pw")
    configure, write, read = _permissions(queue, with_default_exchange=with_default_exchange)
    support.rabbitmqctl(broker, "set_permissions", "-p", "/", user, configure, write, read)
    return user


def test_the_documented_minimal_permissions_are_enough_for_every_path(
    broker, queue, start_consumer, fast_retry
):
    """CLAUDE.md: configure + read on the queues, write on them *and* amq.default."""
    user = _restricted_user(broker, queue, with_default_exchange=True)
    try:
        handler = recorder(
            ProcessingResult.retry("transient"), ProcessingResult.failed("permanent")
        )
        support.publish(broker, queue, b"item")  # as an admin; the worker is the restricted one

        running = start_consumer(queue, handler, user=user, password="pw")

        dead = topology.dead_queue_name(queue)  # retry path, then dead-letter path
        assert support.wait_until(lambda: support.ready_count(broker, dead) == 1, timeout=30)
        assert not running.errors
    finally:
        support.rabbitmqctl(broker, "delete_user", user)


def test_without_write_access_to_the_default_exchange_the_worker_cannot_settle(
    broker, queue, start_consumer
):
    """The failure mode the docs warn about: queue names alone are not enough."""
    user = _restricted_user(broker, queue, with_default_exchange=False)
    try:
        handler = recorder(default=ProcessingResult.retry("transient"))
        support.publish(broker, queue, b"item")

        running = start_consumer(queue, handler, user=user, password="pw")

        running.thread.join(30)
        assert not running.alive  # it stops (so Docker restarts it) instead of losing the message
        assert any(isinstance(e, AMQPError) for e in running.errors)
        assert support.total_count(broker, queue) == 1  # still there, ready or being redelivered
    finally:
        support.rabbitmqctl(broker, "delete_user", user)
