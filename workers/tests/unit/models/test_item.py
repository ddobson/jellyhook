import pytest

from workers.models.item import Item


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Science Fiction, Horror, Action", ("Science Fiction", "Horror", "Action")),
        (["Science Fiction", " Horror "], ("Science Fiction", "Horror")),
        ("Comedy, , Drama,", ("Comedy", "Drama")),
        (["Comedy", "", "  "], ("Comedy",)),
        (42, ("42",)),
        ("", ()),
        (None, ()),
    ],
)
def test_list_fields_accept_strings_and_lists(raw, expected):
    item = Item.from_message({"Genres": raw, "Tags": raw})

    assert item.genres == expected
    assert item.tags == expected


def test_missing_list_fields_are_empty():
    item = Item.from_message({})

    assert item.genres == ()
    assert item.tags == ()


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ({"ItemType": "Movie"}, "Movie"),
        ({"Type": "Episode"}, "Episode"),
        ({"ItemType": "Movie", "Type": "Episode"}, "Movie"),
        ({}, None),
    ],
)
def test_item_type_prefers_item_type_then_type(message, expected):
    assert Item.from_message(message).item_type == expected


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ({"ItemType": "Movie"}, True),
        ({"Type": "Movie"}, True),
        ({"ItemType": "Episode"}, False),
        ({"Type": "Audio"}, False),
        # An untyped message is assumed to be a movie, so movie services still try it
        ({}, True),
    ],
)
def test_is_movie(message, expected):
    assert Item.from_message(message).is_movie is expected


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        # 74_787_210_000 ticks is the example payload's 02:04:38.721
        ({"RunTimeTicks": 74_787_210_000}, 124.645_35),
        ({"RuntimeTicks": 600_000_000}, 1.0),
        ({"RunTimeTicks": "600000000"}, None),
        ({}, None),
    ],
)
def test_runtime_minutes_from_ticks(message, expected):
    assert Item.from_message(message).runtime_minutes == pytest.approx(expected)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ({"ProductionYear": 2019, "Year": 2020}, 2019),
        ({"Year": 2020}, 2020),
        ({"Year": "2020"}, 2020),
        ({"Year": "", "PremiereDate": "2021-04-20"}, 2021),
        ({"DateCreated": "2022-01-01T00:00:00Z"}, 2022),
        ({"PremiereDate": "soon"}, None),
        ({}, None),
    ],
)
def test_year_falls_back_through_known_fields(message, expected):
    assert Item.from_message(message).year == expected


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ({"ItemId": "42685592c6a59cf2e88dfe5a6d584b1e"}, "42685592c6a59cf2e88dfe5a6d584b1e"),
        ({"ItemId": ""}, None),
        ({}, None),
    ],
)
def test_item_id_is_none_when_missing(message, expected):
    assert Item.from_message(message).item_id == expected


def test_embedded_item_fields_override_top_level():
    message = {
        "Genres": "Drama",
        "Year": 2001,
        "Item": {"Genres": ["Horror"], "RunTimeTicks": 600_000_000},
    }

    item = Item.from_message(message)

    assert item.genres == ("Horror",)
    assert item.runtime_minutes == 1.0
    assert item.year == 2001
