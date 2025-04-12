import functools

import pika
import pika.adapters
import pika.adapters.blocking_connection
import pika.spec

from workers import utils
from workers.orchestrator import process_webhook_message


@utils.timer
def handler(
    connection: pika.adapters.BlockingConnection,
    channel: pika.adapters.blocking_connection.BlockingChannel,
    delivery_tag: pika.spec.Basic.Deliver,
    body: bytes,
) -> None:
    """Process an item_added webhook message from Jellyfin.

    Args:
        connection (pika.BlockingConnection): The connection to the RabbitMQ server.
        channel (pika.adapters.BlockingChannel): The channel the message was received on.
        delivery_tag (int): The delivery tag of the message.
        body (bytes): The message body.
    """
    # Process the webhook message using the orchestrator
    completed = process_webhook_message("item_added", body)

    # Acknowledge the message
    cb = functools.partial(utils.ack_message, channel, delivery_tag, completed)
    connection.add_callback_threadsafe(cb)
