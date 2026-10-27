import logging
from collections.abc import Iterator

import pytest
from flask import Flask

from api.logging import LoguruHandler, init_app


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
