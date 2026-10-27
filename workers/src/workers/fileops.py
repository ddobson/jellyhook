"""Safe file handling for jobs that rewrite media in place.

Two rules, both learned the hard way from the old delete-then-rename code:

1. The original file is never removed or moved aside until its replacement is already sitting
   next to it. A crash, a full disk or a cross-device rename can then only ever leave the
   original untouched, never a missing movie.
2. Every job works in its own scratch directory, so two runs (a redelivered message, or two
   queues touching one title) cannot trample each other's intermediates.
"""

import contextlib
import errno
import os
import pathlib
import shutil
import tempfile
import threading
import uuid
from collections.abc import Iterator

from workers.logger import logger


def replace_file(source: pathlib.Path, target: pathlib.Path) -> None:
    """Atomically replace `target` with `source`.

    `os.replace` is atomic on POSIX only within one filesystem
    (https://docs.python.org/3/library/os.html#os.replace). Scratch space is often a different
    mount than the media library (the compose file mounts them separately), and then the rename
    fails with EXDEV. In that case the file is first copied to a hidden sibling of `target`,
    which is on the right filesystem, and that copy is swapped in atomically.

    Args:
        source (pathlib.Path): The finished file. Removed once the swap has happened.
        target (pathlib.Path): The file to replace.

    Raises:
        OSError: If the replacement could not be completed. `target` is left as it was.
    """
    try:
        os.replace(source, target)
        return
    except OSError as e:
        if e.errno != errno.EXDEV:
            raise
        logger.info(f"{source} and {target} are on different filesystems; copying across")

    staged = target.with_name(f".{target.name}.{uuid.uuid4().hex}.jellyhook-tmp")
    try:
        shutil.copyfile(source, staged)
        shutil.copystat(target, staged)  # keep the original's mode and times where possible
        with staged.open("rb") as f:
            os.fsync(f.fileno())
        os.replace(staged, target)
    except BaseException:
        staged.unlink(missing_ok=True)
        raise
    source.unlink(missing_ok=True)


def make_job_dir(base: str | os.PathLike[str], label: str) -> pathlib.Path:
    """Create a scratch directory that no other job can share.

    Args:
        base (str | os.PathLike[str]): The configured temp directory. Created if missing.
        label (str): A short human hint for the directory name (not used for uniqueness).

    Returns:
        pathlib.Path: The new, empty directory.
    """
    base_path = pathlib.Path(base)
    base_path.mkdir(parents=True, exist_ok=True)
    safe_label = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)[:40]
    return pathlib.Path(tempfile.mkdtemp(prefix=f"{safe_label}-", dir=base_path))


def remove_dir(path: pathlib.Path) -> None:
    """Delete a scratch directory and everything in it, ignoring a directory that is gone.

    Args:
        path (pathlib.Path): The directory to remove.
    """
    shutil.rmtree(path, ignore_errors=True)


class _KeyedLocks:
    """A lock per key, created on demand and forgotten when nobody holds or awaits it."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._locks: dict[str, tuple[threading.Lock, int]] = {}

    @contextlib.contextmanager
    def hold(self, key: str) -> Iterator[None]:
        """Hold the lock for `key`, blocking until it is free."""
        with self._guard:
            lock, users = self._locks.get(key, (threading.Lock(), 0))
            self._locks[key] = (lock, users + 1)
        try:
            with lock:
                yield
        finally:
            with self._guard:
                lock, users = self._locks[key]
                if users == 1:
                    del self._locks[key]
                else:
                    self._locks[key] = (lock, users - 1)


_media_locks = _KeyedLocks()


@contextlib.contextmanager
def media_file_lock(path: str | os.PathLike[str]) -> Iterator[None]:
    """Serialise jobs that modify the same media file.

    This is an in-process lock: it covers every consumer and job thread in one worker, which is
    the only kind of concurrency the worker has. Running several worker containers against the
    same library would need a lock all of them can see (a lock file or the broker).

    Args:
        path (str | os.PathLike[str]): The media file about to be modified.
    """
    with _media_locks.hold(str(pathlib.Path(path).resolve())):
        yield
