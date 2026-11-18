from datetime import datetime

import pytest

from workers.config.random_playlists import (
    RandomPlaylist,
    RandomPlaylistConfigError,
    Schedule,
    SeriesById,
    SeriesByTitle,
    parse_random_playlists,
)


def test_valid_entry_loads_with_defaults():
    """Only the required keys given: include_specials and unplayed_only default to False."""
    playlists = parse_random_playlists(
        [
            {
                "name": "Bob's Burgers Random",
                "series": ["Bob's Burgers"],
                "size": 25,
                "schedule": "0 1 * * 0",
            }
        ]
    )

    assert playlists == (
        RandomPlaylist(
            name="Bob's Burgers Random",
            series=(SeriesByTitle("Bob's Burgers"),),
            size=25,
            schedule=Schedule("0 1 * * 0"),
            include_specials=False,
            unplayed_only=False,
        ),
    )


def test_series_by_id_and_by_title_and_explicit_flags():
    """A series can be a title or `{id: ...}`, mixed in one list; flags are taken as given."""
    (playlist,) = parse_random_playlists(
        [
            {
                "name": "Cartoon Night",
                "series": ["Futurama", {"id": "f1e2d3c4"}],
                "size": 10,
                "schedule": "30 18 * * 5",
                "include_specials": True,
                "unplayed_only": True,
            }
        ]
    )

    assert playlist.series == (SeriesByTitle("Futurama"), SeriesById("f1e2d3c4"))
    assert playlist.include_specials is True
    assert playlist.unplayed_only is True


def entry(**overrides):
    """A valid Random Playlist entry named "Shuffle", with `overrides` applied.

    An override of `MISSING` removes the key.
    """
    base = {"name": "Shuffle", "series": ["Bob's Burgers"], "size": 25, "schedule": "0 1 * * 0"}
    base.update(overrides)
    return {key: value for key, value in base.items() if value is not MISSING}


MISSING = object()


@pytest.mark.parametrize(
    "schedule",
    ["not a cron", "0 1 * *", "0 1 * * 0 30", "61 * * * *", "0 0 30 2 *", "@weekly", "", 7],
)
def test_unparseable_schedule_is_rejected(schedule):
    """Anything but a valid standard 5-field cron expression fails, naming the playlist."""
    with pytest.raises(RandomPlaylistConfigError, match=r"'Shuffle'.*schedule"):
        parse_random_playlists([entry(schedule=schedule)])


@pytest.mark.parametrize("size", [0, -3, "25", 2.5, True, None])
def test_size_must_be_an_integer_of_at_least_one(size):
    """A size below 1, or one that is not a whole number, fails, naming the playlist."""
    with pytest.raises(RandomPlaylistConfigError, match=r"'Shuffle'.*size"):
        parse_random_playlists([entry(size=size)])


@pytest.mark.parametrize("series", [[], None, "Bob's Burgers", {"id": "abc"}])
def test_series_must_be_a_non_empty_list(series):
    """An empty `series`, or one that is not a list, fails, naming the playlist."""
    with pytest.raises(RandomPlaylistConfigError, match=r"'Shuffle'.*series"):
        parse_random_playlists([entry(series=series)])


@pytest.mark.parametrize(
    "element",
    [
        42,
        None,
        "",
        "   ",
        ["Bob's Burgers"],
        {},
        {"title": "Bob's Burgers"},
        {"id": ""},
        {"id": 12345},
        {"id": "abc", "title": "Bob's Burgers"},
    ],
)
def test_series_elements_must_be_a_title_or_an_id(element):
    """Each element is a non-empty title string or exactly `{id: <series id>}`."""
    with pytest.raises(RandomPlaylistConfigError, match=r"'Shuffle'.*series"):
        parse_random_playlists([entry(series=["Futurama", element])])


def test_duplicate_names_are_rejected():
    """The name is the playlist's identity, so two entries may not share one."""
    section = [entry(), entry(name="Other"), entry(series=["Futurama"])]

    with pytest.raises(RandomPlaylistConfigError, match=r"'Shuffle'.*more than once"):
        parse_random_playlists(section)


@pytest.mark.parametrize("key", ["series", "size", "schedule"])
def test_required_keys_must_be_present(key):
    """A missing required key fails, naming the playlist and the key."""
    with pytest.raises(RandomPlaylistConfigError, match=rf"'Shuffle'.*{key}"):
        parse_random_playlists([entry(**{key: MISSING})])


@pytest.mark.parametrize("name", [MISSING, "", "  ", 42, None])
def test_name_must_be_a_non_empty_string(name):
    """Without a usable name the error points at the entry's position in the list."""
    with pytest.raises(RandomPlaylistConfigError, match=r"random_playlists\[1\].*name"):
        parse_random_playlists([entry(name="First"), entry(name=name)])


@pytest.mark.parametrize("key", ["include_specials", "unplayed_only"])
@pytest.mark.parametrize("value", ["no", 0, 1, None])
def test_flags_must_be_true_or_false(key, value):
    """`include_specials` and `unplayed_only` take only true or false."""
    with pytest.raises(RandomPlaylistConfigError, match=rf"'Shuffle'.*{key}"):
        parse_random_playlists([entry(**{key: value})])


def test_unknown_keys_are_rejected():
    """A misspelt key would otherwise be ignored silently, leaving the default in force."""
    with pytest.raises(RandomPlaylistConfigError, match=r"'Shuffle'.*unplayed"):
        parse_random_playlists([entry(unplayed=True)])


@pytest.mark.parametrize("bad_entry", ["Shuffle", ["Shuffle"], None])
def test_entries_must_be_mappings(bad_entry):
    """Each entry is a mapping of settings; anything else is rejected by its position."""
    with pytest.raises(RandomPlaylistConfigError, match=r"random_playlists\[0\]"):
        parse_random_playlists([bad_entry])


@pytest.mark.parametrize("section", [{"name": "Shuffle"}, "Shuffle", 3])
def test_section_must_be_a_list(section):
    """`random_playlists:` is a list of entries, even when there is only one."""
    with pytest.raises(RandomPlaylistConfigError, match=r"random_playlists.*list"):
        parse_random_playlists(section)


def test_no_section_means_no_random_playlists():
    """A config without `random_playlists:` (or with it left empty) defines none."""
    assert parse_random_playlists(None) == ()
    assert parse_random_playlists([]) == ()


@pytest.mark.parametrize(
    ("after", "expected"),
    [
        (datetime(2026, 11, 14, 23, 0), datetime(2026, 11, 15, 1, 0)),
        (datetime(2026, 11, 15, 0, 59, 59, 999999), datetime(2026, 11, 15, 1, 0)),
        # Strictly after: a firing at `after` itself is not the next one
        (datetime(2026, 11, 15, 1, 0), datetime(2026, 11, 22, 1, 0)),
        (datetime(2026, 11, 15, 1, 0, 30), datetime(2026, 11, 22, 1, 0)),
    ],
)
def test_next_firing_is_the_first_firing_strictly_after(after, expected):
    """`0 1 * * 0` is Sundays at 1am; 2026-11-15 is a Sunday."""
    assert Schedule("0 1 * * 0").next_firing(after) == expected


@pytest.mark.parametrize(
    ("at_or_before", "expected"),
    [
        (datetime(2026, 11, 15, 0, 59, 59, 999999), datetime(2026, 11, 8, 1, 0)),
        # Inclusive: a firing at `at_or_before` itself counts
        (datetime(2026, 11, 15, 1, 0), datetime(2026, 11, 15, 1, 0)),
        (datetime(2026, 11, 15, 1, 0, 59), datetime(2026, 11, 15, 1, 0)),
        (datetime(2026, 11, 21, 12, 0), datetime(2026, 11, 15, 1, 0)),
    ],
)
def test_latest_firing_is_the_last_firing_at_or_before(at_or_before, expected):
    assert Schedule("0 1 * * 0").latest_firing(at_or_before) == expected
