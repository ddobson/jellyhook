import json
from unittest.mock import MagicMock, patch

import yaml

from workers.config.worker_config import load_config_file
from workers.logger import logger


def test_load_config_file_nonexistent():
    """Test loading a nonexistent config file."""
    result = load_config_file("/nonexistent/config.json")
    assert result == {}


def test_load_config_file_invalid_json(tmp_path):
    """Test loading an invalid JSON file."""
    # Create an invalid JSON file
    config_file = tmp_path / "invalid.json"
    with open(config_file, "w") as f:
        f.write("This is not valid JSON")

    # Load file
    with patch.object(logger, "info", MagicMock()):
        result = load_config_file(str(config_file))

    # Check the result (should be empty dict for invalid files)
    assert result == {}


@patch("yaml.safe_load")
def test_load_config_file_invalid_yaml(mock_yaml_load, tmp_path):
    """Test loading an invalid YAML file."""
    # Create a YAML file
    config_file = tmp_path / "invalid.yaml"
    with open(config_file, "w") as f:
        f.write("key1: value1\n  invalid indentation")

    # Mock YAML load to raise an exception
    mock_yaml_load.side_effect = yaml.YAMLError("YAML parsing error")

    # Load file
    with patch.object(logger, "info", MagicMock()):
        result = load_config_file(str(config_file))

    # Check the result (should be empty dict for invalid files)
    assert result == {}


def test_load_config_file_default_none():
    """Test loading a nonexistent file with default=None."""
    result = load_config_file("/nonexistent/config.json")
    assert result == {}


def test_load_config_file_json(tmp_path):
    """Test loading a JSON config file."""
    # Create a test JSON file
    config_data = {"key1": "value1", "key2": 2}
    config_file = tmp_path / "config.json"
    with open(config_file, "w") as f:
        json.dump(config_data, f)

    # Load the config
    result = load_config_file(str(config_file))

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
    result = load_config_file(str(config_file))

    # Check the result
    assert result == config_data
