import json
import logging
import sys

from flask import Flask, request
from loguru import logger

from api.config import Env

# Completely silence specific libraries
SILENCED_LOGGERS = (
    "werkzeug",  # Flask's development server
    "pika",  # RabbitMQ client
)


def _format_json_message(record):
    """Pretty print dict, list or JSON string log messages."""
    message = record["message"]

    # If message is already a string representation of dict or list
    if isinstance(message, str) and (
        (message.startswith("{") and message.endswith("}"))
        or (message.startswith("[") and message.endswith("]"))
    ):
        try:
            parsed_message = json.loads(message)
            record["message"] = json.dumps(parsed_message, indent=2)
        except json.JSONDecodeError:
            # Not a valid JSON string, keep as is
            pass
    # If message is a dict or list
    elif isinstance(message, (dict, list)):
        record["message"] = json.dumps(message, indent=2)

    return record


class LoguruHandler(logging.Handler):
    """Custom handler to route stdlib logging to loguru."""

    def emit(self, record):
        """Emit a log record."""
        # Get corresponding Loguru level if it exists
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Find caller from where originated the logged message
        frame, depth = logging.currentframe(), 2
        while frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def init_app(app: Flask):
    """Initialize logging configuration for the Flask app.

    Args:
        app: Flask application instance
    """
    # Remove default logger
    logger.remove()

    # Configure format based on environment
    if is_production := app.config["ENV"] == Env.PRODUCTION:
        # Production format (no colors)
        log_format = (
            "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level} | {name}:{function}:{line} | {message}"
        )
    else:
        # Development format (with colors)
        log_format = (
            "<yellow>{time:YYYY-MM-DD HH:mm:ss.SSS}</yellow> | "
            "<level>{level}</level> | "
            "<blue>{name}</blue>:<blue>{function}</blue>:<blue>{line}</blue> | "
            "<level>{message}</level>"
        )

    # Add console handler
    logger.configure(patcher=_format_json_message)
    logger.add(sys.stdout, format=log_format, level="INFO", colorize=not is_production)

    # Disable all handlers for the silenced loggers
    for logger_name in SILENCED_LOGGERS:
        log = logging.getLogger(logger_name)
        log.handlers = []
        log.addHandler(logging.NullHandler())
        log.propagate = False

    # Set up loguru as the handler for all other loggers
    for name in logging.root.manager.loggerDict:
        if name not in SILENCED_LOGGERS:
            log = logging.getLogger(name)
            log.handlers = []
            log.addHandler(LoguruHandler())
            log.propagate = False

    # Override root logger with our custom handler but maintain propagation=False
    # to prevent double logging
    root = logging.getLogger()
    root.handlers = []
    root.addHandler(LoguruHandler())
    root.propagate = False

    # Request logging middleware
    @app.before_request
    def log_request_info():
        logger.info(f"Request: {request.method} {request.path}")

    @app.after_request
    def log_response_info(response):
        logger.info(f"Response: {response.status_code}")
        return response

    # Register exception handler
    @app.errorhandler(Exception)
    def handle_exception(e):
        logger.exception(f"Unhandled exception: {str(e)}")
        # Return appropriate error response to client
        return {"error": "Internal server error"}, 500

    # Override Flask's logger with loguru
    app.logger.handlers = []
    app.logger.addHandler(LoguruHandler())
    app.logger.propagate = False
    app.logger = logger

    # Log app initialization
    logger.info(f"Flask app '{app.name}' initialized with loguru")
    logger.info(f"Running in {'production' if is_production else 'development'} mode")
    logger.info({"test": "json", "pretty": "printing", "works": True})

    return logger
