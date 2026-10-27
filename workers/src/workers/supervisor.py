import threading
from collections.abc import Sequence
from typing import Protocol

from workers.logger import logger

# Slack on top of the shutdown grace, for a consumer to tear down its connection
JOIN_MARGIN_SECONDS = 10


class Consumer(Protocol):
    """Something the supervisor can run in a thread until told to stop."""

    webhook_id: str

    def run(self, shutdown_grace: float) -> None:
        """Run until the shared stop event is set. Raise if it cannot continue."""


def supervise(
    consumers: Sequence[Consumer],
    stop_event: threading.Event,
    shutdown_grace: float,
) -> int:
    """Run consumers until a shutdown is requested or any of them stops.

    A consumer that dies, or exits while nobody asked it to, is a fault: the supervisor stops
    the others and reports failure so the process exits non-zero. With a restart policy,
    Docker then brings the worker back with a fresh connection. This replaces the old behavior
    where a dead consumer thread left a process that was running but consuming nothing.

    Args:
        consumers (Sequence[Consumer]): The consumers to run, one thread each.
        stop_event (threading.Event): Set by signal handlers to request shutdown. The
            supervisor also sets it when a consumer stops.
        shutdown_grace (float): Seconds each consumer gets to finish in-flight work.

    Returns:
        int: 0 after a requested shutdown, 1 if any consumer failed.
    """
    failures: list[str] = []
    failures_lock = threading.Lock()

    def run_consumer(consumer: Consumer) -> None:
        try:
            consumer.run(shutdown_grace)
            if not stop_event.is_set():
                raise RuntimeError("consumer exited without being asked to stop")
        except Exception as e:
            logger.exception(f"Consumer for webhook {consumer.webhook_id} failed")
            with failures_lock:
                failures.append(f"{consumer.webhook_id}: {type(e).__name__}: {e}")
        finally:
            stop_event.set()

    threads = [
        threading.Thread(
            target=run_consumer,
            args=(consumer,),
            name=f"consumer-{consumer.webhook_id}",
            daemon=True,
        )
        for consumer in consumers
    ]
    for thread in threads:
        thread.start()

    stop_event.wait()
    logger.info("Shutdown requested; waiting for consumers to drain")

    # Each consumer drains for at most `shutdown_grace`; the margin covers connection teardown
    for thread in threads:
        thread.join(timeout=shutdown_grace + JOIN_MARGIN_SECONDS)
        if thread.is_alive():
            logger.warning(f"Thread {thread.name} did not stop in time; exiting anyway")

    if failures:
        logger.error(f"Exiting with failure: {'; '.join(failures)}")
        return 1
    logger.info("Shutdown complete")
    return 0
