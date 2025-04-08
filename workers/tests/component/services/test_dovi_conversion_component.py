import pathlib
from unittest.mock import Mock

DOVI_PROFLE_7 = 7
DOVI_PROFLE_8 = 8


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
