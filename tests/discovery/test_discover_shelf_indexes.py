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
    assert keys == {"homogenic|||bjork"}


def test_the_stray_test_indexes_are_dropped_on_start(tmp_path, monkeypatch):
    import sqlite3

    path = str(tmp_path / "stray.db")
    MusicDatabase(path)
    conn = sqlite3.connect(path)
    conn.execute("CREATE INDEX idx_x_rel ON discovery_pool (added_date)")
    conn.execute("CREATE INDEX idx_x_played ON lib2_tracks(added_at)")
    conn.commit()
    conn.close()
    from database import music_database
    monkeypatch.setattr(music_database, "_database_initialized_paths", set())   # a restart
    MusicDatabase(path)
    conn = sqlite3.connect(path)
    try:
        left = conn.execute("SELECT name FROM sqlite_master WHERE name LIKE 'idx_x_%'").fetchall()
    finally:
        conn.close()
    assert left == []


def _shelf_pass_sql(extra_where=""):
    """The candidate pass exactly as PersonalizedPlaylistsService._candidates sends it."""
    from core.personalized_playlists import PersonalizedPlaylistsService as S

    return (f"SELECT id, LOWER(artist_name) IN (?, ?) FROM discovery_pool "
            f"{S._POOL_WHERE} {S._OWNED_CLAUSE} {extra_where}")


@pytest.mark.parametrize("extra_where, extra", [
    ("", ()),                                   # discovery shuffle
    ("AND popularity >= ?", (60,)),             # popular picks
    ("AND popularity < ?", (40,)),              # hidden gems
])
def test_the_drawn_shelves_pick_candidates_from_the_covering_index(db, extra_where, extra):
    """nov 13 2026: this pass read every pool row in full (track json and all,
    500 MB on a 233k-row pool) to get at seven small columns, ~2s a shelf."""
    import random

    from core.personalized_playlists import _ensure_owned_ids

    with closing(db._get_connection()) as conn:
        rng = random.Random(7)
        conn.executemany(
            "INSERT INTO discovery_pool(profile_id, source, artist_name, track_name, album_name, "
            "popularity, spotify_track_id, deezer_track_id, track_data_json) VALUES(?,?,?,?,?,?,?,?,?)",
            [(1 + n % 2, ("deezer", "spotify")[n % 3 == 0], f"artist {n % 400}", f"t{n}", f"al{n}",
              rng.randrange(100), f"sp{n}" if n % 3 == 0 else None, f"dz{n}", "x" * 400)
             for n in range(6000)])
        conn.execute("ANALYZE")
        _ensure_owned_ids(conn)
        params = ("a", "b", "deezer", 1, 1) + extra
        plan = _plan(conn, _shelf_pass_sql(extra_where), params)
        rows = conn.execute(_shelf_pass_sql(extra_where), params).fetchall()
    assert "COVERING INDEX idx_discovery_pool_shelf" in plan, plan
    assert rows, "the query this plan is for found nothing"


def test_the_shelf_index_is_built_once_and_survives_a_restart(tmp_path):
    path = str(tmp_path / "again.db")
    for _ in range(2):
        db = MusicDatabase(path)
        with closing(db._get_connection()) as conn:
            sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='idx_discovery_pool_shelf'").fetchone()
        assert sql and "profile_id, source, artist_name, popularity" in sql[0]
