import functools
import threading
import time

import pika
import pika.adapters.blocking_connection
import pika.credentials
import pika.spec

from workers import config, utils
from workers.logger import logger
from workers.orchestrator import process_webhook_message


@utils.timer
def handler(
    connection: pika.adapters.BlockingConnection,
    channel: pika.adapters.blocking_connection.BlockingChannel,
    delivery_tag: pika.spec.Basic.Deliver,
    body: bytes,
    webhook_id: str,
) -> None:
    """Process an item_added webhook message from Jellyfin.

    Args:
        connection (pika.BlockingConnection): The connection to the RabbitMQ server.
        channel (pika.adapters.BlockingChannel): The channel the message was received on.
        delivery_tag (int): The delivery tag of the message.
        body (bytes): The message body.
        webhook_id (str): The ID of the webhook that sent the message.
    """
    # Process the webhook message using the orchestrator
    completed = process_webhook_message(webhook_id, body)

    # Acknowledge the message
    cb = functools.partial(utils.ack_message, channel, delivery_tag, completed)
    connection.add_callback_threadsafe(cb)


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
    (connection, threads, webhook_id) = args
    delivery_tag = method_frame.delivery_tag
    t = threading.Thread(
        target=handler,
        args=(connection, channel, delivery_tag, body, webhook_id),
    )
    t.start()
    threads.append(t)


def main() -> None:
    """Main function for the worker.

    Starts a long running process that listens for messages on the queue.
    """
    logger.info(f"Loading Jellyhook configuration from {config.JELLYHOOK_CONFIG_PATH}")
    worker_config = config.WorkerConfig.load(config.JELLYHOOK_CONFIG_PATH)  # Singleton first init

   # Get enabled webhooks
    enabled_webhooks = worker_config.get_enabled_webhooks()
    if not enabled_webhooks:
        logger.warning("No enabled webhooks found in configuration file.")
        logger.warning("Exiting...")
        return

    # Load connection credentials
    credentials = pika.credentials.PlainCredentials(
        username=config.RABBITMQ_USER,
        password=config.RABBITMQ_PASS,
    )

    # Start the execution loop:
    # =========================================================
    # 1. Establishes a connection to RabbitMQ server
    # 2. Sets up message queues for each enabled webhook
    # 3. Starts consuming messages from the queues
    # 4. Handle messages in separate threads
    # 6. Implements error recovery and graceful shutdown

    # Loop maintains connection resilience by:
    # - Automatically reconnecting on connection failures
    # - Implementing a 5-second retry delay on connection errors
    # - Properly cleaning up resources on shutdown
    # - Managing thread lifecycle for message processing

    # The loop exits when:
    # - No enabled webhooks are found in configuration
    # - A keyboard interrupt (CTRL+C) is received
    while True:
        try:
            threads = []
            connection = pika.BlockingConnection(
                pika.ConnectionParameters(
                    host=config.RABBITMQ_HOST,
                    credentials=credentials,
                    blocked_connection_timeout=3600,
                ),
            )

            channel = connection.channel()

            # Set up queues for all enabled webhooks
            for webhook_id, webhook_config in enabled_webhooks.items():
                queue = webhook_config["queue"]
                on_message_callback = functools.partial(
                    on_message,
                    args=(connection, threads, webhook_id),
                )
                channel.queue_declare(queue=queue, durable=True)
                channel.basic_consume(
                    queue=queue,
                    on_message_callback=on_message_callback,
                )

            channel.basic_qos(prefetch_count=1)

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
            logger.info("Keyboard Interrupt. Stopping consumption...")
            channel.stop_consuming()
            if connection.is_open:
                connection.close()
            break

        # Clean up completed threads
        threads = [t for t in threads if t.is_alive()]


if __name__ == "__main__":
    if config.DEBUG:
        import debugpy

        debugpy.listen(("0.0.0.0", 5678))
    main()
