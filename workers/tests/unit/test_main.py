from unittest import mock

import pika
import pytest

from workers.main import MessageConsumer


@pytest.fixture
def mock_webhook_configs():
    """Create mock webhook configs for testing."""
    return {
        "item_added": {
            "enabled": True,
            "queue": "jellyfin:item_added",
            "services": [
                {
                    "name": "metadata_update",
                    "enabled": True,
                    "priority": 10,
                }
            ],
        },
        "library_changed": {"enabled": True, "queue": "jellyfin:library_changed", "services": []},
    }


@pytest.fixture
def message_consumer(mock_webhook_configs):
    """Create a MessageConsumer with mocked connection."""
    with mock.patch("pika.BlockingConnection"), mock.patch("workers.main.config") as mock_config:
        # Set up config attributes that will be accessed
        mock_config.RABBITMQ_HOST = "localhost"
        mock_config.RABBITMQ_USER = "guest"
        mock_config.RABBITMQ_PASS = "guest"

        consumer = MessageConsumer(mock_webhook_configs)
        # Mock the connection and channels
        consumer.connection = mock.MagicMock()
        consumer.channels = {webhook_id: mock.MagicMock() for webhook_id in mock_webhook_configs}
        yield consumer

def test_initialization(mock_webhook_configs):
    """Test MessageConsumer initialization."""
    with mock.patch("pika.credentials.PlainCredentials") as mock_credentials:
        consumer = MessageConsumer(mock_webhook_configs)

        # Check that webhook configs are stored
        assert consumer.webhook_configs == mock_webhook_configs

        # Check that initial values are set correctly
        assert consumer.connection is None
        assert consumer.channels == {}
        assert consumer.threads == []
        assert consumer.running is True

        # Verify credentials setup
        mock_credentials.assert_called_once()

def test_connect(message_consumer, mock_webhook_configs):
    """Test connect method creates connection and channels."""
    # Setup mocks
    message_consumer.connection = None  # Reset connection to test creation
    mock_connection = mock.MagicMock()
    mock_channel = mock.MagicMock()
    mock_params = mock.MagicMock()

    with (
        mock.patch("pika.ConnectionParameters", return_value=mock_params),
        mock.patch("pika.BlockingConnection", return_value=mock_connection) as mock_connect,
        mock.patch.object(mock_connection, "channel", return_value=mock_channel),
    ):
        # Execute connect method
        message_consumer.connect()

        # Verify connection was established
        mock_connect.assert_called_once()

        # Verify channels were created for each webhook
        assert len(message_consumer.channels) == len(mock_webhook_configs)
        for webhook_id in mock_webhook_configs:
            assert webhook_id in message_consumer.channels

        # Verify channel setup
        assert mock_channel.queue_declare.call_count == len(mock_webhook_configs)
        assert mock_channel.basic_qos.call_count == len(mock_webhook_configs)
        assert mock_channel.basic_consume.call_count == len(mock_webhook_configs)

def test_channel_consumer_thread(message_consumer):
    """Test the _channel_consumer_thread method."""
    mock_channel = mock.MagicMock()
    webhook_id = "test_webhook"

    # Test normal execution
    with (
        mock.patch.object(message_consumer, "running", True),
        mock.patch("workers.main.logger") as mock_logger,
    ):
        # Run the method
        message_consumer._channel_consumer_thread(webhook_id, mock_channel)

        # Verify channel.start_consuming was called
        mock_channel.start_consuming.assert_called_once()

        # Verify logging
        assert mock_logger.info.call_count >= 1

    # Test exception handling
    mock_channel.start_consuming.side_effect = Exception("Test error")
    with mock.patch("workers.main.logger") as mock_logger:
        # Run the method (should not raise exception)
        message_consumer._channel_consumer_thread(webhook_id, mock_channel)

        # Verify error was logged
        mock_logger.error.assert_called_once()

def test_on_message_callback(message_consumer):
    """Test the _on_message_callback method."""
    mock_channel = mock.MagicMock()
    mock_method_frame = mock.MagicMock()
    mock_method_frame.delivery_tag = 123
    mock_properties = mock.MagicMock()
    mock_body = b"test message"
    webhook_id = "test_webhook"

    # Test the method creates a thread and adds it to the threads list
    with mock.patch("threading.Thread") as mock_thread_class:
        mock_thread = mock.MagicMock()
        mock_thread_class.return_value = mock_thread

        # Execute the callback
        message_consumer._on_message_callback(
            mock_channel, mock_method_frame, mock_properties, mock_body, webhook_id
        )

        # Verify thread was created with correct arguments
        mock_thread_class.assert_called_once()
        args = mock_thread_class.call_args[1]["args"]
        assert args[0] == mock_channel
        assert args[1] == 123  # delivery_tag
        assert args[2] == mock_body
        assert args[3] == webhook_id

        # Verify thread was started and added to threads list
        mock_thread.start.assert_called_once()
        assert mock_thread in message_consumer.threads

def test_process_message(message_consumer):
    """Test the _process_message method."""
    mock_channel = mock.MagicMock()
    delivery_tag = 123
    mock_body = b"test message"
    webhook_id = "test_webhook"

    # Define decorator replacement
    def mock_timer(f):
        return f

    # Test successful message processing
    with (
        mock.patch("workers.main.process_webhook_message", return_value=True) as mock_process,
        mock.patch("functools.partial") as mock_partial,
        mock.patch("workers.utils.timer", mock_timer),
    ):
        # Execute the method
        message_consumer._process_message(mock_channel, delivery_tag, mock_body, webhook_id)

        # Verify message was processed
        mock_process.assert_called_once_with(webhook_id, mock_body)

        # Verify functools.partial was called for acknowledgment
        mock_partial.assert_called_once()
        message_consumer.connection.add_callback_threadsafe.assert_called_once()

    # Test failed message processing
    message_consumer.connection.reset_mock()
    with (
        mock.patch(
            "workers.main.process_webhook_message", side_effect=Exception("Process error")
        ) as mock_process,
        mock.patch("workers.main.logger") as mock_logger,
        mock.patch("functools.partial") as mock_partial,
        mock.patch("workers.utils.timer", mock_timer),
    ):
        # Execute the method
        message_consumer._process_message(mock_channel, delivery_tag, mock_body, webhook_id)

        # Verify error was logged
        mock_logger.error.assert_called_once()

        # Verify negative acknowledgment
        mock_partial.assert_called_once()
        message_consumer.connection.add_callback_threadsafe.assert_called_once()

def test_start(message_consumer):
    """Test the start method."""
    # Setup mock channels and threads
    mock_channels = {"webhook1": mock.MagicMock(), "webhook2": mock.MagicMock()}
    message_consumer.channels = mock_channels

    # Create a flag to check if the loop runs
    loop_executed = False

    def mock_sleep(seconds):
        nonlocal loop_executed
        loop_executed = True
        # Set running to False to exit the loop after one iteration
        message_consumer.running = False

    # Test starting consumers
    with (
        mock.patch("threading.Thread") as mock_thread_class,
        mock.patch("time.sleep", side_effect=mock_sleep),
        mock.patch("workers.main.logger") as mock_logger,
    ):
        mock_thread = mock.MagicMock()
        mock_thread_class.return_value = mock_thread

        # Start the consumer
        message_consumer.start()

        # Verify threads were created for each channel
        assert mock_thread_class.call_count == len(mock_channels)

        # Verify threads were started
        assert mock_thread.start.call_count == len(mock_channels)

        # Verify the main loop ran
        assert loop_executed is True

        # Verify logging
        assert mock_logger.info.call_count >= len(mock_channels)

    # Test KeyboardInterrupt handling
    with (
        mock.patch("threading.Thread", side_effect=KeyboardInterrupt()),
        mock.patch.object(message_consumer, "stop") as mock_stop,
    ):
        # Start should catch the KeyboardInterrupt and call stop
        message_consumer.start()

        # Verify stop was called
        mock_stop.assert_called_once()

def test_stop(message_consumer):
    """Test the stop method."""
    # Setup mock channels and threads
    mock_channels = {"webhook1": mock.MagicMock(), "webhook2": mock.MagicMock()}
    message_consumer.channels = mock_channels

    mock_thread1 = mock.MagicMock()
    mock_thread1.is_alive.return_value = True
    mock_thread2 = mock.MagicMock()
    mock_thread2.is_alive.return_value = False
    message_consumer.threads = [mock_thread1, mock_thread2]

    # Test stopping
    with mock.patch("workers.main.logger") as mock_logger:
        # Stop the consumer
        message_consumer.stop()

        # Verify running flag was set to False
        assert message_consumer.running is False

        # Verify all channels were stopped
        for mock_channel in mock_channels.values():
            mock_channel.stop_consuming.assert_called_once()

        # Verify connection was closed
        message_consumer.connection.close.assert_called_once()

        # Verify only alive threads were joined
        mock_thread1.join.assert_called_once()
        assert not mock_thread2.join.called

        # Verify logging
        assert mock_logger.info.call_count >= 2


def test_main_with_enabled_webhooks():
    """Test the main function with enabled webhooks."""
    mock_config = mock.MagicMock()
    mock_config.get_enabled_webhooks.return_value = {"webhook1": {"queue": "test_queue"}}

    # Mock consumer class
    mock_consumer = mock.MagicMock()

    with (
        mock.patch("workers.main.config.WorkerConfig.load", return_value=mock_config),
        mock.patch("workers.main.MessageConsumer", return_value=mock_consumer),
        mock.patch("workers.main.logger") as mock_logger,
    ):
        # Import here to avoid module-level patch issues
        from workers.main import main

        # Execute main function
        main()

        # Verify consumer was created and started
        mock_consumer.connect.assert_called_once()
        mock_consumer.start.assert_called_once()

        # Verify logs
        assert mock_logger.info.call_count >= 1


def test_main_no_enabled_webhooks():
    """Test the main function with no enabled webhooks."""
    mock_config = mock.MagicMock()
    mock_config.get_enabled_webhooks.return_value = {}

    with (
        mock.patch("workers.main.config.WorkerConfig.load", return_value=mock_config),
        mock.patch("workers.main.logger") as mock_logger,
    ):
        # Import here to avoid module-level patch issues
        from workers.main import main

        # Execute main function
        main()

        # Verify warning was logged and function returned early
        assert mock_logger.warning.call_count >= 1


def test_main_connection_error_retry():
    """Test main function retries on connection error."""
    mock_config = mock.MagicMock()
    mock_config.get_enabled_webhooks.return_value = {"webhook1": {"queue": "test_queue"}}

    # First attempt raises error, second attempt succeeds
    mock_consumer = mock.MagicMock()
    mock_consumer_with_error = mock.MagicMock()
    mock_consumer_with_error.connect.side_effect = pika.exceptions.AMQPConnectionError("Test error")

    mock_consumer_class = mock.MagicMock()
    mock_consumer_class.side_effect = [mock_consumer_with_error, mock_consumer]

    with (
        mock.patch("workers.main.config.WorkerConfig.load", return_value=mock_config),
        mock.patch("workers.main.MessageConsumer", mock_consumer_class),
        mock.patch("workers.main.logger") as mock_logger,
        mock.patch("time.sleep"),
    ):  # Don't actually sleep
        # Import here to avoid module-level patch issues
        from workers.main import main

        # Execute main function
        main()

        # Verify error was logged and retry happened
        assert mock_logger.error.call_count >= 1
        assert mock_consumer_class.call_count == 2

        # Verify second consumer was connected and started
        mock_consumer.connect.assert_called_once()
        mock_consumer.start.assert_called_once()
