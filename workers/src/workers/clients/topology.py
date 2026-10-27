"""Queue layout for a webhook.

Each webhook queue gets two companions, all on the default exchange:

    <queue>         the work queue. Declared exactly as the API declares it (durable, no
                    arguments), so existing deployments keep working.
    <queue>.retry   holds a failed message for RETRY_DELAY_MS, then dead-letters it back onto
                    <queue>.
    <queue>.dead    where messages end up once retries are exhausted or the failure is
                    permanent. Nothing consumes it: inspect it in the management UI, and
                    shovel messages back onto <queue> to replay them.

The retry and dead-letter hops are done by the worker (republish, then ack) instead of with a
broker-side dead-letter exchange on <queue>. Adding `x-dead-letter-exchange` to a queue that
already exists makes every redeclare fail with PRECONDITION_FAILED, which would break the API
until the queue was deleted by hand. It also lets us record why a message failed.

RETRY_DELAY_MS is part of the retry queue's declaration. Changing it means deleting
`<queue>.retry` first (it is empty between failures), or the declare fails the same way.
"""

from pika.adapters.blocking_connection import BlockingChannel

RETRY_DELAY_MS = 60_000

ATTEMPTS_HEADER = "x-jellyhook-attempts"
FAILURE_REASON_HEADER = "x-jellyhook-failure-reason"


def retry_queue_name(queue: str) -> str:
    """Name of the delay queue that feeds failed messages back onto `queue`."""
    return f"{queue}.retry"


def dead_queue_name(queue: str) -> str:
    """Name of the queue that holds messages given up on."""
    return f"{queue}.dead"


def declare_queues(channel: BlockingChannel, queue: str) -> int:
    """Declare a work queue and its retry and dead-letter companions.

    Args:
        channel (BlockingChannel): The channel to declare on.
        queue (str): The name of the work queue.

    Returns:
        int: How many messages are waiting on the dead-letter queue.
    """
    channel.queue_declare(queue=queue, durable=True)
    channel.queue_declare(
        queue=retry_queue_name(queue),
        durable=True,
        arguments={
            "x-message-ttl": RETRY_DELAY_MS,
            "x-dead-letter-exchange": "",
            "x-dead-letter-routing-key": queue,
        },
    )
    dead = channel.queue_declare(queue=dead_queue_name(queue), durable=True)
    return int(dead.method.message_count)
