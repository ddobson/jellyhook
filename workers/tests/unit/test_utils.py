import pathlib
import subprocess
import time
from unittest import mock

import pytest

from workers import utils
from workers.logger import logger


@mock.patch("time.time")
def test_timer_decorator_calculates_elapsed_time(mock_time):
    mock_time.side_effect = [100.0, 105.0]  # Start and end times

    @utils.timer
    def test_func():
        return "result"

    with mock.patch.object(logger, "info") as mock_logger:
        result = test_func()

    assert result == "result"
    mock_logger.assert_called_once_with("test_func took 5.0 seconds")
    assert mock_time.call_count == 2


@mock.patch("time.time")
def test_timer_decorator_with_args(mock_time):
    mock_time.side_effect = [100.0, 107.0]  # Start and end times

    @utils.timer
    def test_func(arg1, arg2, kwarg1=None):
        return f"{arg1}-{arg2}-{kwarg1}"

    with mock.patch.object(logger, "info") as mock_logger:
        result = test_func("a", "b", kwarg1="c")

    assert result == "a-b-c"
    mock_logger.assert_called_once_with("test_func took 7.0 seconds")


def _script(tmp_path: pathlib.Path, body: str) -> pathlib.Path:
    """Write an executable shell script and return its path."""
    script = tmp_path / "script.sh"
    script.write_text(f"#!/bin/bash\n{body}\n")
    script.chmod(0o755)
    return script


def test_run_command_success(tmp_path):
    result = utils.run_command([_script(tmp_path, "echo 'Success!'")])

    assert result.returncode == 0
    assert result.stdout == "Success!"
    assert result.stderr == ""


def test_run_command_failure(tmp_path):
    script = _script(tmp_path, "echo 'Error message' >&2\nexit 1")

    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        utils.run_command([script])

    assert excinfo.value.returncode == 1
    assert "Error message" in excinfo.value.stderr


def test_run_command_accepts_extra_ok_exit_codes(tmp_path):
    """mkvmerge-style: exit 1 means "finished with warnings"."""
    script = _script(tmp_path, "exit 1")

    result = utils.run_command([script], ok_returncodes=(0, 1))

    assert result.returncode == 1


def test_run_command_logs_output_when_requested(tmp_path):
    script = _script(tmp_path, "echo 'Standard output'\necho 'Error output' >&2")

    with (
        mock.patch.object(logger, "info") as mock_info,
        mock.patch.object(logger, "error") as mock_error,
    ):
        utils.run_command([script], log_output=True, log_err=True)

    mock_info.assert_called_with("Standard output")
    mock_error.assert_called_with("Error output")


def test_run_command_rejects_shell_strings():
    """A string would mean a shell; callers must pass an argument list."""
    with pytest.raises(TypeError):
        utils.run_command("echo hi")


def test_run_command_does_not_interpret_shell_syntax(tmp_path):
    """Metacharacters in an argument are just text, not commands."""
    marker = tmp_path / "pwned"

    result = utils.run_command(["echo", f"$(touch {marker}); `touch {marker}`"])

    assert not marker.exists()
    assert "$(touch" in result.stdout


def test_run_command_does_not_deadlock_on_large_stderr(tmp_path):
    """ffmpeg-style progress spam on stderr used to fill the pipe and hang the job forever."""
    script = _script(
        tmp_path,
        "head -c 2000000 /dev/zero | tr '\\0' 'e' | fold -w 100 >&2\necho done",
    )

    result = utils.run_command([script], timeout=30)

    assert result.stdout == "done"
    assert len(result.stderr) > 1_000_000


def test_run_command_kills_a_hung_process(tmp_path):
    script = _script(tmp_path, "sleep 30")

    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        utils.run_command([script], timeout=0.3)

    assert time.monotonic() - started < 10  # killed, not waited out


def test_run_command_passes_popen_options_through(tmp_path):
    result = utils.run_command(["pwd"], cwd=tmp_path)

    assert pathlib.Path(result.stdout).resolve() == tmp_path.resolve()


def test_kill_process_group_falls_back_to_killing_the_process():
    """If the group is already gone (or off limits), kill the direct child instead."""
    process = mock.MagicMock(pid=999_999_999)

    with mock.patch.object(utils.os, "killpg", side_effect=ProcessLookupError):
        utils._kill_process_group(process)

    process.kill.assert_called_once()


def test_run_command_returns_all_output_when_a_grandchild_holds_the_pipe(tmp_path):
    """A background process keeping stdout open must not make us return half the output."""
    script = _script(tmp_path, "(sleep 30 &)\nfor i in $(seq 1 2000); do echo line$i; done")

    started = time.monotonic()
    result = utils.run_command([script], timeout=60)

    assert result.stdout.splitlines()[-1] == "line2000"
    assert len(result.stdout.splitlines()) == 2000
    assert time.monotonic() - started < 15  # the stray `sleep` was killed, not waited for


def test_run_command_raises_rather_than_return_truncated_output():
    """If a reader will not finish even after the group is killed, say so."""
    stuck = mock.MagicMock()
    stuck.is_alive.return_value = True
    process = mock.MagicMock()

    with (
        mock.patch.object(utils, "PIPE_DRAIN_SECONDS", 0),
        mock.patch.object(utils, "_kill_process_group") as kill,
        pytest.raises(RuntimeError, match="all of the command's output"),
    ):
        utils._finish_readers(process, [stuck])

    kill.assert_called_once_with(process)
