import os
import sys
from logging import Logger

from loguru import logger as guru_logger


def get_logger() -> Logger:
    """Return a logger instance."""
    # Remove the default logger
    guru_logger.remove()

    fmt = "{time:YYYY-MM-DD HH:mm:ss.SSS} {level} {name}::{function} | {message}"
    level = "DEBUG" if os.getenv("WORKER_ENV") == "development" else "INFO"

    # Add a logger that logs to stdout
    guru_logger.add(sys.stdout, format=fmt, level=level, colorize=True)

    if os.getenv("WORKER_ENV") != "production":
        return guru_logger

    # Also log to file in production
    guru_logger.add(
        "/config/logs/worker.log",
        format=fmt,
        level=level,
        rotation="10 MB",
        retention="10 days",
    )

    return guru_logger


logger = get_logger()
