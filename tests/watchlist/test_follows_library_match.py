"""#1901: a corrected library artist match carries over to the watchlist.

The watchlist keeps its own ids per source, so fixing the wrong "1905" in the
library used to leave the watchlist scanning the wrong one. Rows are tied to
the library artist by id, never by name (two artists called 1905 is the whole
point), and only the fixing profile's watchlist follows.
"""

from __future__ import annotations

import sqlite3

import pytest

from core.watchlist.library_match import follow_library_artist_match, library_artist_ids

RIGHT = "66oNCpEovvkyBASCRLaI93"
WRONG = "5S5TmGWWg0cKOu3Dv6cQO0"


@pytest.fixture
def cur():
    conn = sqlite3.connect(":memory:")
    c = conn.cursor()
    c.execute("""CREATE TABLE artists (id INTEGER PRIMARY KEY, name TEXT,
        spotify_artist_id TEXT, itunes_artist_id TEXT, deezer_id TEXT, discogs_id TEXT,
        musicbrainz_id TEXT, amazon_id TEXT)""")
    c.execute("""CREATE TABLE watchlist_artists (id INTEGER PRIMARY KEY, artist_name TEXT,
        spotify_artist_id TEXT, itunes_artist_id TEXT, deezer_artist_id TEXT,
        discogs_artist_id TEXT, musicbrainz_artist_id TEXT, amazon_artist_id TEXT,
        profile_id INTEGER DEFAULT 1, updated_at TIMESTAMP,
        UNIQUE(profile_id, spotify_artist_id))""")
    c.execute("INSERT INTO artists (id, name, spotify_artist_id, deezer_id) VALUES (1, '1905', ?, '42')",
              (WRONG,))
    yield c
    conn.close()


def watch(cur, name, profile=1, **ids):
    cols = ", ".join(["artist_name", "profile_id", *ids])
    marks = ", ".join("?" * (2 + len(ids)))
    cur.execute(f"INSERT INTO watchlist_artists ({cols}) VALUES ({marks})", (name, profile, *ids.values()))
    return cur.lastrowid


def wl(cur, row_id, col="spotify_artist_id"):
    return cur.execute(f"SELECT {col} FROM watchlist_artists WHERE id = ?", (row_id,)).fetchone()[0]


def test_the_watched_artist_follows_the_fix(cur):
    row = watch(cur, "1905", spotify_artist_id=WRONG)
    before = library_artist_ids(cur, 1)
    out = follow_library_artist_match(cur, before=before, service="spotify", new_id=RIGHT, profile_id=1)
    assert out == {"updated": ["1905"], "skipped": []}
    assert wl(cur, row) == RIGHT


def test_found_through_another_sources_id_too(cur):
    # the watchlist row has no spotify id yet, but its deezer id is the library's
    row = watch(cur, "1905", deezer_artist_id="42")
    out = follow_library_artist_match(cur, before=library_artist_ids(cur, 1), service="spotify",
                                      new_id=RIGHT, profile_id=1)
    assert out["updated"] == ["1905"]
    assert wl(cur, row) == RIGHT


def test_a_different_artist_with_the_same_name_is_left_alone(cur):
    # watched on purpose, and shares no id with the library artist
    row = watch(cur, "1905", spotify_artist_id="0Fm7AwK6hmLR7KfxSk6ufL")
    out = follow_library_artist_match(cur, before=library_artist_ids(cur, 1), service="spotify",
                                      new_id=RIGHT, profile_id=1)
    assert out == {"updated": [], "skipped": []}
    assert wl(cur, row) == "0Fm7AwK6hmLR7KfxSk6ufL"


def test_another_profiles_watchlist_is_untouched(cur):
    mine = watch(cur, "1905", spotify_artist_id=WRONG, profile=1)
    theirs = watch(cur, "1905", spotify_artist_id=WRONG, profile=2)
    follow_library_artist_match(cur, before=library_artist_ids(cur, 1), service="spotify",
                                new_id=RIGHT, profile_id=1)
    assert wl(cur, mine) == RIGHT
    assert wl(cur, theirs) == WRONG


def test_a_profile_that_alone_watches_it_is_untouched(cur):
    # nobody in the fixing profile watches it, so nothing there clashes either:
    # only the profile filter keeps the other profile's choice its own
    theirs = watch(cur, "1905", spotify_artist_id=WRONG, profile=2)
    out = follow_library_artist_match(cur, before=library_artist_ids(cur, 1), service="spotify",
                                      new_id=RIGHT, profile_id=1)
    assert out == {"updated": [], "skipped": []}
    assert wl(cur, theirs) == WRONG


def test_never_gives_two_watched_artists_the_same_id(cur):
    row = watch(cur, "1905", spotify_artist_id=WRONG)
    watch(cur, "1905 (the real one)", spotify_artist_id=RIGHT)
    out = follow_library_artist_match(cur, before=library_artist_ids(cur, 1), service="spotify",
                                      new_id=RIGHT, profile_id=1)
    assert out == {"updated": [], "skipped": ["1905"]}
    assert wl(cur, row) == WRONG


def test_sources_the_watchlist_keeps_no_id_for_change_nothing(cur):
    row = watch(cur, "1905", spotify_artist_id=WRONG)
    out = follow_library_artist_match(cur, before=library_artist_ids(cur, 1), service="tidal",
                                      new_id="7804", profile_id=1)
    assert out == {"updated": [], "skipped": []}
    assert wl(cur, row) == WRONG


def test_already_pointing_there_is_a_no_op(cur):
    watch(cur, "1905", spotify_artist_id=RIGHT, deezer_artist_id="42")
    out = follow_library_artist_match(cur, before=library_artist_ids(cur, 1), service="spotify",
                                      new_id=RIGHT, profile_id=1)
    assert out == {"updated": [], "skipped": []}


def test_an_unmatched_library_artist_finds_nothing(cur):
    cur.execute("INSERT INTO artists (id, name) VALUES (2, '1905')")
    watch(cur, "1905", spotify_artist_id=WRONG)
    assert library_artist_ids(cur, 2) == {}
    out = follow_library_artist_match(cur, before={}, service="spotify", new_id=RIGHT, profile_id=1)
    assert out == {"updated": [], "skipped": []}
