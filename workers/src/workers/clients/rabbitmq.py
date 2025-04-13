import functools
import threading
import time

import pika
import pika.adapters.blocking_connection
import pika.credentials
import pika.spec

from workers import config, utils
from workers.logger import logger
from workers.services.orchestrator import process_webhook_message


class MessageConsumer:
    """A class to manage RabbitMQ connections, channels, and message consumers."""

    def __init__(self, webhook_configs: dict[str, dict]) -> None:
        """Initialize a MessageConsumer.

        Args:
            webhook_configs: dict of webhook IDs to webhook configurations.
        """
        self.webhook_configs = webhook_configs
        self.connection = None
        self.channels: dict[str, pika.adapters.blocking_connection.BlockingChannel] = {}
        self.threads: list[threading.Thread] = []
        self.running = True

        # Create connection credentials
        self.credentials = pika.credentials.PlainCredentials(
            username=config.RABBITMQ_USER,
            password=config.RABBITMQ_PASS,
        )

    def connect(self) -> None:
        """Establish connection to RabbitMQ and set up channels for each webhook."""
        self.connection = pika.BlockingConnection(
            pika.ConnectionParameters(
                host=config.RABBITMQ_HOST,
                credentials=self.credentials,
                blocked_connection_timeout=3600,
            ),
        )

        # Create a dedicated channel for each webhook
        for webhook_id, webhook_config in self.webhook_configs.items():
            queue = webhook_config["queue"]
            channel = self.connection.channel()
            channel.queue_declare(queue=queue, durable=True)
            channel.basic_qos(prefetch_count=1)

            # Set up consumer for this webhook
            on_message_callback = functools.partial(
                self._on_message_callback,
                webhook_id=webhook_id,
            )
            channel.basic_consume(
                queue=queue,
                on_message_callback=on_message_callback,
            )

            # Store channel for later use
            self.channels[webhook_id] = channel
            logger.info(f"Set up consumer for webhook {webhook_id} on queue {queue}")

    def start(self) -> None:
        """Start consuming messages on all channels."""
        logger.info(f"Starting consumers for {len(self.channels)} webhooks")
        try:
            # Start a separate thread for each channel to consume independently
            for webhook_id, channel in self.channels.items():
                consumer_thread = threading.Thread(
                    target=self._channel_consumer_thread,
                    args=(webhook_id, channel),
                    daemon=True,
                )
                consumer_thread.start()
                logger.info(f"Started consumer thread for webhook {webhook_id}")

            # Keep the main thread alive while consumers are running
            while self.running:
                # Clean up completed message processing threads periodically
                self.threads = [t for t in self.threads if t.is_alive()]
                time.sleep(1)

        except KeyboardInterrupt:
            self.stop()

    def stop(self) -> None:
        """Stop all consumers and close connection."""
        logger.info("Stopping all consumers...")
        self.running = False

        # Stop consuming on all channels
        for webhook_id, channel in self.channels.items():
            if channel.is_open:
                channel.stop_consuming()
                logger.info(f"Stopped consumer for webhook {webhook_id}")

        # Close connection
        if self.connection and self.connection.is_open:
            self.connection.close()
            logger.info("Closed RabbitMQ connection")

        # Wait for message processing threads to complete
        for thread in self.threads:
            if thread.is_alive():
                thread.join(timeout=5.0)
        logger.info("All message threads joined")

    def _channel_consumer_thread(
        self, webhook_id: str, channel: pika.adapters.blocking_connection.BlockingChannel
    ) -> None:
        """Run a consumer in a dedicated thread.

        Args:
            webhook_id: The ID of the webhook this channel is for.
            channel: The channel to consume from.
        """
        try:
            logger.info(f"Channel consumer for {webhook_id} started")
            channel.start_consuming()
        except Exception as e:
            logger.error(f"Error in channel consumer for {webhook_id}: {e}")
        finally:
            logger.info(f"Channel consumer for {webhook_id} stopped")

    def _on_message_callback(
        self,
        channel: pika.adapters.blocking_connection.BlockingChannel,
        method_frame: pika.spec.Basic.Deliver,
        properties: pika.spec.BasicProperties,
        body: bytes,
        webhook_id: str,
    ) -> None:
        """Handle incoming messages by processing them in separate threads.

        Args:
            channel: The channel the message was received on.
            method_frame: The method frame.
            properties: The message properties.
            body: The message body.
            webhook_id: The ID of the webhook that sent the message.
        """
        delivery_tag = method_frame.delivery_tag

        # Process message in a separate thread
        thread = threading.Thread(
            target=self._process_message,
            args=(channel, delivery_tag, body, webhook_id),
        )
        thread.start()
        self.threads.append(thread)

    @utils.timer
    def _process_message(
        self,
        channel: pika.adapters.blocking_connection.BlockingChannel,
        delivery_tag: int,
        body: bytes,
        webhook_id: str,
    ) -> None:
        """Process a webhook message.

        Args:
            channel: The channel the message was received on.
            delivery_tag: The delivery tag of the message.
            body: The message body.
            webhook_id: The ID of the webhook that sent the message.
        """
        try:
            # Process the webhook message using the orchestrator
            completed = process_webhook_message(webhook_id, body)

            # Acknowledge the message
            cb = functools.partial(self._ack_message, channel, delivery_tag, completed)
            if self.connection and self.connection.is_open:
                self.connection.add_callback_threadsafe(cb)
        except Exception as e:
            logger.error(f"Error processing message for webhook {webhook_id}: {e}")
            # Negatively acknowledge the message
            cb = functools.partial(self._ack_message, channel, delivery_tag, False)
            if self.connection and self.connection.is_open:
                self.connection.add_callback_threadsafe(cb)

    def _ack_message(self, channel, delivery_tag: int, completed: bool) -> None:
        """Acknowledge a message.

        Note: that `channel` must be the same pika channel instance via which
        the message being ACKed was retrieved (AMQP protocol constraint).

        Args:
            channel (pika.channel.Channel): The channel to acknowledge the message on.
            delivery_tag (int): The delivery tag of the message to acknowledge.
            completed (bool): Whether the message was successfully processed.
        """
        if not channel.is_open:
            logger.info(
                f"Unable to acknowledge message with delivery tag: {delivery_tag}. "
                "Connection closed.",
            )
            return

        if completed:
            channel.basic_ack(delivery_tag)
        else:
            channel.basic_nack(delivery_tag, requeue=False)

        logger.info(f"Acknowledged message with delivery tag: {delivery_tag}")
