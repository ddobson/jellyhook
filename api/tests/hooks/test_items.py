import json
from unittest.mock import MagicMock

from api.hooks.items import ITEM_ADDED_QUEUE


def test_item_added(app, client):
    # Mock RabbitMQ
    mock_rabbitmq = MagicMock()
    app.extensions["rabbitmq"] = mock_rabbitmq

    test_data = {"itemId": "123", "type": "movie"}

    response = client.post("/hooks/item_added", json=test_data)

    # Check status code
    assert response.status_code == 202

    # Verify RabbitMQ publish was called
    mock_rabbitmq.publish.assert_called_once_with(
        queue=ITEM_ADDED_QUEUE,
        body=json.dumps(test_data),
    )
