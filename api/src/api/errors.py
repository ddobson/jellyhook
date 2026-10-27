"""Turn exceptions into JSON error responses with the right status code."""

from flask import Flask
from loguru import logger
from werkzeug.exceptions import HTTPException

from api.rabbitmq import PublishError

# Seconds a client is told to wait before sending a webhook again after a 503
RETRY_AFTER_SECONDS = 5


def register_error_handlers(app: Flask) -> None:
    """Register the app's error handlers.

    Without the HTTPException handler, Flask's own 400, 404 and 405 responses were caught by a
    blanket `Exception` handler and reported as 500, hiding client mistakes as server faults.

    Args:
        app (Flask): The Flask app.
    """

    @app.errorhandler(HTTPException)
    def handle_http_exception(
        e: HTTPException,
    ) -> tuple[dict[str, str], int, dict[str, str]]:
        # Keep headers that matter to the client (Allow on a 405, WWW-Authenticate on a 401)
        # but not the HTML content headers: the body is JSON.
        headers = {
            name: value
            for name, value in e.get_headers()
            if name.lower() not in {"content-type", "content-length"}
        }
        return {"error": e.description or ""}, e.code or 500, headers

    @app.errorhandler(PublishError)
    def handle_publish_error(e: PublishError) -> tuple[dict[str, str], int, dict[str, str]]:
        logger.error(f"Webhook not queued: {e}")
        return (
            {"error": "Message queue unavailable, try again shortly"},
            503,
            {"Retry-After": str(RETRY_AFTER_SECONDS)},
        )

    @app.errorhandler(Exception)
    def handle_exception(e: Exception) -> tuple[dict[str, str], int]:
        logger.exception(f"Unhandled exception: {e}")
        return {"error": "Internal server error"}, 500
