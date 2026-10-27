from flask import Flask

from api import logging

# Blueprint routes must be imported to be registered in the app factory function
# That is why hooks.items is imported here
from api.config import config
from api.errors import register_error_handlers
from api.hooks import items  # noqa: F401
from api.rabbitmq import RabbitMQ
from api.routes import webhooks


def create_app(config_name: str) -> Flask:
    """Create a Flask app instance.

    Args:
        config_name (str): The name of the configuration to use.

    Returns:
        Flask: The Flask app instance.
    """
    app = Flask(__name__)
    app.config.from_object(config[config_name])

    logging.init_app(app)
    register_error_handlers(app)

    rabbitmq = RabbitMQ()
    rabbitmq.init_app(app)

    if not app.config.get("WEBHOOK_TOKEN"):
        app.logger.warning("WEBHOOK_TOKEN is not set: /hooks/* accepts requests from anyone")

    app.add_url_rule("/health", view_func=lambda: {"status": "OK"})
    app.register_blueprint(webhooks)

    return app
