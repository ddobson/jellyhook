import pathlib
from unittest import mock

import pytest

from workers.models.items import Movie


def test_movie_initialization():
    """Test the basic initialization of a Movie object."""
    file_path = pathlib.Path("/path/to/movie.mkv")
    movie = Movie(
        file=file_path,
        title="Test Movie",
        year="2023",
        tmdb_id="12345",
        edition="Director's Cut",
        video_codec="x265",
        audio="DTS-HD",
        quality="2160p",
        format_="IMAX",
        dynamic_range="HDR10",
        release_group="GROUP",
        is_3d=False,
    )

    assert movie.title == "Test Movie"
    assert movie.year == "2023"
    assert movie.tmdb_id == "12345"
    assert movie.edition == "Director's Cut"
    assert movie.video_codec == "x265"
    assert movie.audio == "DTS-HD"
    assert movie.quality == "2160p"
    assert movie.format_ == "IMAX"
    assert movie.dynamic_range == "HDR10"
    assert movie.release_group == "GROUP"
    assert movie.is_3d is False


def test_movie_properties():
    """Test the computed properties of a Movie object."""
    file_path = pathlib.Path("/path/to/movie.mkv")
    movie = Movie(file=file_path, title="Test: Movie", year="2023")

    assert movie.full_title == "Test: Movie (2023)"
    assert movie.folder_title == "Test - Movie (2023)"
    assert movie.full_path == str(file_path.resolve())

    # Test escaped properties
    with mock.patch("shlex.quote", return_value="'/path/to/movie.mkv'"):
        assert movie.escaped_path == "'/path/to/movie.mkv'"

    with mock.patch("re.escape", return_value="Test\\:\\ Movie\\.mkv"):
        assert movie.escaped_file_name == "Test\\:\\ Movie\\.mkv"


def test_movie_from_file(worker_config):
    """Test creating a Movie from a file path using from_file class method."""
    file_path = pathlib.Path(
        "Test Movie (2023) [tmdbid-12345] - Director's Cut [x265] [HDR10] [DTS-HD] [2160p] [IMAX]-GROUP.mkv"
    )

    with mock.patch.object(
        Movie,
        "parse_movie_filename",
        return_value={
            "title": "Test Movie",
            "year": "2023",
            "tmdb_id": "12345",
            "edition": "Director's Cut",
            "video_codec": "x265",
            "dynamic_range": "HDR10",
            "audio": "DTS-HD",
            "quality": "2160p",
            "format_": "IMAX",
            "release_group": "GROUP",
        },
    ):
        movie = Movie.from_file(file_path)

    assert movie.title == "Test Movie"
    assert movie.year == "2023"
    assert movie.tmdb_id == "12345"
    assert movie.edition == "Director's Cut"
    assert movie.video_codec == "x265"
    assert movie.dynamic_range == "HDR10"
    assert movie.audio == "DTS-HD"
    assert movie.quality == "2160p"
    assert movie.format_ == "IMAX"
    assert movie.release_group == "GROUP"


def test_parse_movie_filename_with_parser_result():
    """Test parsing different movie filenames."""
    filename = "Test Movie (2003).mkv"
    mock_parsed = {"title": "Test Movie"}
    mock_parser = mock.Mock(parse=mock.Mock(return_value=mock_parsed))
    result = Movie.parse_movie_filename(filename, mock_parser)
    assert result == mock_parsed
    mock_parser.parse.assert_called_once_with(filename)


@pytest.mark.parametrize(
    "filename, expected",
    [
        ("Test Movie (2003).mkv", {"title": "Test Movie", "year": "2003"}),
        ("Another Film [2020].mp4", {"title": "Another Film", "year": "2020"}),
        ("Some.Movie.2019.mkv", {"title": "Some Movie", "year": "2019"}),
        ("Old_Film (1995) HDRip.avi", {"title": "Old_Film", "year": "1995"}),
        ("No Year Movie.mkv", {"title": "No Year Movie", "year": ""}),
    ],
)
def test_parse_movie_filename_fallback(filename, expected):
    """Test fallback parsing when parser returns None for various filename formats."""
    mock_parser = mock.Mock(parse=mock.Mock(return_value=None))
    result = Movie.parse_movie_filename(filename, mock_parser)
    assert result == expected
    mock_parser.parse.assert_called_once_with(filename)


def test_delete_movie():
    """Test the delete method removes the movie file."""
    mock_file = mock.MagicMock(spec=pathlib.Path)
    movie = Movie(file=mock_file, title="Test Movie", year="2023")

    movie.delete()

    mock_file.unlink.assert_called_once()
