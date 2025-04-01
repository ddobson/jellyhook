from unittest.mock import MagicMock, patch

from flask import g


@patch("api.rabbitmq.pika.BlockingConnection")
@patch("api.rabbitmq.pika.PlainCredentials")
@patch("api.rabbitmq.pika.ConnectionParameters")
def test_connect(mock_connection_params, mock_credentials, mock_blocking_connection, app):
    """Test the connect method."""
    mock_blocking_connection.return_value = MagicMock()
    with app.app_context():
        rabbitmq = app.extensions["rabbitmq"]
        connection = rabbitmq.connect()
        assert connection is not None
        mock_credentials.assert_called_once_with(username="guest", password="guest")  # noqa: S106
        mock_connection_params.assert_called_once()
        mock_blocking_connection.assert_called_once()


@patch("api.rabbitmq.pika.BlockingConnection")
def test_publish(mock_blocking_connection, app):
    """Test the publish method."""
    mock_connection = MagicMock()
    mock_channel = MagicMock()
    mock_connection.channel.return_value = mock_channel
    mock_blocking_connection.return_value = mock_connection

    with app.app_context():
        rabbitmq = app.extensions["rabbitmq"]
        rabbitmq.publish(queue="test_queue", body="test_message")
        mock_channel.queue_declare.assert_called_once_with(queue="test_queue", durable=True)
        mock_channel.basic_publish.assert_called_once_with(
            exchange="",
            routing_key="test_queue",
            body="test_message",
        )


@patch("api.rabbitmq.pika.BlockingConnection")
def test_teardown(mock_blocking_connection, app):
    """Test the teardown method."""
    mock_connection = MagicMock()
    mock_blocking_connection.return_value = mock_connection

    with app.app_context():
        g._rabbitmq_connection = mock_connection
        rabbitmq = app.extensions["rabbitmq"]
        rabbitmq.teardown(None)
        mock_connection.close.assert_called_once()


@patch("api.rabbitmq.pika.BlockingConnection")
def test_connection_context_manager(mock_blocking_connection, app):
    """Test the connection context manager."""
    mock_connection = MagicMock()
    mock_blocking_connection.return_value = mock_connection

    with app.app_context():
        rabbitmq = app.extensions["rabbitmq"]
        with rabbitmq.connection() as connection:
            assert connection is mock_connection
        mock_connection.close.assert_not_called()  # Ensure connection is not closed prematurely
