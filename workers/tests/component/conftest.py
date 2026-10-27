import pathlib
import shutil
from unittest.mock import MagicMock, patch

import pytest

from workers import fileops, utils
from workers.movie import Movie
from workers.services import DoviConversionService, MediaTrackCleanService


@pytest.fixture
def temp_dir(tmp_path):
    return tmp_path


@pytest.fixture
def asset_copies(temp_dir):
    # Get the path to the assets directory
    assets_dir = pathlib.Path(__file__).parent.parent / "assets"

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

    # Copy the multi-track test file
    multitrack_source = (
        assets_dir
        / "Multi-Track Test File (2025) [tmdbid45678] - [Bluray-2160p][AAC][x264]-TESTFILE.mkv"
    )
    multitrack_dest = test_assets_dir / multitrack_source.name
    shutil.copy2(multitrack_source, multitrack_dest)

    return {"p7": p7_dest, "p8": p8_dest, "multitrack": multitrack_dest}


@pytest.fixture
def p7_movie(asset_copies):
    return Movie.from_file(asset_copies["p7"])


@pytest.fixture
def p8_movie(asset_copies):
    return Movie.from_file(asset_copies["p8"])


@pytest.fixture
def multitrack_movie(asset_copies):
    return Movie.from_file(asset_copies["multitrack"])


@pytest.fixture
def p7_service(p7_movie, temp_dir):
    return DoviConversionService(p7_movie, fileops.make_job_dir(temp_dir / "scratch", "p7"))


@pytest.fixture
def p8_service(p8_movie, temp_dir):
    return DoviConversionService(p8_movie, fileops.make_job_dir(temp_dir / "scratch", "p8"))


@pytest.fixture
def multitrack_service_all_eng(multitrack_movie, temp_dir):
    """MediaTrackCleanService configured to keep only English tracks."""
    return MediaTrackCleanService(
        multitrack_movie,
        keep_original=True,
        keep_default=True,
        keep_audio_langs=["eng"],
        keep_sub_langs=["eng"],
        temp_dir=str(temp_dir / "scratch"),
    )


@pytest.fixture
def multitrack_service_multi_langs(multitrack_movie, temp_dir):
    """MediaTrackCleanService configured to keep English, Spanish and German."""
    return MediaTrackCleanService(
        multitrack_movie,
        keep_original=False,
        keep_default=False,
        keep_audio_langs=["eng", "spa", "deu"],
        keep_sub_langs=["eng", "deu"],
        temp_dir=str(temp_dir / "scratch"),
    )


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
def run_command_mock():
    """Mock for run_command that wraps the real function."""
    with patch(
        "workers.services.dovi_conversion.utils.run_command", new=MagicMock(wraps=utils.run_command)
    ) as mock_fn:
        yield mock_fn


@pytest.fixture
def track_clean_command_mock():
    """Mock for run_command that wraps the real function for MediaTrackCleanService."""
    with patch(
        "workers.services.media_track_clean.run_command", new=MagicMock(wraps=utils.run_command)
    ) as mock_fn:
        yield mock_fn


@pytest.fixture
def leftovers_beside():
    """Find backup or staging files sitting next to a media file (there should be none)."""

    def find(path: pathlib.Path) -> list[str]:
        return [
            p.name for p in path.parent.iterdir() if p.suffix == ".bak" or "jellyhook-tmp" in p.name
        ]

    return find
