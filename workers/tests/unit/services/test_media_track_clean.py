import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from workers.models.items import MediaStream, MediaTrackCleanConfig
from workers.services.media_track_clean import MediaTrackCleanService


def test_media_stream_from_ffprobe():
    """Test creating a MediaStream from ffprobe output."""
    # Sample ffprobe data for a stream
    stream_data = {
        "index": 1,
        "codec_type": "audio",
        "tags": {"language": "eng"},
        "disposition": {"default": 1, "original": 0},
    }

    stream = MediaStream.from_ffprobe(stream_data)

    assert stream.index == 1
    assert stream.codec_type == "audio"
    assert stream.language == "eng"
    assert stream.is_default is True
    assert stream.is_original is False


def test_media_stream_from_ffprobe_no_tags():
    """Test creating a MediaStream with missing tags."""
    stream_data = {
        "index": 1,
        "codec_type": "audio",
        "disposition": {"default": 1, "original": 0},
    }

    stream = MediaStream.from_ffprobe(stream_data)

    assert stream.index == 1
    assert stream.codec_type == "audio"
    assert stream.language == "und"  # Default to 'und' when language is missing
    assert stream.is_default is True
    assert stream.is_original is False


def test_media_stream_from_ffprobe_no_disposition():
    """Test creating a MediaStream with missing disposition."""
    stream_data = {"index": 1, "codec_type": "audio", "tags": {"language": "eng"}}

    stream = MediaStream.from_ffprobe(stream_data)

    assert stream.index == 1
    assert stream.codec_type == "audio"
    assert stream.language == "eng"
    assert stream.is_default is False
    assert stream.is_original is False


def test_media_track_clean_config_with_defaults():
    """Test initialization with default values."""
    config = MediaTrackCleanConfig()

    assert config.keep_original is True
    assert config.keep_default is True
    assert config.keep_audio_langs == []
    assert config.keep_sub_langs == []


def test_media_track_clean_config_with_values():
    """Test initialization with provided values."""
    config = MediaTrackCleanConfig(
        keep_original=False,
        keep_default=False,
        keep_audio_langs=["eng"],
        keep_sub_langs=["eng", "spa"],
    )

    assert config.keep_original is False
    assert config.keep_default is False
    assert config.keep_audio_langs == ["eng"]
    assert config.keep_sub_langs == ["eng", "spa"]


def test_media_track_clean_config_from_dict():
    """Test creating configuration from a dictionary."""
    config_dict = {
        "keep_original": False,
        "keep_default": True,
        "keep_audio_langs": ["eng", "jpn"],
        "keep_sub_langs": ["eng", "spa", "fre"],
    }

    config = MediaTrackCleanConfig.from_dict(config_dict)

    assert config.keep_original is False
    assert config.keep_default is True
    assert config.keep_audio_langs == ["eng", "jpn"]
    assert config.keep_sub_langs == ["eng", "spa", "fre"]


def test_media_track_clean_config_from_dict_defaults():
    """Test creating configuration from a partial dictionary."""
    config_dict = {"keep_audio_langs": ["eng"]}

    config = MediaTrackCleanConfig.from_dict(config_dict)

    assert config.keep_original is True  # Default
    assert config.keep_default is True  # Default
    assert config.keep_audio_langs == ["eng"]
    assert config.keep_sub_langs == []  # Default


@patch("workers.services.media_track_clean.tempfile.mkdtemp")
@patch("workers.services.media_track_clean.Movie")
def test_media_track_clean_service_init(mock_movie, mock_mkdtemp):
    """Test initializing the service."""
    mock_movie.full_title = "Test Movie (2023)"
    mock_mkdtemp.return_value = "/tmp/test_dir"

    service = MediaTrackCleanService(
        movie=mock_movie,
        keep_original=True,
        keep_default=True,
        keep_audio_langs=["eng"],
        keep_sub_langs=["eng", "spa"],
    )

    assert service.movie == mock_movie
    assert service.config.keep_original is True
    assert service.config.keep_default is True
    assert service.config.keep_audio_langs == ["eng"]
    assert service.config.keep_sub_langs == ["eng", "spa"]
    assert service.tmp_dir == Path("/tmp/test_dir")


@pytest.fixture
def mock_movie():
    """Create a mock movie for testing."""
    mock = MagicMock()
    mock.full_path = "/fake/path/movie.mkv"
    mock.full_title = "Test Movie (2023)"
    return mock


@pytest.fixture
def mock_ffprobe_output():
    """Create sample ffprobe output for testing."""
    return {
        "streams": [
            {"index": 0, "codec_type": "video", "disposition": {"default": 1, "original": 0}},
            {
                "index": 1,
                "codec_type": "audio",
                "tags": {"language": "eng"},
                "disposition": {"default": 1, "original": 0},
            },
            {
                "index": 2,
                "codec_type": "audio",
                "tags": {"language": "spa"},
                "disposition": {"default": 0, "original": 0},
            },
            {
                "index": 3,
                "codec_type": "subtitle",
                "tags": {"language": "eng"},
                "disposition": {"default": 0, "original": 0},
            },
        ]
    }


@patch("workers.services.media_track_clean.os.path.exists")
@patch("workers.services.media_track_clean.run_command")
@patch("workers.services.media_track_clean.os.rename")
@patch("workers.services.media_track_clean.os.unlink")
def test_media_track_clean_service_exec_success(
    mock_unlink, mock_rename, mock_run_command, mock_exists, mock_movie, mock_ffprobe_output
):
    """Test successful execution of the service."""
    # Setup mocks
    mock_exists.return_value = True

    # Set up the movie mock with escaped_path
    mock_movie.escaped_path = "/fake/path/movie.mkv"

    # Mock run_command to return ffprobe output and success for ffmpeg
    ffprobe_result = MagicMock()
    ffprobe_result.stdout = json.dumps(mock_ffprobe_output)

    ffmpeg_result = MagicMock()
    ffmpeg_result.returncode = 0

    mock_run_command.side_effect = [ffprobe_result, ffmpeg_result]

    # Create and configure service
    service = MediaTrackCleanService(
        mock_movie,
        keep_original=True,
        keep_default=True,
        keep_audio_langs=["eng"],
        keep_sub_langs=["eng"],
    )
    service.tmp_dir = Path("/tmp/test_dir")

    # Run the service
    service.exec()

    # Check if ffprobe was called with the right command
    ffprobe_expected_cmd = "ffprobe -v error -show_streams -print_format json /fake/path/movie.mkv"
    assert mock_run_command.call_args_list[0][0][0].strip() == ffprobe_expected_cmd.strip()

    # Check if ffmpeg was called
    ffmpeg_call = mock_run_command.call_args_list[1][0][0]
    assert "ffmpeg" in ffmpeg_call
    assert "-i /fake/path/movie.mkv" in ffmpeg_call

    # Verify the map arguments for streams we want to keep
    assert "-map 0:0" in ffmpeg_call  # Video stream
    assert "-map 0:1" in ffmpeg_call  # English audio (default)
    assert "-map 0:3" in ffmpeg_call  # English subtitle
    assert "-map 0:2" not in ffmpeg_call  # Spanish audio should be removed

    # Verify copy settings and metadata preservation
    assert "-c copy" in ffmpeg_call
    assert "-map_metadata 0" in ffmpeg_call

    # Check file operations
    mock_rename.assert_any_call("/fake/path/movie.mkv", "/fake/path/movie.mkv.bak")
    # The second rename uses a Path object, so we need to check more carefully
    assert any(
        args[0][0] == Path("/tmp/test_dir/cleaned.mkv") and args[0][1] == "/fake/path/movie.mkv"
        for args in mock_rename.call_args_list
    )
    mock_unlink.assert_called_once_with("/fake/path/movie.mkv.bak")


@patch("workers.services.media_track_clean.os.path.exists")
@patch("workers.services.media_track_clean.run_command")
def test_media_track_clean_service_exec_skip_when_no_changes(
    mock_run_command, mock_exists, mock_movie
):
    """Test that processing is skipped when no streams would be removed."""
    # Mock ffprobe output - all streams match our criteria
    ffprobe_output = {
        "streams": [
            {"index": 0, "codec_type": "video", "disposition": {"default": 1, "original": 0}},
            {
                "index": 1,
                "codec_type": "audio",
                "tags": {"language": "eng"},
                "disposition": {"default": 1, "original": 0},
            },
        ]
    }

    # Setup mocks
    mock_exists.return_value = True
    mock_movie.escaped_path = "/fake/path/movie.mkv"

    # Mock run_command to return ffprobe output
    ffprobe_result = MagicMock()
    ffprobe_result.stdout = json.dumps(ffprobe_output)
    mock_run_command.return_value = ffprobe_result

    # Create and configure service
    service = MediaTrackCleanService(
        mock_movie,
        keep_original=True,
        keep_default=True,
        keep_audio_langs=["eng"],
        keep_sub_langs=["eng"],
    )

    # Run the service
    service.exec()

    # Check that ffprobe was called but ffmpeg was not
    assert mock_run_command.call_count == 1

    # Verify the command contained ffprobe
    ffprobe_call = mock_run_command.call_args[0][0]
    assert "ffprobe" in ffprobe_call


@patch.object(MediaTrackCleanService, "file_from_message")
@patch("workers.services.media_track_clean.Movie")
def test_media_track_clean_service_from_message(mock_movie_class, mock_file_from_message):
    """Test creating a service instance from a message."""
    mock_file_path = MagicMock()
    mock_movie = MagicMock()

    # Setup mocks
    mock_file_from_message.return_value = mock_file_path
    mock_movie_class.from_file.return_value = mock_movie

    # Create service from message
    message = {"key": "value"}
    service_config = {"keep_original": False, "keep_audio_langs": ["eng"]}

    service = MediaTrackCleanService.from_message(message, service_config)

    # Verify mocks were called correctly
    mock_file_from_message.assert_called_once_with(message)
    mock_movie_class.from_file.assert_called_once_with(mock_file_path)

    # Verify service attributes
    assert service.movie == mock_movie
    assert service.config.keep_original is False
    assert service.config.keep_default is True  # Default value
    assert service.config.keep_audio_langs == ["eng"]
    assert service.config.keep_sub_langs == []  # Default value
