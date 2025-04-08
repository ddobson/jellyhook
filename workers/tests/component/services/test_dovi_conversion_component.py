import json
import pathlib
from unittest.mock import Mock

from workers import utils

DOVI_PROFLE_7 = 7
DOVI_PROFLE_8 = 8


## Helper function to verify the DoVi profile of a media file
def _verify_dovi_profile(media_path, expected_profile):
    """Verify that a media file has the expected Dolby Vision profile.

    Args:
        media_path: Path to the media file to check
        expected_profile: The expected Dolby Vision profile (7 or 8)
    """
    # Run ffprobe on the file
    ffprobe_cmd = (
        f"ffprobe -v quiet -print_format json -show_streams -select_streams v:0 {media_path}"
    )
    ffprobe_result = utils.run_command(ffprobe_cmd)
    media_info = json.loads(ffprobe_result.stdout)

    # Extract and verify the DoVi profile
    dovi_profile = media_info["streams"][0]["side_data_list"][0]["dv_profile"]
    assert dovi_profile == expected_profile, (
        f"Expected DoVi profile {expected_profile}, but got {dovi_profile}"
    )


### === Component test cases for the DoviConversionService === ###


def test_service_correctly_identifies_p7_profile(p7_service):
    """Test that the service correctly identifies a Profile 7 Dolby Vision file."""
    assert p7_service.get_dovi_profile() == DOVI_PROFLE_7


def test_service_correctly_identifies_p8_profile(p8_service):
    """Test that the service correctly identifies a Profile 8 Dolby Vision file."""
    assert p8_service.get_dovi_profile() == DOVI_PROFLE_8


def test_layers_in_sync_returns_true_for_valid_p7(p7_service, temp_dir):
    video_path = temp_dir / "video.hevc"
    el_video_path = temp_dir / "EL.hevc"

    # Create test files with same content
    video_path.write_bytes(b"test content")
    el_video_path.write_bytes(b"test content")

    bl_layer = temp_dir / "BL_RPU.bin"
    el_layer = temp_dir / "EL_RPU.bin"

    p7_service.extract_layer = Mock(side_effect=[bl_layer, el_layer])
    p7_service.demux_video = Mock(return_value=el_video_path)

    bl_checksum = p7_service.calculate_sha512(str(video_path))
    el_checksum = p7_service.calculate_sha512(str(el_video_path))

    assert bl_checksum == el_checksum


def test_conversion_creates_p8_output(p7_service, temp_dir):
    input_path = temp_dir / "video.hevc"
    input_path.write_bytes(b"test content")

    output_path = p7_service.convert_dovi_profile(str(input_path))
    assert pathlib.Path(output_path).exists()


def test_p8_file_is_not_processed(p8_service, monkeypatch):
    """Test that P8 files are not processed (they're already P8)."""
    # Mock the get_dovi_profile method to return Profile 8
    monkeypatch.setattr(p8_service, "get_dovi_profile", lambda: DOVI_PROFLE_8)

    # Create a mock to verify that certain methods are not called
    mock_extract = Mock()
    monkeypatch.setattr(p8_service, "extract_video", mock_extract)

    # Run the process
    p8_service.exec()

    # Verify that extract_video was not called, meaning processing was skipped
    mock_extract.assert_not_called()


def test_service_extracts_p7_layers(p7_service):
    """Test that the service can extract layers from a P7 file."""
    # First extract the video stream
    assert not (p7_service.tmp_dir / "video.hevc").exists()
    video_path = p7_service.extract_video()
    assert pathlib.Path(video_path).exists()
    assert str(pathlib.Path(video_path)) == str((p7_service.tmp_dir / "video.hevc"))

    # Extract RPU layer
    assert not (p7_service.tmp_dir / "test_layer.bin").exists()
    rpu_layer = p7_service.extract_layer(video_path, "test_layer.bin")
    assert pathlib.Path(rpu_layer).exists()
    assert str(pathlib.Path(rpu_layer)) == str((p7_service.tmp_dir / "test_layer.bin"))

    # Should be able to demux video
    assert not (p7_service.tmp_dir / "EL.hevc").exists()
    el_video_path = p7_service.demux_video(video_path)
    assert pathlib.Path(el_video_path).exists()
    assert str(pathlib.Path(el_video_path)) == str((p7_service.tmp_dir / "EL.hevc"))


def test_p7_conversion_end_to_end(
    p7_service,
    monkeypatch,
    mock_file_operations,
    mock_completion_tracker,
    spy_merge_mkv,
    expected_dovi_commands,
    run_command_mock,
):
    """Test full conversion flow from P7 to P8."""
    # Setup mocks for file operations
    mock_file_operations(p7_service)

    # Setup completion tracker
    mock_move, was_completed = mock_completion_tracker
    monkeypatch.setattr(p7_service, "move_to_target", mock_move)

    # Setup spy for merge_mkv
    merge_spy_fn, get_output_path = spy_merge_mkv(p7_service)
    monkeypatch.setattr(p7_service, "merge_mkv", merge_spy_fn)

    # Get expected command calls
    expected_calls = expected_dovi_commands(p7_service)

    # Execute the conversion
    p7_service.exec()

    # Verify the process ran to completion
    assert was_completed() is True

    # Verify expected commands were called
    for expected_call in expected_calls:
        assert expected_call in run_command_mock.call_args_list

    # Get the output file path captured by our spy
    final_output_path = get_output_path()

    # Verify we captured the output path from merge_mkv
    assert final_output_path is not None
    assert final_output_path.exists()

    # Verify the converted video file exists
    p8_hevc_path = pathlib.Path(p7_service.tmp_dir) / "P8.hevc"
    assert p8_hevc_path.exists()

    # Run ffprobe on the output file and verify DoVi profile
    _verify_dovi_profile(final_output_path, DOVI_PROFLE_8)
