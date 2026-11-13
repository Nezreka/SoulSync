"""#2080 the original release, and #2081 no clean copies (RetroPup).

#2080: "only grab an album's original version (no deluxe/special editions)".
the Standard edition preference took the fewest tracks, and a remaster has as
many as the original, so the first one the provider listed won.

#2081: "clean versions slip through". prefer explicit only chose between
editions once one edition per album was on; with every edition wanted, the
clean copy was wished for next to the explicit one.
"""

from __future__ import annotations

from types import SimpleNamespace

from core.edition_grouping import (
    drop_clean_twins,
    is_clean_marked,
    is_repressing,
    reduce_edition_group,
)


def _rel(name, tracks, *, explicit=False, date=""):
    return {"name": name, "track_count": tracks, "explicit": explicit, "release_date": date}


def _names(releases):
    return [r["name"] for r in releases]


def test_standard_is_the_original_not_the_remaster_listed_first():
    group = [_rel("Metallica (Remastered)", 12, date="2021-09-10"),
             _rel("Metallica", 12, date="1991-08-12"),
             _rel("Metallica (Remastered Deluxe Box Set)", 53, date="2021-09-10")]
    assert _names(reduce_edition_group(group, "one_standard")) == ["Metallica"]


def test_between_two_plain_titles_the_earlier_one_is_the_original():
    group = [_rel("Nevermind", 13, date="2011-09-27"), _rel("Nevermind", 13, date="1991-09-24")]
    assert reduce_edition_group(group, "one_standard")[0]["release_date"] == "1991-09-24"
    # undated editions keep the provider's order, as before
    undated = [_rel("Nevermind", 13), _rel("Nevermind", 13)]
    assert reduce_edition_group(undated, "one_standard")[0] is undated[0]


def test_fewest_tracks_still_comes_first_and_explicit_before_pressing():
    group = [_rel("Album", 14), _rel("Album (Deluxe)", 20), _rel("Album (Remastered)", 12)]
    assert _names(reduce_edition_group(group, "one_standard")) == ["Album (Remastered)"]
    group = [_rel("Album", 12), _rel("Album (Remastered)", 12, explicit=True)]
    assert _names(reduce_edition_group(group, "one_standard")) == ["Album (Remastered)"]
    assert _names(reduce_edition_group(group, "one_standard", prefer_explicit=False)) == ["Album"]


def test_explicit_and_clean_are_not_pressings():
    assert not is_repressing("Album (Explicit)")
    assert not is_repressing("Album [Clean]")
    assert not is_repressing("Album")
    assert is_repressing("Album (2011 Remaster)")
    assert is_repressing("Album (Explicit) [Deluxe Edition]")
    assert not is_repressing("Deluxe")   # a title that is only the word is a title


def test_clean_is_a_bracket_of_its_own():
    for title in ("Crunk Juice (Clean)", "1992 [Clean]", "Song (Clean Version)", "Song (Edited)"):
        assert is_clean_marked(title), title
    for title in ("Heartbreak Anthem (Clean Bandit Remix)", "Mr. Clean", "Clean", "Squeaky Clean"):
        assert not is_clean_marked(title), title


def test_the_clean_copy_goes_when_the_album_is_there_uncensored():
    group = [_rel("Crunk Juice", 19, explicit=True), _rel("Crunk Juice (Clean)", 19)]
    assert _names(drop_clean_twins(group)) == ["Crunk Juice"]
    # no title says so: the not-explicit edition with the same tracks is the clean one
    group = [_rel("Album", 12), _rel("Album", 12, explicit=True), _rel("Album (Deluxe)", 18)]
    assert drop_clean_twins(group) == [group[1], group[2]]


def test_an_album_that_only_exists_clean_keeps_its_edition():
    only = [_rel("Kidz Bop 40 (Clean)", 15)]
    assert drop_clean_twins(only) == only
    both_clean = [_rel("Album (Clean)", 12), _rel("Album [Clean Version]", 12)]
    assert drop_clean_twins(both_clean) == both_clean
    # a different track count is another edition, not a clean copy
    group = [_rel("Album", 12), _rel("Album (Deluxe)", 18, explicit=True)]
    assert drop_clean_twins(group) == group


def test_every_edition_mode_leaves_the_clean_copy_out_while_explicit_is_preferred():
    group = [_rel("Album", 12, explicit=True), _rel("Album (Clean)", 12),
             _rel("Album (Deluxe)", 18, explicit=True)]
    assert _names(reduce_edition_group(group, "all")) == ["Album", "Album (Deluxe)"]
    assert _names(reduce_edition_group(group, "one_standard")) == ["Album"]
    assert _names(reduce_edition_group(group, "one_complete")) == ["Album (Deluxe)"]
    # with the preference off nothing is left out
    assert reduce_edition_group(group, "all", prefer_explicit=False) == group


def test_the_scanner_with_all_editions_skips_the_clean_twin_and_keeps_the_order():
    from core.watchlist_scanner import _without_clean_twins

    albums = [SimpleNamespace(id="1", name="Tha Carter III (Clean)", total_tracks=16),
              SimpleNamespace(id="2", name="Rebirth", total_tracks=12),
              SimpleNamespace(id="3", name="Tha Carter III", total_tracks=16),
              SimpleNamespace(id="4", name="Kidz Bop (Clean)", total_tracks=10)]
    assert [a.id for a in _without_clean_twins(albums, True)] == ["2", "3", "4"]
    assert _without_clean_twins(albums, False) == albums


def test_the_library_drops_the_clean_twin_and_reads_the_setting(tmp_path):
    from contextlib import closing

    from core.library2.edition_selection import select_automatic_album_ids
    from database.music_database import MusicDatabase

    class _Config:
        def __init__(self, **values):
            self.values = values

        def get(self, key, default=None):
            return self.values.get(key, default)

    db = MusicDatabase(str(tmp_path / "m.db"))
    with closing(db._get_connection()) as conn:
        artist = conn.execute("INSERT INTO lib2_artists(name) VALUES('Lil Jon')").lastrowid
        ids = {}
        for title, explicit, date in (("Crunk Juice", 1, "2004-11-16"),
                                      ("Crunk Juice (Clean)", 0, "2004-11-16"),
                                      ("Crunk Juice (Remastered)", 1, "2019-01-01")):
            ids[title] = conn.execute(
                "INSERT INTO lib2_albums(primary_artist_id, title, album_type, track_count,"
                " explicit, release_date) VALUES(?,?, 'album', 19, ?, ?)",
                (artist, title, explicit, date)).lastrowid
        conn.commit()
        every = list(ids.values())

        def pick(**config):
            chosen = select_automatic_album_ids(conn, every, _Config(**config))
            return sorted(t for t, i in ids.items() if i in chosen)

        assert pick() == ["Crunk Juice", "Crunk Juice (Remastered)"]
        assert pick(**{"watchlist.prefer_explicit_edition": False}) == sorted(ids)
        # the original, not the remaster with the same tracks (#2080)
        assert pick(**{"watchlist.edition_preference": "one_standard"}) == ["Crunk Juice"]
        # with explicit not preferred, the setting is what decides: it was ignored here
        assert pick(**{"watchlist.edition_preference": "one_standard",
                       "watchlist.prefer_explicit_edition": False}) == ["Crunk Juice"]
