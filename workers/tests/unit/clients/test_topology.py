from unittest import mock

from workers.clients import topology


def test_companion_queue_names():
    assert topology.retry_queue_name("jellyfin:item_added") == "jellyfin:item_added.retry"
    assert topology.dead_queue_name("jellyfin:item_added") == "jellyfin:item_added.dead"


def test_declare_queues_keeps_work_queue_compatible_with_the_api():
    """The API declares the work queue as durable with no arguments; a mismatch would fail it."""
    channel = mock.MagicMock()

    topology.declare_queues(channel, "q")

    channel.queue_declare.assert_any_call(queue="q", durable=True)


def test_declare_queues_routes_retries_back_to_the_work_queue_after_a_delay():
    channel = mock.MagicMock()

    topology.declare_queues(channel, "q")

    channel.queue_declare.assert_any_call(
        queue="q.retry",
        durable=True,
        arguments={
            "x-message-ttl": topology.RETRY_DELAY_MS,
            "x-dead-letter-exchange": "",
            "x-dead-letter-routing-key": "q",
        },
    )


def test_declare_queues_declares_a_durable_dead_letter_queue():
    channel = mock.MagicMock()

    topology.declare_queues(channel, "q")

    channel.queue_declare.assert_any_call(queue="q.dead", durable=True)
    assert channel.queue_declare.call_count == 3
