"""Two Discover shelves read the whole table without an index of their own.

decades grouped the pool by release year through the profile_id index alone,
so every call read each pool row in full (6.9s live, 25s cold), and the label
explorer walked every album for the few thousand played tracks (5.8s live).
Nov 4 2026.
"""

from __future__ import annotations

from contextlib import closing

import pytest

from database.music_database import MusicDatabase


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "m.db"))


def _plan(conn, sql, params=()):
    return " | ".join(str(r[-1]) for r in conn.execute("EXPLAIN QUERY PLAN " + sql, params))


def test_decades_are_read_from_the_covering_index(db):
    from api.discover_routes import AVAILABLE_DECADES_SQL

    with closing(db._get_connection()) as conn:
        plan = _plan(conn, AVAILABLE_DECADES_SQL, (1,))
    assert "COVERING INDEX idx_discovery_pool_profile_release" in plan, plan


def test_label_explorer_starts_from_the_played_tracks(db):
    from core.discovery.labels import your_labels

    with closing(db._get_connection()) as conn:
        seen = []
        conn.set_trace_callback(seen.append)
        try:
            your_labels(conn)
        finally:
            conn.set_trace_callback(None)
        played = next(s for s in seen if "t.play_count > 0" in s)
        plan = _plan(conn, played)
    assert "idx_lib2_tracks_played" in plan, plan
    assert "SCAN al" not in plan, plan


def _own(conn, artist, title, *, live=True):
    aid = conn.execute("SELECT id FROM lib2_artists WHERE name=?", (artist,)).fetchone()
    aid = aid[0] if aid else conn.execute(
        "INSERT INTO lib2_artists(name) VALUES(?)", (artist,)).lastrowid
    al = conn.execute("INSERT INTO lib2_albums(primary_artist_id, title) VALUES(?,?)",
                      (aid, title)).lastrowid
    t = conn.execute("INSERT INTO lib2_tracks(album_id, title) VALUES(?, 'x')", (al,)).lastrowid
    conn.execute("INSERT INTO lib2_track_files(track_id, path, file_state) VALUES(?,?,?)",
                 (t, f"/m/{artist}/{title}.flac", "active" if live else "missing"))


def test_undiscovered_excludes_only_the_top_artists_owned_albums(db):
    # 70k album keys were built on every call to drop albums by 25 artists
    from api.discover_routes import _owned_album_keys_for

    with closing(db._get_connection()) as conn:
        _own(conn, "BJÖRK", "Homogenic")
        _own(conn, "Björk", "Vespertine", live=False)
        _own(conn, "Someone Else", "Elsewhere")
        conn.commit()
        keys = _owned_album_keys_for(conn, ["björk ", "Nobody"])
    # the listened name folds to the library's, past sqlite's ascii-only LOWER
    assert keys == {("homogenic", "björk")}
