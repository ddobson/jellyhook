import signal
import sys
import threading
from types import FrameType

from workers import config
from workers.clients import rabbitmq
from workers.logger import logger
from workers.services.orchestrator import process_webhook_message
from workers.supervisor import supervise


def install_signal_handlers(stop_event: threading.Event) -> None:
    """Turn SIGTERM (docker stop) and SIGINT (ctrl-c) into a graceful shutdown request.

    The handler only sets the event. Logging here could deadlock if the signal lands while
    the main thread is mid-log, and the supervisor logs the shutdown once it wakes up.

    Args:
        stop_event (threading.Event): The event to set.
    """

    def request_stop(signum: int, frame: FrameType | None) -> None:
        stop_event.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)


def main() -> int:
    """Main function for the worker.

    Starts a long running process that listens for messages on the queue.

    Returns:
        int: The process exit code. Non-zero when the worker cannot run, so a restart policy
            can bring it back.
    """
    logger.info(f"Loading Jellyhook configuration from {config.JELLYHOOK_CONFIG_PATH}")
    worker_config = config.WorkerConfig.load(config.JELLYHOOK_CONFIG_PATH)  # Singleton first init

    # Get enabled webhooks
    enabled_webhooks = worker_config.get_enabled_webhooks()
    if not enabled_webhooks:
        logger.warning("No enabled webhooks found in configuration file.")
        logger.warning("Exiting...")
        return 1

    stop_event = threading.Event()
    install_signal_handlers(stop_event)

    job_slots = threading.BoundedSemaphore(config.MAX_CONCURRENT_JOBS)
    consumers = [
        rabbitmq.QueueConsumer(
            webhook_id=webhook_id,
            queue=webhook_config["queue"],
            handler=process_webhook_message,
            job_slots=job_slots,
            stop_event=stop_event,
        )
        for webhook_id, webhook_config in enabled_webhooks.items()
    ]
    return supervise(consumers, stop_event, config.SHUTDOWN_GRACE_SECONDS)


if __name__ == "__main__":
    if config.DEBUG:
        import debugpy

        debugpy.listen(("0.0.0.0", 5678))
    sys.exit(main())
