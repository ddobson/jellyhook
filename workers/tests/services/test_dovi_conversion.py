import json
import pathlib
from unittest import mock

import pytest

from workers.errors import WebhookWorkerError
from workers.movie import Movie
from workers.services.dovi_conversion import DoviConversionService


@pytest.fixture
def mock_movie():
    """Create a mock movie object for testing."""
    mock_file = mock.MagicMock(spec=pathlib.Path)
    mock_file.name = "Test Movie (2023) [tmdbid-123] [x265] [DV] [TrueHD] [2160p]-RELEASE.mkv"
    mock_file.resolve.return_value = (
        "/path/to/Test Movie (2023) [tmdbid-123] [x265] [DV] [TrueHD] [2160p]-RELEASE.mkv"
    )

    return Movie(
        file=mock_file,
        title="Test Movie",
        year="2023",
        tmdb_id="123",
        video_codec="x265",
        dynamic_range="DV",
        audio="TrueHD",
        quality="2160p",
        release_group="RELEASE",
    )


@pytest.fixture
def dovi_service(mock_movie, media_dir):
    """Create a DoviConversionService instance for testing."""
    tmp_dir = f"{media_dir['tmp_path']}/{media_dir['movie_path']}"
    return DoviConversionService(mock_movie, tmp_dir)


def test_get_dovi_profile_with_dovi(dovi_service):
    with mock.patch("workers.utils.run_command") as mock_run_command:
        # Set up the mock to return a valid ffprobe output with Dolby Vision
        mock_process = mock.MagicMock()
        mock_process.stdout = json.dumps({"streams": [{"side_data_list": [{"dv_profile": 7}]}]})
        mock_run_command.return_value = mock_process

        # Call the method
        profile = dovi_service.get_dovi_profile()

        # Verify the correct command was run
        mock_run_command.assert_called_once()
        assert profile == 7


def test_get_dovi_profile_without_dovi(dovi_service):
    """Test handling when no Dolby Vision profile exists."""
    with mock.patch("workers.utils.run_command") as mock_run_command:
        # Set up the mock to return a valid ffprobe output without Dolby Vision
        mock_process = mock.MagicMock()
        mock_process.stdout = json.dumps({"streams": [{"codec_name": "hevc"}]})
        mock_run_command.return_value = mock_process

        # Call the method
        profile = dovi_service.get_dovi_profile()

        # Verify the result
        assert profile is None


def test_extract_video(dovi_service):
    """Test video extraction command.."""
    with mock.patch("workers.utils.run_command") as mock_run_command:
        # Call the method
        video_path = dovi_service.extract_video()

        # Verify the correct command was run and the path is returned
        mock_run_command.assert_called_once()
        assert video_path == f"{dovi_service.tmp_dir}/video.hevc"


def test_demux_video(dovi_service):
    """Test demuxing video command.."""
    with mock.patch("workers.utils.run_command") as mock_run_command:
        # Call the method
        input_path = f"{dovi_service.tmp_dir}/video.hevc"
        el_video_path = dovi_service.demux_video(input_path)

        # Verify the correct command was run and the path is returned
        mock_run_command.assert_called_once()
        assert el_video_path == f"{dovi_service.tmp_dir}/EL.hevc"


def test_extract_layer(dovi_service):
    """Test layer extraction command."""
    with mock.patch("workers.utils.run_command") as mock_run_command:
        # Call the method
        hevc_path = f"{dovi_service.tmp_dir}/video.hevc"
        output_layer = "BL_RPU.bin"
        layer_path = dovi_service.extract_layer(hevc_path, output_layer)

        # Verify the correct command was run and the path is returned
        mock_run_command.assert_called_once()
        assert layer_path == f"{dovi_service.tmp_dir}/{output_layer}"


def test_calculate_sha512(dovi_service):
    """Test SHA512 checksum calculation."""
    test_data = b"test data"
    with mock.patch("builtins.open", mock.mock_open(read_data=test_data)):
        # Call the method
        file_path = f"{dovi_service.tmp_dir}/test_file"
        checksum = dovi_service.calculate_sha512(file_path)

        # Verify a checksum is returned with expected length
        assert isinstance(checksum, str)
        assert len(checksum) == 128  # SHA-512 produces 128 hex characters


def test_convert_dovi_profile(dovi_service):
    """Test Dolby Vision profile conversion command."""
    with mock.patch("workers.utils.run_command") as mock_run_command:
        # Call the method
        input_path = f"{dovi_service.tmp_dir}/video.hevc"
        p8_video_path = dovi_service.convert_dovi_profile(input_path)

        # Verify the correct command was run and the path is returned
        mock_run_command.assert_called_once()
        assert p8_video_path == f"{dovi_service.tmp_dir}/P8.hevc"


def test_merge_mkv(dovi_service):
    """Test MKV merging command."""
    with mock.patch("workers.utils.run_command") as mock_run_command:
        # Call the method
        original_mkv = dovi_service.movie.full_path
        p8_video_path = f"{dovi_service.tmp_dir}/P8.hevc"
        result = dovi_service.merge_mkv(original_mkv, p8_video_path)

        # Verify the correct command was run and the path is returned
        mock_run_command.assert_called_once()
        assert result == pathlib.Path(f"{dovi_service.tmp_dir}/final_output.mkv")


def test_move_to_target(dovi_service):
    """Test moving final output to target location."""
    # Create mock files
    output_file = mock.MagicMock(spec=pathlib.Path)
    target = "/path/to/movie.mkv"

    # Call the method
    dovi_service.move_to_target(output_file, target)

    # Verify the original file is deleted and the new file is renamed
    dovi_service.movie._file.unlink.assert_called_once()
    output_file.rename.assert_called_once_with(target)


def test_from_message_success():
    """Test creating service from a message - success case."""
    # Mock the message
    message = {"Name": "Test Movie", "Year": "2023"}

    with (
        mock.patch("workers.movie.Movie.from_file") as mock_from_file,
        mock.patch("pathlib.Path") as mock_path,
        mock.patch("workers.services.dovi_conversion.TEMP_DIR", "/data/tmp"),
        mock.patch(
            "workers.services.dovi_conversion.DoviConversionService.file_from_message"
        ) as mock_file_from_message,
    ):
        # Setup the mock file result
        mock_file = mock.MagicMock()
        mock_file_from_message.return_value = mock_file

        # Setup tmp_dir
        mock_tmp_dir = mock.MagicMock()
        mock_path.side_effect = lambda x: mock_tmp_dir

        # Mock Movie.from_file to return a movie
        mock_movie = mock.MagicMock()
        mock_movie.folder_title = "Test Movie (2023)"
        mock_from_file.return_value = mock_movie

        # Call the method
        service = DoviConversionService.from_message(message)

        # Verify the service was created
        assert isinstance(service, DoviConversionService)
        mock_file_from_message.assert_called_once_with(message)
        mock_from_file.assert_called_once_with(mock_file)
        mock_tmp_dir.mkdir.assert_called_once_with(exist_ok=True)


def test_from_message_no_movie_found():
    """Test creating service from a message - no movie found case."""
    # Mock the message
    message = {"Name": "Missing Movie", "Year": "2023"}

    with pytest.raises(WebhookWorkerError) as exc_info:
        DoviConversionService.from_message(message)

    assert str(exc_info.value) == "No video found for 'Missing Movie'"


@pytest.mark.parametrize(
    "profile,checksums_match,expected_calls",
    [
        (
            7,
            True,
            [
                "get_dovi_profile",
                "extract_video",
                "demux_video",
                "extract_layer",
                "calculate_sha512",
                "convert_dovi_profile",
                "merge_mkv",
                "move_to_target",
            ],
        ),
        (5, True, ["get_dovi_profile"]),  # Should abort early if profile is not 7
        (
            7,
            False,
            [
                "get_dovi_profile",
                "extract_video",
                "demux_video",
                "extract_layer",
                "calculate_sha512",
            ],
        ),  # Should abort if checksums don't match
    ],
)
def test_exec_flow(dovi_service, profile, checksums_match, expected_calls):
    """Test the exec method flow with different conditions."""
    # Setup all the mocks we'll need
    with mock.patch.multiple(
        "workers.services.dovi_conversion.DoviConversionService",
        get_dovi_profile=mock.DEFAULT,
        extract_video=mock.DEFAULT,
        demux_video=mock.DEFAULT,
        extract_layer=mock.DEFAULT,
        calculate_sha512=mock.DEFAULT,
        convert_dovi_profile=mock.DEFAULT,
        merge_mkv=mock.DEFAULT,
        move_to_target=mock.DEFAULT,
    ) as mocks:
        # Configure mock behavior
        mocks["get_dovi_profile"].return_value = profile
        mocks["extract_video"].return_value = "/tmp/video.hevc"
        mocks["demux_video"].return_value = "/tmp/EL.hevc"
        mocks["extract_layer"].side_effect = ["/tmp/BL_RPU.bin", "/tmp/EL_RPU.bin"]

        # Set checksums to match or not based on parameter
        if checksums_match:
            mocks["calculate_sha512"].return_value = "same_checksum"
        else:
            mocks["calculate_sha512"].side_effect = [
                "bl_checksum",
                "el_checksum",
            ]  # Different checksums

        mocks["convert_dovi_profile"].return_value = "/tmp/P8.hevc"
        mocks["merge_mkv"].return_value = pathlib.Path("/tmp/final_output.mkv")

        # Call the exec method
        dovi_service.exec()

        # Verify the expected methods were called
        for method in mocks:
            if method in expected_calls:
                assert mocks[method].called, f"{method} should have been called"
            else:
                assert not mocks[method].called, f"{method} should not have been called"
