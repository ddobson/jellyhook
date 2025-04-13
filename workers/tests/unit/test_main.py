from unittest import mock

import pika


def test_main_with_enabled_webhooks():
    """Test the main function with enabled webhooks."""
    mock_config = mock.MagicMock()
    mock_config.get_enabled_webhooks.return_value = {"webhook1": {"queue": "test_queue"}}

    # Mock consumer class
    mock_consumer = mock.MagicMock()

    with (
        mock.patch("workers.main.config.WorkerConfig.load", return_value=mock_config),
        mock.patch("workers.main.rabbitmq.MessageConsumer", return_value=mock_consumer),
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
        mock.patch("workers.main.rabbitmq.MessageConsumer", mock_consumer_class),
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
