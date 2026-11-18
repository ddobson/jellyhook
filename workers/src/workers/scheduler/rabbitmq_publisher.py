import contextlib
from collections.abc import Callable

import pika
import pika.exceptions
from pika.adapters.blocking_connection import BlockingConnection

from workers.clients import rabbitmq, topology
from workers.services.refresh import refresh_message


class RabbitMQRefreshPublisher:
    """Publishes Refresh messages to the Refresh queue, on a new connection for each publish.

    The scheduler can wait a week between firings, and a `BlockingConnection` only answers
    heartbeats while something pumps it, so a connection kept open across that wait would be
    dropped by the broker. Connecting, publishing and closing each time costs a few milliseconds
    once a week and leaves nothing to go stale. It also means a broker outage between firings is
    noticed at the next publish, which then fails and makes the scheduler exit non-zero.

    Args:
        queue (str): The Refresh queue (`REFRESH_QUEUE`).
        connect (Callable | None): Opens a connection; defaults to the worker's RabbitMQ settings.
    """

    def __init__(self, queue: str, connect: Callable[[], BlockingConnection] | None = None) -> None:
        """Remember where to publish; nothing connects until `publish` is called."""
        self.queue = queue
        self._connect = connect or rabbitmq.open_connection

    def publish(self, playlist_name: str) -> None:
        """Publish a Refresh for `playlist_name` and wait for the broker's confirm.

        The Refresh queue and its `.retry` / `.dead` companions are declared exactly as the worker
        declares them, so a Refresh published before the worker has ever started is kept, and
        neither side's redeclare is refused.

        Args:
            playlist_name (str): The Random Playlist's name.

        Raises:
            pika.exceptions.AMQPError: The broker could not be reached, dropped the connection,
                or did not confirm the message (refused or unroutable).
            pika.adapters.utils.connection_workflow.AMQPConnectorException: The connection
                could not be opened.
        """
        connection = self._connect()
        try:
            channel = connection.channel()
            channel.confirm_delivery()
            topology.declare_queues(channel, self.queue)
            # With confirms on, this returns only once the broker has taken the message, and
            # mandatory makes an unroutable message an error rather than a silent drop
            channel.basic_publish(
                exchange="",
                routing_key=self.queue,
                body=refresh_message(playlist_name),
                properties=pika.BasicProperties(
                    delivery_mode=pika.DeliveryMode.Persistent,
                    content_type="application/json",
                ),
                mandatory=True,
            )
        finally:
            _close(connection)


def _close(connection: BlockingConnection) -> None:
    """Close a connection, ignoring errors: the publish outcome is already decided."""
    with contextlib.suppress(pika.exceptions.AMQPError, OSError):
        if connection.is_open:
            connection.close()
