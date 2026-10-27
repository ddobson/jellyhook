import threading
import time
from unittest import mock

import pika
import pika.exceptions
import pytest

from workers.clients import rabbitmq, topology
from workers.clients.rabbitmq import QueueConsumer
from workers.results import ProcessingResult

QUEUE = "jellyfin:item_added"


def wait_until(condition, timeout=2.0):
    """Poll until the condition holds, failing the test if it never does."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.005)
    pytest.fail("condition not met in time")


def make_props(headers=None, content_type="application/json"):
    """Build message properties."""
    return pika.BasicProperties(headers=headers, content_type=content_type)


@pytest.fixture
def connection():
    """A fake connection whose threadsafe callbacks run immediately."""
    connection = mock.MagicMock(name="connection")
    connection.is_open = True
    connection.add_callback_threadsafe.side_effect = lambda callback: callback()
    return connection


@pytest.fixture
def channel(connection):
    """The channel the fake connection hands out."""
    channel = connection.channel.return_value
    channel.basic_consume.return_value = "ctag-1"
    channel.queue_declare.return_value.method.message_count = 0
    return channel


@pytest.fixture
def stop_event():
    return threading.Event()


@pytest.fixture
def make_consumer(connection, stop_event):
    """Factory for consumers wired to the fake connection."""

    def factory(handler=None, max_attempts=3, job_slots=None):
        handler = handler or mock.MagicMock(return_value=ProcessingResult.success())
        consumer = QueueConsumer(
            webhook_id="item_added",
            queue=QUEUE,
            handler=handler,
            job_slots=job_slots or threading.BoundedSemaphore(1),
            stop_event=stop_event,
            max_attempts=max_attempts,
            connect=lambda: connection,
        )
        consumer._connection = connection
        return consumer

    return factory


def deliver(consumer, channel, body=b"{}", headers=None, delivery_tag=7):
    """Simulate the broker delivering a message and wait for the job to settle."""
    method = mock.MagicMock(delivery_tag=delivery_tag)
    consumer._on_message(channel, method, make_props(headers), body)
    wait_until(lambda: consumer.in_flight == 0)


def published(channel):
    """The (queue, properties) of every message the consumer republished."""
    return [
        (c.kwargs["routing_key"], c.kwargs["properties"])
        for c in channel.basic_publish.call_args_list
    ]


# --- connection -------------------------------------------------------------------------


def test_open_connection_sets_heartbeat_and_timeouts():
    """Heartbeats and timeouts are explicit so a dead peer is noticed, not waited on forever."""
    with (
        mock.patch.object(rabbitmq, "BlockingConnection") as blocking_connection,
        mock.patch.object(rabbitmq.pika, "ConnectionParameters") as parameters,
        mock.patch.object(rabbitmq.config, "RABBITMQ_HOST", "broker"),
        mock.patch.object(rabbitmq.config, "RABBITMQ_PORT", 5999),
        mock.patch.object(rabbitmq.config, "RABBITMQ_HEARTBEAT", 30),
    ):
        result = rabbitmq.open_connection()

    kwargs = parameters.call_args.kwargs
    assert kwargs["host"] == "broker"
    assert kwargs["port"] == 5999
    assert kwargs["heartbeat"] == 30
    assert kwargs["socket_timeout"] == 10
    assert kwargs["connection_attempts"] == 3
    assert result is blocking_connection.return_value


# --- run loop ---------------------------------------------------------------------------


def test_run_sets_up_channel_then_consumes_until_stopped(
    make_consumer, connection, channel, stop_event
):
    """A single prefetch, publisher confirms, all three queues, then pump events until stopped."""
    consumer = make_consumer()
    calls = []

    def pump(time_limit):
        calls.append(time_limit)
        if len(calls) == 3:
            stop_event.set()

    connection.process_data_events.side_effect = pump

    consumer.run(shutdown_grace=1)

    channel.confirm_delivery.assert_called_once()
    channel.basic_qos.assert_called_once_with(prefetch_count=1)
    assert channel.queue_declare.call_count == 3
    channel.basic_consume.assert_called_once_with(
        queue=QUEUE, on_message_callback=consumer._on_message
    )
    assert calls[:3] == [1, 1, 1]
    channel.basic_cancel.assert_called_once_with("ctag-1")
    connection.close.assert_called_once()


def test_run_raises_when_the_connection_drops_and_still_closes(make_consumer, connection, channel):
    """No in-process reconnect: the error escapes so the supervisor can end the process."""
    consumer = make_consumer()
    connection.process_data_events.side_effect = pika.exceptions.StreamLostError("gone")

    with pytest.raises(pika.exceptions.StreamLostError):
        consumer.run(shutdown_grace=1)

    connection.close.assert_called_once()


def test_run_tolerates_close_failing_on_a_dead_connection(make_consumer, connection, channel):
    consumer = make_consumer()
    connection.process_data_events.side_effect = pika.exceptions.StreamLostError("gone")
    connection.close.side_effect = pika.exceptions.ConnectionWrongStateError("already closed")

    with pytest.raises(pika.exceptions.StreamLostError):
        consumer.run(shutdown_grace=1)


def test_shutdown_waits_for_in_flight_jobs(make_consumer, connection, channel, stop_event):
    """On stop, a running job gets time to finish and be acked before the connection closes."""
    consumer = make_consumer()
    consumer._in_flight = 1
    stop_event.set()
    pumped = []

    def pump(time_limit):
        pumped.append(time_limit)
        if len(pumped) == 2:
            consumer._end_job()

    connection.process_data_events.side_effect = pump

    consumer.run(shutdown_grace=5)

    assert len(pumped) == 2
    assert consumer.in_flight == 0
    connection.close.assert_called_once()


def test_shutdown_gives_up_after_the_grace_period(make_consumer, connection, channel, stop_event):
    """A job that outlives the grace is abandoned; the broker redelivers its message."""
    consumer = make_consumer()
    consumer._in_flight = 1
    stop_event.set()

    with mock.patch.object(rabbitmq, "logger") as logger:
        consumer.run(shutdown_grace=0.05)

    logger.warning.assert_called_once()
    assert "redeliver" in logger.warning.call_args.args[0]
    connection.close.assert_called_once()


# --- message handling -------------------------------------------------------------------


def test_successful_job_is_acked_without_republishing(make_consumer, channel):
    handler = mock.MagicMock(return_value=ProcessingResult.success())
    consumer = make_consumer(handler)

    deliver(consumer, channel, body=b'{"Name": "x"}', delivery_tag=7)

    handler.assert_called_once_with("item_added", b'{"Name": "x"}')
    channel.basic_ack.assert_called_once_with(7)
    channel.basic_publish.assert_not_called()


def test_retryable_failure_goes_to_the_retry_queue_before_the_ack(make_consumer, channel):
    """Publish first, ack second: a crash in between duplicates the message, never loses it."""
    handler = mock.MagicMock(return_value=ProcessingResult.retry("file not on disk"))
    consumer = make_consumer(handler)

    deliver(consumer, channel, body=b"payload", delivery_tag=9)

    [(queue, properties)] = published(channel)
    assert queue == f"{QUEUE}.retry"
    assert properties.headers[topology.ATTEMPTS_HEADER] == 1
    assert properties.headers[topology.FAILURE_REASON_HEADER] == "file not on disk"
    assert properties.delivery_mode == pika.DeliveryMode.Persistent.value
    assert channel.basic_publish.call_args.kwargs["body"] == b"payload"
    assert channel.basic_publish.call_args.kwargs["mandatory"] is True

    call_names = [c[0] for c in channel.method_calls if c[0] in ("basic_publish", "basic_ack")]
    assert call_names == ["basic_publish", "basic_ack"]
    channel.basic_ack.assert_called_once_with(9)


def test_attempt_count_carries_over_from_previous_deliveries(make_consumer, channel):
    handler = mock.MagicMock(return_value=ProcessingResult.retry("still missing"))
    consumer = make_consumer(handler, max_attempts=5)

    deliver(consumer, channel, headers={topology.ATTEMPTS_HEADER: 2})

    [(queue, properties)] = published(channel)
    assert queue == f"{QUEUE}.retry"
    assert properties.headers[topology.ATTEMPTS_HEADER] == 3


def test_retries_exhausted_moves_message_to_dead_letter_queue(make_consumer, channel):
    handler = mock.MagicMock(return_value=ProcessingResult.retry("still missing"))
    consumer = make_consumer(handler, max_attempts=3)

    deliver(consumer, channel, headers={topology.ATTEMPTS_HEADER: 2})

    [(queue, properties)] = published(channel)
    assert queue == f"{QUEUE}.dead"
    assert properties.headers[topology.ATTEMPTS_HEADER] == 3
    assert properties.headers[topology.FAILURE_REASON_HEADER] == "still missing"
    channel.basic_ack.assert_called_once()


def test_permanent_failure_is_dead_lettered_not_dropped(make_consumer, channel):
    """The old behavior was nack(requeue=False) with no DLX, which silently discarded."""
    handler = mock.MagicMock(return_value=ProcessingResult.failed("Unknown service: x"))
    consumer = make_consumer(handler)

    deliver(consumer, channel)

    [(queue, properties)] = published(channel)
    assert queue == f"{QUEUE}.dead"
    assert properties.headers[topology.FAILURE_REASON_HEADER] == "Unknown service: x"
    channel.basic_nack.assert_not_called()
    channel.basic_ack.assert_called_once()


def test_republish_keeps_custom_headers_and_drops_broker_death_headers(make_consumer, channel):
    handler = mock.MagicMock(return_value=ProcessingResult.retry("again"))
    consumer = make_consumer(handler)
    headers = {
        "source": "jellyfin",
        "x-death": [{"count": 1, "reason": "expired"}],
        "x-first-death-queue": f"{QUEUE}.retry",
    }

    deliver(consumer, channel, headers=headers)

    [(_, properties)] = published(channel)
    assert properties.headers["source"] == "jellyfin"
    assert "x-death" not in properties.headers
    assert "x-first-death-queue" not in properties.headers
    assert properties.content_type == "application/json"


def test_handler_that_raises_is_dead_lettered_with_the_reason(make_consumer, channel):
    """The handler should not raise, but if it does the message must survive."""
    handler = mock.MagicMock(side_effect=KeyError("Name"))
    consumer = make_consumer(handler)

    with mock.patch.object(rabbitmq, "logger"):
        deliver(consumer, channel)

    [(queue, properties)] = published(channel)
    assert queue == f"{QUEUE}.dead"
    assert "KeyError" in properties.headers[topology.FAILURE_REASON_HEADER]


def test_handler_that_raises_a_transient_error_is_retried(make_consumer, channel):
    handler = mock.MagicMock(side_effect=ConnectionError("jellyfin down"))
    consumer = make_consumer(handler)

    with mock.patch.object(rabbitmq, "logger"):
        deliver(consumer, channel)

    [(queue, _)] = published(channel)
    assert queue == f"{QUEUE}.retry"


def test_failed_republish_requeues_instead_of_acking(make_consumer, channel):
    """If the broker won't confirm the retry copy, the original must stay on the queue."""
    channel.basic_publish.side_effect = pika.exceptions.UnroutableError([])
    handler = mock.MagicMock(return_value=ProcessingResult.retry("x"))
    consumer = make_consumer(handler)

    with mock.patch.object(rabbitmq, "logger"):
        deliver(consumer, channel, delivery_tag=4)

    channel.basic_ack.assert_not_called()
    channel.basic_nack.assert_called_once_with(4, requeue=True)


def test_ack_on_a_closed_channel_does_not_raise(make_consumer, channel):
    """A lost channel means the broker redelivers; the job thread bookkeeping must still finish."""
    channel.basic_ack.side_effect = pika.exceptions.ChannelWrongStateError("closed")
    channel.basic_nack.side_effect = pika.exceptions.ChannelWrongStateError("closed")
    consumer = make_consumer()

    with mock.patch.object(rabbitmq, "logger"):
        deliver(consumer, channel)

    assert consumer.in_flight == 0


def test_job_finishing_after_the_connection_closed_is_left_for_redelivery(
    make_consumer, connection, channel
):
    connection.add_callback_threadsafe.side_effect = pika.exceptions.ConnectionWrongStateError("x")
    consumer = make_consumer()

    with mock.patch.object(rabbitmq, "logger") as logger:
        deliver(consumer, channel)

    channel.basic_ack.assert_not_called()
    assert "redeliver" in logger.error.call_args.args[0]


def test_jobs_share_a_slot_pool_so_heavy_work_runs_one_at_a_time(make_consumer, channel):
    """A second delivery waits for the slot instead of running concurrently."""
    slots = threading.BoundedSemaphore(1)
    running = []
    peak = []
    gate = threading.Event()

    def handler(webhook_id, body):
        running.append(body)
        peak.append(len(running))
        gate.wait(timeout=2)
        running.remove(body)
        return ProcessingResult.success()

    consumer = make_consumer(handler, job_slots=slots)
    method = mock.MagicMock(delivery_tag=1)
    consumer._on_message(channel, method, make_props(), b"one")
    consumer._on_message(channel, method, make_props(), b"two")
    wait_until(lambda: len(running) == 1)
    time.sleep(0.05)
    assert len(running) == 1  # the other job is waiting for the slot

    gate.set()
    wait_until(lambda: consumer.in_flight == 0)
    assert max(peak) == 1


def test_job_waiting_for_a_slot_is_abandoned_on_shutdown(make_consumer, channel, stop_event):
    """It never started, so it is not acked; the broker redelivers it after we disconnect."""
    slots = threading.BoundedSemaphore(1)
    slots.acquire()  # another job holds the only slot
    handler = mock.MagicMock()
    consumer = make_consumer(handler, job_slots=slots)
    stop_event.set()

    with mock.patch.object(rabbitmq, "logger"):
        deliver(consumer, channel)

    handler.assert_not_called()
    channel.basic_ack.assert_not_called()
    channel.basic_publish.assert_not_called()


def test_slot_is_released_when_the_handler_raises(make_consumer, channel):
    slots = threading.BoundedSemaphore(1)
    handler = mock.MagicMock(side_effect=ValueError("boom"))
    consumer = make_consumer(handler, job_slots=slots)

    with mock.patch.object(rabbitmq, "logger"):
        deliver(consumer, channel)

    assert slots.acquire(blocking=False) is True


def test_run_warns_when_messages_are_waiting_on_the_dead_letter_queue(
    make_consumer, connection, channel, stop_event
):
    """Nothing consumes `.dead`, so startup is the worker's chance to point at it."""
    channel.queue_declare.return_value.method.message_count = 3
    stop_event.set()
    consumer = make_consumer()

    with mock.patch.object(rabbitmq, "logger") as logger:
        consumer.run(shutdown_grace=1)

    [warning] = [c.args[0] for c in logger.warning.call_args_list if "waiting on" in c.args[0]]
    assert "3 message(s)" in warning
    assert f"{QUEUE}.dead" in warning


def test_run_stays_quiet_when_the_dead_letter_queue_is_empty(
    make_consumer, connection, channel, stop_event
):
    stop_event.set()
    consumer = make_consumer()

    with mock.patch.object(rabbitmq, "logger") as logger:
        consumer.run(shutdown_grace=1)

    logger.warning.assert_not_called()


def test_close_without_a_connection_is_a_no_op(make_consumer, connection):
    consumer = make_consumer()
    consumer._connection = None

    consumer._close()

    connection.close.assert_not_called()


def test_close_leaves_an_already_closed_connection_alone(make_consumer, connection):
    connection.is_open = False
    consumer = make_consumer()

    consumer._close()

    connection.close.assert_not_called()
    assert consumer._connection is None


def test_job_keeps_waiting_for_a_slot_until_one_frees_up(make_consumer, channel):
    """The slot wait polls once a second so it can notice a shutdown; it must not give up early."""
    slots = mock.MagicMock()
    slots.acquire.side_effect = [False, False, True]
    handler = mock.MagicMock(return_value=ProcessingResult.success())
    consumer = make_consumer(handler, job_slots=slots)

    deliver(consumer, channel)

    assert slots.acquire.call_count == 3
    handler.assert_called_once()
    slots.release.assert_called_once()
    channel.basic_ack.assert_called_once()


def test_job_finishing_after_the_consumer_closed_is_left_for_redelivery(make_consumer, channel):
    """If the connection is already gone there is nothing to settle on."""
    consumer = make_consumer()
    consumer._connection = None

    with mock.patch.object(rabbitmq, "logger") as logger:
        deliver(consumer, channel)

    channel.basic_ack.assert_not_called()
    assert "redeliver" in logger.error.call_args.args[0]


def test_republish_without_a_reason_omits_the_reason_header(make_consumer, channel):
    handler = mock.MagicMock(return_value=ProcessingResult.retry(""))
    consumer = make_consumer(handler)

    deliver(consumer, channel)

    [(_, properties)] = published(channel)
    assert topology.FAILURE_REASON_HEADER not in properties.headers
    assert properties.headers[topology.ATTEMPTS_HEADER] == 1
