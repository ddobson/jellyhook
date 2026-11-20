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
