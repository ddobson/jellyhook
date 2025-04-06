from unittest import mock

from workers.services.metadata_update import MetadataUpdateService


@mock.patch("workers.clients.jellyfin.client")
@mock.patch("workers.services.metadata_update.MetadataUpdateService.file_from_message")
@mock.patch("workers.services.metadata_update.Movie.from_file")
def test_metadata_update_service_init_from_message(
    mock_from_file, mock_file_from_message, mock_client, mock_message_standup
):
    # Setup
    file_path = "/data/media/stand-up/Bobby Guy (2023)/Bobby.Guy.2023.2160p.WEBRip.x265.10bit.HDR.DTS-HD.MA.5.1-SWTYBLZ.mkv"
    mock_file_from_message.return_value = file_path
    mock_movie = mock.MagicMock()
    mock_movie.full_path = file_path
    mock_movie.full_title = "Bobby Guy (2023)"
    mock_from_file.return_value = mock_movie

    # Execute
    service = MetadataUpdateService.from_message(mock_message_standup)

    # Assert
    assert service.item_id == "123456"
    assert service.original_genres == ["Comedy", "Documentary"]
    assert service.original_tags == ["Netflix", "Special"]
    assert service.item_data == mock_message_standup


@mock.patch("workers.clients.jellyfin.client")
@mock.patch(
    "workers.services.metadata_update.METADATA_RULES",
    {
        "paths": [
            {
                "path": "/data/media/stand-up",
                "genres": {"new_genres": ["Stand-Up"], "replace_existing": True},
            }
        ],
        "rules": [],
    },
)
def test_find_matching_rules_path_match(mock_client, mock_movie_standup):
    # Setup
    service = MetadataUpdateService(
        movie=mock_movie_standup,
        item_id="123456",
    )

    # Execute
    service.find_matching_rules()

    # Assert
    assert len(service.matching_rules) == 1
    assert service.matching_rules[0]["genres"]["new_genres"] == ["Stand-Up"]
    assert service.matching_rules[0]["genres"]["replace_existing"] is True


@mock.patch("workers.clients.jellyfin.client")
@mock.patch(
    "workers.services.metadata_update.METADATA_RULES",
    {
        "paths": [
            {
                "path": "/data/media/stand-up",
                "genres": {"new_genres": ["Stand-Up"], "replace_existing": True},
            }
        ],
        "rules": [],
    },
)
def test_find_matching_rules_path_no_match(mock_client, mock_movie_anime):
    # Setup
    service = MetadataUpdateService(
        movie=mock_movie_anime,
        item_id="789012",
    )

    # Execute
    service.find_matching_rules()

    # Assert
    assert len(service.matching_rules) == 0


@mock.patch("workers.clients.jellyfin.client")
@mock.patch(
    "workers.services.metadata_update.METADATA_RULES",
    {
        "paths": [],
        "rules": [
            {
                "match_pattern": ".*perform.*",
                "match_field": "Overview",
                "case_insensitive": True,
                "genres": {"new_genres": ["Live Performance"], "replace_existing": False},
            }
        ],
    },
)
def test_find_matching_rules_pattern_match(mock_client, mock_movie_standup):
    # Setup
    service = MetadataUpdateService(
        movie=mock_movie_standup,
        item_id="123456",
        item_data={"Overview": "The comedian performs a set at Boston's Symphony Hall."},
    )

    # Execute
    service.find_matching_rules()

    # Assert
    assert len(service.matching_rules) == 1
    assert service.matching_rules[0]["genres"]["new_genres"] == ["Live Performance"]
    assert service.matching_rules[0]["genres"]["replace_existing"] is False


@mock.patch("workers.clients.jellyfin.client")
@mock.patch(
    "workers.services.metadata_update.METADATA_RULES",
    {
        "paths": [],
        "rules": [
            {
                "match_pattern": ".*concert.*",
                "match_field": "Overview",
                "case_insensitive": True,
                "genres": {"new_genres": ["Live Performance"], "replace_existing": False},
            }
        ],
    },
)
def test_find_matching_rules_pattern_no_match(mock_client, mock_movie_standup):
    # Setup
    service = MetadataUpdateService(
        movie=mock_movie_standup,
        item_id="123456",
        item_data={"Overview": "The comedian performs a set at Boston's Symphony Hall."},
    )

    # Execute
    service.find_matching_rules()

    # Assert
    assert len(service.matching_rules) == 0


def test_calculate_new_genres_replace():
    # Setup
    service = MetadataUpdateService(
        movie=None,
        item_id="123456",
        original_genres=["Comedy", "Documentary"],
    )
    service.matching_rules = [{"genres": {"new_genres": ["Stand-Up"], "replace_existing": True}}]

    # Execute
    new_genres = service.calculate_new_genres()

    # Assert
    assert new_genres == ["Stand-Up"]


def test_calculate_new_genres_add():
    # Setup
    service = MetadataUpdateService(
        movie=None,
        item_id="123456",
        original_genres=["Comedy", "Documentary"],
    )
    service.matching_rules = [{"genres": {"new_genres": ["Stand-Up"], "replace_existing": False}}]

    # Execute
    new_genres = service.calculate_new_genres()

    # Assert
    assert sorted(new_genres) == sorted(["Comedy", "Documentary", "Stand-Up"])


def test_calculate_new_genres_add_no_duplicates():
    # Setup
    service = MetadataUpdateService(
        movie=None,
        item_id="123456",
        original_genres=["Comedy", "Documentary"],
    )
    service.matching_rules = [
        {"genres": {"new_genres": ["Comedy", "Stand-Up"], "replace_existing": False}}
    ]

    # Execute
    new_genres = service.calculate_new_genres()

    # Assert
    assert sorted(new_genres) == sorted(["Comedy", "Documentary", "Stand-Up"])


def test_calculate_new_genres_multiple_rules():
    # Setup
    service = MetadataUpdateService(movie=None, item_id="123456", original_genres=["Comedy"])
    service.matching_rules = [
        {"genres": {"new_genres": ["Stand-Up"], "replace_existing": False}},
        {"genres": {"new_genres": ["Live Performance"], "replace_existing": False}},
    ]

    # Execute
    new_genres = service.calculate_new_genres()

    # Assert
    assert sorted(new_genres) == sorted(["Comedy", "Stand-Up", "Live Performance"])


def test_calculate_new_genres_replace_then_add():
    # Setup
    service = MetadataUpdateService(
        movie=None,
        item_id="123456",
        original_genres=["Comedy", "Documentary"],
    )
    service.matching_rules = [
        {"genres": {"new_genres": ["Stand-Up"], "replace_existing": True}},
        {"genres": {"new_genres": ["Comedy"], "replace_existing": False}},
    ]

    # Execute
    new_genres = service.calculate_new_genres()

    # Assert
    assert sorted(new_genres) == sorted(["Stand-Up", "Comedy"])


def test_calculate_new_tags_replace():
    # Setup
    service = MetadataUpdateService(
        movie=None,
        item_id="123456",
        original_tags=["Netflix", "Special"],
    )
    service.matching_rules = [{"tags": {"new_tags": ["Comedy Special"], "replace_existing": True}}]

    # Execute
    new_tags = service.calculate_new_tags()

    # Assert
    assert new_tags == ["Comedy Special"]


def test_calculate_new_tags_add():
    # Setup
    service = MetadataUpdateService(
        movie=None,
        item_id="123456",
        original_tags=["Netflix", "Special"],
    )
    service.matching_rules = [{"tags": {"new_tags": ["Comedy Special"], "replace_existing": False}}]

    # Execute
    new_tags = service.calculate_new_tags()

    # Assert
    assert sorted(new_tags) == sorted(["Netflix", "Special", "Comedy Special"])


@mock.patch("workers.clients.jellyfin.client.jellyfin")
def test_update_metadata_with_genre_changes(mock_client, mock_movie_standup):
    # Setup
    service = MetadataUpdateService(
        movie=mock_movie_standup,
        item_id="123456",
        original_genres=["Comedy"],
    )
    service.matching_rules = [{"genres": {"new_genres": ["Stand-Up"], "replace_existing": True}}]

    # Execute
    service.update_metadata()

    # Assert
    mock_client.update_item.assert_called_once_with(
        "123456",
        {"Genres": ["Stand-Up"]},
    )


@mock.patch("workers.clients.jellyfin.client.jellyfin")
def test_update_metadata_with_tag_changes(mock_client, mock_movie_standup):
    # Setup
    service = MetadataUpdateService(
        movie=mock_movie_standup,
        item_id="123456",
        original_tags=["Netflix"],
    )
    service.matching_rules = [{"tags": {"new_tags": ["Comedy Special"], "replace_existing": True}}]

    # Execute
    service.update_metadata()

    # Assert
    mock_client.update_item.assert_called_once_with(
        "123456",
        {"Tags": ["Comedy Special"]},
    )


@mock.patch("workers.clients.jellyfin.client.jellyfin")
def test_update_metadata_with_both_changes(mock_client, mock_movie_standup):
    # Setup
    service = MetadataUpdateService(
        movie=mock_movie_standup,
        item_id="123456",
        original_genres=["Comedy"],
        original_tags=["Netflix"],
    )
    service.matching_rules = [
        {
            "genres": {"new_genres": ["Stand-Up"], "replace_existing": True},
            "tags": {"new_tags": ["Comedy Special"], "replace_existing": True},
        }
    ]

    # Execute
    service.update_metadata()

    # Assert
    mock_client.update_item.assert_called_once_with(
        "123456",
        {"Genres": ["Stand-Up"], "Tags": ["Comedy Special"]},
    )


@mock.patch("workers.clients.jellyfin.client.jellyfin")
def test_update_metadata_no_changes_needed(mock_client, mock_movie_standup):
    # Setup
    service = MetadataUpdateService(
        movie=mock_movie_standup,
        item_id="123456",
        original_genres=["Stand-Up"],
        original_tags=["Comedy Special"],
    )
    service.matching_rules = [
        {
            "genres": {"new_genres": ["Stand-Up"], "replace_existing": True},
            "tags": {"new_tags": ["Comedy Special"], "replace_existing": True},
        }
    ]

    # Execute
    service.update_metadata()

    # Assert
    mock_client.update_item.assert_not_called()


@mock.patch.object(MetadataUpdateService, "find_matching_rules")
@mock.patch.object(MetadataUpdateService, "update_metadata")
def test_exec_with_matching_rules(
    mock_update_metadata, mock_find_matching_rules, mock_movie_standup
):
    # Setup
    mock_find_matching_rules.side_effect = lambda: setattr(
        MetadataUpdateService, "matching_rules", [{"genres": {"new_genres": ["Stand-Up"]}}]
    )

    service = MetadataUpdateService(movie=mock_movie_standup, item_id="123456")
    service.matching_rules = [{"genres": {"new_genres": ["Stand-Up"]}}]

    # Execute
    service.exec()

    # Assert
    mock_find_matching_rules.assert_called_once()
    mock_update_metadata.assert_called_once()


@mock.patch.object(MetadataUpdateService, "find_matching_rules")
@mock.patch.object(MetadataUpdateService, "update_metadata")
def test_exec_without_matching_rules(
    mock_update_metadata, mock_find_matching_rules, mock_movie_standup
):
    # Setup
    mock_find_matching_rules.return_value = None

    service = MetadataUpdateService(movie=mock_movie_standup, item_id="123456")
    service.matching_rules = []

    # Execute
    service.exec()

    # Assert
    mock_find_matching_rules.assert_called_once()
    mock_update_metadata.assert_not_called()


@mock.patch(
    "workers.services.metadata_update.METADATA_RULES",
    {
        "paths": [
            {
                "path": "/data/media/stand-up",
                "genres": {"new_genres": ["Stand-Up"], "replace_existing": True},
            }
        ],
        "rules": [],
    },
)
@mock.patch("workers.clients.jellyfin.client.jellyfin")
@mock.patch("workers.services.metadata_update.MetadataUpdateService.file_from_message")
@mock.patch("workers.services.metadata_update.Movie.from_file")
def test_end_to_end_standup_path(
    mock_from_file, mock_file_from_message, mock_client, mock_message_standup
):
    # Setup
    file_path = "/data/media/stand-up/Bobby Guy (2023)/Bobby.Guy.2023.2160p.WEBRip.x265.10bit.HDR.DTS-HD.MA.5.1-SWTYBLZ.mkv"
    mock_file_from_message.return_value = file_path
    mock_movie = mock.MagicMock()
    mock_movie.full_path = file_path
    mock_movie.full_title = "Bobby Guy (2023)"
    mock_from_file.return_value = mock_movie

    # Execute
    service = MetadataUpdateService.from_message(mock_message_standup)
    service.exec()

    # Assert - should match path rule and update genres
    mock_client.update_item.assert_called_once_with(
        "123456",
        {"Genres": ["Stand-Up"]},
    )


@mock.patch(
    "workers.services.metadata_update.METADATA_RULES",
    {
        "paths": [],
        "rules": [
            {
                "match_pattern": ".*perform.*",
                "match_field": "Overview",
                "case_insensitive": True,
                "genres": {"new_genres": ["Live Performance"], "replace_existing": False},
            }
        ],
    },
)
@mock.patch("workers.clients.jellyfin.client.jellyfin")
@mock.patch("workers.services.metadata_update.MetadataUpdateService.file_from_message")
@mock.patch("workers.services.metadata_update.Movie.from_file")
def test_end_to_end_pattern_match(
    mock_from_file, mock_file_from_message, mock_client, mock_message_standup
):
    # Setup
    file_path = "/data/media/movies/Bobby Guy (2023)/Bobby.Guy.2023.2160p.WEBRip.x265.10bit.HDR.DTS-HD.MA.5.1-SWTYBLZ.mkv"
    mock_file_from_message.return_value = file_path
    mock_movie = mock.MagicMock()
    mock_movie.full_path = file_path
    mock_movie.full_title = "Bobby Guy (2023)"
    mock_from_file.return_value = mock_movie

    # Execute
    service = MetadataUpdateService.from_message(mock_message_standup)
    service.exec()

    # Assert - should match pattern rule and add genre without replacing
    mock_client.update_item.assert_called_once_with(
        "123456",
        {"Genres": ["Comedy", "Documentary", "Live Performance"]},
    )
