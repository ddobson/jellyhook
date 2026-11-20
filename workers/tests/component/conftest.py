import pathlib
import shutil
from unittest.mock import MagicMock, patch

import pytest

from workers import utils
from workers.job import Job
from workers.services import DoviConversionService, MediaTrackCleanService


@pytest.fixture
def temp_dir(tmp_path):
    return tmp_path


@pytest.fixture
def media_root(temp_dir):
    """A media library root; scratch space lives beside it in `temp_dir / "scratch"`."""
    root = temp_dir / "media"
    root.mkdir()
    return root


@pytest.fixture
def media_job(media_root, temp_dir):
    """Factory for Jobs over the temporary media tree, closed (scratch removed) after the test."""
    jobs: list[Job] = []

    def make(name: str, year: int = 2025) -> Job:
        job = Job(
            {"Name": name, "Year": year, "ItemType": "Movie"},
            media_paths=(str(media_root),),
            temp_dir=str(temp_dir / "scratch"),
        )
        jobs.append(job)
        return job

    yield make
    for job in jobs:
        job.close()


@pytest.fixture
def asset_copies(media_root):
    """Copy the test assets into the media tree, one "<Name> (<Year>)" folder each."""
    assets_dir = pathlib.Path(__file__).parent.parent / "assets"
    names = {
        "p7": "DoVi P7 Test File (2025) [tmdbid-67890] - [Bluray-2160p][DV HDR10][DTS-HD MA 5.1][x265]-TESTFILE.mkv",
        "p8": "DoVi P8 Test File (2025) [tmdbid-12345] - [Bluray-2160p][DV HDR10][DTS-HD MA 5.1][x265]-TESTFILE.mkv",
        "multitrack": "Multi-Track Test File (2025) [tmdbid45678] - [Bluray-2160p][AAC][x264]-TESTFILE.mkv",
    }
    copies = {}
    for key, file_name in names.items():
        folder = media_root / f"{file_name.split(' (2025)')[0]} (2025)"
        folder.mkdir()
        copies[key] = folder / file_name
        shutil.copy2(assets_dir / file_name, copies[key])
    return copies


@pytest.fixture
def p7_job(asset_copies, media_job):
    return media_job("DoVi P7 Test File")


@pytest.fixture
def p8_job(asset_copies, media_job):
    return media_job("DoVi P8 Test File")


@pytest.fixture
def multitrack_job(asset_copies, media_job):
    return media_job("Multi-Track Test File")


@pytest.fixture
def p7_service(p7_job):
    return DoviConversionService(p7_job)


@pytest.fixture
def p8_service(p8_job):
    return DoviConversionService(p8_job)


@pytest.fixture
def multitrack_service_all_eng(multitrack_job):
    """MediaTrackCleanService configured to keep only English tracks."""
    return MediaTrackCleanService(
        multitrack_job,
        keep_original=True,
        keep_default=True,
        keep_audio_langs=["eng"],
        keep_sub_langs=["eng"],
    )


@pytest.fixture
def multitrack_service_multi_langs(multitrack_job):
    """MediaTrackCleanService configured to keep English, Spanish and German."""
    return MediaTrackCleanService(
        multitrack_job,
        keep_original=False,
        keep_default=False,
        keep_audio_langs=["eng", "spa", "deu"],
        keep_sub_langs=["eng", "deu"],
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
