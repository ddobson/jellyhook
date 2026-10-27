import os
from enum import StrEnum

from flask import Flask


def _port_from_env(name: str, default: int) -> int:
    """Read a port from the environment: blank means the default, junk is a clear error.

    Some platforms inject `<SERVICE>_PORT=tcp://host:port` for a service named `rabbitmq` (legacy
    Docker links, Kubernetes service links), which `int()` alone reports as a bare ValueError.
    """
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(
            f"{name} must be a port number, got {raw!r}. If the platform injects this variable "
            f"for a service named rabbitmq, set {name} explicitly to override it."
        ) from None


class Env(StrEnum):
    """Environment variables."""

    DEVELOPMENT = "development"
    PRODUCTION = "production"
    TESTING = "testing"


class Config:
    """Base configuration."""

    LOG_LEVEL = "INFO"
    RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "rabbitmq")
    RABBITMQ_USER = os.getenv("RABBITMQ_USER", "guest")
    RABBITMQ_PASS = os.getenv("RABBITMQ_PASS", "guest")
    RABBITMQ_PORT = _port_from_env("RABBITMQ_PORT", 5672)
    RABBITMQ_VHOST = os.getenv("RABBITMQ_VHOST", "/")
    # When set, /hooks/* requires "Authorization: Bearer <token>". Unset leaves them open.
    WEBHOOK_TOKEN = os.getenv("WEBHOOK_TOKEN")
    # Webhook payloads are small; refuse anything bigger than 1 MiB (HTTP 413)
    MAX_CONTENT_LENGTH = 1024 * 1024

    @staticmethod
    def init_app(app: Flask) -> None:
        """Initialize the app with the configuration."""


class DevelopmentConfig(Config):
    """Development configuration."""

    SECRET_KEY = "dev"  # noqa: S105
    LOG_LEVEL = "DEBUG"
    ENV = os.getenv("FLASK_ENV")


class ProductionConfig(Config):
    """Production configuration."""

    SECRET_KEY = os.getenv("SECRET_KEY")
    ENV = os.getenv("FLASK_ENV")


config = {"development": DevelopmentConfig(), "production": ProductionConfig()}
