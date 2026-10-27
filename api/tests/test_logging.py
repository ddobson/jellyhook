import logging
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest
from flask import Flask
from loguru import logger

from api.logging import LoguruHandler, _format_json_message, init_app


@pytest.fixture
def logging_app() -> Iterator[Flask]:
    """Flask app whose logging is initialised, with global logging state restored afterwards."""
    root = logging.getLogger()
    saved_root = (root.handlers[:], root.propagate)
    saved_loggers = {
        name: (log.handlers[:], log.propagate, log.level)
        for name in ("pika", "werkzeug")
        for log in [logging.getLogger(name)]
    }

    app = Flask(__name__)
    app.config["ENV"] = "development"
    init_app(app)
    yield app

    root.handlers, root.propagate = saved_root[0], saved_root[1]
    for name, (handlers, propagate, level) in saved_loggers.items():
        log = logging.getLogger(name)
        log.handlers, log.propagate = handlers, propagate
        log.setLevel(level)


def test_pika_warnings_are_not_silenced(logging_app: Flask) -> None:
    """Connection errors from pika must reach loguru instead of a NullHandler."""
    pika_logger = logging.getLogger("pika")

    assert any(isinstance(h, LoguruHandler) for h in pika_logger.handlers)
    assert not any(isinstance(h, logging.NullHandler) for h in pika_logger.handlers)
    assert pika_logger.isEnabledFor(logging.WARNING)


def test_pika_debug_noise_is_filtered(logging_app: Flask) -> None:
    """Only warning level and above is let through, so routine pika chatter stays out."""
    child = logging.getLogger("pika.adapters.blocking_connection")

    assert not child.isEnabledFor(logging.INFO)
    assert child.isEnabledFor(logging.ERROR)


def test_werkzeug_stays_silenced(logging_app: Flask) -> None:
    """The dev server's request logging is still dropped (loguru logs requests itself)."""
    werkzeug_logger = logging.getLogger("werkzeug")

    assert all(isinstance(h, logging.NullHandler) for h in werkzeug_logger.handlers)
    assert werkzeug_logger.propagate is False


@pytest.fixture
def captured() -> Iterator[list[str]]:
    """Messages that reach loguru, after init_app has replaced its sinks."""
    messages: list[str] = []
    sink_id = logger.add(lambda message: messages.append(message.record["message"]), level="DEBUG")
    yield messages
    logger.remove(sink_id)


def test_stdlib_records_are_forwarded_to_loguru(logging_app: Flask, captured: list[str]) -> None:
    logging.getLogger("some.library").warning("connection lost")

    assert "connection lost" in captured


def test_records_with_a_custom_level_still_get_through(
    logging_app: Flask, captured: list[str]
) -> None:
    """A level loguru has no name for falls back to the number instead of raising."""
    logging.addLevelName(35, "NOTICEISH")
    logging.getLogger("some.library").log(35, "custom level")

    assert "custom level" in captured


def test_emit_survives_a_missing_caller_frame(captured: list[str]) -> None:
    record = logging.LogRecord("x", logging.ERROR, __file__, 1, "no frame", None, None)

    with mock.patch("api.logging.logging.currentframe", return_value=None):
        LoguruHandler().emit(record)

    assert "no frame" in captured


def test_emit_skips_frames_inside_the_logging_module(captured: list[str]) -> None:
    """The reported caller is the first frame outside stdlib logging, not a logging internal."""
    caller = SimpleNamespace(f_code=SimpleNamespace(co_filename="app.py"), f_back=None)
    inside_logging = SimpleNamespace(
        f_code=SimpleNamespace(co_filename=logging.__file__), f_back=caller
    )
    record = logging.LogRecord("x", logging.ERROR, __file__, 1, "walked", None, None)

    with mock.patch("api.logging.logging.currentframe", return_value=inside_logging):
        LoguruHandler().emit(record)

    assert "walked" in captured


def test_requests_and_responses_are_logged(logging_app: Flask, captured: list[str]) -> None:
    @logging_app.get("/ping")
    def ping() -> str:
        return "pong"

    logging_app.test_client().get("/ping")

    assert "Request: GET /ping" in captured
    assert "Response: 200" in captured


def test_production_logging_initialises(logging_app: Flask) -> None:
    """The production format drops colour; initialising with it must not raise."""
    app = Flask("production-app")
    app.config["ENV"] = "production"

    assert init_app(app) is logger


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ('{"a": 1}', '{\n  "a": 1\n}'),
        ("[1, 2]", "[\n  1,\n  2\n]"),
        ("{not json}", "{not json}"),
        ({"a": 1}, '{\n  "a": 1\n}'),
        ("plain text", "plain text"),
    ],
)
def test_json_messages_are_pretty_printed(message: Any, expected: str) -> None:
    record: Any = {"message": message}

    _format_json_message(record)

    assert record["message"] == expected
