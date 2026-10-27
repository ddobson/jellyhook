import logging
from unittest.mock import patch

from api.app import create_app


def test_create_app_wires_health_hooks_and_extension():
    with patch("api.app.logging.init_app"):
        app = create_app("development")

    client = app.test_client()
    assert client.get("/health").get_json() == {"status": "OK"}
    assert "rabbitmq" in app.extensions
    assert app.config["RABBITMQ_PORT"] == 5672
    assert client.get("/nope").status_code == 404  # error handlers are registered


def test_health_needs_no_token_even_when_hooks_do(monkeypatch):
    with patch("api.app.logging.init_app"):
        app = create_app("development")
    app.config["WEBHOOK_TOKEN"] = "abc"  # noqa: S105

    assert app.test_client().get("/health").status_code == 200
    assert app.test_client().post("/hooks/item_added", json={}).status_code == 401


def test_create_app_warns_when_hooks_are_unauthenticated(monkeypatch, caplog):
    monkeypatch.setattr("api.config.ProductionConfig.WEBHOOK_TOKEN", None, raising=False)
    monkeypatch.setattr("api.config.DevelopmentConfig.WEBHOOK_TOKEN", None, raising=False)
    caplog.set_level(logging.WARNING)

    with patch("api.app.logging.init_app"):
        create_app("development")

    assert any("WEBHOOK_TOKEN is not set" in r.message for r in caplog.records)


def test_create_app_is_quiet_when_a_token_is_configured(monkeypatch, caplog):
    monkeypatch.setattr("api.config.DevelopmentConfig.WEBHOOK_TOKEN", "abc", raising=False)
    caplog.set_level(logging.WARNING)

    with patch("api.app.logging.init_app"):
        create_app("development")

    assert not any("WEBHOOK_TOKEN" in r.message for r in caplog.records)
