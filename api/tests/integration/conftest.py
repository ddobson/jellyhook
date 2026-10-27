"""Fixtures for the real-RabbitMQ integration suite (see the worker suite for the rationale).

Not part of the default run: `uv run pytest -m integration tests/integration`, or
`make test_integration`. Without Docker the tests are skipped, unless JELLYHOOK_IT_REQUIRED=1.
"""

import os
import uuid
from collections.abc import Iterator

import pytest
import support
from flask import Flask
from support import Broker


@pytest.fixture(scope="session")
def broker() -> Iterator[Broker]:
    """One real RabbitMQ for the whole run. Tests isolate themselves with unique queue names."""
    if not support.docker_available():
        message = "Docker is not available, so the integration tests cannot run"
        if os.getenv("JELLYHOOK_IT_REQUIRED") == "1":
            pytest.fail(message)
        pytest.skip(message)

    running = support.start_broker()
    yield running
    support.remove_broker(running)


@pytest.fixture
def queue(broker: Broker) -> Iterator[str]:
    """A unique queue name that is deleted afterwards."""
    name = f"it:{uuid.uuid4().hex[:10]}"
    yield name
    support.delete_queues(broker, name)


@pytest.fixture
def live_app(app: Flask, broker: Broker) -> Flask:
    """The test app, pointed at the test broker."""
    app.config.update(RABBITMQ_HOST="127.0.0.1", RABBITMQ_PORT=broker.port)
    return app
