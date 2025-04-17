from pathlib import Path
from unittest import mock

import pytest

from workers.models.items import Movie
from workers.parsers.movies import StandardMovieParser


@pytest.mark.parametrize(
    "filename, expected",
    [
        # Test case for a standard BluRay release with DTS audio
        (
            "Test.Movie.1999.1080p.BluRay.x264.DTS-FGT.mkv",
            {
                "title": "Test Movie",
                "year": "1999",
                "quality": "1080p",
                "audio": "DTS",
                "video_codec": "x264",
                "dynamic_range": None,
                "release_group": "FGT",
                "tmdb_id": None,
                "imdb_id": None,
                "is_3d": False,
            },
        ),
        # Test case for an HDRip release with AAC audio
        (
            "The.Movie.Title.2010.720p.HDRip.x264.AAC-ETRG.mp4",
            {
                "title": "The Movie Title",
                "year": "2010",
                "quality": "720p",
                "audio": "AAC",
                "video_codec": "x264",
                "dynamic_range": None,
                "release_group": "ETRG",
                "tmdb_id": None,
                "imdb_id": None,
                "is_3d": False,
            },
        ),
        # Test case for a 4K HDR10 release with DTS-HD audio
        (
            "Test.Movie.2009.2160p.HDR10.x265.DTS-HD-FGT.mkv",
            {
                "title": "Test Movie",
                "year": "2009",
                "quality": "2160p",
                "audio": "DTS-HD",
                "video_codec": "x265",
                "dynamic_range": "HDR10",
                "release_group": "FGT",
                "tmdb_id": None,
                "imdb_id": None,
                "is_3d": False,
            },
        ),
        # Test case for a BluRay release with an IMDB ID
        (
            "The.Movie.Title.1994.1080p.BluRay.x264.DTS.tt0110357.mkv",
            {
                "title": "The Movie Title",
                "year": "1994",
                "quality": "1080p",
                "audio": "DTS",
                "video_codec": "x264",
                "dynamic_range": None,
                "release_group": None,
                "tmdb_id": None,
                "imdb_id": "tt0110357",
                "is_3d": False,
            },
        ),
        # Test case for a 4K HDR release with a TMDB ID
        (
            "Test.Movie.2014.4K.HDR.x265.TrueHD-tmdb:157336.mkv",
            {
                "title": "Test Movie",
                "year": "2014",
                "quality": "4K",
                "audio": "TrueHD",
                "video_codec": "x265",
                "dynamic_range": "HDR",
                "release_group": None,
                "tmdb_id": "157336",
                "imdb_id": None,
                "is_3d": False,
            },
        ),
    ],
)
def test_standard_format(filename, expected):
    result = StandardMovieParser.parse(filename)
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
    result = StandardMovieParser.parse("random_filename.mkv")
    assert result is None


@mock.patch("workers.parsers.movies.StandardMovieParser.parse", wraps=StandardMovieParser.parse)
def test_fallback_to_minimal_parsing(mock_parse):
    # This should fall through to the fallback parser
    movie_file = Path("Some.Random.2022.File.mkv")
    movie = Movie.parse_movie_filename(movie_file.name, StandardMovieParser)

    # Assert the parse method was called
    mock_parse.assert_called_once_with(movie_file.name)

    # Assert the parsed result
    assert movie["title"] == "Some Random"
    assert movie["year"] == "2022"
