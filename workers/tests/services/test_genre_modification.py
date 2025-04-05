from unittest import mock

import pytest

from workers.services.genre_modification import GenreModificationService


@pytest.fixture
def mock_movie_standup():
    return mock.MagicMock(
        full_title="John Mulaney: Baby J",
        full_path="/data/media/stand-up/John Mulaney Baby J (2023)/John.Mulaney.Baby.J.2023.2160p.WEBRip.x265.10bit.HDR.DTS-HD.MA.5.1-SWTYBLZ.mkv",
    )


@pytest.fixture
def mock_movie_anime():
    return mock.MagicMock(
        full_title="Your Name",
        full_path="/data/media/anime/Your Name (2016)/Your.Name.2016.2160p.BluRay.x265.10bit.HDR.DTS-HD.MA.5.1-SWTYBLZ.mkv",
    )


@pytest.fixture
def mock_message_standup():
    return {
        "ServerUrl": "jellyfin.server",
        "ItemId": "123456",
        "Name": "John Mulaney: Baby J",
        "ItemType": "Movie",
        "Genres": "Comedy, Documentary",
        "Overview": "The comedian performs a set at Boston's Symphony Hall.",
    }


@pytest.fixture
def mock_message_anime():
    return {
        "ServerUrl": "jellyfin.server",
        "ItemId": "789012",
        "Name": "Your Name",
        "ItemType": "Movie",
        "Genres": "Romance, Drama",
        "Overview": "A teenage boy and girl embark on a quest to meet each other for the first time after they magically swap bodies.",
    }


@pytest.fixture
def genre_config_with_paths():
    return {
        "paths": [
            {"path": "/data/media/stand-up", "genres": ["Stand-Up"], "replace_existing": True},
            {
                "path": "/data/media/anime",
                "genres": ["Anime", "Animation"],
                "replace_existing": False,
            },
        ],
        "rules": [],
    }


@pytest.fixture
def genre_config_with_rules():
    return {
        "paths": [],
        "rules": [
            {
                "match_pattern": ".*concert.*|.*perform.*",
                "match_field": "Overview",
                "case_insensitive": True,
                "genres": ["Live Performance"],
                "replace_existing": False,
            },
            {
                "match_pattern": ".*anime.*",
                "match_field": "Name",
                "case_insensitive": True,
                "genres": ["Anime"],
                "replace_existing": True,
            },
        ],
    }


@mock.patch("workers.services.genre_modification.JELLYFIN_API_KEY", "api_key_123")
@mock.patch("workers.services.genre_modification.requests.post")
@mock.patch("workers.services.genre_modification.GenreModificationService.file_from_message")
@mock.patch("workers.services.genre_modification.Movie.from_file")
def test_genre_modification_service_init_from_message(
    mock_from_file, mock_file_from_message, mock_post, mock_message_standup
):
    # Setup
    file_path = "/data/media/stand-up/John Mulaney Baby J (2023)/John.Mulaney.Baby.J.2023.2160p.WEBRip.x265.10bit.HDR.DTS-HD.MA.5.1-SWTYBLZ.mkv"
    mock_file_from_message.return_value = file_path
    mock_movie = mock.MagicMock()
    mock_movie.full_path = file_path
    mock_movie.full_title = "John Mulaney Baby J (2023)"
    mock_from_file.return_value = mock_movie

    # Execute
    service = GenreModificationService.from_message(mock_message_standup)

    # Assert
    assert service.jellyfin_url == "http://jellyfin.server:8096"
    assert service.api_key == "api_key_123"
    assert service.item_id == "123456"
    assert service.original_genres == ["Comedy", "Documentary"]
    assert service.item_data == mock_message_standup


@mock.patch("workers.services.genre_modification.requests.post")
@mock.patch(
    "workers.services.genre_modification.GENRE_MAPPINGS",
    {
        "paths": [
            {"path": "/data/media/stand-up", "genres": ["Stand-Up"], "replace_existing": True}
        ],
        "rules": [],
    },
)
def test_find_matching_rules_path_match(mock_post, mock_movie_standup):
    # Setup
    service = GenreModificationService(
        mock_movie_standup, "http://jellyfin.server", "api_key_123", "123456"
    )

    # Execute
    service.find_matching_rules()

    # Assert
    assert len(service.matching_rules) == 1
    assert service.matching_rules[0]["genres"] == ["Stand-Up"]
    assert service.matching_rules[0]["replace_existing"] is True


@mock.patch("workers.services.genre_modification.requests.post")
@mock.patch(
    "workers.services.genre_modification.GENRE_MAPPINGS",
    {
        "paths": [
            {"path": "/data/media/stand-up", "genres": ["Stand-Up"], "replace_existing": True}
        ],
        "rules": [],
    },
)
def test_find_matching_rules_path_no_match(mock_post, mock_movie_anime):
    # Setup
    service = GenreModificationService(
        mock_movie_anime, "http://jellyfin.server", "api_key_123", "789012"
    )

    # Execute
    service.find_matching_rules()

    # Assert
    assert len(service.matching_rules) == 0


@mock.patch("workers.services.genre_modification.requests.post")
@mock.patch(
    "workers.services.genre_modification.GENRE_MAPPINGS",
    {
        "paths": [],
        "rules": [
            {
                "match_pattern": ".*perform.*",
                "match_field": "Overview",
                "case_insensitive": True,
                "genres": ["Live Performance"],
                "replace_existing": False,
            }
        ],
    },
)
def test_find_matching_rules_pattern_match(mock_post, mock_movie_standup):
    # Setup
    service = GenreModificationService(
        mock_movie_standup,
        "http://jellyfin.server",
        "api_key_123",
        "123456",
        item_data={"Overview": "The comedian performs a set at Boston's Symphony Hall."},
    )

    # Execute
    service.find_matching_rules()

    # Assert
    assert len(service.matching_rules) == 1
    assert service.matching_rules[0]["genres"] == ["Live Performance"]
    assert service.matching_rules[0]["replace_existing"] is False


@mock.patch("workers.services.genre_modification.requests.post")
@mock.patch(
    "workers.services.genre_modification.GENRE_MAPPINGS",
    {
        "paths": [],
        "rules": [
            {
                "match_pattern": ".*concert.*",
                "match_field": "Overview",
                "case_insensitive": True,
                "genres": ["Live Performance"],
                "replace_existing": False,
            }
        ],
    },
)
def test_find_matching_rules_pattern_no_match(mock_post, mock_movie_standup):
    # Setup
    service = GenreModificationService(
        mock_movie_standup,
        "http://jellyfin.server",
        "api_key_123",
        "123456",
        item_data={"Overview": "The comedian performs a set at Boston's Symphony Hall."},
    )

    # Execute
    service.find_matching_rules()

    # Assert
    assert len(service.matching_rules) == 0


def test_calculate_new_genres_replace():
    # Setup
    service = GenreModificationService(
        None,
        "http://jellyfin.server",
        "api_key_123",
        "123456",
        original_genres=["Comedy", "Documentary"],
    )
    service.matching_rules = [{"genres": ["Stand-Up"], "replace_existing": True}]

    # Execute
    new_genres = service.calculate_new_genres()

    # Assert
    assert new_genres == ["Stand-Up"]


def test_calculate_new_genres_add():
    # Setup
    service = GenreModificationService(
        None,
        "http://jellyfin.server",
        "api_key_123",
        "123456",
        original_genres=["Comedy", "Documentary"],
    )
    service.matching_rules = [{"genres": ["Stand-Up"], "replace_existing": False}]

    # Execute
    new_genres = service.calculate_new_genres()

    # Assert
    assert sorted(new_genres) == sorted(["Comedy", "Documentary", "Stand-Up"])


def test_calculate_new_genres_add_no_duplicates():
    # Setup
    service = GenreModificationService(
        None,
        "http://jellyfin.server",
        "api_key_123",
        "123456",
        original_genres=["Comedy", "Documentary"],
    )
    service.matching_rules = [{"genres": ["Comedy", "Stand-Up"], "replace_existing": False}]

    # Execute
    new_genres = service.calculate_new_genres()

    # Assert
    assert sorted(new_genres) == sorted(["Comedy", "Documentary", "Stand-Up"])


def test_calculate_new_genres_multiple_rules():
    # Setup
    service = GenreModificationService(
        None, "http://jellyfin.server", "api_key_123", "123456", original_genres=["Comedy"]
    )
    service.matching_rules = [
        {"genres": ["Stand-Up"], "replace_existing": False},
        {"genres": ["Live Performance"], "replace_existing": False},
    ]

    # Execute
    new_genres = service.calculate_new_genres()

    # Assert
    assert sorted(new_genres) == sorted(["Comedy", "Stand-Up", "Live Performance"])


def test_calculate_new_genres_replace_then_add():
    # Setup
    service = GenreModificationService(
        None,
        "http://jellyfin.server",
        "api_key_123",
        "123456",
        original_genres=["Comedy", "Documentary"],
    )
    service.matching_rules = [
        {"genres": ["Stand-Up"], "replace_existing": True},
        {"genres": ["Comedy"], "replace_existing": False},
    ]

    # Execute
    new_genres = service.calculate_new_genres()

    # Assert
    assert sorted(new_genres) == sorted(["Stand-Up", "Comedy"])


@mock.patch("workers.services.genre_modification.requests.post")
def test_update_genres_with_changes(mock_post, mock_movie_standup):
    # Setup
    mock_response = mock.MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_post.return_value = mock_response

    service = GenreModificationService(
        mock_movie_standup,
        "http://jellyfin.server",
        "api_key_123",
        "123456",
        original_genres=["Comedy"],
    )
    service.matching_rules = [{"genres": ["Stand-Up"], "replace_existing": True}]

    # Execute
    service.update_genres()

    # Assert
    mock_post.assert_called_once_with(
        "http://jellyfin.server/Items/123456",
        headers={"Authorization": "api_key_123", "Content-Type": "application/json"},
        json={"Genres": ["Stand-Up"]},
    )


@mock.patch("workers.services.genre_modification.requests.post")
def test_update_genres_no_changes_needed(mock_post, mock_movie_standup):
    # Setup
    service = GenreModificationService(
        mock_movie_standup,
        "http://jellyfin.server",
        "api_key_123",
        "123456",
        original_genres=["Stand-Up"],
    )
    service.matching_rules = [{"genres": ["Stand-Up"], "replace_existing": True}]

    # Execute
    service.update_genres()

    # Assert
    mock_post.assert_not_called()


@mock.patch.object(GenreModificationService, "find_matching_rules")
@mock.patch.object(GenreModificationService, "update_genres")
def test_exec_with_matching_rules(mock_update_genres, mock_find_matching_rules, mock_movie_standup):
    # Setup
    mock_find_matching_rules.side_effect = lambda: setattr(
        GenreModificationService, "matching_rules", [{"genres": ["Stand-Up"]}]
    )

    service = GenreModificationService(
        mock_movie_standup, "http://jellyfin.server", "api_key_123", "123456"
    )
    service.matching_rules = [{"genres": ["Stand-Up"]}]

    # Execute
    service.exec()

    # Assert
    mock_find_matching_rules.assert_called_once()
    mock_update_genres.assert_called_once()


@mock.patch.object(GenreModificationService, "find_matching_rules")
@mock.patch.object(GenreModificationService, "update_genres")
def test_exec_without_matching_rules(
    mock_update_genres, mock_find_matching_rules, mock_movie_standup
):
    # Setup
    mock_find_matching_rules.return_value = None

    service = GenreModificationService(
        mock_movie_standup, "http://jellyfin.server", "api_key_123", "123456"
    )
    service.matching_rules = []

    # Execute
    service.exec()

    # Assert
    mock_find_matching_rules.assert_called_once()
    mock_update_genres.assert_not_called()


@mock.patch(
    "workers.services.genre_modification.GENRE_MAPPINGS",
    {
        "paths": [
            {"path": "/data/media/stand-up", "genres": ["Stand-Up"], "replace_existing": True}
        ],
        "rules": [],
    },
)
@mock.patch("workers.services.genre_modification.JELLYFIN_API_KEY", "api_key_123")
@mock.patch("workers.services.genre_modification.requests.post")
@mock.patch("workers.services.genre_modification.GenreModificationService.file_from_message")
@mock.patch("workers.services.genre_modification.Movie.from_file")
def test_end_to_end_standup_path(
    mock_from_file, mock_file_from_message, mock_post, mock_message_standup
):
    # Setup
    file_path = "/data/media/stand-up/John Mulaney Baby J (2023)/John.Mulaney.Baby.J.2023.2160p.WEBRip.x265.10bit.HDR.DTS-HD.MA.5.1-SWTYBLZ.mkv"
    mock_file_from_message.return_value = file_path
    mock_movie = mock.MagicMock()
    mock_movie.full_path = file_path
    mock_movie.full_title = "John Mulaney Baby J (2023)"
    mock_from_file.return_value = mock_movie
    mock_response = mock.MagicMock()
    mock_post.return_value = mock_response

    # Execute
    service = GenreModificationService.from_message(mock_message_standup)
    service.exec()

    # Assert - should match path rule and update genres
    mock_post.assert_called_once_with(
        "http://jellyfin.server:8096/Items/123456",
        headers={"Authorization": "api_key_123", "Content-Type": "application/json"},
        json={"Genres": ["Stand-Up"]},
    )


@mock.patch(
    "workers.services.genre_modification.GENRE_MAPPINGS",
    {
        "paths": [],
        "rules": [
            {
                "match_pattern": ".*perform.*",
                "match_field": "Overview",
                "case_insensitive": True,
                "genres": ["Live Performance"],
                "replace_existing": False,
            }
        ],
    },
)
@mock.patch("workers.services.genre_modification.JELLYFIN_API_KEY", "api_key_123")
@mock.patch("workers.services.genre_modification.requests.post")
@mock.patch("workers.services.genre_modification.GenreModificationService.file_from_message")
@mock.patch("workers.services.genre_modification.Movie.from_file")
def test_end_to_end_pattern_match(
    mock_from_file, mock_file_from_message, mock_post, mock_message_standup
):
    # Setup
    file_path = "/data/media/movies/John Mulaney Baby J (2023)/John.Mulaney.Baby.J.2023.2160p.WEBRip.x265.10bit.HDR.DTS-HD.MA.5.1-SWTYBLZ.mkv"
    mock_file_from_message.return_value = file_path
    mock_movie = mock.MagicMock()
    mock_movie.full_path = file_path
    mock_movie.full_title = "John Mulaney Baby J (2023)"
    mock_from_file.return_value = mock_movie
    mock_response = mock.MagicMock()
    mock_post.return_value = mock_response

    # Execute
    service = GenreModificationService.from_message(mock_message_standup)
    service.exec()

    # Assert - should match pattern rule and add genre without replacing
    mock_post.assert_called_once_with(
        "http://jellyfin.server:8096/Items/123456",
        headers={"Authorization": "api_key_123", "Content-Type": "application/json"},
        json={"Genres": ["Comedy", "Documentary", "Live Performance"]},
    )
