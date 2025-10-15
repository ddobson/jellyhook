"""Tests for the playlist assignment service module."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from workers.services.playlist_assignment import (
    SECONDS_PER_MINUTE,
    TICKS_PER_SECOND,
    PlaylistAssignmentError,
    PlaylistAssignmentService,
)


def _make_client(
    api: object,
    *,
    http: object | None = None,
    base_url: str = "https://jelly.example.com",
) -> SimpleNamespace:
    if http is None:
        http = SimpleNamespace(request=Mock())
    return SimpleNamespace(
        jellyfin=api,
        http=http,
        config=SimpleNamespace(data={"auth.server": base_url, "auth.user_id": "test-user-id"}),
    )


def _make_runtime_ticks(minutes: float) -> float:
    return minutes * SECONDS_PER_MINUTE * TICKS_PER_SECOND


def _build_service(
    *,
    item_id: str = "item123",
    message: dict | None = None,
    rules: list[dict] | None = None,
    api: object | None = None,
    http: object | None = None,
    base_url: str = "https://jelly.example.com",
) -> PlaylistAssignmentService:
    runtime_ticks = _make_runtime_ticks(90)
    default_message = {
        "ItemId": item_id,
        "ItemType": "Movie",
        "RunTimeTicks": runtime_ticks,
        "ProductionYear": 2020,
        "Genres": ["Action", "Adventure"],
        "Tags": ["favorite", "watchlist"],
    }
    message = {**default_message, **(message or {})}

    default_rules = [{"playlist_id": "playlist-1", "conditions": {}}]
    rules = rules if rules is not None else default_rules

    if api is None:
        api = SimpleNamespace(_post=Mock())

    client = _make_client(api, http=http, base_url=base_url)
    return PlaylistAssignmentService(
        item_id=item_id,
        message=message,
        rules=rules,
        jellyfin_client=client,
    )


def test_from_message_populates_service_fields() -> None:
    message = {"ItemId": "abc123"}
    rules = [{"playlist_id": "playlist"}]
    service = PlaylistAssignmentService.from_message(message, {"rules": rules})

    assert service.item_id == "abc123"
    assert service.rules == rules


def test_from_message_requires_rule_list() -> None:
    message = {"ItemId": "abc123"}
    config = {"rules": {"not": "a list"}}

    with pytest.raises(PlaylistAssignmentError):
        PlaylistAssignmentService.from_message(message, config)


def test_exec_without_item_id_raises_error() -> None:
    service = _build_service(item_id="", message={"ItemId": ""})

    with pytest.raises(PlaylistAssignmentError):
        service.exec()


def test_exec_with_no_rules_skips_playlist_calls() -> None:
    service = _build_service(rules=[])

    service.exec()

    service._client.http.request.assert_not_called()  # type: ignore[attr-defined]


def test_exec_ignores_non_mapping_rules() -> None:
    http = SimpleNamespace(request=Mock())
    service = _build_service(rules=["invalid"], http=http)

    service.exec()

    http.request.assert_not_called()


def test_exec_applies_matching_rule_posts_items() -> None:
    api = SimpleNamespace(_post=Mock())
    http = SimpleNamespace(request=Mock())
    rules = [
        {
            "playlist_id": "playlist-123",
            "playlist_name": "Sci-Fi Favorites",
            "conditions": {
                "item_types": ["movie"],
                "min_runtime_minutes": 80,
                "max_runtime_minutes": 110,
                "min_release_year": 2019,
                "max_release_year": 2021,
                "required_genres": ["Action"],
                "required_tags": ["favorite"],
                "excluded_tags": ["skip"],
            },
        }
    ]
    service = _build_service(api=api, http=http, rules=rules)

    service.exec()

    http.request.assert_called_once_with(
        {
            "type": "POST",
            "handler": "Playlists/playlist-123/Items",
            "params": {"Ids": "item123", "UserId": "test-user-id"},
        }
    )


def test_exec_requires_post_helper() -> None:
    service = _build_service(api=SimpleNamespace(), http=SimpleNamespace())

    with pytest.raises(PlaylistAssignmentError):
        service.exec()


def test_exec_raises_playlist_assignment_error_on_http_error() -> None:
    api = SimpleNamespace(_post=Mock())
    http = SimpleNamespace(request=Mock(side_effect=requests.HTTPError("boom")))
    service = _build_service(api=api, http=http)

    with pytest.raises(PlaylistAssignmentError):
        service.exec()


def test_add_to_playlist_without_playlist_id_does_nothing() -> None:
    http = SimpleNamespace(request=Mock())
    service = _build_service(http=http)

    service._add_to_playlist({"conditions": {}})

    http.request.assert_not_called()


def test_load_item_details_fetches_missing_metadata() -> None:
    fetched_details = {
        "RunTimeTicks": _make_runtime_ticks(100),
        "Genres": ["Drama"],
        "Tags": ["award"],
        "ProductionYear": 2021,
    }
    get_item = Mock(return_value=fetched_details)
    api = SimpleNamespace(get_item=get_item)
    message = {
        "ItemId": "item123",
        "RunTimeTicks": None,
        "Genres": [],
        "Tags": [],
        "ProductionYear": None,
    }
    service = _build_service(message=message, api=api)

    details = service._load_item_details()

    assert details["RunTimeTicks"] == fetched_details["RunTimeTicks"]
    assert details["Genres"] == fetched_details["Genres"]
    assert details["Tags"] == fetched_details["Tags"]
    assert details["ProductionYear"] == fetched_details["ProductionYear"]
    get_item.assert_called_once_with("item123")

    service._load_item_details()
    assert get_item.call_count == 1


def test_rule_matches_handles_runtime_and_release_year_bounds() -> None:
    service = _build_service()
    conditions = {
        "min_runtime_minutes": 89,
        "max_runtime_minutes": 91,
        "min_release_year": 2019,
        "max_release_year": 2021,
    }
    rule = {"conditions": conditions}

    assert service._rule_matches(rule, service._load_item_details(), runtime_minutes=None)

    conditions["min_runtime_minutes"] = 200
    assert not service._rule_matches(rule, service._load_item_details(), runtime_minutes=None)

    missing_runtime_details = dict(service._load_item_details())
    missing_runtime_details.pop("RunTimeTicks", None)
    conditions["min_runtime_minutes"] = 10
    assert not service._rule_matches(rule, missing_runtime_details, runtime_minutes=None)


def test_rule_matches_validates_item_type_and_tag_requirements() -> None:
    service = _build_service()
    details = service._load_item_details()
    rule = {
        "conditions": {
            "item_types": ["movie"],
            "required_genres": ["Action"],
            "required_tags": ["favorite"],
            "excluded_tags": ["skip"],
        }
    }

    assert service._rule_matches(rule, details, runtime_minutes=None)

    rule["conditions"]["required_genres"] = ["Nonexistent"]
    assert not service._rule_matches(rule, details, runtime_minutes=None)


def test_rule_matches_rejects_mismatched_item_type() -> None:
    service = _build_service()
    details = service._load_item_details()
    rule = {"conditions": {"item_types": ["episode"]}}

    assert not service._rule_matches(rule, details, runtime_minutes=None)


def test_load_item_details_merges_embedded_payload() -> None:
    embedded = {
        "RunTimeTicks": _make_runtime_ticks(45),
        "Genres": ["Comedy"],
        "Tags": ["laugh"],
        "ProductionYear": 2018,
    }
    message = {
        "Item": embedded,
        "RunTimeTicks": None,
        "Genres": [],
        "Tags": [],
        "ProductionYear": None,
    }
    service = _build_service(message=message)

    details = service._load_item_details()

    assert details["RunTimeTicks"] == embedded["RunTimeTicks"]
    assert details["Genres"] == embedded["Genres"]
    assert details["Tags"] == embedded["Tags"]
    assert details["ProductionYear"] == embedded["ProductionYear"]


def test_enrich_item_details_skips_when_get_item_missing() -> None:
    api = SimpleNamespace()  # no get_item attribute
    message = {
        "RunTimeTicks": None,
        "Genres": [],
        "Tags": [],
        "ProductionYear": None,
    }
    service = _build_service(message=message, api=api)

    details = service._load_item_details()

    assert details["RunTimeTicks"] is None
    assert details["Genres"] == []
    assert details["Tags"] == []


def test_enrich_item_details_handles_fetch_failure() -> None:
    get_item = Mock(side_effect=RuntimeError("boom"))
    api = SimpleNamespace(get_item=get_item)
    message = {
        "RunTimeTicks": None,
        "Genres": [],
        "Tags": [],
        "ProductionYear": None,
    }
    service = _build_service(message=message, api=api)

    details = service._load_item_details()

    assert details["RunTimeTicks"] is None
    assert get_item.call_count == 1


def test_rule_matches_requires_mapping_conditions() -> None:
    service = _build_service()
    details = service._load_item_details()
    rule = {"conditions": "not-a-dict"}

    assert not service._rule_matches(rule, details, runtime_minutes=None)


def test_rule_matches_returns_false_when_release_year_missing() -> None:
    api = SimpleNamespace()
    message = {
        "ProductionYear": None,
        "PremiereDate": None,
        "DateCreated": None,
    }
    service = _build_service(message=message, api=api)
    details = service._load_item_details()
    rule = {"conditions": {"min_release_year": 2000}}

    assert not service._rule_matches(rule, details, runtime_minutes=None)


def test_rule_matches_checks_required_and_excluded_tags() -> None:
    service = _build_service()
    details = service._load_item_details()

    required_rule = {"conditions": {"required_tags": ["missing"]}}
    assert not service._rule_matches(required_rule, details, runtime_minutes=None)

    excluded_rule = {"conditions": {"excluded_tags": ["favorite"]}}
    assert not service._rule_matches(excluded_rule, details, runtime_minutes=None)


def test_evaluate_runtime_enforces_maximum() -> None:
    service = _build_service()

    assert not service._evaluate_runtime({"max_runtime_minutes": 10}, runtime_minutes=15)


def test_evaluate_release_year_bounds() -> None:
    service = _build_service()

    assert not service._evaluate_release_year({"max_release_year": 2010}, 2015)
    assert not service._evaluate_release_year({"min_release_year": 2025}, 2020)


def test_add_to_playlist_wraps_request_exception() -> None:
    http = SimpleNamespace(request=Mock(side_effect=requests.ConnectionError("fail")))
    service = _build_service(http=http)

    with pytest.raises(PlaylistAssignmentError):
        service._add_to_playlist({"playlist_id": "playlist-123"})


def test_add_to_playlist_wraps_unexpected_exception() -> None:
    http = SimpleNamespace(request=Mock(side_effect=RuntimeError("boom")))
    service = _build_service(http=http)

    with pytest.raises(PlaylistAssignmentError):
        service._add_to_playlist({"playlist_id": "playlist-123"})


def test_post_playlist_items_requires_post_helper() -> None:
    service = _build_service(http=SimpleNamespace())

    with pytest.raises(PlaylistAssignmentError):
        service._post_playlist_items("playlist-123")


def test_extract_runtime_minutes_uses_embedded_item_ticks() -> None:
    minutes = 42
    details = {"Item": {"RunTimeTicks": _make_runtime_ticks(minutes)}}

    extracted = PlaylistAssignmentService._extract_runtime_minutes(details)

    assert extracted == pytest.approx(minutes)


def test_extract_release_year_from_premiere_date() -> None:
    details = {"PremiereDate": "2010-05-04T00:00:00Z"}
    invalid_details = {"PremiereDate": "Year"}

    assert PlaylistAssignmentService._extract_release_year(details) == 2010
    assert PlaylistAssignmentService._extract_release_year(invalid_details) is None


def test_to_float_handles_invalid_values() -> None:
    assert PlaylistAssignmentService._to_float("not-a-number") is None


def test_to_int_handles_invalid_values() -> None:
    assert PlaylistAssignmentService._to_int("not-a-number") is None


def test_extract_list_field_normalizes_values() -> None:
    assert PlaylistAssignmentService._extract_list_field({}, "Tags") == []
    assert PlaylistAssignmentService._extract_list_field({"Tags": "A, B"}, "Tags") == ["A", "B"]
    assert PlaylistAssignmentService._extract_list_field({"Tags": 42}, "Tags") == ["42"]


def test_normalize_str_iterable_handles_various_inputs() -> None:
    assert PlaylistAssignmentService._normalize_str_iterable("Movie") == {"movie"}
    assert PlaylistAssignmentService._normalize_str_iterable(123) == {"123"}
