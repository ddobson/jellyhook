from flask import Blueprint

from api.auth import require_webhook_token

webhooks = Blueprint("webhooks", __name__, url_prefix="/hooks")
# Runs for every matched /hooks route, including ones added later. /health is not under it.
# (Flask skips blueprint hooks when routing itself fails, so an unknown path or wrong method
# still gets 404/405 without a token; that reveals nothing the public repo does not.)
webhooks.before_request(require_webhook_token)
