from collections.abc import Generator
from contextlib import contextmanager

import pika
from flask import Flask, current_app, g


class RabbitMQ:
    """RabbitMQ connection manager for Flask applications."""

    app: Flask | None = None

    def __init__(self, app: Flask | None = None) -> None:
        """Initialize the RabbitMQ connection manager."""
        self.app = app
        if app is not None:
            self.init_app(app)

    def init_app(self, app: Flask) -> None:
        """Initialize the RabbitMQ connection manager with the Flask app.

        This method sets up the RabbitMQ connection parameters and adds a teardown
        function to close the connection when the app context ends.

        Args:
            app (Flask): The Flask app instance.
        """
        if not isinstance(app, Flask):
            raise TypeError("Argument 'app' must be a Flask instance")

        self.app = app

        # Add RabbitMQ configuration defaults
        app.config.setdefault("RABBITMQ_HOST", "localhost")
        app.config.setdefault("RABBITMQ_PORT", 5672)
        app.config.setdefault("RABBITMQ_USER", "guest")
        app.config.setdefault("RABBITMQ_PASS", "guest")
        app.config.setdefault("RABBITMQ_VHOST", "/")

        if not hasattr(app, "extensions"):
            app.extensions = {}

        app.extensions["rabbitmq"] = self

        # Set up the proxy for current_app.rabbitmq
        app.rabbitmq = self

        # Store configuration
        self.host = app.config["RABBITMQ_HOST"]
        self.port = app.config["RABBITMQ_PORT"]
        self.username = app.config["RABBITMQ_USER"]
        self.password = app.config["RABBITMQ_PASS"]

        # Add teardown context
        app.teardown_appcontext(self.teardown)

    def connect(self) -> pika.BlockingConnection:
        """Create a new RabbitMQ connection."""
        credentials = pika.PlainCredentials(
            username=self.app.config["RABBITMQ_USER"],
            password=self.app.config["RABBITMQ_PASS"],
        )

        parameters = pika.ConnectionParameters(
            host=self.app.config["RABBITMQ_HOST"],
            port=self.app.config["RABBITMQ_PORT"],
            virtual_host=self.app.config["RABBITMQ_VHOST"],
            credentials=credentials,
        )

        return pika.BlockingConnection(parameters)

    def teardown(self, exception: Exception) -> None:
        """Teardown function to close the RabbitMQ connection.

        This function is called when the app context ends. It closes the RabbitMQ
        connection if it exists.

        Args:
            exception: The exception that occurred, if any.
        """
        connection = getattr(g, "_rabbitmq_connection", None)
        if connection is not None:
            connection.close()

    def publish(self, queue: str, body: str | bytes, exchange: str = "") -> None:
        """Publish a message to a RabbitMQ queue.

        Args:
            queue (str): The name of the queue to publish to.
            body (str | bytes): The message body to publish.
            exchange (str): The exchange to publish to. Defaults to an empty string.
        """
        if not isinstance(body, (str, bytes)):
            raise TypeError("Body must be a string or bytes")

        with self.connection() as connection:
            channel = connection.channel()
            channel.queue_declare(queue=queue, durable=True)
            channel.basic_publish(exchange=exchange, routing_key=queue, body=body)

    @contextmanager
    def connection(self) -> Generator[pika.BlockingConnection]:
        """Context manager for handling RabbitMQ connections."""
        if not hasattr(g, "_rabbitmq_connection"):
            g._rabbitmq_connection = self.connect()
        try:
            yield g._rabbitmq_connection
        except Exception as e:
            # Log the error and try to reconnect
            current_app.logger.exception(f"RabbitMQ connection error: {e}")
            if hasattr(g, "_rabbitmq_connection"):
                delattr(g, "_rabbitmq_connection")
            raise


def get_rabbitmq_connection() -> pika.BlockingConnection | None:
    """Helper function to get the current RabbitMQ connection."""
    if "_rabbitmq_connection" not in g:
        g._rabbitmq_connection = current_app.rabbitmq.connect()
    return g._rabbitmq_connection
