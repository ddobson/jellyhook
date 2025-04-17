import json
from unittest import mock

import yaml

from workers.config.worker_config import WorkerConfig, load_config_file
from workers.logger import logger


def test_get_enabled_webhooks(worker_config):
    """Test getting enabled webhooks."""
    enabled_webhooks = worker_config.get_enabled_webhooks()

    # Should only get the enabled webhook
    assert len(enabled_webhooks) == 1
    assert "item_added" in enabled_webhooks
    assert "disabled_webhook" not in enabled_webhooks

    # Check the webhook config is correct
    webhook_config = enabled_webhooks["item_added"]
    assert webhook_config["queue"] == "jellyfin:item_added"
    assert len(webhook_config["services"]) == 3


def test_get_webhook_config(worker_config):
    """Test getting a specific webhook configuration."""
    # Get existing webhook
    webhook_config = worker_config.get_webhook_config("item_added")
    assert webhook_config["enabled"] is True
    assert webhook_config["queue"] == "jellyfin:item_added"

    # Get non-existent webhook (should return empty dict)
    webhook_config = worker_config.get_webhook_config("non_existent")
    assert webhook_config == {}


def test_get_enabled_services(worker_config):
    """Test getting enabled services for a webhook."""
    # Get services for enabled webhook
    services = worker_config.get_enabled_services("item_added")

    # Should have 2 enabled services, sorted by priority
    assert len(services) == 2
    assert services[0]["name"] == "metadata_update"
    assert services[1]["name"] == "dovi_conversion"

    # Check priorities are maintained
    assert services[0]["priority"] == 10
    assert services[1]["priority"] == 20

    # Check disabled service is excluded
    service_names = [s["name"] for s in services]
    assert "disabled_service" not in service_names

    # Get services for disabled webhook (should return empty list)
    services = worker_config.get_enabled_services("disabled_webhook")
    assert services == []

    # Get services for non-existent webhook (should return empty list)
    services = worker_config.get_enabled_services("non_existent")
    assert services == []


def test_get_service_config(worker_config):
    """Test getting configuration for a specific service."""
    # Get config for metadata_update service
    service_config = worker_config.get_service_config("item_added", "metadata_update")
    assert "paths" in service_config
    assert service_config["paths"][0]["path"] == "/data/media/stand-up"

    # Get config for dovi_conversion service
    service_config = worker_config.get_service_config("item_added", "dovi_conversion")
    assert service_config["temp_dir"] == "/tmp/dovi_conversion"

    # Get config for non-existent service (should return empty dict)
    service_config = worker_config.get_service_config("item_added", "non_existent")
    assert service_config == {}

    # Get config for service in non-existent webhook (should return empty dict)
    service_config = worker_config.get_service_config("non_existent", "metadata_update")
    assert service_config == {}


@mock.patch("workers.config.worker_config.load_config_file")
def test_load_worker_config(mock_load_config, mock_config_data):
    """Test loading WorkerConfig from a file."""
    # Mock the config file loading
    mock_load_config.return_value = mock_config_data

    # Load the config
    config = WorkerConfig.load("/path/to/config.json")

    # Check mock was called
    mock_load_config.assert_called_once_with("/path/to/config.json")

    # Check config was loaded correctly
    assert isinstance(config, WorkerConfig)
    assert config.config == mock_config_data["worker"]


@mock.patch.dict("workers.utils.SingletonMeta._instances", {}, clear=True)
def test_singleton_behavior(mock_config_file, mock_config_data):
    """Test that WorkerConfig behaves as a singleton."""
    # Create a test instance
    config1 = WorkerConfig.load(str(mock_config_file))

    # Create a second instance that should be the same object
    config2 = WorkerConfig()

    # They should be the same instance
    assert config1 is config2
    assert config1.config == mock_config_data["worker"]
    assert config2.config == mock_config_data["worker"]
    assert config1.config is config2.config


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
    with mock.patch.object(logger, "info", mock.MagicMock()):
        result = load_config_file(str(config_file))

    # Check the result (should be empty dict for invalid files)
    assert result == {}


@mock.patch("yaml.safe_load")
def test_load_config_file_invalid_yaml(mock_yaml_load, tmp_path):
    """Test loading an invalid YAML file."""
    # Create a YAML file
    config_file = tmp_path / "invalid.yaml"
    with open(config_file, "w") as f:
        f.write("key1: value1\n  invalid indentation")

    # Mock YAML load to raise an exception
    mock_yaml_load.side_effect = yaml.YAMLError("YAML parsing error")

    # Load file
    with mock.patch.object(logger, "info", mock.MagicMock()):
        result = load_config_file(str(config_file))

    # Check the result (should be empty dict for invalid files)
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
