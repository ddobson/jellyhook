import pathlib
import subprocess
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


def test_run_command_success(tmp_path):
    # Create a test script that succeeds
    script_path = tmp_path / "test_script.sh"
    script_path.write_text("#!/bin/bash\necho 'Success!'\nexit 0\n")
    script_path.chmod(0o755)

    result = utils.run_command(f"{script_path}")

    assert result.returncode == 0
    assert "Success!" in result.stdout
    assert result.stderr == ""


def test_run_command_failure(tmp_path):
    # Create a test script that fails
    script_path = tmp_path / "test_fail.sh"
    script_path.write_text("#!/bin/bash\necho 'Error message' >&2\nexit 1\n")
    script_path.chmod(0o755)

    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        utils.run_command(f"{script_path}")

    assert excinfo.value.returncode == 1
    assert "Error message" in excinfo.value.stderr


def test_run_command_logs_output_when_requested(tmp_path):
    # Create a test script with output
    script_path = tmp_path / "test_log.sh"
    script_path.write_text("#!/bin/bash\necho 'Standard output'\necho 'Error output' >&2\nexit 0\n")
    script_path.chmod(0o755)

    with (
        mock.patch.object(logger, "info") as mock_info,
        mock.patch.object(logger, "error") as mock_error,
    ):
        utils.run_command(f"{script_path}", log_output=True, log_err=True)

    mock_info.assert_called_with("Standard output")
    mock_error.assert_called_with("Error output")


def test_clean_dir_empty(tmp_path):
    """Test clean_dir on an empty directory."""
    # Create a directory structure
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()

    utils.clean_dir(empty_dir)

    # Directory should be removed
    assert not empty_dir.exists()


def test_clean_dir_with_files(tmp_path):
    """Test clean_dir on a directory with files."""
    # Create a directory with files
    test_dir = tmp_path / "test_dir"
    test_dir.mkdir()
    (test_dir / "file1.txt").write_text("content1")
    (test_dir / "file2.txt").write_text("content2")

    utils.clean_dir(test_dir)

    # Directory should be removed
    assert not test_dir.exists()


def test_clean_dir_with_subdirs(tmp_path):
    """Test clean_dir on a directory with subdirectories."""
    # Create a nested directory structure
    test_dir = tmp_path / "test_dir"
    test_dir.mkdir()
    subdir1 = test_dir / "subdir1"
    subdir1.mkdir()
    (subdir1 / "file1.txt").write_text("content1")
    subdir2 = test_dir / "subdir2"
    subdir2.mkdir()
    (subdir2 / "file2.txt").write_text("content2")

    utils.clean_dir(test_dir)

    # Directory should be removed
    assert not test_dir.exists()
    assert not subdir1.exists()
    assert not subdir2.exists()


def test_clean_dir_nonexistent():
    """Test clean_dir on a nonexistent directory."""
    with pytest.raises(FileNotFoundError):
        utils.clean_dir(pathlib.Path("/nonexistent/directory"))
