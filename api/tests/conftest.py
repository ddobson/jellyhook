import pytest
from flask import Flask

from api.rabbitmq import RabbitMQ
from api.routes import webhooks


@pytest.fixture
def app():
    """Fixture to create a Flask app instance."""
    app = Flask(__name__)
    app.register_blueprint(webhooks)
    app.config.update(
        {
            "RABBITMQ_HOST": "localhost",
            "RABBITMQ_PORT": 5672,
            "RABBITMQ_USERNAME": "guest",
            "RABBITMQ_PASSWORD": "guest",
        },
    )
    rabbitmq = RabbitMQ()
    rabbitmq.init_app(app)
    # Verify the extension was registered
    assert "rabbitmq" in app.extensions
    assert hasattr(app, "rabbitmq")
    return app


@pytest.fixture
def client(app):
    return app.test_client()
