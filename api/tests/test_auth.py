from unittest.mock import MagicMock

import pytest

TOKEN = "s3cret-token"  # noqa: S105


@pytest.fixture
def secured(app):
    app.config["WEBHOOK_TOKEN"] = TOKEN
    app.extensions["rabbitmq"] = MagicMock()
    return app


def test_hooks_are_open_when_no_token_is_configured(app, client):
    app.extensions["rabbitmq"] = MagicMock()

    assert client.post("/hooks/item_added", json={"a": 1}).status_code == 202


def test_empty_token_means_no_authentication(app, client):
    app.config["WEBHOOK_TOKEN"] = ""
    app.extensions["rabbitmq"] = MagicMock()

    assert client.post("/hooks/item_added", json={"a": 1}).status_code == 202


def test_valid_token_is_accepted(secured):
    response = secured.test_client().post(
        "/hooks/item_added", json={"a": 1}, headers={"Authorization": f"Bearer {TOKEN}"}
    )

    assert response.status_code == 202
    secured.extensions["rabbitmq"].publish.assert_called_once()


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer wrong"},
        {"Authorization": "Bearer "},
        {"Authorization": TOKEN},  # right secret, no scheme
        {"Authorization": f"Basic {TOKEN}"},
        {"Authorization": f"bearer {TOKEN}"},  # the scheme is matched exactly
    ],
)
def test_missing_or_wrong_token_is_401_and_nothing_is_queued(secured, headers):
    response = secured.test_client().post("/hooks/item_added", json={"a": 1}, headers=headers)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"].startswith("Bearer")
    assert TOKEN not in response.get_data(as_text=True)
    secured.extensions["rabbitmq"].publish.assert_not_called()


def test_token_is_checked_before_the_body_is_read(secured):
    """An unauthenticated caller learns nothing about body validation."""
    response = secured.test_client().post(
        "/hooks/item_added", data=b"not json", content_type="application/json"
    )

    assert response.status_code == 401
