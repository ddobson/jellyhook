from flask import Blueprint

from api.auth import require_webhook_token

webhooks = Blueprint("webhooks", __name__, url_prefix="/hooks")
# Applies to every /hooks route, including ones added later. /health is not under it.
webhooks.before_request(require_webhook_token)
