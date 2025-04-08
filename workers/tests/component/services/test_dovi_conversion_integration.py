import pathlib
from unittest.mock import Mock

import pytest

from workers.movie import Movie
from workers.services import DoviConversionService


@pytest.fixture
def p7_movie(asset_copies):
    return Movie.from_file(asset_copies["p7"])


@pytest.fixture
def p8_movie(asset_copies):
    return Movie.from_file(asset_copies["p8"])


@pytest.fixture
def p7_service(p7_movie, temp_dir):
    return DoviConversionService(p7_movie, temp_dir)


@pytest.fixture
def p8_service(p8_movie, temp_dir):
    return DoviConversionService(p8_movie, temp_dir)


# Integration tests for DoviConversionService


def test_service_extracts_p7_layers(p7_service, temp_dir):
    """Test that the service can extract layers from a P7 file."""
    # First extract the video stream
    video_path = p7_service.extract_video()
    assert pathlib.Path(video_path).exists()

    # Extract RPU layer
    rpu_layer = p7_service.extract_layer(video_path, "test_layer.bin")
    assert pathlib.Path(rpu_layer).exists()

    # Should be able to demux video
    el_video_path = p7_service.demux_video(video_path)
    assert pathlib.Path(el_video_path).exists()


def test_p7_conversion_end_to_end(p7_service, asset_copies, monkeypatch):
    """Test full conversion flow from P7 to P8."""
    # Mock methods that would alter our test files
    monkeypatch.setattr(p7_service.movie, "delete", lambda: None)
    monkeypatch.setattr(pathlib.Path, "rename", lambda self, target: self)

    # Create a flag to verify the process ran to completion
    completed = False

    # Create a mock for move_to_target to verify it was called
    def mock_move(*args, **kwargs):
        nonlocal completed
        completed = True
        # Don't actually rename/move anything
        return None

    monkeypatch.setattr(p7_service, "move_to_target", mock_move)

    # Run the conversion process
    p7_service.exec()

    # Verify the process ran to completion
    assert completed is True


def test_p8_file_is_not_processed(p8_service, monkeypatch):
    """Test that P8 files are not processed (they're already P8)."""
    # Mock the get_dovi_profile method to return Profile 8
    monkeypatch.setattr(p8_service, "get_dovi_profile", lambda: 8)

    # Create a mock to verify that certain methods are not called
    mock_extract = Mock()
    monkeypatch.setattr(p8_service, "extract_video", mock_extract)

    # Run the process
    p8_service.exec()

    # Verify that extract_video was not called, meaning processing was skipped
    mock_extract.assert_not_called()
