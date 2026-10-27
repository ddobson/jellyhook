"""Optional shared-secret check for the webhook endpoints."""

import hmac

from flask import current_app, request
from werkzeug.datastructures import WWWAuthenticate
from werkzeug.exceptions import Unauthorized

BEARER_PREFIX = "Bearer "


def require_webhook_token() -> None:
    """Reject requests that do not carry the configured token.

    Does nothing when `WEBHOOK_TOKEN` is unset or empty, so existing deployments keep working.
    Otherwise the request must send `Authorization: Bearer <token>`, which the Jellyfin webhook
    plugin can add as a custom request header. The comparison is constant-time so response timing
    does not leak the token.

    Raises:
        werkzeug.exceptions.Unauthorized: If a token is required and is missing or wrong.
    """
    expected = current_app.config.get("WEBHOOK_TOKEN")
    if not expected:
        return

    header = request.headers.get("Authorization", "")
    supplied = header.removeprefix(BEARER_PREFIX) if header.startswith(BEARER_PREFIX) else ""
    if not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise Unauthorized(
            "Missing or invalid webhook token",
            www_authenticate=WWWAuthenticate("Bearer"),
        )
