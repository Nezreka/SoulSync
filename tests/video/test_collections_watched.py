"""Watch-state rules: the scanner carries server play counts into the library
(movies.play_count / shows.watched_episodes) and the 'watched' smart field
filters on them — Unwatched collections shrink as you watch."""

from __future__ import annotations

import pytest

from core.video.collections.presets import expand_pack
from core.video.collections.smart_filter import SmartFilterError, compile_rules, field_schema
from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def _add(db, mid, title, plays):
    conn = db._get_connection()
    try:
        conn.execute("INSERT INTO movies (id, server_source, server_id, title, play_count, has_file) "
                     "VALUES (?,?,?,?,?,1)", (mid, "plex", f"m{mid}", title, plays))
        conn.commit()
    finally:
        conn.close()


def test_watched_field_compiles_and_resolves(db):
    _add(db, 1, "Seen", 3)
    _add(db, 2, "Fresh", 0)
    _add(db, 3, "Legacy", None)          # pre-migration rows count as unwatched

    unwatched = {"rules": [{"field": "watched", "op": "is", "value": False}]}
    rows = db.resolve_smart_members("movie", unwatched)
    assert sorted(r["title"] for r in rows) == ["Fresh", "Legacy"]

    watched = {"rules": [{"field": "watched", "op": "is", "value": True}]}
    rows = db.resolve_smart_members("movie", watched)
    assert [r["title"] for r in rows] == ["Seen"]


def test_watched_show_side_and_op_guard(db):
    conn = db._get_connection()
    conn.execute("INSERT INTO shows (id, server_source, server_id, title, watched_episodes) "
                 "VALUES (1, 'plex', 's1', 'Started', 4)")
    conn.execute("INSERT INTO shows (id, server_source, server_id, title, watched_episodes) "
                 "VALUES (2, 'plex', 's2', 'Untouched', 0)")
    conn.commit(); conn.close()
    rows = db.resolve_smart_members(
        "show", {"rules": [{"field": "watched", "op": "is", "value": True}]})
    assert [r["title"] for r in rows] == ["Started"]
    with pytest.raises(SmartFilterError):
        compile_rules({"rules": [{"field": "watched", "op": "gte", "value": 1}]}, "movie")


def test_watched_in_field_schema_and_essentials(db):
    schema = {f["field"]: f for f in field_schema("movie")}
    assert schema["watched"]["type"] == "bool" and schema["watched"]["ops"] == ["is"]
    assert "watched" in {f["field"] for f in field_schema("show")}

    _add(db, 1, "Fresh", 0)
    ess = {e["name"]: e for e in expand_pack(db, "essentials", "movie")}
    assert ess["Unwatched"]["count"] == 1
    assert ess["Unwatched"]["definition"]["rules"] == [
        {"field": "watched", "op": "is", "value": False}]


def test_upserts_carry_watch_state(db):
    db.upsert_movie("plex", {"server_id": "m9", "title": "Movie", "play_count": 2,
                             "file": {"relative_path": "m.mkv"}})
    conn = db._get_connection()
    assert conn.execute("SELECT play_count FROM movies WHERE server_id='m9'").fetchone()[0] == 2
    conn.close()
    db.upsert_show_tree("plex", {"server_id": "s9", "title": "Show",
                                 "watched_episodes": 7, "seasons": []})
    conn = db._get_connection()
    assert conn.execute("SELECT watched_episodes FROM shows WHERE server_id='s9'").fetchone()[0] == 7
    conn.close()
