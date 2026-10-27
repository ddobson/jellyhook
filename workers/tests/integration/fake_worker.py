"""A worker process with a scriptable job, for tests that need real signals and real exits.

It wires the real QueueConsumer, supervisor and signal handlers exactly as `workers.main` does,
but the job is a stand-in: it sleeps for the number of seconds in the message and leaves marker
files so the test can see what happened. Configuration comes from the environment:

    RABBITMQ_HOST / RABBITMQ_PORT   where the broker is
    FAKE_QUEUE                      the work queue to consume
    FAKE_MARKER_DIR                 where to write started-<id> / finished-<id> markers
    WORKER_SHUTDOWN_GRACE_SECONDS   drain time on SIGTERM (read by workers.config)
"""

import json
import os
import pathlib
import sys
import threading

from workers import config
from workers.clients import rabbitmq
from workers.main import install_signal_handlers
from workers.results import ProcessingResult
from workers.supervisor import supervise


def job(webhook_id: str, body: bytes) -> ProcessingResult:
    """Sleep as the message says, recording when the job started and finished."""
    message = json.loads(body)
    markers = pathlib.Path(os.environ["FAKE_MARKER_DIR"])
    earlier_runs = list(markers.glob(f"started-{message['id']}-*"))
    (markers / f"started-{message['id']}-{os.getpid()}").touch()
    seconds = message.get("sleep", 0)
    if earlier_runs:  # a redelivery; "sleep_on_rerun" lets a test make it short without editing
        seconds = message.get("sleep_on_rerun", seconds)
    threading.Event().wait(seconds)
    (markers / f"finished-{message['id']}-{os.getpid()}").touch()
    return ProcessingResult.success()


def main() -> int:
    stop_event = threading.Event()
    install_signal_handlers(stop_event)
    consumer = rabbitmq.QueueConsumer(
        webhook_id="fake",
        queue=os.environ["FAKE_QUEUE"],
        handler=job,
        job_slots=threading.BoundedSemaphore(1),
        stop_event=stop_event,
    )
    return supervise([consumer], stop_event, config.SHUTDOWN_GRACE_SECONDS)


if __name__ == "__main__":
    sys.exit(main())
