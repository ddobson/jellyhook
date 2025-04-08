import pathlib
import shutil

import pytest

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
