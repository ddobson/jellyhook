import json
from unittest.mock import MagicMock, patch

import pytest

from api.hooks.items import ITEM_ADDED_QUEUE
from api.rabbitmq import PublishError


@pytest.fixture
def mock_rabbitmq(app):
    mock = MagicMock()
    app.extensions["rabbitmq"] = mock
    return mock


def test_item_added(client, mock_rabbitmq):
    test_data = {"itemId": "123", "type": "movie"}

    response = client.post("/hooks/item_added", json=test_data)

    assert response.status_code == 202
    mock_rabbitmq.publish.assert_called_once_with(
        queue=ITEM_ADDED_QUEUE,
        body=json.dumps(test_data),
        content_type="application/json",
    )


def test_item_added_accepts_json_sent_with_another_content_type(client, mock_rabbitmq):
    """The plugin does not always label its body as JSON; that must keep working."""
    response = client.post(
        "/hooks/item_added",
        data=json.dumps({"itemId": "1"}),
        content_type="text/plain",
    )

    assert response.status_code == 202
    mock_rabbitmq.publish.assert_called_once()


@pytest.mark.parametrize(
    "body",
    [b"", b"not json", b"[1, 2, 3]", b"null", b'"a string"'],
)
def test_item_added_rejects_bodies_that_are_not_json_objects(client, mock_rabbitmq, body):
    response = client.post("/hooks/item_added", data=body, content_type="application/json")

    assert response.status_code == 400
    assert response.get_json() == {"error": "Request body must be a JSON object"}
    mock_rabbitmq.publish.assert_not_called()


def test_item_added_returns_503_when_the_message_cannot_be_queued(client, mock_rabbitmq):
    """202 must only ever mean "queued": a broker outage is reported, not swallowed."""
    mock_rabbitmq.publish.side_effect = PublishError("broker down")

    response = client.post("/hooks/item_added", json={"itemId": "123"})

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "5"
    assert "broker down" not in response.get_data(as_text=True)  # internals stay in the log


def test_item_added_unexpected_error_is_a_generic_500(client, mock_rabbitmq):
    mock_rabbitmq.publish.side_effect = RuntimeError("secret internals")

    response = client.post("/hooks/item_added", json={"itemId": "123"})

    assert response.status_code == 500
    assert response.get_json() == {"error": "Internal server error"}


def test_oversized_body_is_413(app, client, mock_rabbitmq):
    app.config["MAX_CONTENT_LENGTH"] = 100

    response = client.post("/hooks/item_added", json={"blob": "x" * 500})

    assert response.status_code == 413
    mock_rabbitmq.publish.assert_not_called()


def test_wrong_method_is_405_with_allow_header(client):
    response = client.get("/hooks/item_added")

    assert response.status_code == 405
    assert "POST" in response.headers["Allow"]
    assert response.is_json


def test_unknown_hook_is_404_not_500(client):
    response = client.post("/hooks/nope", json={})

    assert response.status_code == 404


def test_a_logging_failure_cannot_turn_a_queued_message_into_a_500(app, client, mock_rabbitmq):
    """The payload is logged before publishing, so publishing is the last thing that can fail."""
    order = []
    mock_rabbitmq.publish.side_effect = lambda **kw: order.append("publish")
    with patch.object(app.logger, "info", side_effect=lambda *a, **k: order.append("log")):
        client.post("/hooks/item_added", json={"itemId": "1"})

    assert order == ["log", "publish"]
