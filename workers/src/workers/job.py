"""The per-message Job: everything a service needs from the outside world for one message.

Each webhook message gets one `Job`. It locates the media file once, builds the `Movie` once,
hands out scratch directories on demand and always removes them when the message is done. Services
ask the Job instead of scanning `MEDIA_PATHS` or reading config globals themselves, so file
lookup, cleanup, locking and "file not found" handling behave the same for every service.
"""

import contextlib
import glob
import os
import pathlib
from collections.abc import Iterator, Sequence
from types import TracebackType
from typing import Any

from workers import fileops
from workers.errors import MediaNotFoundError, WebhookWorkerError
from workers.logger import logger
from workers.models.item import Item
from workers.models.movie import Movie

MEDIA_FILE_TYPES = (".mkv", ".mp4", ".avi")


def find_media_file(message: dict[str, Any], media_paths: Sequence[str]) -> pathlib.Path:
    """Find the video file for a Jellyfin item_added webhook message.

    Only movies (including stand-up, which Jellyfin also types as Movie) live in a
    "<Name> (<Year>)" folder under the media paths.

    Args:
        message: The webhook message, containing 'Name' and 'Year' keys.
        media_paths: The library roots to search, in order. The first with a match wins.

    Returns:
        The path to the video file.

    Raises:
        MediaNotFoundError: If no file is found (yet). Retryable: Jellyfin can announce an
            item before the file has finished landing.
        WebhookWorkerError: If more than one file matches.
    """
    search_result: list[pathlib.Path] = []

    for base_dir in media_paths:
        dirname = f"{base_dir}/{message['Name']} ({message['Year']})".replace(":", " -")
        obj_dir = pathlib.Path(dirname)

        if not obj_dir.exists():
            continue

        # Escaped: titles such as "[REC]" would otherwise be read as glob syntax
        name_pattern = glob.escape(message["Name"].replace(":", ""))
        patterns = [f"{name_pattern}*{file_type}" for file_type in MEDIA_FILE_TYPES]
        files_found = [result for pattern in patterns for result in obj_dir.glob(pattern)]

        if files_found:
            search_result = files_found
            break

    if not search_result:
        raise MediaNotFoundError(f"No video found for '{message['Name']}'")

    if len(search_result) != 1:
        raise WebhookWorkerError(f"Found more than one video for '{message['Name']}'")

    return search_result[0]


class Job:
    """Owns the media file, scratch space and lock for one webhook message.

    Use it as a context manager (or call `close()`) so scratch directories are removed whether the
    services succeed or fail.
    """

    def __init__(
        self,
        message: dict[str, Any],
        media_paths: Sequence[str],
        temp_dir: str | os.PathLike[str],
    ) -> None:
        """Initialize the job.

        Args:
            message: The raw Jellyfin webhook message.
            media_paths: The library roots to search for the item's file.
            temp_dir: The default base directory for scratch space.
        """
        self.message = message
        self.item = Item.from_message(message)
        self._media_paths = tuple(media_paths)
        self._temp_dir = temp_dir
        self._media_file: pathlib.Path | None = None
        self._movie: Movie | None = None
        self._scratch_dirs: list[pathlib.Path] = []

    @property
    def media_file(self) -> pathlib.Path:
        """The item's media file, located on first use and cached.

        Raises:
            MediaNotFoundError: If the file is not on disk (yet). Retryable.
            WebhookWorkerError: If more than one file matches.
        """
        if self._media_file is None:
            self._media_file = find_media_file(self.message, self._media_paths)
        return self._media_file

    @property
    def movie(self) -> Movie:
        """The item's media file parsed into a `Movie`, built on first use and cached."""
        if self._movie is None:
            self._movie = Movie.from_file(self.media_file)
        return self._movie

    def scratch_dir(self, label: str, base: str | os.PathLike[str] | None = None) -> pathlib.Path:
        """Create a new scratch directory, removed when the job closes.

        Args:
            label: A short human hint for the directory name.
            base: Where to create it. Defaults to the job's temp directory; services pass their
                own `temp_dir` setting here.

        Returns:
            The new, empty directory. Every call returns a different one.
        """
        path = fileops.make_job_dir(base if base is not None else self._temp_dir, label)
        # Registered straight away so it is removed even if the caller fails right after
        self._scratch_dirs.append(path)
        logger.info(f"Using temporary directory: {path}")
        return path

    @contextlib.contextmanager
    def file_lock(self) -> Iterator[None]:
        """Hold the lock for the media file, so two jobs never rewrite it at once."""
        with fileops.media_file_lock(self.media_file):
            yield

    def replace_media_file(self, new_file: pathlib.Path) -> None:
        """Atomically replace the media file with a rewritten one.

        Args:
            new_file: The finished file. It is consumed by the swap.
        """
        fileops.replace_file(new_file, self.media_file)

    def close(self) -> None:
        """Remove every scratch directory this job created. Never raises; safe to repeat."""
        for path in self._scratch_dirs:
            logger.info(f"Cleaning temporary files for '{path}'...")
            fileops.remove_dir(path)
        self._scratch_dirs.clear()

    def __enter__(self) -> "Job":
        """Enter the job's cleanup scope."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Remove scratch space, whether or not the services failed."""
        self.close()
