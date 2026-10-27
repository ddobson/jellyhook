import json

from flask import Response, abort, current_app, request

from api.routes import webhooks

ITEM_ADDED_QUEUE = "jellyfin:item_added"


@webhooks.route("/item_added", methods=["POST"])
def item_added() -> Response:
    """Handle item added webhook from Jellyfin.

    This endpoint is called when a new item is added to the Jellyfin library.
    It publishes the item data to a RabbitMQ queue for further processing.

    Returns:
        Response: A 202 Accepted response, sent only once RabbitMQ has confirmed the message.
            400 for a body that is not a JSON object, 401 if a token is required and missing,
            503 (with Retry-After) if the message could not be queued.
    """
    # force=True: accept the body whatever Content-Type the sender used (the Jellyfin plugin
    # does not always label it as JSON). Anything that is not a JSON object is the client's error.
    data = request.get_json(force=True, silent=True)
    if not isinstance(data, dict):
        abort(400, "Request body must be a JSON object")

    # Logged before publishing so nothing that can go wrong in logging can turn a message that is
    # already queued into a 500 (and a duplicate when the sender retries).
    current_app.logger.info({"operation": "item_added", "data": data})

    rabbitmq = current_app.extensions["rabbitmq"]
    rabbitmq.publish(
        queue=ITEM_ADDED_QUEUE,
        body=json.dumps(data),
        content_type="application/json",
    )
    return Response(status=202)
