import functools
import threading
import time

import pika
import pika.adapters.blocking_connection
import pika.credentials
import pika.spec

from workers import config
from workers.hooks.item_added import item_added
from workers.logger import logger


def on_message(
    channel: pika.adapters.blocking_connection.BlockingChannel,
    method_frame: pika.spec.Basic.Deliver,
    properties: pika.spec.BasicProperties,  # noqa: ARG001
    body: bytes,
    args: tuple,
) -> None:
    """Callback for when a message is received from the queue.

    Args:
        channel (pika.adapters.BlockingChannel): The channel the message was received on.
        method_frame (pika.spec.Basic.Deliver): The method frame.
        properties (pika.spec.BasicProperties): The message properties.
        body (bytes): The message body.
        args (tuple): Additional arguments passed to the callback.
    """
    (connection, threads) = args
    delivery_tag = method_frame.delivery_tag
    t = threading.Thread(
        target=item_added,
        args=(connection, channel, delivery_tag, body),
    )
    t.start()
    threads.append(t)


def main() -> None:
    """Main function for the worker.

    Starts a long running process that listens for messages on the queue.
    """
    while True:
        try:
            credentials = pika.credentials.PlainCredentials(
                username=config.RABBITMQ_USER,
                password=config.RABBITMQ_PASS,
            )
            connection = pika.BlockingConnection(
                pika.ConnectionParameters(
                    host=config.RABBITMQ_HOST,
                    credentials=credentials,
                    blocked_connection_timeout=3600,
                ),
            )

            channel = connection.channel()
            channel.queue_declare(queue="jellyfin:item_added", durable=True)
            channel.basic_qos(prefetch_count=1)

            threads = []
            on_message_callback = functools.partial(
                on_message,
                args=(connection, threads),
            )
            channel.basic_consume(
                queue="jellyfin:item_added",
                on_message_callback=on_message_callback,
            )

            logger.info("Waiting for messages. To exit press CTRL+C")
            channel.start_consuming()

            # Wait for all to complete
            for thread in threads:
                thread.join()

            connection.close()

        except pika.exceptions.AMQPConnectionError as e:
            logger.error(f"Connection error: {e}. Retrying in 5 seconds...")
            time.sleep(5)
        except KeyboardInterrupt:
            logger.error("Keyboard Interupt. Exiting...")
            break


if __name__ == "__main__":
    if config.DEBUG:
        import debugpy

        debugpy.listen(("0.0.0.0", 5678))
    main()
