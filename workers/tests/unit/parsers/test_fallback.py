from pathlib import Path

import pytest

from workers.models.items import Movie
from workers.parsers.movies import FallbackMovieParser


@pytest.mark.parametrize(
    "filename, expected",
    [
        # Test case for simple naming patterns with year
        (
            "Movie.Title.2020.mkv",
            {
                "title": "Movie Title",
                "year": "2020",
            },
        ),
        # Test case for filename with parentheses around year
        (
            "Movie Title (2018).mp4",
            {
                "title": "Movie Title",
                "year": "2018",
            },
        ),
        # Test case for filename with brackets around year
        (
            "Movie_Title_[2019].mkv",
            {
                "title": "Movie_Title_",
                "year": "2019",
            },
        ),
        # Test case for filename with random text after year
        (
            "Movie.Title.2021.Random.Extra.Text.mp4",
            {
                "title": "Movie Title",
                "year": "2021",
            },
        ),
        # Test case for preserving underscores
        (
            "Movie_with_underscores.2022.mkv",
            {
                "title": "Movie_with_underscores",
                "year": "2022",
            },
        ),
        # Test case for no year - should use filename without extension
        (
            "Just A Movie Name.mkv",
            {
                "title": "Just A Movie Name",
                "year": "",
            },
        ),
    ],
)
def test_fallback_format(filename, expected):
    """Test the fallback parser with various filename formats."""
    result = FallbackMovieParser.parse(filename)
    assert result.get("title") == expected.get("title")
    assert result.get("year") == expected.get("year")


def test_fallback_integration_with_movie():
    """Test that the fallback parser works correctly with the Movie class."""
    movie_file = Path("Some.Weird.Movie.2022.mkv")
    movie = Movie.from_file(movie_file)

    assert movie.title == "Some Weird Movie"
    assert movie.year == "2022"
    assert movie.full_title == "Some Weird Movie (2022)"


def test_fallback_edge_cases():
    """Test edge cases for the fallback parser."""
    # Multiple years in filename - should pick the first one
    result = FallbackMovieParser.parse("Movie.2020.From.2019.mkv")
    assert result.get("title") == "Movie"
    assert result.get("year") == "2020"

    # Year at start of filename
    result = FallbackMovieParser.parse("2018.Movie.Title.mkv")
    assert result.get("title") == ""
    assert result.get("year") == "2018"

    # Special characters in filename
    result = FallbackMovieParser.parse("Special-@#$-Characters.2023.mkv")
    assert result.get("title") == "Special-@#$-Characters"
    assert result.get("year") == "2023"

    # Empty filename
    result = FallbackMovieParser.parse("")
    assert result.get("title") == ""
    assert result.get("year") == ""
