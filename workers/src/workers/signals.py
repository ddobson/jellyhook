import signal
import threading
from types import FrameType


def install_signal_handlers(stop_event: threading.Event) -> None:
    """Turn SIGTERM (docker stop) and SIGINT (ctrl-c) into a graceful shutdown request.

    Shared by the worker and the scheduler. The handler only sets the event. Logging here could
    deadlock if the signal lands while the main thread is mid-log, so the process logs the
    shutdown once it wakes up.

    Args:
        stop_event (threading.Event): The event to set.
    """

    def request_stop(signum: int, frame: FrameType | None) -> None:
        stop_event.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
