import json
from unittest import mock

import pytest

from workers.config.worker_config import WorkerConfig


@pytest.fixture
def mock_config_data():
    """Create mock configuration data for testing."""
    return {
        "worker": {
            "general": {"naming_schemes": {"movie": "trash"}},
            "webhooks": {
                "item_added": {
                    "enabled": True,
                    "queue": "jellyfin:item_added",
                    "services": [
                        {
                            "name": "metadata_update",
                            "enabled": True,
                            "priority": 10,
                            "config": {
                                "paths": [
                                    {
                                        "path": "/data/media/stand-up",
                                        "genres": {
                                            "new_genres": ["Stand-Up"],
                                            "replace_existing": True,
                                        },
                                    }
                                ],
                                "patterns": [],
                            },
                        },
                        {
                            "name": "dovi_conversion",
                            "enabled": True,
                            "priority": 20,
                            "config": {"temp_dir": "/tmp/dovi_conversion"},
                        },
                        {
                            "name": "disabled_service",
                            "enabled": False,
                            "priority": 30,
                            "config": {},
                        },
                    ],
                },
                "disabled_webhook": {
                    "enabled": False,
                    "queue": "jellyfin:disabled",
                    "services": [],
                },
            },
        }
    }


@pytest.fixture
def mock_config_file(tmp_path, mock_config_data):
    """Create a temporary config file for testing."""
    config_file = tmp_path / "config.json"
    with open(config_file, "w") as f:
        json.dump(mock_config_data, f)
    return config_file


@pytest.fixture
def worker_config(mock_config_file):
    """Create a WorkerConfig instance with mock data."""
    return WorkerConfig.load(str(mock_config_file))


@pytest.fixture
def media_dir(tmpdir):
    """Mocks a media directory."""
    # Create movie directory structure
    movie_dir = tmpdir.mkdir("movies")
    movie_folder = movie_dir.mkdir("Test Movie (2023)")
    standup_dir = tmpdir.mkdir("standup")
    standup_folder = standup_dir.mkdir("Comedy Special (2022)")
    tmp_path = tmpdir.mkdir("tmp")

    # Create movie files
    movie_file = movie_folder.join("Test Movie.mkv")
    movie_file.write("movie content")

    standup_file = standup_folder.join("Comedy Special.mp4")
    standup_file.write("standup content")

    # Setup environment
    return {
        "movie_path": str(movie_dir),
        "standup_path": str(standup_dir),
        "movie_file": str(movie_file),
        "standup_file": str(standup_file),
        "tmp_path": str(tmp_path),
    }


@pytest.fixture
def mock_movie_standup():
    return mock.MagicMock(
        full_title="John Mulaney: Baby J",
        full_path="/data/media/stand-up/Bobby Guy (2023)/Bobby.Guy.2023.2160p.WEBRip.x265.10bit.HDR.DTS-HD.MA.5.1-SWTYBLZ.mkv",
    )


@pytest.fixture
def mock_movie_anime():
    return mock.MagicMock(
        full_title="Your Name",
        full_path="/data/media/anime/Sumo Pizza (2016)/Your.Name.2016.2160p.BluRay.x265.10bit.HDR.DTS-HD.MA.5.1-SWTYBLZ.mkv",
    )


@pytest.fixture
def mock_message_standup():
    return {
        "ServerUrl": "jellyfin.server",
        "ItemId": "123456",
        "Name": "Bobby Guy",
        "Year": 2023,
        "ItemType": "Movie",
        "Genres": "Comedy, Documentary",
        "Tags": "Netflix, Special",
        "Overview": "The comedian performs a set at Boston's Symphony Hall.",
    }


@pytest.fixture
def mock_message_anime():
    return {
        "ServerUrl": "jellyfin.server",
        "ItemId": "789012",
        "Name": "Sumo Pizza",
        "Year": 2016,
        "ItemType": "Movie",
        "Genres": "Comedy, Drama",
        "Tags": "Foreign, Subtitled",
        "Overview": "Two friends navigate the world of sumo wrestling and pizza delivery.",
    }
