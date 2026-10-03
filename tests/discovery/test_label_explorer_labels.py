"""the label explorer is built from the labels you play, not the first 30 sqlite finds.

sept 29 2026: on boulder's library the old `SELECT DISTINCT label ... LIMIT 30`
returned zalgo-text labels and seven "$EBU & someone" variants.
"""

import sqlite3

import pytest

from core.discovery.labels import your_labels


@pytest.fixture
def conn():
    # Library v2: an album is yours when one of its tracks has a live file
    c = sqlite3.connect(':memory:')
    c.executescript("""
        CREATE TABLE lib2_albums (id INTEGER PRIMARY KEY, label TEXT);
        CREATE TABLE lib2_tracks (id INTEGER PRIMARY KEY, album_id INTEGER, play_count INTEGER);
        CREATE TABLE lib2_track_files (id INTEGER PRIMARY KEY, track_id INTEGER, path TEXT,
                                       file_state TEXT, owner_profile_id INTEGER);
    """)
    return c


def _album(c, aid, label, plays=(), unplayed=0):
    c.execute("INSERT INTO lib2_albums VALUES (?, ?)", (aid, label))
    for p in [*plays, *([0] * unplayed)]:
        tid = c.execute("INSERT INTO lib2_tracks (album_id, play_count) VALUES (?, ?)",
                        (aid, p)).lastrowid
        c.execute("INSERT INTO lib2_track_files (track_id, path) VALUES (?, ?)",
                  (tid, f'/m/{tid}.flac'))


def test_the_labels_you_play_lead(conn):
    _album(conn, 1, 'First In The Table', unplayed=5)   # what LIMIT 30 used to hit first
    _album(conn, 2, 'Monstercat', plays=[40, 30])
    _album(conn, 3, 'Warp', plays=[100])
    assert your_labels(conn, limit=2) == ['Warp', 'Monstercat']


def test_case_and_spacing_are_one_label(conn):
    _album(conn, 1, 'Warp', plays=[10])
    _album(conn, 2, ' warp ', plays=[4])
    _album(conn, 3, 'Ninja Tune', plays=[15])
    # the better-played spelling is the one kept (the cache matches exactly)
    assert your_labels(conn, limit=5)[:2] == ['Ninja Tune', 'Warp']
    assert len([x for x in your_labels(conn, limit=5) if x.strip().lower() == 'warp']) == 1


def test_a_fresh_library_falls_back_to_what_you_own_most_of(conn):
    _album(conn, 1, 'Rare', unplayed=1)
    for i in range(2, 5):
        _album(conn, i, 'Columbia', unplayed=1)
    _album(conn, 5, 'Warp', plays=[3])
    assert your_labels(conn, limit=3) == ['Warp', 'Columbia', 'Rare']


def test_blank_labels_never_count(conn):
    _album(conn, 1, '', plays=[99])
    _album(conn, 2, '   ', plays=[99])
    _album(conn, 3, None, plays=[99])
    _album(conn, 4, 'Warp', plays=[1])
    assert your_labels(conn) == ['Warp']
