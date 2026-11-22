import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from workers.errors import MediaNotFoundError, is_transient
from workers.services.media_track_clean import (
    MediaStream,
    MediaTrackCleanConfig,
    MediaTrackCleanService,
    TrackCleanError,
)


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


STREAMS_WITH_SPANISH = {
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


@pytest.fixture
def job(make_job):
    """A real Job for the movie in the temporary media tree."""
    return make_job({"Name": "Test Movie", "Year": "2023"})


@pytest.fixture
def movie_file(media_dir):
    """The media file the `job` fixture locates."""
    return Path(media_dir["movie_file"])


@pytest.fixture
def mock_ffprobe_output():
    """Create sample ffprobe output for testing."""
    return STREAMS_WITH_SPANISH


def test_media_track_clean_service_init(job, media_dir):
    """Test initializing the service."""
    service = MediaTrackCleanService(
        job,
        keep_original=True,
        keep_default=True,
        keep_audio_langs=["eng"],
        keep_sub_langs=["eng", "spa"],
    )

    assert service.movie is job.movie
    assert service.config.keep_original is True
    assert service.config.keep_default is True
    assert service.config.keep_audio_langs == ["eng"]
    assert service.config.keep_sub_langs == ["eng", "spa"]
    # Nothing is created until the service asks for scratch space
    assert list(Path(media_dir["tmp_path"]).iterdir()) == []
    assert service.tmp_dir.parent == Path(media_dir["tmp_path"])


def test_media_track_clean_service_scratch_dirs_are_unique(make_job):
    """Two runs must never share a scratch directory."""
    message = {"Name": "Test Movie", "Year": "2023"}
    first = MediaTrackCleanService(make_job(message))
    second = MediaTrackCleanService(make_job(message))

    assert first.tmp_dir != second.tmp_dir


def test_media_track_clean_service_rewrites_the_media_file():
    """The orchestrator locks the file for services that declare this."""
    assert MediaTrackCleanService.modifies_media is True


@patch("workers.services.media_track_clean.run_command")
def test_media_track_clean_service_exec_success(
    mock_run_command, job, movie_file, mock_ffprobe_output
):
    """Streams are filtered with an argument list, and the result replaces the original."""
    ffprobe_result = MagicMock()
    ffprobe_result.stdout = json.dumps(mock_ffprobe_output)
    service = MediaTrackCleanService(
        job,
        keep_original=True,
        keep_default=True,
        keep_audio_langs=["eng"],
        keep_sub_langs=["eng"],
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
    # video, English audio, English subtitle (not Spanish), then any attachments (MKV fonts)
    assert maps == ["0:0", "0:1", "0:3", "0:t?"]
    assert ffmpeg_cmd[ffmpeg_cmd.index("-c") + 1] == "copy"
    assert ffmpeg_cmd[ffmpeg_cmd.index("-map_metadata") + 1] == "0"

    # The original was swapped for the cleaned file, with nothing left beside it
    assert movie_file.read_bytes() == b"cleaned"
    assert [p.name for p in movie_file.parent.iterdir()] == [movie_file.name]


@patch("workers.services.media_track_clean.run_command")
def test_media_track_clean_service_exec_failure_leaves_original(
    mock_run_command, job, movie_file, mock_ffprobe_output
):
    """If ffmpeg fails the original must be untouched, and the job still cleans up."""
    ffprobe_result = MagicMock()
    ffprobe_result.stdout = json.dumps(mock_ffprobe_output)
    mock_run_command.side_effect = [
        ffprobe_result,
        subprocess.CalledProcessError(1, ["ffmpeg"], stderr="boom"),
    ]
    service = MediaTrackCleanService(job, keep_audio_langs=["eng"])

    with pytest.raises(subprocess.CalledProcessError):
        service.exec()
    scratch = service.tmp_dir
    job.close()

    assert movie_file.read_text() == "movie content"
    assert not scratch.exists()


@patch("workers.services.media_track_clean.run_command")
def test_media_track_clean_service_exec_skip_when_no_changes(mock_run_command, job):
    """Test that processing is skipped when no streams would be removed."""
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
    mock_run_command.return_value = MagicMock(stdout=json.dumps(ffprobe_output))
    service = MediaTrackCleanService(
        job,
        keep_original=True,
        keep_default=True,
        keep_audio_langs=["eng"],
        keep_sub_langs=["eng"],
    )

    service.exec()

    # ffprobe was called but ffmpeg was not
    assert mock_run_command.call_count == 1
    assert mock_run_command.call_args[0][0][0] == "ffprobe"


@patch("workers.services.media_track_clean.run_command")
def test_cleaning_that_would_drop_all_audio_is_refused(mock_run_command, job, movie_file):
    """A movie with audio must not be rewritten without any. The failure is permanent."""
    mock_run_command.return_value = MagicMock(stdout=json.dumps(STREAMS_WITH_SPANISH))
    service = MediaTrackCleanService(
        job, keep_original=False, keep_default=False, keep_audio_langs=["fra"]
    )

    with pytest.raises(TrackCleanError, match="audio") as exc:
        service.exec()

    assert not is_transient(exc.value)
    assert mock_run_command.call_count == 1  # ffprobe only, ffmpeg never runs
    assert movie_file.read_text() == "movie content"


@patch("workers.services.media_track_clean.run_command")
def test_empty_output_never_replaces_the_original(
    mock_run_command, job, movie_file, mock_ffprobe_output
):
    """Ffmpeg can exit 0 and still leave nothing behind. The original must stay."""
    mock_run_command.side_effect = [
        MagicMock(stdout=json.dumps(mock_ffprobe_output)),
        MagicMock(returncode=0),
    ]
    service = MediaTrackCleanService(job, keep_audio_langs=["eng"])

    with pytest.raises(TrackCleanError, match="no output"):
        service.exec()

    assert movie_file.read_text() == "movie content"


def test_media_track_clean_service_from_message(job, tmp_path):
    """Test creating a service instance from a job and its service config."""
    service_config = {
        "keep_original": False,
        "keep_audio_langs": ["eng"],
        "temp_dir": str(tmp_path / "clean"),
    }

    service = MediaTrackCleanService.from_message(job, service_config)

    assert isinstance(service, MediaTrackCleanService)
    assert service.movie is job.movie
    assert service.config.keep_original is False
    assert service.config.keep_default is True  # Default value
    assert service.config.keep_audio_langs == ["eng"]
    assert service.config.keep_sub_langs == []  # Default value
    assert service.tmp_dir.parent == tmp_path / "clean"


def test_from_message_for_a_missing_file_is_transient(make_job):
    """Same classification as every other service: the file may not have landed yet."""
    job = make_job({"Name": "Missing Movie", "Year": "2023"})

    with pytest.raises(MediaNotFoundError) as exc:
        MediaTrackCleanService.from_message(job, {})

    assert is_transient(exc.value)


@patch("workers.services.media_track_clean.run_command")
def test_unreadable_ffprobe_output_is_an_error(mock_run_command, job):
    mock_run_command.return_value = MagicMock(stdout="not json")
    service = MediaTrackCleanService(job)

    with pytest.raises(json.JSONDecodeError):
        service.exec()


def test_evaluate_streams_applies_each_keep_rule(job):
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
        job,
        keep_original=True,
        keep_default=True,
        keep_audio_langs=["eng"],
        keep_sub_langs=["eng"],
    )

    kept = [s.index for s in service._evaluate_streams(streams)]

    assert kept == [0, 1, 2, 3, 4]  # only the German subtitle goes


@patch("workers.services.media_track_clean.run_command")
def test_skipping_leaves_no_scratch_directory_behind(
    mock_run_command, job, media_dir, mock_ffprobe_output
):
    """A run with nothing to remove must not litter the temp dir."""
    mock_run_command.return_value = MagicMock(stdout=json.dumps(mock_ffprobe_output))
    service = MediaTrackCleanService(job, keep_audio_langs=["eng", "spa"], keep_sub_langs=["eng"])

    service.exec()

    assert list(Path(media_dir["tmp_path"]).iterdir()) == []
