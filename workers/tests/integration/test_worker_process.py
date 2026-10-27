"""Worker processes against a real broker: real signals, real exit codes, real kills."""

import json
import os
import pathlib
import signal
import subprocess
import sys

import pytest
import support

from workers.clients import topology

pytestmark = pytest.mark.integration

HERE = pathlib.Path(__file__).parent


def spawn(broker, env_extra: dict[str, str], *args: str) -> subprocess.Popen:
    """Start a worker process pointed at the test broker."""
    env = {
        **os.environ,
        "RABBITMQ_HOST": "127.0.0.1",
        "RABBITMQ_PORT": str(broker.port),
        "WORKER_SHUTDOWN_GRACE_SECONDS": "5",
        **env_extra,
    }
    return subprocess.Popen(  # noqa: S603 - our own interpreter and test scripts
        [sys.executable, *args],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def reap(process: subprocess.Popen, timeout: float = 30) -> int:
    """Wait for a process to exit, killing it if it will not."""
    try:
        return process.wait(timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        output = process.stdout.read() if process.stdout else ""
        pytest.fail(f"process did not exit in {timeout}s:\n{output}")


@pytest.fixture
def processes():
    """Track spawned processes so none outlives its test."""
    started: list[subprocess.Popen] = []
    yield started
    for process in started:
        if process.poll() is None:
            process.kill()
            process.wait()


@pytest.fixture
def markers(tmp_path: pathlib.Path) -> pathlib.Path:
    directory = tmp_path / "markers"
    directory.mkdir()
    return directory


def fake_worker(broker, queue, markers, processes, *, grace: int = 5) -> subprocess.Popen:
    process = spawn(
        broker,
        {
            "FAKE_QUEUE": queue,
            "FAKE_MARKER_DIR": str(markers),
            "WORKER_SHUTDOWN_GRACE_SECONDS": str(grace),
        },
        str(HERE / "fake_worker.py"),
    )
    processes.append(process)
    return process


def started(markers: pathlib.Path, job_id: int) -> list[str]:
    return [p.name for p in markers.glob(f"started-{job_id}-*")]


def finished(markers: pathlib.Path, job_id: int) -> list[str]:
    return [p.name for p in markers.glob(f"finished-{job_id}-*")]


def consuming(broker, queue) -> bool:
    """True once a consumer is attached to the queue."""
    out = support.rabbitmqctl(broker, "list_queues", "name", "consumers", "--quiet")
    return any(line.split("\t")[:2] == [queue, "1"] for line in out.splitlines())


def test_sigterm_lets_the_running_job_finish_then_exits_zero(broker, queue, markers, processes):
    support.publish(broker, queue, json.dumps({"id": 1, "sleep": 2}))
    worker = fake_worker(broker, queue, markers, processes, grace=10)
    assert support.wait_until(lambda: started(markers, 1), timeout=30)

    worker.send_signal(signal.SIGTERM)

    assert reap(worker) == 0
    assert finished(markers, 1)  # the job was allowed to complete
    assert support.ready_count(broker, queue) == 0
    assert support.unacked_count(broker, queue) == 0  # and was acknowledged, not left hanging


def test_sigterm_past_the_grace_period_exits_and_the_job_is_redelivered(
    broker, queue, markers, processes
):
    support.publish(broker, queue, json.dumps({"id": 2, "sleep": 60}))
    worker = fake_worker(broker, queue, markers, processes, grace=2)
    assert support.wait_until(lambda: started(markers, 2), timeout=30)

    worker.send_signal(signal.SIGTERM)

    assert reap(worker) == 0
    assert not finished(markers, 2)
    # The abandoned message is back on the queue for the next worker (a new deploy)
    assert support.wait_until(lambda: support.ready_count(broker, queue) == 1)


def test_a_job_killed_mid_run_is_picked_up_again_by_the_next_worker(
    broker, queue, markers, processes
):
    """Kill -9 / OOM / power cut: no goodbye. At-least-once means the next worker runs it."""
    support.publish(broker, queue, json.dumps({"id": 3, "sleep": 60, "sleep_on_rerun": 0}))
    first = fake_worker(broker, queue, markers, processes)
    assert support.wait_until(lambda: started(markers, 3), timeout=30)

    first.kill()
    first.wait()
    second = fake_worker(broker, queue, markers, processes)  # nothing new is published

    assert support.wait_until(lambda: finished(markers, 3), timeout=30)
    assert {name.split("-")[2] for name in started(markers, 3)} == {
        str(first.pid),
        str(second.pid),
    }
    assert [name.split("-")[2] for name in finished(markers, 3)] == [str(second.pid)]
    # The redelivered job completed and was acknowledged: nothing left ready or unacknowledged
    assert support.wait_until(lambda: support.total_count(broker, queue) == 0)
    second.send_signal(signal.SIGTERM)
    assert reap(second) == 0


def test_the_worker_exits_nonzero_when_the_broker_restarts_and_the_next_one_carries_on(
    broker, queue, markers, processes
):
    """Fail fast: Docker's restart policy does the reconnecting, not the process."""
    worker = fake_worker(broker, queue, markers, processes)
    assert support.wait_until(lambda: consuming(broker, queue), timeout=30)

    support.restart_broker(broker)

    assert reap(worker, timeout=60) == 1  # what makes `restart: unless-stopped` kick in

    # What the restart policy would do: start it again. Work published meanwhile is waiting.
    support.publish(broker, queue, json.dumps({"id": 4, "sleep": 0}))
    replacement = fake_worker(broker, queue, markers, processes)
    assert support.wait_until(lambda: finished(markers, 4), timeout=30)
    replacement.send_signal(signal.SIGTERM)
    assert reap(replacement) == 0


def test_a_broker_restart_in_the_middle_of_a_job_costs_a_rerun_not_the_message(
    broker, queue, markers, processes
):
    """The worst timing: the job is running when the connection drops, so it cannot be acked."""
    support.publish(broker, queue, json.dumps({"id": 5, "sleep": 3}))
    first = fake_worker(broker, queue, markers, processes)
    assert support.wait_until(lambda: started(markers, 5), timeout=30)

    support.restart_broker(broker)

    assert reap(first, timeout=60) == 1
    assert support.wait_until(lambda: support.ready_count(broker, queue) == 1)  # still queued
    second = fake_worker(broker, queue, markers, processes)
    assert support.wait_until(lambda: finished(markers, 5), timeout=30)
    # At-least-once: the job started in both processes, and the message is now finally acked
    assert len(started(markers, 5)) == 2
    assert support.wait_until(lambda: support.unacked_count(broker, queue) == 0)
    assert support.ready_count(broker, queue) == 0
    second.send_signal(signal.SIGTERM)
    assert reap(second) == 0


def test_messages_queued_before_a_broker_restart_survive_it(broker, queue, markers, processes):
    """Persistent messages on a durable queue: the whole reason the API sets delivery_mode=2."""
    for n in (10, 11, 12):
        support.publish(broker, queue, json.dumps({"id": n, "sleep": 0}))

    support.restart_broker(broker)

    assert support.ready_count(broker, queue) == 3
    worker = fake_worker(broker, queue, markers, processes)
    assert support.wait_until(lambda: all(finished(markers, n) for n in (10, 11, 12)), timeout=30)
    worker.send_signal(signal.SIGTERM)
    assert reap(worker) == 0


def test_messages_survive_the_broker_being_killed_outright(broker, queue):
    """No clean shutdown at all (SIGKILL): confirmed persistent messages are still there."""
    support.establish_queue(broker, queue)  # a queue that has long existed, as in production
    support.publish(broker, queue, b'{"id": 20}')

    support.kill_broker(broker)
    support.start_stopped_broker(broker)

    assert support.ready_count(broker, queue) == 1


# --- the real entry point -----------------------------------------------------------------


def write_real_worker_config(tmp_path: pathlib.Path, queue: str) -> dict[str, str]:
    """Config and media for `python -m workers.main` using a service that needs no Jellyfin."""
    movies = tmp_path / "movies"
    movie_dir = movies / "Item A (2023)"
    movie_dir.mkdir(parents=True)
    (movie_dir / "Item A (2023) [tmdbid-1].mkv").write_bytes(b"")
    standup = tmp_path / "standup"
    standup.mkdir()
    config_file = tmp_path / "jellyhook.json"
    config_file.write_text(
        json.dumps(
            {
                "worker": {
                    "general": {"naming_schemes": {"movie": "trash"}},
                    "webhooks": {
                        "item_added": {
                            "enabled": True,
                            "queue": queue,
                            "services": [
                                {
                                    "name": "metadata_update",
                                    "enabled": True,
                                    "priority": 10,
                                    "config": {"paths": [], "patterns": []},
                                }
                            ],
                        }
                    },
                }
            }
        )
    )
    return {
        "JELLYHOOK_CONFIG_PATH": str(config_file),
        "MOVIE_PATH": str(movies),
        "STANDUP_PATH": str(standup),
        "TEMP_DIR": str(tmp_path / "tmp"),
    }


def message(name: str) -> str:
    return json.dumps(
        {"Name": name, "Year": 2023, "ItemId": "1", "ItemType": "Movie", "Genres": "", "Tags": ""}
    )


def test_the_real_worker_processes_a_message_and_stops_cleanly_on_sigterm(
    broker, queue, tmp_path, processes
):
    """`python -m workers.main` end to end: config, orchestrator, service, ack, SIGTERM."""
    env = write_real_worker_config(tmp_path, queue)
    support.publish(broker, queue, message("Item A"))
    worker = spawn(broker, env, "-m", "workers.main")
    processes.append(worker)

    assert support.wait_until(
        lambda: consuming(broker, queue)
        and support.ready_count(broker, queue) == 0
        and support.unacked_count(broker, queue) == 0,
        timeout=60,
    )
    worker.send_signal(signal.SIGTERM)

    assert reap(worker) == 0
    assert support.ready_count(broker, topology.retry_queue_name(queue)) == 0
    assert support.ready_count(broker, topology.dead_queue_name(queue)) == 0


def test_the_real_worker_parks_a_message_for_a_file_that_has_not_landed_on_retry(
    broker, queue, tmp_path, processes
):
    """Jellyfin announces an item before the file is on disk: retried, not dropped."""
    env = write_real_worker_config(tmp_path, queue)
    support.publish(broker, queue, message("Item Z"))  # no such file
    worker = spawn(broker, env, "-m", "workers.main")
    processes.append(worker)

    retry = topology.retry_queue_name(queue)
    assert support.wait_until(lambda: support.ready_count(broker, retry) == 1, timeout=60)
    worker.send_signal(signal.SIGTERM)
    assert reap(worker) == 0

    ((properties, _),) = support.take_all(broker, retry)
    assert properties.headers[topology.ATTEMPTS_HEADER] == 1
    assert "MediaNotFoundError" in properties.headers[topology.FAILURE_REASON_HEADER]
    assert support.ready_count(broker, topology.dead_queue_name(queue)) == 0
