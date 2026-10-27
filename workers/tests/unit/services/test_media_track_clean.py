import json
import subprocess
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


@patch("workers.services.media_track_clean.Movie")
def test_media_track_clean_service_init(mock_movie, tmp_path):
    """Test initializing the service."""
    mock_movie.full_title = "Test Movie (2023)"

    service = MediaTrackCleanService(
        movie=mock_movie,
        keep_original=True,
        keep_default=True,
        keep_audio_langs=["eng"],
        keep_sub_langs=["eng", "spa"],
        temp_dir=str(tmp_path),
    )

    assert service.movie == mock_movie
    assert service.config.keep_original is True
    assert service.config.keep_default is True
    assert service.config.keep_audio_langs == ["eng"]
    assert service.config.keep_sub_langs == ["eng", "spa"]
    # Each run gets its own scratch path inside the configured one, but nothing is created
    # until the service actually runs
    assert service.tmp_dir.parent == tmp_path
    assert not service.tmp_dir.exists()
    assert list(tmp_path.iterdir()) == []


def test_media_track_clean_service_scratch_dirs_are_unique(mock_movie, tmp_path):
    """Two runs must never share a scratch directory."""
    first = MediaTrackCleanService(mock_movie, temp_dir=str(tmp_path))
    second = MediaTrackCleanService(mock_movie, temp_dir=str(tmp_path))

    assert first.tmp_dir != second.tmp_dir


def test_media_track_clean_service_locks_the_movie_file(mock_movie, tmp_path):
    service = MediaTrackCleanService(mock_movie, temp_dir=str(tmp_path))

    assert service.locked_path == Path(mock_movie.full_path)


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


@patch("workers.services.media_track_clean.run_command")
def test_media_track_clean_service_exec_success(
    mock_run_command, mock_movie, mock_ffprobe_output, tmp_path
):
    """Streams are filtered with an argument list, and the result replaces the original."""
    movie_file = tmp_path / "movie.mkv"
    movie_file.write_bytes(b"original")
    mock_movie.full_path = str(movie_file)

    ffprobe_result = MagicMock()
    ffprobe_result.stdout = json.dumps(mock_ffprobe_output)

    service = MediaTrackCleanService(
        mock_movie,
        keep_original=True,
        keep_default=True,
        keep_audio_langs=["eng"],
        keep_sub_langs=["eng"],
        temp_dir=str(tmp_path / "scratch"),
    )

    def fake_ffmpeg(cmd, **kwargs):
        if cmd[0] == "ffprobe":
            return ffprobe_result
        Path(cmd[-1]).write_bytes(b"cleaned")
        return MagicMock(returncode=0)

    mock_run_command.side_effect = fake_ffmpeg

    service.exec()

    ffprobe_cmd = mock_run_command.call_args_list[0][0][0]
    assert ffprobe_cmd == [
        "ffprobe",
        "-v",
        "error",
        "-show_streams",
        "-print_format",
        "json",
        str(movie_file),
    ]

    ffmpeg_cmd = mock_run_command.call_args_list[1][0][0]
    assert ffmpeg_cmd[0] == "ffmpeg"
    assert ffmpeg_cmd[ffmpeg_cmd.index("-i") + 1] == str(movie_file)
    maps = [ffmpeg_cmd[i + 1] for i, part in enumerate(ffmpeg_cmd) if part == "-map"]
    assert maps == ["0:0", "0:1", "0:3"]  # video, English audio, English subtitle; not Spanish
    assert ffmpeg_cmd[ffmpeg_cmd.index("-c") + 1] == "copy"
    assert ffmpeg_cmd[ffmpeg_cmd.index("-map_metadata") + 1] == "0"

    # The original was swapped for the cleaned file, with nothing left beside it
    assert movie_file.read_bytes() == b"cleaned"
    assert [p.name for p in tmp_path.iterdir() if p.name != "scratch"] == ["movie.mkv"]


@patch("workers.services.media_track_clean.run_command")
def test_media_track_clean_service_exec_failure_leaves_original(
    mock_run_command, mock_movie, mock_ffprobe_output, tmp_path
):
    """If ffmpeg fails the original must be untouched."""
    movie_file = tmp_path / "movie.mkv"
    movie_file.write_bytes(b"original")
    mock_movie.full_path = str(movie_file)
    ffprobe_result = MagicMock()
    ffprobe_result.stdout = json.dumps(mock_ffprobe_output)
    mock_run_command.side_effect = [
        ffprobe_result,
        subprocess.CalledProcessError(1, ["ffmpeg"], stderr="boom"),
    ]
    service = MediaTrackCleanService(
        mock_movie, keep_audio_langs=["eng"], temp_dir=str(tmp_path / "scratch")
    )

    with pytest.raises(subprocess.CalledProcessError):
        service.exec()

    assert movie_file.read_bytes() == b"original"


@patch("workers.services.media_track_clean.os.path.exists")
@patch("workers.services.media_track_clean.run_command")
def test_media_track_clean_service_exec_skip_when_no_changes(
    mock_run_command, mock_exists, mock_movie, tmp_path
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
        temp_dir=str(tmp_path),
    )

    # Run the service
    service.exec()

    # Check that ffprobe was called but ffmpeg was not
    assert mock_run_command.call_count == 1

    # Verify the command contained ffprobe
    ffprobe_call = mock_run_command.call_args[0][0]
    assert ffprobe_call[0] == "ffprobe"


@patch.object(MediaTrackCleanService, "file_from_message")
@patch("workers.services.media_track_clean.Movie")
def test_media_track_clean_service_from_message(mock_movie_class, mock_file_from_message, tmp_path):
    """Test creating a service instance from a message."""
    mock_file_path = MagicMock()
    mock_movie = MagicMock()

    # Setup mocks
    mock_file_from_message.return_value = mock_file_path
    mock_movie_class.from_file.return_value = mock_movie

    # Create service from message
    message = {"key": "value"}
    service_config = {
        "keep_original": False,
        "keep_audio_langs": ["eng"],
        "temp_dir": str(tmp_path),
    }

    service = MediaTrackCleanService.from_message(message, service_config)

    # Verify mocks were called correctly
    mock_file_from_message.assert_called_once_with(message)
    mock_movie_class.from_file.assert_called_once_with(mock_file_path)

    # Verify service attributes
    assert isinstance(service, MediaTrackCleanService)
    assert service.movie == mock_movie
    assert service.config.keep_original is False
    assert service.config.keep_default is True  # Default value
    assert service.config.keep_audio_langs == ["eng"]
    assert service.config.keep_sub_langs == []  # Default value


def test_exec_raises_when_the_movie_file_is_gone(mock_movie, tmp_path):
    mock_movie.full_path = str(tmp_path / "missing.mkv")
    service = MediaTrackCleanService(mock_movie, temp_dir=str(tmp_path / "scratch"))

    with pytest.raises(FileNotFoundError):
        service.exec()


@patch("workers.services.media_track_clean.run_command")
def test_unreadable_ffprobe_output_is_an_error(mock_run_command, mock_movie, tmp_path):
    movie_file = tmp_path / "movie.mkv"
    movie_file.write_bytes(b"x")
    mock_movie.full_path = str(movie_file)
    mock_run_command.return_value = MagicMock(stdout="not json")
    service = MediaTrackCleanService(mock_movie, temp_dir=str(tmp_path / "scratch"))

    with pytest.raises(json.JSONDecodeError):
        service.exec()


def test_evaluate_streams_applies_each_keep_rule(mock_movie, tmp_path):
    """Original flag, default flag and per-type language lists each keep a stream."""

    def stream(index, codec_type, language, *, default=False, original=False):
        return MediaStream(
            index=index,
            codec_type=codec_type,
            language=language,
            is_default=default,
            is_original=original,
        )

    streams = [
        stream(0, "video", "und"),
        stream(1, "audio", "jpn", original=True),
        stream(2, "audio", "fra", default=True),
        stream(3, "audio", "eng"),
        stream(4, "subtitle", "eng"),
        stream(5, "subtitle", "deu"),
    ]
    service = MediaTrackCleanService(
        mock_movie,
        keep_original=True,
        keep_default=True,
        keep_audio_langs=["eng"],
        keep_sub_langs=["eng"],
        temp_dir=str(tmp_path),
    )

    kept = [s.index for s in service._evaluate_streams(streams)]

    assert kept == [0, 1, 2, 3, 4]  # only the German subtitle goes


@patch("workers.services.media_track_clean.run_command")
def test_skipping_leaves_no_scratch_directory_behind(
    mock_run_command, mock_movie, mock_ffprobe_output, tmp_path
):
    """A run with nothing to remove must not litter TEMP_DIR."""
    movie_file = tmp_path / "movie.mkv"
    movie_file.write_bytes(b"x")
    mock_movie.full_path = str(movie_file)
    mock_run_command.return_value = MagicMock(stdout=json.dumps(mock_ffprobe_output))
    scratch = tmp_path / "scratch"
    service = MediaTrackCleanService(
        mock_movie,
        keep_audio_langs=["eng", "spa"],
        keep_sub_langs=["eng"],
        temp_dir=str(scratch),
    )

    service.exec()

    assert not scratch.exists()
