import errno
import os
import pathlib
import threading
import time

import pytest

from workers import fileops


def test_replace_file_swaps_on_the_same_filesystem(tmp_path):
    target = tmp_path / "movie.mkv"
    target.write_bytes(b"original")
    source = tmp_path / "new.mkv"
    source.write_bytes(b"replacement")

    fileops.replace_file(source, target)

    assert target.read_bytes() == b"replacement"
    assert not source.exists()


def _force_cross_device(monkeypatch, fail_times: int = 1):
    """Make the first `fail_times` os.replace calls fail like a cross-mount rename does."""
    real_replace = os.replace
    calls = {"n": 0}

    def fake_replace(src, dst):
        calls["n"] += 1
        if calls["n"] <= fail_times:
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        real_replace(src, dst)

    monkeypatch.setattr(fileops.os, "replace", fake_replace)


def test_replace_file_copies_across_filesystems_then_swaps(tmp_path, monkeypatch):
    target = tmp_path / "library" / "movie.mkv"
    target.parent.mkdir()
    target.write_bytes(b"original")
    source = tmp_path / "scratch" / "new.mkv"
    source.parent.mkdir()
    source.write_bytes(b"replacement")
    _force_cross_device(monkeypatch)

    fileops.replace_file(source, target)

    assert target.read_bytes() == b"replacement"
    assert not source.exists()
    # No hidden staging file is left beside the movie
    assert [p.name for p in target.parent.iterdir()] == ["movie.mkv"]


def test_replace_file_keeps_original_if_the_copy_fails(tmp_path, monkeypatch):
    """The disk-full case: the original must survive and no partial file may linger."""
    target = tmp_path / "movie.mkv"
    target.write_bytes(b"original")
    source = tmp_path / "new.mkv"
    source.write_bytes(b"replacement")
    _force_cross_device(monkeypatch)

    def disk_full(src, dst):
        pathlib.Path(dst).write_bytes(b"par")  # a partial copy lands first
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(fileops.shutil, "copyfile", disk_full)

    with pytest.raises(OSError, match="No space"):
        fileops.replace_file(source, target)

    assert target.read_bytes() == b"original"
    assert source.read_bytes() == b"replacement"  # the finished file is still there to retry
    assert {p.name for p in tmp_path.iterdir()} == {"movie.mkv", "new.mkv"}


def test_replace_file_reraises_other_os_errors(tmp_path):
    target = tmp_path / "movie.mkv"
    target.write_bytes(b"original")

    with pytest.raises(FileNotFoundError):
        fileops.replace_file(tmp_path / "missing.mkv", target)

    assert target.read_bytes() == b"original"


def test_make_job_dir_is_unique_and_under_base(tmp_path):
    base = tmp_path / "does" / "not" / "exist"

    first = fileops.make_job_dir(base, "Item A (2023)")
    second = fileops.make_job_dir(base, "Item A (2023)")

    assert first != second
    assert first.parent == base and first.is_dir()
    assert first.name.startswith("Item_A__2023_-")  # unsafe characters are flattened


def test_remove_dir_deletes_contents_and_tolerates_missing(tmp_path):
    job = fileops.make_job_dir(tmp_path, "job")
    (job / "sub").mkdir()
    (job / "sub" / "file").write_bytes(b"x")

    fileops.remove_dir(job)
    fileops.remove_dir(job)  # already gone: no error

    assert not job.exists()


def test_media_file_lock_serialises_the_same_file(tmp_path):
    movie = tmp_path / "movie.mkv"
    movie.write_bytes(b"x")
    running = 0
    peak = 0
    guard = threading.Lock()

    def work():
        nonlocal running, peak
        with fileops.media_file_lock(movie):
            with guard:
                running += 1
                peak = max(peak, running)
            time.sleep(0.03)
            with guard:
                running -= 1

    threads = [threading.Thread(target=work) for _ in range(5)]
    [t.start() for t in threads]
    [t.join() for t in threads]

    assert peak == 1


def test_media_file_lock_lets_different_files_run_together(tmp_path):
    barrier = threading.Barrier(2, timeout=5)

    def work(name):
        with fileops.media_file_lock(tmp_path / name):
            barrier.wait()  # only passes if both threads are inside their locks at once

    threads = [threading.Thread(target=work, args=(n,)) for n in ("a.mkv", "b.mkv")]
    [t.start() for t in threads]
    [t.join() for t in threads]

    assert not barrier.broken


def test_media_file_lock_forgets_idle_keys(tmp_path):
    with fileops.media_file_lock(tmp_path / "movie.mkv"):
        pass

    assert fileops._media_locks._locks == {}


def test_media_file_lock_releases_on_error(tmp_path):
    movie = tmp_path / "movie.mkv"

    with pytest.raises(RuntimeError), fileops.media_file_lock(movie):
        raise RuntimeError("boom")

    with fileops.media_file_lock(movie):  # would hang if the lock leaked
        pass
