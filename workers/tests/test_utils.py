import os
import pathlib
from unittest import mock

import pytest

from workers.utils import WebhookWorkerError, file_from_message


def test_file_from_message_finds_movie(media_dir):
    """Test finding a movie file from a message."""
    with (
        mock.patch("workers.utils.MOVIE_PATH", media_dir["movie_path"]),
        mock.patch("workers.utils.STANDUP_PATH", media_dir["standup_path"]),
    ):
        message = {"Name": "Test Movie", "Year": "2023"}
        result = file_from_message(message)

        assert isinstance(result, pathlib.Path)
        assert str(result) == media_dir["movie_file"]


def test_file_from_message_finds_standup(media_dir):
    """Test finding a standup file from a message."""
    with (
        mock.patch("workers.utils.MOVIE_PATH", media_dir["movie_path"]),
        mock.patch("workers.utils.STANDUP_PATH", media_dir["standup_path"]),
    ):
        message = {"Name": "Comedy Special", "Year": "2022"}
        result = file_from_message(message)

        assert isinstance(result, pathlib.Path)
        assert str(result) == media_dir["standup_file"]


def test_file_from_message_with_colon_in_name(media_dir, tmpdir):
    """Test finding a file with a colon in the name."""
    # Setup a movie with a colon in the name
    movie_dir = pathlib.Path(media_dir["movie_path"])
    colon_movie_dir = movie_dir / "Movie - Subtitle (2024)"  # Directory name with colon replaced
    os.makedirs(colon_movie_dir, exist_ok=True)

    colon_movie_file = colon_movie_dir / "Movie Subtitle.mkv"  # File name with colon removed
    with open(colon_movie_file, "w") as f:
        f.write("movie with colon content")

    with (
        mock.patch("workers.utils.MOVIE_PATH", media_dir["movie_path"]),
        mock.patch("workers.utils.STANDUP_PATH", media_dir["standup_path"]),
    ):
        message = {"Name": "Movie: Subtitle", "Year": "2024"}
        result = file_from_message(message)

        assert isinstance(result, pathlib.Path)
        assert str(result) == str(colon_movie_file)


def test_file_from_message_no_file_found(media_dir):
    """Test error when no file is found."""
    with (
        mock.patch("workers.utils.MOVIE_PATH", media_dir["movie_path"]),
        mock.patch("workers.utils.STANDUP_PATH", media_dir["standup_path"]),
    ):
        message = {"Name": "Non Existent Movie", "Year": "2023"}

        with pytest.raises(WebhookWorkerError) as exc_info:
            file_from_message(message)

        assert "No video found for 'Non Existent Movie'" in str(exc_info.value)


def test_file_from_message_multiple_files_found(media_dir, tmpdir):
    """Test error when multiple files are found."""
    # Add a second movie file to the same directory
    movie_folder = pathlib.Path(media_dir["movie_file"]).parent
    second_file = movie_folder / "Test Movie.mp4"
    with open(second_file, "w") as f:
        f.write("second movie file content")

    with (
        mock.patch("workers.utils.MOVIE_PATH", media_dir["movie_path"]),
        mock.patch("workers.utils.STANDUP_PATH", media_dir["standup_path"]),
    ):
        message = {"Name": "Test Movie", "Year": "2023"}

        with pytest.raises(WebhookWorkerError) as exc_info:
            file_from_message(message)

        assert "Found more than one video for 'Test Movie'" in str(exc_info.value)
