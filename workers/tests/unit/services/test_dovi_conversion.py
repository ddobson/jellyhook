import json
import pathlib
from unittest import mock

import pytest

from workers.errors import MediaNotFoundError, is_transient
from workers.services.dovi_conversion import DoviConversionService


@pytest.fixture
def dovi_service(make_job):
    """A DoviConversionService over a real Job for the movie in the temporary media tree."""
    return DoviConversionService(make_job({"Name": "Test Movie", "Year": "2023"}))


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


def test_move_to_target_swaps_the_file_in(dovi_service, media_dir, tmp_path):
    """The converted file replaces the original in one step."""
    output_file = tmp_path / "final_output.mkv"
    output_file.write_bytes(b"converted")

    dovi_service.move_to_target(output_file)

    assert pathlib.Path(media_dir["movie_file"]).read_bytes() == b"converted"
    assert not output_file.exists()


def test_move_to_target_failure_keeps_the_original(dovi_service, media_dir, tmp_path):
    """Nothing is deleted up front, so a failed swap cannot lose the movie."""
    with pytest.raises(FileNotFoundError):
        dovi_service.move_to_target(tmp_path / "never_written.mkv")

    assert pathlib.Path(media_dir["movie_file"]).read_text() == "movie content"


def test_dovi_conversion_rewrites_the_media_file():
    """The orchestrator locks the file for services that declare this."""
    assert DoviConversionService.modifies_media is True


def test_from_message_creates_no_scratch_dir_until_one_is_used(make_job, media_dir):
    job = make_job({"Name": "Test Movie", "Year": "2023"})

    service = DoviConversionService.from_message(job, {})

    assert isinstance(service, DoviConversionService)
    assert list(pathlib.Path(media_dir["tmp_path"]).iterdir()) == []

    assert service.tmp_dir.parent == pathlib.Path(media_dir["tmp_path"])
    assert service.tmp_dir.is_dir()


def test_from_message_honours_the_configured_temp_dir(make_job, tmp_path):
    job = make_job({"Name": "Test Movie", "Year": "2023"})

    service = DoviConversionService.from_message(job, {"temp_dir": str(tmp_path / "dovi")})

    assert service.tmp_dir.parent == tmp_path / "dovi"


def test_scratch_dirs_differ_between_jobs_for_the_same_title(make_job):
    """A redelivery of the same title must not reuse the previous run's files."""
    message = {"Name": "Test Movie", "Year": "2023"}
    first = DoviConversionService.from_message(make_job(message), {})
    second = DoviConversionService.from_message(make_job(message), {})

    assert first.tmp_dir != second.tmp_dir


def test_scratch_dir_is_removed_when_the_job_closes(make_job):
    job = make_job({"Name": "Test Movie", "Year": "2023"})
    service = DoviConversionService.from_message(job, {})
    scratch = service.tmp_dir

    job.close()

    assert not scratch.exists()


def test_commands_are_argument_lists_with_paths_intact(make_job, media_dir):
    """Odd characters in file names must reach the tools untouched (no shell is involved)."""
    folder = pathlib.Path(media_dir["movie_path"]) / 'It\'s $(a) "Test" (2023)'
    folder.mkdir()
    (folder / 'It\'s $(a) "Test" movie; rm -rf x.mkv').write_text("movie")
    dovi_service = DoviConversionService(make_job({"Name": 'It\'s $(a) "Test"', "Year": "2023"}))
    nasty = dovi_service.movie.full_path

    with mock.patch("workers.utils.run_command") as mock_run_command:
        mock_run_command.return_value.stdout = "{}"
        dovi_service.get_dovi_profile()
        dovi_service.extract_video()
        dovi_service.merge_mkv(nasty, "/tmp/P8.hevc")

    probe, extract, merge = (call.args[0] for call in mock_run_command.call_args_list)
    assert probe[-1] == nasty
    assert extract[1] == nasty
    assert merge[-1] == nasty
    # mkvmerge exits 1 for warnings, which must not be treated as a failure
    assert mock_run_command.call_args_list[2].kwargs["ok_returncodes"] == (0, 1)


def test_from_message_no_movie_found_is_transient(make_job):
    """The file may simply not have landed yet, so the message is retried."""
    job = make_job({"Name": "Missing Movie", "Year": "2023"})

    with pytest.raises(MediaNotFoundError, match="No video found for 'Missing Movie'") as exc:
        DoviConversionService.from_message(job, {})

    assert is_transient(exc.value)


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
