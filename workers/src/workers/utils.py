import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Collection, Sequence
from threading import Lock
from typing import IO, Any

from workers.logger import logger

# Longest any one external program may run. Remuxing a large 4K file is slow but not this slow;
# past it the program is almost certainly hung, and a stuck job would otherwise hold its queue
# slot (and eventually the broker's consumer_timeout) forever.
DEFAULT_COMMAND_TIMEOUT_SECONDS = 6 * 60 * 60

# How long to wait for output still in flight once the process has exited. The readers run
# alongside the process, so only what fits in a pipe buffer (about 64 KB) is left by then.
PIPE_DRAIN_SECONDS = 5


class SingletonMeta(type):
    """A thread-safe implementation of Singleton."""

    _instances = {}
    _lock = Lock()

    def __call__(cls, *args, **kwargs):
        """Override the __call__ method to control instance creation."""
        with cls._lock:
            if cls not in cls._instances:
                instance = super().__call__(*args, **kwargs)
                cls._instances[cls] = instance
        return cls._instances[cls]


def timer(func: Callable) -> Callable:
    """Time the execution of a function.

    Args:
        func (function): The function to time.

    Returns:
        function: The wrapped function
    """

    def wrapper(*args: tuple, **kwargs: dict) -> any:
        start = time.time()
        result = func(*args, **kwargs)
        end = time.time()
        logger.info(f"{func.__name__} took {end - start} seconds")
        return result

    return wrapper


def _drain_stream(
    stream: IO[str],
    sink: list[str],
    log: Callable[[str], None] | None,
) -> None:
    """Read a pipe to EOF, keeping every line and optionally logging it as it arrives.

    Args:
        stream (IO[str]): The pipe to read.
        sink (list[str]): Where the (right-stripped) lines are collected.
        log (Callable[[str], None] | None): Called with each line, or None to stay quiet.
    """
    for line in stream:
        stripped = line.rstrip()
        sink.append(stripped)
        if log is not None:
            log(stripped)


def _kill_process_group(process: subprocess.Popen[str]) -> None:
    """SIGKILL a process and everything it started (it leads its own process group)."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        process.kill()


def _finish_readers(process: subprocess.Popen[str], readers: list[threading.Thread]) -> None:
    """Wait for the pipe readers to reach EOF, so the captured output is complete.

    The readers normally finish as soon as the process exits. If one is still going, something the
    process started (a grandchild) is holding the pipe open: kill the process group to close it.

    Raises:
        RuntimeError: If a reader still has not finished, since the output would be incomplete.
    """
    if _join_all(readers, PIPE_DRAIN_SECONDS):
        return
    logger.warning("Output pipe still open after the process exited; killing its process group")
    _kill_process_group(process)
    if not _join_all(readers, PIPE_DRAIN_SECONDS):
        raise RuntimeError("Could not read all of the command's output")


def _join_all(threads: list[threading.Thread], seconds: float) -> bool:
    """Join threads within one shared deadline. Returns True if all of them finished."""
    deadline = time.monotonic() + seconds
    for thread in threads:
        thread.join(timeout=max(0.0, deadline - time.monotonic()))
    return not any(thread.is_alive() for thread in threads)


def run_command(
    cmd: Sequence[str | os.PathLike[str]],
    log_output: bool = False,
    log_err: bool = False,
    timeout: float | None = DEFAULT_COMMAND_TIMEOUT_SECONDS,
    ok_returncodes: Collection[int] = (0,),
    **kwargs: Any,
) -> subprocess.CompletedProcess[str]:
    """Run an external program and capture its output.

    The command is an argument list and never goes through a shell, so file names with spaces,
    quotes or `$(...)` in them are just file names. stdout and stderr are drained on separate
    threads: ffmpeg writes a steady stream of progress to stderr, and reading one pipe to EOF
    before the other deadlocks as soon as the unread pipe's buffer (about 64 KB) fills up.
    See https://docs.python.org/3/library/subprocess.html#subprocess.Popen.communicate

    Args:
        cmd (Sequence[str | os.PathLike[str]]): The program and its arguments.
        log_output (bool): Log stdout lines at INFO as they arrive.
        log_err (bool): Log stderr lines at ERROR as they arrive.
        timeout (float | None): Seconds before the process is killed. None waits forever.
        ok_returncodes (Collection[int]): Exit codes that count as success. mkvmerge, for
            one, exits 1 for "finished with warnings".
        **kwargs: Passed to `subprocess.Popen` (for example `cwd`). `shell` is not accepted.

    Raises:
        TypeError: If `cmd` is a string. Pass a list so no shell is involved.
        subprocess.TimeoutExpired: If the process outlived `timeout`. It has been killed.
        subprocess.CalledProcessError: If the exit code is not in `ok_returncodes`.

    Returns:
        subprocess.CompletedProcess[str]: The finished process with its output.
    """
    if isinstance(cmd, str | bytes):
        raise TypeError("run_command takes an argument list, not a shell string")
    args = [os.fspath(part) for part in cmd]

    # Own process group, so a timeout can kill the program's children too. Killing only the
    # direct child leaves grandchildren running with our pipes open.
    kwargs.setdefault("start_new_session", True)
    process = subprocess.Popen(  # noqa: S603 - argument list, no shell
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        **kwargs,
    )
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    readers = [
        threading.Thread(
            target=_drain_stream,
            args=(process.stdout, stdout_lines, logger.info if log_output else None),
            daemon=True,
        ),
        threading.Thread(
            target=_drain_stream,
            args=(process.stderr, stderr_lines, logger.error if log_err else None),
            daemon=True,
        ),
    ]
    for reader in readers:
        reader.start()

    try:
        return_code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        logger.error(f"{args[0]} ran longer than {timeout}s; killing it")
        _kill_process_group(process)
        process.wait()
        raise
    finally:
        _finish_readers(process, readers)

    output = "\n".join(stdout_lines)
    stderr = "\n".join(stderr_lines)
    if return_code not in ok_returncodes:
        raise subprocess.CalledProcessError(return_code, args, output=output, stderr=stderr)
    return subprocess.CompletedProcess(args, return_code, stdout=output, stderr=stderr)
