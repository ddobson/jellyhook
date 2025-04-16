from pathlib import Path
from unittest import mock

import pytest

from workers.models.items import Movie
from workers.parsers.movies import TrashMovieParser


@pytest.mark.parametrize(
    "filename, expected",
    [
        # Test case for standard parsing with TMDB ID
        (
            "Test File (2003) [tmdbid-67890] - [Bluray-2160p][DV HDR10][DTS-HD MA 5.1][x265]-TESTFILE.mkv",
            {
                "title": "Test File",
                "year": "2003",
                "quality": "Bluray-2160p",
                "audio": "DTS-HD MA 5.1",
                "video_codec": "x265",
                "dynamic_range": "DV HDR10",
                "release_group": "TESTFILE",
                "tmdb_id": "67890",
            },
        ),
        # Test case for standard parsing with IMDB ID and additional attributes
        (
            "Test File 2 (2010) [imdbid-tt0106145] - Ultimate Extended Edition [IMAX HYBRID][Bluray-2160p Proper][3D][DV HDR10][DTS 5.1][x264]-EVOLVE.mkv",
            {
                "title": "Test File 2",
                "year": "2010",
                "edition": "Ultimate Extended Edition",
                "quality": "Bluray-2160p Proper",
                "audio": "DTS 5.1",
                "video_codec": "x264",
                "dynamic_range": "DV HDR10",
                "release_group": "EVOLVE",
                "imdb_id": "tt0106145",
                "is_3d": True,
            },
        ),
        # Test case for standard parsing with TMDB ID and minimal attributes
        (
            "Test File 3 (2014) [tmdbid-12345] - [Bluray-1080p][AC3][x264].mkv",
            {
                "title": "Test File 3",
                "year": "2014",
                "quality": "Bluray-1080p",
                "audio": "AC3",
                "video_codec": "x264",
                "tmdb_id": "12345",
            },
        ),
        # Test case for missing TMDB and IMDB IDs
        (
            "Another Test (2020) [Bluray-1080p][AC3][x264]-GROUP.mkv",
            {
                "title": "Another Test",
                "year": "2020",
                "quality": "Bluray-1080p",
                "audio": "AC3",
                "video_codec": "x264",
                "release_group": "GROUP",
            },
        ),
        # Test case for missing release group
        (
            "No Group (2015) [tmdbid-54321][Bluray-720p][AAC][x265].mkv",
            {
                "title": "No Group",
                "year": "2015",
                "quality": "Bluray-720p",
                "audio": "AAC",
                "video_codec": "x265",
                "tmdb_id": "54321",
            },
        ),
        # Test case for multiple formats
        (
            "Multi Format (2018) [tmdbid-98765][IMAX HYBRID][Bluray-2160p][DTS-HD MA 7.1][x265]-MULTI.mkv",
            {
                "title": "Multi Format",
                "year": "2018",
                "quality": "Bluray-2160p",
                "audio": "DTS-HD MA 7.1",
                "video_codec": "x265",
                "tmdb_id": "98765",
                "format_": "IMAX HYBRID",
                "release_group": "MULTI",
            },
        ),
        # Test case for missing quality and audio attributes
        (
            "Minimal Info (2021) [tmdbid-11111][x264]-MINIMAL.mkv",
            {
                "title": "Minimal Info",
                "year": "2021",
                "video_codec": "x264",
                "tmdb_id": "11111",
                "release_group": "MINIMAL",
            },
        ),
    ],
)
def test_trash_format(filename, expected):
    result = TrashMovieParser.parse(filename)
    assert result.get("title") == expected.get("title")
    assert result.get("year") == expected.get("year")
    assert result.get("quality") == expected.get("quality")
    assert result.get("audio") == expected.get("audio")
    assert result.get("video_codec") == expected.get("video_codec")
    assert result.get("dynamic_range") == expected.get("dynamic_range")
    assert result.get("release_group") == expected.get("release_group")
    assert result.get("tmdb_id") == expected.get("tmdb_id")
    assert result.get("imdb_id") == expected.get("imdb_id")
    assert result.get("is_3d") is result.get("is_3d", False)


def test_no_match():
    result = TrashMovieParser.parse("random_filename.mkv")
    assert result is None


@mock.patch("workers.parsers.movies.TrashMovieParser.parse", wraps=TrashMovieParser.parse)
def test_fallback_to_minimal_parsing(mock_parse):
    # This should fall through to the fallback parser
    movie_file = Path("Some Random [2022] --Extended Edition-- EAC3.mkv")
    movie = Movie.parse_movie_filename(movie_file.name, TrashMovieParser)

    # Assert the parse method was called
    mock_parse.assert_called_once_with(movie_file.name)

    # Assert the parsed result
    assert movie["title"] == "Some Random"
    assert movie["year"] == "2022"
