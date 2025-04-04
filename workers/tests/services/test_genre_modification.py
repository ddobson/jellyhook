from unittest import mock

import pytest

from workers.movie import Movie
from workers.services.genre_modification import GenreModificationService


@pytest.fixture
def mock_movie():
    return mock.MagicMock(
        full_title="John Mulaney: Baby J",
        full_path="/data/media/stand-up/John Mulaney Baby J (2023)/John.Mulaney.Baby.J.2023.2160p.WEBRip.x265.10bit.HDR.DTS-HD.MA.5.1-SWTYBLZ.mkv",
    )


@pytest.fixture
def mock_message():
    return {
        "ServerUrl": "jellyfin.server",
        "ItemId": "123456",
        "Name": "John Mulaney: Baby J",
        "ItemType": "Movie",
        "Genres": "Comedy, Documentary",
    }


@mock.patch("workers.services.genre_modification.requests.post")
@mock.patch("workers.services.genre_modification.JELLYFIN_API_KEY", "api_key_123")
@mock.patch("workers.services.genre_modification.utils.file_from_message")
@mock.patch("workers.services.genre_modification.Movie.from_file")
def test_genre_modification_service_init_from_message(
    mock_from_file, mock_file_from_message, mock_post, mock_message
):
    # Setup
    mock_file_from_message.return_value = "/data/media/stand-up/John Mulaney Baby J (2023)/John.Mulaney.Baby.J.2023.2160p.WEBRip.x265.10bit.HDR.DTS-HD.MA.5.1-SWTYBLZ.mkv"
    mock_from_file.return_value = Movie(
        "John Mulaney Baby J",
        "/data/media/stand-up/John Mulaney Baby J (2023)",
        "John.Mulaney.Baby.J.2023.2160p.WEBRip.x265.10bit.HDR.DTS-HD.MA.5.1-SWTYBLZ.mkv",
    )

    # Execute
    service = GenreModificationService.from_message(mock_message)

    # Assert
    assert service.jellyfin_url == "http://jellyfin.server:8096"
    assert service.api_key == "api_key_123"
    assert service.item_id == "123456"


@mock.patch("workers.services.genre_modification.requests.post")
@mock.patch("workers.services.genre_modification.STANDUP_PATH", "/data/media/stand-up")
def test_is_standup_file_true(mock_post, mock_movie):
    # Setup
    service = GenreModificationService(
        mock_movie, "http://jellyfin.server", "api_key_123", "123456"
    )

    # Execute
    result = service.is_standup_file()

    # Assert
    assert result is True


@mock.patch("workers.services.genre_modification.requests.post")
@mock.patch("workers.services.genre_modification.STANDUP_PATH", "/data/media/stand-up")
def test_is_standup_file_false(mock_post):
    # Setup
    mock_movie = mock.MagicMock(
        full_title="Godzilla Minus One",
        full_path="/data/media/movies/Godzilla Minus One (2023)/Godzilla.Minus.One.2023.2160p.WEBRip.x265.10bit.HDR.DTS-HD.MA.5.1-SWTYBLZ.mkv",
    )
    service = GenreModificationService(
        mock_movie, "http://jellyfin.server", "api_key_123", "123456"
    )

    # Execute
    result = service.is_standup_file()

    # Assert
    assert result is False


@mock.patch("workers.services.genre_modification.requests.post")
@mock.patch("workers.services.genre_modification.STANDUP_PATH", "/data/media/stand-up")
def test_update_genres(mock_post, mock_movie):
    # Setup
    mock_response = mock.MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_post.return_value = mock_response

    service = GenreModificationService(
        mock_movie, "http://jellyfin.server", "api_key_123", "123456"
    )

    # Execute
    service.update_genres()

    # Assert
    mock_post.assert_called_once_with(
        "http://jellyfin.server/Items/123456",
        headers={"X-Emby-Token": "api_key_123", "Content-Type": "application/json"},
        json={"Genres": ["Stand-Up"]},
    )


@mock.patch.object(GenreModificationService, "is_standup_file")
@mock.patch.object(GenreModificationService, "update_genres")
def test_exec_for_standup_file(mock_update_genres, mock_is_standup_file, mock_movie):
    # Setup
    mock_is_standup_file.return_value = True

    service = GenreModificationService(
        mock_movie, "http://jellyfin.server", "api_key_123", "123456"
    )

    # Execute
    service.exec()

    # Assert
    mock_update_genres.assert_called_once()


@mock.patch.object(GenreModificationService, "is_standup_file")
@mock.patch.object(GenreModificationService, "update_genres")
def test_exec_for_non_standup_file(mock_update_genres, mock_is_standup_file, mock_movie):
    # Setup
    mock_is_standup_file.return_value = False

    service = GenreModificationService(
        mock_movie, "http://jellyfin.server", "api_key_123", "123456"
    )

    # Execute
    service.exec()

    # Assert
    mock_update_genres.assert_not_called()
