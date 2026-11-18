"""The scheduler process: `python -m workers.scheduler [run "<playlist name>"]`.

Wires the scheduler core to the real clock, the RabbitMQ publisher and the signal handlers, and
turns its result into the process exit code.
"""

import sys
import threading
from collections.abc import Sequence
from pathlib import Path

from workers import config
from workers.scheduler.clock import SystemClock
from workers.scheduler.rabbitmq_publisher import RabbitMQRefreshPublisher
from workers.scheduler.scheduler import run_scheduler
from workers.signals import install_signal_handlers


def main(args: Sequence[str]) -> int:
    """Run the scheduler until SIGTERM or SIGINT, or publish one manual Refresh.

    Args:
        args (Sequence[str]): The command-line arguments after the program name: none, or
            `["run", "<playlist name>"]`.

    Returns:
        int: The process exit code, as `run_scheduler` reports it. A Refresh that cannot be
            published (the broker is unreachable, or drops the connection) is non-zero, so
            Docker's restart policy brings the scheduler back.
    """
    stop_event = threading.Event()
    install_signal_handlers(stop_event)
    return run_scheduler(
        args,
        config_path=config.JELLYHOOK_CONFIG_PATH,
        state_path=Path(config.SCHEDULER_STATE_PATH),
        clock=SystemClock(),
        publisher=RabbitMQRefreshPublisher(config.REFRESH_QUEUE),
        stop_event=stop_event,
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
