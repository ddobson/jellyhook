import pathlib
from unittest import mock

import pytest

from workers.movie import Movie


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


def test_movie_from_file():
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


@pytest.mark.parametrize(
    "filename,expected",
    [
        # Standard movie with all attributes
        (
            "Test Movie (1992) [tmdbid-12345] - Director's Cut [x265] [HDR10] [DTS-HD] [2160p]-GROUP.mkv",
            {
                "title": "Test Movie",
                "year": "1992",
                "tmdb_id": "12345",
                "edition": "Director's Cut",
                "video_codec": "x265",
                "dynamic_range": "HDR10",
                "audio": "DTS-HD",
                "quality": "2160p",
                "release_group": "GROUP",
            },
        ),
        # Different ordering of attributes
        (
            "Test Movie 2 (1994) [tmdbid-67890] [HYBRID] [1080p] [HEVC] [DV] [TrueHD]-RELEASE.mkv",
            {
                "title": "Test Movie 2",
                "year": "1994",
                "tmdb_id": "67890",
                "video_codec": "HEVC",
                "dynamic_range": "DV",
                "audio": "TrueHD",
                "quality": "1080p",
                "format_": "HYBRID",
                "release_group": "RELEASE",
            },
        ),
        # 3D movie
        (
            "3D Movie (2012) [tmdbid-54321] [x264] [3D] [AC3] [Bluray]-RIP.mkv",
            {
                "title": "3D Movie",
                "year": "2012",
                "tmdb_id": "54321",
                "video_codec": "x264",
                "is_3d": True,
                "audio": "AC3",
                "quality": "Bluray",
                "release_group": "RIP",
            },
        ),
        # Minimal movie with only title and year
        (
            "Basic Movie (2022).mkv",
            {
                "title": "Basic Movie",
                "year": "2022",
            },
        ),
        # Movie with colon in title
        (
            "Movie: Subtitle (2021) [tmdbid-11111]-GROUP.mkv",
            {
                "title": "Movie: Subtitle",
                "year": "2021",
                "tmdb_id": "11111",
                "release_group": "GROUP",
            },
        ),
        # Movie with multiple format tags
        (
            "Multi Format (2020) [tmdbid-22222] [IMAX HYBRID] [HDR]-GROUP.mkv",
            {
                "title": "Multi Format",
                "year": "2020",
                "tmdb_id": "22222",
                "format_": "IMAX HYBRID",
                "dynamic_range": "HDR",
                "release_group": "GROUP",
            },
        ),
        # No release group
        (
            "No Group (2018) [tmdbid-33333] [x264].mkv",
            {
                "title": "No Group",
                "year": "2018",
                "tmdb_id": "33333",
                "video_codec": "x264",
            },
        ),
        # Movie with edition but no tmdbid
        (
            "No TMDB (2017) - Extended Cut [1080p] [DTS]-GROUP.mkv",
            {
                "title": "No TMDB",
                "year": "2017",
                "quality": "1080p",
                "audio": "DTS",
                "release_group": "GROUP",
            },
        ),
        # Complex title with lot's of punctuation
        (
            "Complex: Title! With, Punctuation? (2016) [tmdbid-44444] [x265]-GROUP.mkv",
            {
                "title": "Complex: Title! With, Punctuation?",
                "year": "2016",
                "tmdb_id": "44444",
                "video_codec": "x265",
                "release_group": "GROUP",
            },
        ),
        # Movie with spaces in edition name
        (
            "Spaced Edition (2015) [tmdbid-55555] - Special Extended Edition [Remux]-GROUP.mkv",
            {
                "title": "Spaced Edition",
                "year": "2015",
                "tmdb_id": "55555",
                "edition": "Special Extended Edition",
                "quality": "Remux",
                "release_group": "GROUP",
            },
        ),
        # File with .mp4 extension
        (
            "Different Extension (2014) [tmdbid-66666] [x264].mp4",
            {
                "title": "Different Extension",
                "year": "2014",
                "tmdb_id": "66666",
                "video_codec": "x264",
            },
        ),
        # Movie with multiple audio formats
        (
            "Audio Movie (2013) [tmdbid-77777] [DTS-HD TrueHD AAC]-GROUP.mkv",
            {
                "title": "Audio Movie",
                "year": "2013",
                "tmdb_id": "77777",
                "audio": "DTS-HD TrueHD AAC",
                "release_group": "GROUP",
            },
        ),
        # Movie with multiple dynamic range formats
        (
            "Dynamic Range (2012) [tmdbid-88888] [HDR10 DV] [x265]-GROUP.mkv",
            {
                "title": "Dynamic Range",
                "year": "2012",
                "tmdb_id": "88888",
                "dynamic_range": "HDR10 DV",
                "video_codec": "x265",
                "release_group": "GROUP",
            },
        ),
    ],
)
def test_parse_movie_filename(filename, expected):
    """Test parsing different movie filenames."""
    result = Movie.parse_movie_filename(filename)
    for key, value in expected.items():
        assert result[key] == value


def test_delete_movie():
    """Test the delete method removes the movie file."""
    mock_file = mock.MagicMock(spec=pathlib.Path)
    movie = Movie(file=mock_file, title="Test Movie", year="2023")

    movie.delete()

    mock_file.unlink.assert_called_once()
