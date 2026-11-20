import pytest

from workers.models.item import Item


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Science Fiction, Horror, Action", ("Science Fiction", "Horror", "Action")),
        (["Science Fiction", " Horror "], ("Science Fiction", "Horror")),
        ("Comedy, , Drama,", ("Comedy", "Drama")),
        (["Comedy", "", "  "], ("Comedy",)),
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
