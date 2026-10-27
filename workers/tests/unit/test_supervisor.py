import threading
from types import SimpleNamespace

from workers.supervisor import supervise


def make_consumer(name, run):
    """Build a stand-in consumer."""
    return SimpleNamespace(webhook_id=name, run=run)


def test_clean_shutdown_exits_zero():
    """A requested stop that every consumer honours is not a failure."""
    stop = threading.Event()
    consumer = make_consumer("a", lambda grace: stop.wait())
    threading.Timer(0.05, stop.set).start()

    assert supervise([consumer], stop, shutdown_grace=1) == 0


def test_consumers_receive_the_shutdown_grace():
    stop = threading.Event()
    received = []
    consumer = make_consumer("a", lambda grace: (received.append(grace), stop.wait()))
    threading.Timer(0.05, stop.set).start()

    supervise([consumer], stop, shutdown_grace=42)

    assert received == [42]


def test_crashed_consumer_stops_everything_and_exits_non_zero():
    """The old worker kept running with no consumers; now a dead consumer ends the process."""
    stop = threading.Event()

    def crash(grace):
        raise ConnectionError("broker went away")

    healthy_saw_stop = threading.Event()
    healthy = make_consumer("healthy", lambda grace: (stop.wait(), healthy_saw_stop.set()))

    code = supervise([make_consumer("bad", crash), healthy], stop, shutdown_grace=1)

    assert code == 1
    assert stop.is_set()
    assert healthy_saw_stop.is_set()


def test_consumer_returning_without_a_stop_request_is_a_failure():
    """A consumer must not quietly walk away while the process keeps running."""
    stop = threading.Event()
    consumer = make_consumer("a", lambda grace: None)

    assert supervise([consumer], stop, shutdown_grace=1) == 1


def test_stop_requested_from_outside_wakes_the_supervisor():
    """Signal handlers only set the event; the supervisor must react to it."""
    stop = threading.Event()
    consumer = make_consumer("a", lambda grace: stop.wait())
    threading.Thread(target=lambda: stop.set()).start()

    assert supervise([consumer], stop, shutdown_grace=1) == 0
