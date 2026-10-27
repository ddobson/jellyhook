from unittest.mock import MagicMock, patch

import pika
import pytest
from flask import Flask
from pika.adapters.utils.connection_workflow import AMQPConnectorStackTimeout
from pika.exceptions import AMQPConnectionError, NackError, StreamLostError, UnroutableError

from api.rabbitmq import PublishError, RabbitMQ


@pytest.fixture
def broker():
    """A fake BlockingConnection returned by pika, with its channel."""
    with patch("api.rabbitmq.pika.BlockingConnection") as blocking_connection:
        connection = MagicMock()
        connection.is_closed = False
        channel = connection.channel.return_value
        blocking_connection.return_value = connection
        yield MagicMock(connection=connection, channel=channel)


@patch("api.rabbitmq.pika.BlockingConnection")
@patch("api.rabbitmq.pika.PlainCredentials")
@patch("api.rabbitmq.pika.ConnectionParameters")
def test_connect(mock_connection_params, mock_credentials, mock_blocking_connection, app):
    mock_blocking_connection.return_value = MagicMock()

    connection = app.extensions["rabbitmq"].connect()

    assert connection is not None
    mock_credentials.assert_called_once_with(username="guest", password="guest")  # noqa: S106
    mock_connection_params.assert_called_once()
    mock_blocking_connection.assert_called_once()


def test_connect_passes_retry_and_timeout_settings_to_pika(app):
    with (
        patch("api.rabbitmq.pika.BlockingConnection"),
        patch("api.rabbitmq.pika.ConnectionParameters") as params,
    ):
        app.extensions["rabbitmq"].connect()

    kwargs = params.call_args.kwargs
    assert kwargs["connection_attempts"] == 3
    assert kwargs["retry_delay"] == 1
    assert kwargs["socket_timeout"] == 3
    assert kwargs["stack_timeout"] == 3
    assert kwargs["port"] == 5672


def test_publish_is_confirmed_and_persistent(app, broker):
    app.extensions["rabbitmq"].publish(
        queue="test_queue", body="test_message", content_type="application/json"
    )

    broker.channel.queue_declare.assert_called_once_with(queue="test_queue", durable=True)
    publish_call = broker.channel.basic_publish.call_args
    assert publish_call.kwargs["exchange"] == ""
    assert publish_call.kwargs["routing_key"] == "test_queue"
    assert publish_call.kwargs["body"] == "test_message"
    assert publish_call.kwargs["mandatory"] is True
    properties = publish_call.kwargs["properties"]
    assert properties.delivery_mode == pika.DeliveryMode.Persistent.value  # 2 on the wire
    assert properties.content_type == "application/json"


def test_publish_turns_on_confirms_before_publishing(app, broker):
    app.extensions["rabbitmq"].publish(queue="q", body="b")

    names = [c[0] for c in broker.channel.method_calls]
    assert names.index("confirm_delivery") < names.index("basic_publish")


def test_publish_closes_its_connection(app, broker):
    app.extensions["rabbitmq"].publish(queue="q", body="b")

    broker.connection.close.assert_called_once()


@pytest.mark.parametrize(
    "error",
    [
        UnroutableError([]),
        NackError([]),
        StreamLostError("lost"),
        OSError("network is unreachable"),
        AMQPConnectorStackTimeout("handshake stalled"),
    ],
)
def test_publish_failure_becomes_publish_error_and_still_closes(app, broker, error):
    broker.channel.basic_publish.side_effect = error

    with pytest.raises(PublishError) as excinfo:
        app.extensions["rabbitmq"].publish(queue="q", body="b")

    assert excinfo.value.__cause__ is error
    broker.connection.close.assert_called_once()


def test_publish_error_when_the_broker_cannot_be_reached(app):
    with (
        patch("api.rabbitmq.pika.BlockingConnection", side_effect=AMQPConnectionError("refused")),
        pytest.raises(PublishError, match="refused"),
    ):
        app.extensions["rabbitmq"].publish(queue="q", body="b")


def test_publish_does_not_mask_the_error_if_closing_also_fails(app, broker):
    broker.channel.basic_publish.side_effect = NackError([])
    broker.connection.close.side_effect = StreamLostError("already gone")

    with pytest.raises(PublishError, match="Nack"):
        app.extensions["rabbitmq"].publish(queue="q", body="b")


def test_publish_skips_closing_an_already_closed_connection(app, broker):
    broker.connection.is_closed = True

    app.extensions["rabbitmq"].publish(queue="q", body="b")

    broker.connection.close.assert_not_called()


def test_publish_rejects_non_string_bodies(app):
    with pytest.raises(TypeError):
        app.extensions["rabbitmq"].publish(queue="q", body={"not": "bytes"})


def test_init_app_requires_a_flask_app():
    with pytest.raises(TypeError):
        RabbitMQ().init_app(object())


def test_constructor_can_register_the_app_directly():
    app = Flask(__name__)

    rabbitmq = RabbitMQ(app)

    assert app.extensions["rabbitmq"] is rabbitmq
    assert app.config["RABBITMQ_CONNECTION_ATTEMPTS"] == 3


def test_publish_call_order_is_confirm_declare_publish(app, broker):
    app.extensions["rabbitmq"].publish(queue="q", body="b")

    assert [c[0] for c in broker.channel.method_calls] == [
        "confirm_delivery",
        "queue_declare",
        "basic_publish",
    ]


def test_publish_error_when_the_handshake_stalls(app):
    """A broker that accepts the TCP connection but never answers is a 503, not a 500."""
    with (
        patch(
            "api.rabbitmq.pika.BlockingConnection",
            side_effect=AMQPConnectorStackTimeout("Timeout during AMQP handshake"),
        ),
        pytest.raises(PublishError, match="handshake"),
    ):
        app.extensions["rabbitmq"].publish(queue="q", body="b")
