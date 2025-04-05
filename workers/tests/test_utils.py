import json
import pathlib
import subprocess
from unittest import mock

import pytest
import yaml

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


def test_ack_message_acknowledged():
    """Test that ack_message calls basic_ack when completed is True."""
    mock_channel = mock.MagicMock()
    mock_channel.is_open = True

    with mock.patch.object(logger, "info") as mock_info:
        utils.ack_message(mock_channel, 123, True)

    mock_channel.basic_ack.assert_called_once_with(123)
    mock_channel.basic_nack.assert_not_called()
    mock_info.assert_called_once_with("Acknowledged message with delivery tag: 123")


def test_ack_message_negative_acknowledged():
    """Test that ack_message calls basic_nack when completed is False."""
    mock_channel = mock.MagicMock()
    mock_channel.is_open = True

    with mock.patch.object(logger, "info") as mock_info:
        utils.ack_message(mock_channel, 456, False)

    mock_channel.basic_nack.assert_called_once_with(456, requeue=False)
    mock_channel.basic_ack.assert_not_called()
    mock_info.assert_called_once_with("Acknowledged message with delivery tag: 456")


def test_ack_message_channel_closed():
    """Test that ack_message logs but does not call ack/nack when channel is closed."""
    mock_channel = mock.MagicMock()
    mock_channel.is_open = False

    with mock.patch.object(logger, "info") as mock_info:
        utils.ack_message(mock_channel, 789, True)

    mock_channel.basic_ack.assert_not_called()
    mock_channel.basic_nack.assert_not_called()
    mock_info.assert_any_call(
        "Unable to acknowledge message with delivery tag: 789. Connection closed."
    )


def test_load_config_file_json(tmp_path):
    """Test loading a JSON config file."""
    # Create a test JSON file
    config_data = {"key1": "value1", "key2": 2}
    config_file = tmp_path / "config.json"
    with open(config_file, "w") as f:
        json.dump(config_data, f)

    # Load the config
    result = utils.load_config_file(str(config_file))

    # Check the result
    assert result == config_data


def test_load_config_file_yaml(tmp_path):
    """Test loading a YAML config file."""
    # Create a test YAML file
    config_data = {"key1": "value1", "key2": 2, "nested": {"subkey": "subvalue"}}
    config_file = tmp_path / "config.yaml"
    with open(config_file, "w") as f:
        yaml.dump(config_data, f)

    # Load the config
    result = utils.load_config_file(str(config_file))

    # Check the result
    assert result == config_data


def test_load_config_file_nonexistent():
    """Test loading a nonexistent config file."""
    default_config = {"default": "config"}
    result = utils.load_config_file("/nonexistent/config.json", default=default_config)
    assert result == default_config


def test_load_config_file_invalid_json(tmp_path):
    """Test loading an invalid JSON file."""
    # Create an invalid JSON file
    config_file = tmp_path / "invalid.json"
    with open(config_file, "w") as f:
        f.write("This is not valid JSON")

    # Load with default
    default_config = {"default": "config"}
    with mock.patch.object(logger, "info") as mock_info:
        result = utils.load_config_file(str(config_file), default=default_config)

    # Check the result and log
    assert result == default_config
    assert mock_info.call_count == 1
    assert "Error loading config file" in mock_info.call_args[0][0]


@mock.patch("yaml.safe_load")
def test_load_config_file_invalid_yaml(mock_yaml_load, tmp_path):
    """Test loading an invalid YAML file."""
    # Create a YAML file
    config_file = tmp_path / "invalid.yaml"
    with open(config_file, "w") as f:
        f.write("key1: value1\n  invalid indentation")

    # Mock YAML load to raise an exception
    mock_yaml_load.side_effect = yaml.YAMLError("YAML parsing error")

    # Load with default
    default_config = {"default": "config"}
    with mock.patch.object(logger, "info") as mock_info:
        result = utils.load_config_file(str(config_file), default=default_config)

    # Check the result and log
    assert result == default_config
    assert mock_info.call_count == 1
    assert "Error loading config file" in mock_info.call_args[0][0]


def test_load_config_file_default_none():
    """Test loading a nonexistent file with default=None."""
    result = utils.load_config_file("/nonexistent/config.json", default=None)
    assert result == {}
