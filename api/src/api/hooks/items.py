import json

from flask import Response, current_app, request

from api.routes import webhooks

ITEM_ADDED_QUEUE = "jellyfin:item_added"


@webhooks.route("/item_added", methods=["POST"])
def item_added() -> Response:
    """Handle item added webhook from Jellyfin.

    This endpoint is called when a new item is added to the Jellyfin library.
    It publishes the item data to a RabbitMQ queue for further processing.

    Returns:
        Response: A 202 Accepted response indicating that the request was received successfully.
    """
    data = request.json
    rabbitmq = current_app.extensions["rabbitmq"]
    rabbitmq.publish(queue=ITEM_ADDED_QUEUE, body=json.dumps(data))
    current_app.logger.info({"operation": "item_added", "data": data})
    return Response(status=202)
