import pathlib
import threading

import pytest

from workers.errors import MediaNotFoundError, WebhookWorkerError, is_transient
from workers.job import Job


def test_media_file_finds_movie(make_job, media_dir):
    job = make_job({"Name": "Test Movie", "Year": "2023"})

    assert str(job.media_file) == media_dir["movie_file"]


def test_media_file_finds_standup(make_job, media_dir):
    job = make_job({"Name": "Comedy Special", "Year": "2022"})

    assert str(job.media_file) == media_dir["standup_file"]


def test_media_file_with_colon_in_name(make_job, media_dir):
    folder = pathlib.Path(media_dir["movie_path"]) / "Movie - Subtitle (2024)"
    folder.mkdir()
    (folder / "Movie Subtitle.mkv").write_text("content")

    job = make_job({"Name": "Movie: Subtitle", "Year": "2024"})

    assert job.media_file == folder / "Movie Subtitle.mkv"


def test_media_file_missing_is_transient(make_job):
    job = make_job({"Name": "Non Existent Movie", "Year": "2023"})

    with pytest.raises(MediaNotFoundError, match="No video found for 'Non Existent Movie'") as exc:
        _ = job.media_file

    assert is_transient(exc.value)


def test_media_file_multiple_matches_is_permanent(make_job, media_dir):
    (pathlib.Path(media_dir["movie_file"]).parent / "Test Movie.mp4").write_text("second")
    job = make_job({"Name": "Test Movie", "Year": "2023"})

    with pytest.raises(WebhookWorkerError, match="more than one video") as exc:
        _ = job.media_file

    assert not is_transient(exc.value)


@pytest.mark.parametrize("name", ["[REC]", "Who? Me*", "Brackets [Extended] Cut"])
def test_media_file_treats_glob_characters_in_the_title_literally(make_job, media_dir, name):
    folder = pathlib.Path(media_dir["movie_path"]) / f"{name} (2007)"
    folder.mkdir()
    (folder / f"{name}.mkv").write_text("movie")

    job = make_job({"Name": name, "Year": "2007"})

    assert job.media_file == folder / f"{name}.mkv"


def test_media_file_is_located_once(make_job, media_dir):
    job = make_job({"Name": "Test Movie", "Year": "2023"})
    first = job.media_file

    # Removing the file would make a second scan fail; a cached result does not notice
    pathlib.Path(media_dir["movie_file"]).unlink()

    assert job.media_file == first


def test_nothing_is_located_until_asked(make_job):
    """A job for a message with no file (a service that never touches it) must not need one."""
    job = make_job({"Name": "Non Existent Movie", "Year": "2023"})

    assert job.item is not None  # building and reading the item does no file lookup


def test_movie_is_parsed_from_the_media_file(make_job, media_dir):
    job = make_job({"Name": "Test Movie", "Year": "2023"})

    assert job.movie.full_path == str(pathlib.Path(media_dir["movie_file"]).resolve())
    assert job.movie is job.movie


def test_item_is_parsed_from_the_message(make_job):
    job = make_job({"ItemId": "abc", "ItemType": "Episode"})

    assert job.item.item_id == "abc"
    assert not job.item.is_movie


def test_scratch_dir_is_not_created_until_asked(make_job, media_dir):
    make_job({"Name": "Test Movie", "Year": "2023"})

    assert list(pathlib.Path(media_dir["tmp_path"]).iterdir()) == []


def test_scratch_dir_defaults_to_the_job_temp_dir(make_job, media_dir):
    job = make_job({"Name": "Test Movie", "Year": "2023"})

    scratch = job.scratch_dir("dovi")

    assert scratch.is_dir()
    assert scratch.parent == pathlib.Path(media_dir["tmp_path"])


def test_scratch_dir_honours_a_service_override(make_job, tmp_path):
    job = make_job({"Name": "Test Movie", "Year": "2023"})

    scratch = job.scratch_dir("dovi", base=str(tmp_path / "elsewhere"))

    assert scratch.parent == tmp_path / "elsewhere"


def test_each_scratch_dir_request_is_a_separate_directory(make_job):
    job = make_job({"Name": "Test Movie", "Year": "2023"})

    assert job.scratch_dir("a") != job.scratch_dir("a")


def test_close_removes_every_scratch_dir(make_job, tmp_path):
    job = make_job({"Name": "Test Movie", "Year": "2023"})
    first = job.scratch_dir("a")
    second = job.scratch_dir("b", base=str(tmp_path / "other"))
    (first / "leftover.bin").write_text("x")

    job.close()

    assert not first.exists()
    assert not second.exists()


def test_close_is_safe_to_repeat(make_job):
    job = make_job({"Name": "Test Movie", "Year": "2023"})
    job.scratch_dir("a")

    job.close()
    job.close()


def test_context_manager_cleans_up_after_a_failure(media_dir):
    with (
        pytest.raises(RuntimeError),
        Job({}, media_paths=(), temp_dir=media_dir["tmp_path"]) as job,
    ):
        scratch = job.scratch_dir("a")
        raise RuntimeError("boom")

    assert not scratch.exists()


def test_replace_media_file_swaps_in_the_new_file(make_job, media_dir, tmp_path):
    job = make_job({"Name": "Test Movie", "Year": "2023"})
    new_file = tmp_path / "cleaned.mkv"
    new_file.write_text("new content")

    job.replace_media_file(new_file)

    assert pathlib.Path(media_dir["movie_file"]).read_text() == "new content"
    assert not new_file.exists()


def test_file_lock_blocks_another_job_on_the_same_file(make_job):
    message = {"Name": "Test Movie", "Year": "2023"}
    first, second = make_job(message), make_job(message)
    entered = threading.Event()

    def contend() -> None:
        with second.file_lock():
            entered.set()

    with first.file_lock():
        thread = threading.Thread(target=contend)
        thread.start()
        assert not entered.wait(timeout=0.2)
    thread.join(timeout=2)

    assert entered.is_set()
