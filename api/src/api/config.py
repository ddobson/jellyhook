import os
from enum import StrEnum

from flask import Flask


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
    RABBITMQ_VHOST = os.getenv("RABBITMQ_VHOST", "/")

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
