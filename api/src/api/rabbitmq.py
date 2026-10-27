"""Publishing to RabbitMQ from Flask.

Every publish uses its own short-lived connection and waits for the broker to confirm it. At
webhook volumes (a burst when a library scan finishes) a connection per request costs a few
milliseconds, and in exchange there is no cached connection to go stale after a broker restart
and nothing to leak when a request fails half way.

Two settings make "202 Accepted" mean the event is safe:

- **Publisher confirms**: `basic_publish` does not return until the broker has taken
  responsibility for the message, and raises if it cannot route or refuses it.
  https://www.rabbitmq.com/docs/confirms#publisher-confirms
- **Persistent delivery mode**: the message is written to disk, so a broker restart does not lose
  it while it waits in the (durable) queue.
  https://www.rabbitmq.com/docs/persistence-conf
"""

import contextlib

import pika
from flask import Flask
from pika.adapters.utils.connection_workflow import AMQPConnectorException
from pika.exceptions import AMQPError


class PublishError(Exception):
    """The message could not be handed to RabbitMQ (unreachable, refused or unroutable)."""


class RabbitMQ:
    """RabbitMQ publisher for Flask applications."""

    app: Flask | None = None

    def __init__(self, app: Flask | None = None) -> None:
        """Initialize the RabbitMQ publisher."""
        self.app = app
        if app is not None:
            self.init_app(app)

    def init_app(self, app: Flask) -> None:
        """Register the extension with the Flask app and set configuration defaults.

        Args:
            app (Flask): The Flask app instance.
        """
        if not isinstance(app, Flask):
            raise TypeError("Argument 'app' must be a Flask instance")

        self.app = app

        app.config.setdefault("RABBITMQ_HOST", "localhost")
        app.config.setdefault("RABBITMQ_PORT", 5672)
        app.config.setdefault("RABBITMQ_USER", "guest")
        app.config.setdefault("RABBITMQ_PASS", "guest")
        app.config.setdefault("RABBITMQ_VHOST", "/")
        # How many times to try to connect, and how long to wait between and during tries. A broker
        # that drops packets costs attempts * socket timeout + (attempts - 1) * delay (11 s by
        # default); a refused connection fails in about 2 s. Keep this well under the web server's
        # request timeout.
        app.config.setdefault("RABBITMQ_CONNECTION_ATTEMPTS", 3)
        app.config.setdefault("RABBITMQ_RETRY_DELAY", 1)
        app.config.setdefault("RABBITMQ_SOCKET_TIMEOUT", 3)
        # Longest the AMQP handshake may take. pika's own default is 15 s per attempt, which a
        # broker that accepts the connection but never answers would multiply by every attempt.
        app.config.setdefault("RABBITMQ_STACK_TIMEOUT", 3)

        app.extensions["rabbitmq"] = self

    def connect(self) -> pika.BlockingConnection:
        """Open a new RabbitMQ connection, retrying a few times.

        Raises:
            pika.exceptions.AMQPConnectionError: If every attempt failed.
        """
        config = self.app.config
        parameters = pika.ConnectionParameters(
            host=config["RABBITMQ_HOST"],
            port=config["RABBITMQ_PORT"],
            virtual_host=config["RABBITMQ_VHOST"],
            credentials=pika.PlainCredentials(
                username=config["RABBITMQ_USER"],
                password=config["RABBITMQ_PASS"],
            ),
            connection_attempts=config["RABBITMQ_CONNECTION_ATTEMPTS"],
            retry_delay=config["RABBITMQ_RETRY_DELAY"],
            socket_timeout=config["RABBITMQ_SOCKET_TIMEOUT"],
            stack_timeout=config["RABBITMQ_STACK_TIMEOUT"],
            blocked_connection_timeout=config["RABBITMQ_SOCKET_TIMEOUT"] * 6,
        )
        return pika.BlockingConnection(parameters)

    def publish(
        self,
        queue: str,
        body: str | bytes,
        exchange: str = "",
        content_type: str | None = None,
    ) -> None:
        """Publish a message and wait for the broker to confirm it.

        The queue is declared durable with no arguments. The worker declares it the same way,
        and any difference makes the second declaration fail with PRECONDITION_FAILED.

        Args:
            queue (str): The name of the queue to publish to.
            body (str | bytes): The message body to publish.
            exchange (str): The exchange to publish to. Defaults to the default exchange.
            content_type (str | None): Optional MIME type recorded on the message.

        Raises:
            TypeError: If the body is not a string or bytes.
            PublishError: If the broker could not be reached or did not accept the message.
        """
        if not isinstance(body, (str, bytes)):
            raise TypeError("Body must be a string or bytes")

        properties = pika.BasicProperties(
            delivery_mode=pika.DeliveryMode.Persistent,
            content_type=content_type,
        )
        connection = None
        try:
            connection = self.connect()
            channel = connection.channel()
            channel.confirm_delivery()
            channel.queue_declare(queue=queue, durable=True)
            # mandatory: fail loudly if the message cannot be routed to a queue
            channel.basic_publish(
                exchange=exchange,
                routing_key=queue,
                body=body,
                properties=properties,
                mandatory=True,
            )
        except (AMQPError, AMQPConnectorException, OSError) as e:
            # AMQPConnectorException (handshake timeout, connect failure) is a sibling of
            # AMQPError in pika, not a subclass, so it has to be named here.
            raise PublishError(f"Could not publish to {queue!r}: {type(e).__name__}: {e}") from e
        finally:
            self._close(connection)

    @staticmethod
    def _close(connection: pika.BlockingConnection | None) -> None:
        """Close a connection, ignoring errors: the publish outcome is already decided."""
        if connection is None or connection.is_closed:
            return
        with contextlib.suppress(AMQPError, OSError):
            connection.close()
