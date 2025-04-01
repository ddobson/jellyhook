import logging
import sys
from logging import LogRecord

from flask import Flask
from loguru import logger


class InterceptHandler(logging.Handler):
    """Intercepts logging messages and sends them to loguru."""

    def emit(self, record: LogRecord) -> None:
        """Intercept the log record and send it to loguru."""
        logger_opt = logger.opt(depth=6, exception=record.exc_info)
        logger_opt.log(record.levelno, record.getMessage())


class PropagateHandler(logging.Handler):
    """Propagates log messages to the gunicorn logger."""

    def emit(self, record: LogRecord) -> None:
        """Propagate the log record to the gunicorn logger."""
        logging.getLogger("gunicorn").handle(record)
        logging.getLogger("gunicorn.access").handle(record)
        logging.getLogger("gunicorn.error").handle(record)


def init_app(app: Flask) -> None:
    """Initialize the logging configuration for the Flask app."""
    fmt = "{time} {level} {message}"

    if app.config["ENV"] == "production":
        logger.add(PropagateHandler(), level=app.config["LOG_LEVEL"])
        logger.add(
            "/config/logs/api.log",
            format=fmt,
            level=app.config["LOG_LEVEL"],
            rotation="10 MB",
            retention="10 days",
        )

    logger.add(sys.stdout, level=app.config["LOG_LEVEL"], format=fmt, colorize=True)

    app.logger.addHandler(InterceptHandler())
