import functools
import threading
import time
from collections.abc import Callable

import pika
import pika.credentials
import pika.exceptions
import pika.spec
from pika.adapters.blocking_connection import BlockingChannel, BlockingConnection

from workers import config
from workers.clients import topology
from workers.clients.retry_policy import Disposition, decide
from workers.errors import is_transient
from workers.logger import logger
from workers.results import ProcessingResult

MessageHandler = Callable[[str, bytes], ProcessingResult]

# Broker-added headers that must not be copied when a message is republished
_DEATH_HEADER_PREFIXES = ("x-death", "x-first-death", "x-last-death")


def open_connection() -> BlockingConnection:
    """Open a RabbitMQ connection using the worker configuration.

    Returns:
        BlockingConnection: A new connection. The caller owns it and must only use it from one
            thread (apart from `add_callback_threadsafe`), as pika requires.
    """
    credentials = pika.credentials.PlainCredentials(
        username=config.RABBITMQ_USER,
        password=config.RABBITMQ_PASS,
    )
    return BlockingConnection(
        pika.ConnectionParameters(
            host=config.RABBITMQ_HOST,
            port=config.RABBITMQ_PORT,
            credentials=credentials,
            heartbeat=config.RABBITMQ_HEARTBEAT,
            blocked_connection_timeout=300,
            socket_timeout=10,
            connection_attempts=3,
            retry_delay=2,
        ),
    )


class QueueConsumer:
    """Consumes one queue on a connection of its own.

    pika's BlockingConnection is not thread-safe, so the thread that calls `run()` is the only
    one that touches the connection, and it keeps heartbeats flowing by pumping
    `process_data_events()`. Jobs run on separate threads and hand their result back through
    `add_callback_threadsafe`, which is the one thread-safe call pika offers.

    Delivery is at-least-once: a message is only acknowledged after it has been processed (or
    parked on the retry/dead-letter queue). If the process dies or the connection drops first,
    the broker redelivers it.
    """

    def __init__(
        self,
        webhook_id: str,
        queue: str,
        handler: MessageHandler,
        job_slots: threading.BoundedSemaphore,
        stop_event: threading.Event,
        max_attempts: int = config.MAX_ATTEMPTS,
        connect: Callable[[], BlockingConnection] = open_connection,
    ) -> None:
        """Initialize a QueueConsumer.

        Args:
            webhook_id (str): ID of the webhook this queue belongs to.
            queue (str): Name of the queue to consume.
            handler (MessageHandler): Processes a message body and reports how it went.
            job_slots (threading.BoundedSemaphore): Caps how many jobs run at once. Shared by
                every consumer so heavy media jobs on different queues don't pile up.
            stop_event (threading.Event): Set to request a graceful shutdown.
            max_attempts (int): Tries allowed before a retryable failure is dead-lettered.
            connect (Callable): Opens the connection. Injectable for tests.
        """
        self.webhook_id = webhook_id
        self.queue = queue
        self._handler = handler
        self._job_slots = job_slots
        self._stop_event = stop_event
        self._max_attempts = max_attempts
        self._connect = connect
        self._connection: BlockingConnection | None = None
        self._in_flight = 0
        self._in_flight_lock = threading.Lock()

    @property
    def in_flight(self) -> int:
        """Number of delivered messages that have not been settled yet."""
        with self._in_flight_lock:
            return self._in_flight

    def run(self, shutdown_grace: float) -> None:
        """Consume until the stop event is set, then drain and disconnect.

        Any connection problem raises out of this method. It deliberately does not try to
        reconnect: the supervisor turns that into a process exit and Docker restarts the
        container with clean state.

        Args:
            shutdown_grace (float): Seconds to wait for in-flight jobs once stopping.
        """
        self._connection = self._connect()
        try:
            channel = self._connection.channel()
            channel.confirm_delivery()
            dead_count = topology.declare_queues(channel, self.queue)
            if dead_count:
                dead_queue = topology.dead_queue_name(self.queue)
                logger.warning(
                    f"{dead_count} message(s) are waiting on {dead_queue}. "
                    "Nothing consumes that queue; inspect them in the RabbitMQ management UI."
                )
            channel.basic_qos(prefetch_count=1)
            consumer_tag = channel.basic_consume(
                queue=self.queue,
                on_message_callback=self._on_message,
            )
            logger.info(f"Consuming queue {self.queue} for webhook {self.webhook_id}")

            while not self._stop_event.is_set():
                self._connection.process_data_events(time_limit=1)

            self._drain(self._connection, channel, consumer_tag, shutdown_grace)
        finally:
            self._close()

    def _drain(
        self,
        connection: BlockingConnection,
        channel: BlockingChannel,
        consumer_tag: str,
        grace: float,
    ) -> None:
        """Stop taking new messages and give in-flight jobs time to finish."""
        logger.info(f"Stopping consumer for {self.queue}, {self.in_flight} job(s) in flight")
        channel.basic_cancel(consumer_tag)

        deadline = time.monotonic() + grace
        while self.in_flight and time.monotonic() < deadline:
            connection.process_data_events(time_limit=0.5)

        if self.in_flight:
            logger.warning(
                f"{self.in_flight} job(s) on {self.queue} still running after {grace}s. "
                "Disconnecting; the broker will redeliver the unacknowledged message(s)."
            )

    def _close(self) -> None:
        """Close the connection, ignoring errors from an already-dead one."""
        connection, self._connection = self._connection, None
        if connection is None:
            return
        try:
            if connection.is_open:
                connection.close()
        except pika.exceptions.AMQPError as e:
            logger.warning(f"Error closing connection for {self.queue}: {e}")

    def _on_message(
        self,
        channel: BlockingChannel,
        method: pika.spec.Basic.Deliver,
        properties: pika.spec.BasicProperties,
        body: bytes,
    ) -> None:
        """Hand a delivered message to a job thread. Runs on the connection thread."""
        prior_attempts = int((properties.headers or {}).get(topology.ATTEMPTS_HEADER, 0))
        with self._in_flight_lock:
            self._in_flight += 1

        threading.Thread(
            target=self._run_job,
            args=(channel, method.delivery_tag, properties, body, prior_attempts + 1),
            name=f"job-{self.webhook_id}-{method.delivery_tag}",
            daemon=True,  # a stuck job must not stop the process from exiting
        ).start()

    def _run_job(
        self,
        channel: BlockingChannel,
        delivery_tag: int,
        properties: pika.spec.BasicProperties,
        body: bytes,
        attempt: int,
    ) -> None:
        """Run the handler on a job slot and queue the settlement. Runs on a job thread."""
        while not self._job_slots.acquire(timeout=1):
            if self._stop_event.is_set():
                logger.info(f"Shutting down before job {delivery_tag} started; leaving it queued")
                self._end_job()
                return

        try:
            result = self._handler(self.webhook_id, body)
        except Exception as e:  # the handler should not raise, but never lose the message
            logger.exception(f"Handler raised for webhook {self.webhook_id}")
            reason = f"{type(e).__name__}: {e}"
            result = (
                ProcessingResult.retry(reason)
                if is_transient(e)
                else ProcessingResult.failed(reason)
            )
        finally:
            self._job_slots.release()

        settle = functools.partial(
            self._settle, channel, delivery_tag, properties, body, attempt, result
        )
        connection = self._connection
        try:
            if connection is None:
                raise pika.exceptions.ConnectionWrongStateError("consumer already closed")
            connection.add_callback_threadsafe(settle)
        except pika.exceptions.AMQPError:
            logger.error(
                f"Connection closed before job {delivery_tag} could be settled. "
                "The broker will redeliver it, so it may run again."
            )
            self._end_job()

    def _settle(
        self,
        channel: BlockingChannel,
        delivery_tag: int,
        properties: pika.spec.BasicProperties,
        body: bytes,
        attempt: int,
        result: ProcessingResult,
    ) -> None:
        """Ack the message, after parking it on the retry or dead-letter queue if needed.

        Runs on the connection thread. The ack always comes after the republish is confirmed,
        so a failure in between leaves the original on the queue instead of losing it.
        """
        disposition = decide(result, attempt, self._max_attempts)
        try:
            if disposition is Disposition.RETRY:
                self._republish(
                    channel,
                    topology.retry_queue_name(self.queue),
                    properties,
                    body,
                    attempt,
                    result,
                )
                logger.warning(
                    f"Attempt {attempt}/{self._max_attempts} failed for {self.queue}; "
                    f"retrying in {topology.RETRY_DELAY_MS // 1000}s: {result.reason}"
                )
            elif disposition is Disposition.DEAD:
                self._republish(
                    channel, topology.dead_queue_name(self.queue), properties, body, attempt, result
                )
                logger.error(
                    f"Giving up on a message from {self.queue} after {attempt} attempt(s); "
                    f"moved to {topology.dead_queue_name(self.queue)}: {result.reason}"
                )
            channel.basic_ack(delivery_tag)
        except pika.exceptions.AMQPError as e:
            logger.error(f"Could not settle message {delivery_tag} ({disposition}): {e!r}")
            self._requeue(channel, delivery_tag)
        finally:
            self._end_job()

    def _republish(
        self,
        channel: BlockingChannel,
        target_queue: str,
        properties: pika.spec.BasicProperties,
        body: bytes,
        attempt: int,
        result: ProcessingResult,
    ) -> None:
        """Publish the message to another queue and wait for the broker to confirm it."""
        headers = {
            key: value
            for key, value in (properties.headers or {}).items()
            if not key.startswith(_DEATH_HEADER_PREFIXES)
        }
        headers[topology.ATTEMPTS_HEADER] = attempt
        if result.reason:
            headers[topology.FAILURE_REASON_HEADER] = result.reason

        channel.basic_publish(
            exchange="",
            routing_key=target_queue,
            body=body,
            properties=pika.BasicProperties(
                delivery_mode=pika.DeliveryMode.Persistent,
                content_type=properties.content_type,
                headers=headers,
            ),
            mandatory=True,
        )

    def _requeue(self, channel: BlockingChannel, delivery_tag: int) -> None:
        """Best-effort nack so a message we could not settle is redelivered promptly."""
        try:
            channel.basic_nack(delivery_tag, requeue=True)
        except pika.exceptions.AMQPError:
            logger.warning(
                f"Could not requeue {delivery_tag}; the broker redelivers it on disconnect"
            )

    def _end_job(self) -> None:
        """Record that a delivered message has been dealt with."""
        with self._in_flight_lock:
            self._in_flight -= 1
