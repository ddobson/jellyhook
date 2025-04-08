from unittest import mock

import pytest


@pytest.fixture
def media_dir(tmpdir):
    """Mocks a media directory."""
    # Create movie directory structure
    movie_dir = tmpdir.mkdir("movies")
    movie_folder = movie_dir.mkdir("Test Movie (2023)")
    standup_dir = tmpdir.mkdir("standup")
    standup_folder = standup_dir.mkdir("Comedy Special (2022)")
    tmp_path = tmpdir.mkdir("tmp")

    # Create movie files
    movie_file = movie_folder.join("Test Movie.mkv")
    movie_file.write("movie content")

    standup_file = standup_folder.join("Comedy Special.mp4")
    standup_file.write("standup content")

    # Setup environment
    return {
        "movie_path": str(movie_dir),
        "standup_path": str(standup_dir),
        "movie_file": str(movie_file),
        "standup_file": str(standup_file),
        "tmp_path": str(tmp_path),
    }


@pytest.fixture
def mock_movie_standup():
    return mock.MagicMock(
        full_title="John Mulaney: Baby J",
        full_path="/data/media/stand-up/Bobby Guy (2023)/Bobby.Guy.2023.2160p.WEBRip.x265.10bit.HDR.DTS-HD.MA.5.1-SWTYBLZ.mkv",
    )


@pytest.fixture
def mock_movie_anime():
    return mock.MagicMock(
        full_title="Your Name",
        full_path="/data/media/anime/Sumo Pizza (2016)/Your.Name.2016.2160p.BluRay.x265.10bit.HDR.DTS-HD.MA.5.1-SWTYBLZ.mkv",
    )


@pytest.fixture
def mock_message_standup():
    return {
        "ServerUrl": "jellyfin.server",
        "ItemId": "123456",
        "Name": "Bobby Guy",
        "Year": 2023,
        "ItemType": "Movie",
        "Genres": "Comedy, Documentary",
        "Tags": "Netflix, Special",
        "Overview": "The comedian performs a set at Boston's Symphony Hall.",
    }


@pytest.fixture
def mock_message_anime():
    return {
        "ServerUrl": "jellyfin.server",
        "ItemId": "789012",
        "Name": "Sumo Pizza",
        "Year": 2016,
        "ItemType": "Movie",
        "Genres": "Comedy, Drama",
        "Tags": "Foreign, Subtitled",
        "Overview": "Two friends navigate the world of sumo wrestling and pizza delivery.",
    }
