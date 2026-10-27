"""The API against a real RabbitMQ: what "202 Accepted" actually promises."""

import json
import socket
import threading
import time

import pika
import pytest
import support
from pika.exceptions import ChannelClosedByBroker

from api.hooks import items
from api.rabbitmq import PublishError

pytestmark = pytest.mark.integration


@pytest.fixture
def item_queue(monkeypatch, queue):
    """Point the item_added hook at this test's own queue."""
    monkeypatch.setattr(items, "ITEM_ADDED_QUEUE", queue)
    return queue


def post(app, payload=None):
    return app.test_client().post("/hooks/item_added", json=payload or {"Name": "Item A"})


def test_202_means_the_message_is_on_the_queue_and_persistent(broker, live_app, item_queue):
    response = post(live_app, {"Name": "Item A", "Year": 2023})

    assert response.status_code == 202
    assert support.ready_count(broker, item_queue) == 1
    ((properties, body),) = support.take_all(broker, item_queue)
    assert json.loads(body) == {"Name": "Item A", "Year": 2023}
    assert properties.delivery_mode == pika.DeliveryMode.Persistent.value
    assert properties.content_type == "application/json"


def test_an_accepted_webhook_survives_a_broker_restart(broker, live_app, item_queue):
    assert post(live_app).status_code == 202

    support.restart_broker(broker)

    assert support.ready_count(broker, item_queue) == 1


def test_an_accepted_webhook_survives_the_broker_being_killed(broker, live_app, item_queue):
    """No clean shutdown: the confirm only came back after the message was safely stored."""
    support.establish_queue(broker, item_queue)  # a queue that has long existed, as in production
    assert post(live_app).status_code == 202

    support.kill_broker(broker)
    support.start_stopped_broker(broker)

    assert support.ready_count(broker, item_queue) == 1


def test_a_stopped_broker_is_a_fast_503_and_the_api_recovers_by_itself(
    broker, live_app, item_queue
):
    support.stop_broker(broker)
    try:
        started = time.monotonic()
        response = post(live_app)
        elapsed = time.monotonic() - started
    finally:
        support.start_stopped_broker(broker)

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "5"
    assert elapsed < 15  # refused connections fail fast; nothing hangs the request

    # No restart of the API, no stale connection to clear
    assert post(live_app).status_code == 202
    assert support.ready_count(broker, item_queue) == 1


def test_a_broker_that_accepts_connections_but_never_answers_is_a_bounded_503(live_app):
    """The failure mode of a hung broker or a firewall that swallows the handshake."""
    with socket.socket() as silent:
        silent.bind(("127.0.0.1", 0))
        silent.listen(8)
        live_app.config["RABBITMQ_PORT"] = silent.getsockname()[1]

        started = time.monotonic()
        response = post(live_app)
        elapsed = time.monotonic() - started

    assert response.status_code == 503
    assert elapsed < 20  # 3 attempts x 3 s handshake timeout + retry delays, about 11 s


def test_a_queue_declared_with_different_arguments_is_a_503_not_a_lost_message(
    broker, live_app, item_queue
):
    """Why the declaration must match the worker's: a mismatch must be loud, not swallowed."""
    with support.connect(broker) as connection:
        connection.channel().queue_declare(
            queue=item_queue, durable=True, arguments={"x-dead-letter-exchange": ""}
        )

    response = post(live_app)

    assert response.status_code == 503
    assert support.ready_count(broker, item_queue) == 0


def test_publishing_to_a_missing_virtual_host_or_bad_credentials_is_a_503(live_app):
    live_app.config["RABBITMQ_PASS"] = "wrong"  # noqa: S105

    response = post(live_app)

    assert response.status_code == 503


def test_a_burst_from_a_library_scan_is_queued_exactly_once_each(broker, live_app, item_queue):
    """200 webhooks from 8 concurrent senders (the 2 workers x 4 threads gunicorn layout)."""
    total, senders = 200, 8
    statuses: list[int] = []
    lock = threading.Lock()

    def sender(batch: range) -> None:
        client = live_app.test_client()
        for n in batch:
            code = client.post("/hooks/item_added", json={"n": n}).status_code
            with lock:
                statuses.append(code)

    per_sender = total // senders
    threads = [
        threading.Thread(target=sender, args=(range(k * per_sender, (k + 1) * per_sender),))
        for k in range(senders)
    ]
    [t.start() for t in threads]
    [t.join(120) for t in threads]

    assert statuses.count(202) == total
    received = [json.loads(body)["n"] for _, body in support.take_all(broker, item_queue)]
    assert sorted(received) == list(range(total))  # none lost, none duplicated


def test_a_rejected_body_never_reaches_the_queue(broker, live_app, item_queue):
    client = live_app.test_client()

    response = client.post("/hooks/item_added", data=b"[1, 2]", content_type="application/json")

    assert response.status_code == 400
    assert support.ready_count(broker, item_queue) == 0


def test_publish_error_is_raised_for_an_unreachable_broker(live_app):
    live_app.config["RABBITMQ_PORT"] = support.free_port()  # nothing listens here

    with pytest.raises(PublishError), live_app.app_context():
        live_app.extensions["rabbitmq"].publish(queue="whatever", body="x")


def test_declaring_the_queue_the_way_the_api_does_is_idempotent(broker, live_app, item_queue):
    """Many requests redeclare the same queue; that must never fail or change it."""
    for _ in range(3):
        assert post(live_app).status_code == 202

    with support.connect(broker) as connection:
        channel = connection.channel()
        assert channel.queue_declare(queue=item_queue, durable=True).method.message_count == 3
        with pytest.raises(ChannelClosedByBroker):
            connection.channel().queue_declare(queue=item_queue, durable=False)
