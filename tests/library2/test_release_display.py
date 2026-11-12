"""one release, one card on the artist page: which album rows are the same
release, and which of them stands for it."""

import json

from core.library2.release_display import stamp_display_groups


def _entry(album_id, title="bbno$", album_type="album", year=2025, present=0, total=21,
           origin="library", monitored=False, release_date=None):
    return {"id": album_id, "title": title, "album_type": album_type, "year": year,
            "release_date": release_date, "tracks_present": present, "track_count": total,
            "origin": origin, "monitored": monitored}


def _raw(group="", spotify=None, **external):
    return {"musicbrainz_release_group_id": group, "spotify_id": spotify,
            "musicbrainz_id": None, "soul_id": None, "external_ids": json.dumps(external)}


def _stamp(pairs):
    entries = [entry for entry, _raw_row in pairs]
    stamp_display_groups(entries, {int(entry["id"]): raw for entry, raw in pairs})
    return {entry["id"]: (entry["release_lead_id"], entry["release_copies"]) for entry in entries}


GROUP = "e9c40725-3fc7-4ae1-a112-4f0ca7a64a7b"


def test_the_live_bbno_page_is_one_release_led_by_the_complete_copy():
    stamped = _stamp([
        (_entry(9430, present=1, total=22), _raw(GROUP, spotify="6Nn", discogs="36050005")),
        (_entry(68930, present=21), _raw(GROUP, spotify="6Nn", discogs="r36050005", deezer="810624501")),
        (_entry(72802, origin="discography", monitored=True), _raw(GROUP, deezer="810624501")),
        (_entry(5, title="eat ya veggies", year=2021, present=12, total=12), _raw("other")),
    ])
    assert stamped[9430] == stamped[68930] == stamped[72802] == (68930, 2)
    assert stamped[5] == (5, 1)


def test_two_albums_of_one_name_in_two_release_groups_stay_two():
    stamped = _stamp([
        (_entry(1, title="Weezer", year=1994, present=10, total=10), _raw("blue")),
        (_entry(2, title="Weezer", year=1994, present=10, total=10), _raw("green")),
    ])
    assert stamped == {1: (1, 1), 2: (2, 1)}


def test_a_same_named_album_from_another_year_is_another_album():
    stamped = _stamp([
        (_entry(1, title="Eagles", year=1972, present=3, total=10), _raw()),
        (_entry(2, title="Eagles", year=2005, origin="discography", total=40), _raw()),
    ])
    assert stamped == {1: (1, 1), 2: (2, 0)}


def test_a_shared_provider_id_outweighs_a_differing_year():
    stamped = _stamp([
        (_entry(1, year=2025, present=21), _raw(deezer="810624501")),
        (_entry(2, year=2026, origin="discography"), _raw(deezer="810624501")),
    ])
    assert stamped == {1: (1, 1), 2: (1, 1)}


def test_an_entry_with_no_ids_joins_the_owned_copy_of_its_year():
    stamped = _stamp([
        (_entry(1, present=21), _raw(GROUP)),
        (_entry(2, origin="discography"), _raw()),
    ])
    assert stamped[2] == (1, 1)


def test_an_album_and_its_same_named_single_are_two_releases():
    stamped = _stamp([
        (_entry(1, title="Edamame", present=12, total=12), _raw()),
        (_entry(2, title="Edamame", album_type="single", present=1, total=1), _raw()),
    ])
    assert stamped == {1: (1, 1), 2: (2, 1)}


def test_untitled_rows_are_never_one_release():
    stamped = _stamp([(_entry(1, title=""), _raw()), (_entry(2, title=""), _raw())])
    assert stamped == {1: (1, 0), 2: (2, 0)}


def test_between_two_entries_the_wanted_one_leads():
    stamped = _stamp([
        (_entry(1, origin="discography"), _raw()),
        (_entry(2, origin="discography", monitored=True), _raw()),
    ])
    assert stamped == {1: (2, 0), 2: (2, 0)}


def test_a_row_only_like_a_member_and_not_the_lead_stays_out():
    # A (group x) leads. B has no group and matches A by year. C is in group
    # y: like B by year, unlike A. C is its own release
    stamped = _stamp([
        (_entry(1, present=10), _raw("x")),
        (_entry(2, present=5), _raw()),
        (_entry(3, present=1), _raw("y")),
    ])
    assert stamped[1] == stamped[2] == (1, 2)
    assert stamped[3] == (3, 1)
