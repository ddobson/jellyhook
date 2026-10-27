import importlib
import runpy
import sys
from unittest import mock

import pytest
from flask import Flask


@pytest.fixture(autouse=True)
def fresh_main_module(monkeypatch: pytest.MonkeyPatch) -> None:
    """Importing api.main builds the app at import time, so each test needs a clean import."""
    monkeypatch.delitem(sys.modules, "api.main", raising=False)


def test_main_requires_flask_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FLASK_ENV", raising=False)

    with pytest.raises(RuntimeError, match="FLASK_ENV"):
        importlib.import_module("api.main")


def test_main_builds_the_app_for_the_configured_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FLASK_ENV", "production")
    flask_app = Flask(__name__)

    with mock.patch("api.app.create_app", return_value=flask_app) as create_app:
        module = importlib.import_module("api.main")

    create_app.assert_called_once_with("production")
    assert module.app is flask_app


def test_running_the_module_starts_the_dev_server(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLASK_ENV", "development")

    with mock.patch.object(Flask, "run") as run:
        runpy.run_module("api.main", run_name="__main__")

    run.assert_called_once_with()
