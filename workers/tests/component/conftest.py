import pathlib
import shutil
from unittest.mock import MagicMock, call, patch

import pytest

from workers import utils
from workers.movie import Movie
from workers.services import DoviConversionService


@pytest.fixture
def temp_dir(tmp_path):
    return tmp_path


@pytest.fixture
def asset_copies(temp_dir):
    # Get the path to the assets directory
    assets_dir = pathlib.Path(__file__).parent / "assets"

    # Create a copy directory for test assets
    test_assets_dir = temp_dir / "assets"
    test_assets_dir.mkdir(exist_ok=True)

    # Copy the P7 test file
    p7_source = (
        assets_dir
        / "DoVi P7 Test File (2025) [tmdbid-67890] - [Bluray-2160p][DV HDR10][DTS-HD MA 5.1][x265]-TESTFILE.mkv"
    )
    p7_dest = test_assets_dir / p7_source.name
    shutil.copy2(p7_source, p7_dest)

    # Copy the P8 test file
    p8_source = (
        assets_dir
        / "DoVi P8 Test File (2025) [tmdbid-12345] - [Bluray-2160p][DV HDR10][DTS-HD MA 5.1][x265]-TESTFILE.mkv"
    )
    p8_dest = test_assets_dir / p8_source.name
    shutil.copy2(p8_source, p8_dest)

    return {"p7": p7_dest, "p8": p8_dest}


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


@pytest.fixture
def mock_file_operations(monkeypatch):
    """Mock file operations that would alter test files."""

    def setup_mocks(service):
        monkeypatch.setattr(service.movie, "delete", lambda: None)
        monkeypatch.setattr(pathlib.Path, "rename", lambda self, target: self)
        return service

    return setup_mocks


@pytest.fixture
def mock_completion_tracker():
    """Fixture to track when move_to_target is called."""
    completion_flag = {"completed": False}

    def mock_move(*args, **kwargs):
        completion_flag["completed"] = True
        return None

    def was_completed():
        return completion_flag["completed"]

    return (mock_move, was_completed)


@pytest.fixture
def spy_merge_mkv():
    """Fixture to spy on merge_mkv method and track output path."""
    output_path = {"path": None}

    def setup_spy(service):
        original_merge_mkv = service.merge_mkv

        def spy_fn(*args, **kwargs):
            result = original_merge_mkv(*args, **kwargs)
            output_path["path"] = result
            return result

        return (spy_fn, lambda: output_path["path"])

    return setup_spy


@pytest.fixture
def expected_dovi_commands():
    """Expected command calls for DoVi conversion process."""

    def get_commands(service):
        return [
            call(
                "ffprobe -v quiet -print_format json -show_streams -select_streams v:0 "
                + service.movie.escaped_path
            ),
            call(
                'mkvextract "'
                + service.movie.full_path
                + '" tracks "0:'
                + str(service.tmp_dir)
                + '/video.hevc"',
                log_output=True,
                log_err=True,
            ),
            call(
                f'dovi_tool demux "{service.tmp_dir}/video.hevc" --el-only -e  "{service.tmp_dir}/EL.hevc"',
                log_output=True,
                log_err=True,
            ),
            call(
                f'dovi_tool -m 0 extract-rpu "{service.tmp_dir}/video.hevc" -o "{service.tmp_dir}/BL_RPU.bin"'
            ),
            call(
                f'dovi_tool -m 0 extract-rpu "{service.tmp_dir}/EL.hevc" -o "{service.tmp_dir}/EL_RPU.bin"'
            ),
            call(
                f'dovi_tool -m 2 convert --discard "{service.tmp_dir}/video.hevc" -o "{service.tmp_dir}/P8.hevc"',
                log_output=True,
                log_err=True,
            ),
            call(
                f'mkvmerge --output "{service.tmp_dir}/final_output.mkv" "{service.tmp_dir}/P8.hevc" --no-video "{service.movie.full_path}"'
            ),
        ]

    return get_commands


@pytest.fixture
def run_command_mock():
    """Mock for run_command that wraps the real function."""
    with patch(
        "workers.services.dovi_conversion.utils.run_command", new=MagicMock(wraps=utils.run_command)
    ) as mock_fn:
        yield mock_fn
