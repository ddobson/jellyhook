from flask import Blueprint

webhooks = Blueprint("webhooks", __name__, url_prefix="/hooks")
