"""Server-side entity resolution for manual grabs (audit P1-16/P1-17).

The browser names a lib2 entity; the server decides whether it exists and
which quality profile applies. A named-but-invalid entity must fail the
grab, not degrade to a context-free download.
"""

from __future__ import annotations

import sqlite3

import pytest

from core.library2.grab_context import resolve_lib2_grab_context
from core.library2.schema import ensure_library_v2_schema


class _Shim:
    def __init__(self, path: str):
        self.path = path

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "lib2.db")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    ensure_library_v2_schema(conn)
    cur = conn.cursor()
    cur.execute("INSERT INTO lib2_artists(name) VALUES('A')")
    artist_id = cur.lastrowid
    cur.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, quality_profile_id) "
        "VALUES(?, 'Alb', 7)", (artist_id,))
    album_id = cur.lastrowid
    cur.execute(
        "INSERT INTO lib2_tracks(album_id, title, track_number, quality_profile_id) "
        "VALUES(?, 'T', 1, 9)", (album_id,))
    track_id = cur.lastrowid
    conn.commit()
    conn.close()
    shim = _Shim(path)
    shim.ids = {"album": album_id, "track": track_id}
    return shim


def test_absent_when_no_entity_named(db):
    assert resolve_lib2_grab_context(db, {}) == ("absent", None)
    assert resolve_lib2_grab_context(db, {"title": "x"}) == ("absent", None)


def test_track_resolves_with_own_profile(db):
    state, ctx = resolve_lib2_grab_context(db, {"lib2_track_id": db.ids["track"]})
    assert state == "ok"
    assert ctx == {"track_id": db.ids["track"], "album_id": db.ids["album"],
                   "quality_profile_id": 9}


def test_album_resolves_with_album_profile_not_artist(db):
    """P1-17: an album grab carries the ALBUM's own profile."""
    state, ctx = resolve_lib2_grab_context(db, {"lib2_album_id": db.ids["album"]})
    assert state == "ok"
    assert ctx == {"album_id": db.ids["album"], "quality_profile_id": 7}


def test_unknown_ids_are_invalid(db):
    assert resolve_lib2_grab_context(db, {"lib2_track_id": 999999})[0] == "invalid"
    assert resolve_lib2_grab_context(db, {"lib2_album_id": 999999})[0] == "invalid"


def test_non_numeric_ids_are_invalid(db):
    assert resolve_lib2_grab_context(db, {"lib2_track_id": "abc"})[0] == "invalid"
    assert resolve_lib2_grab_context(db, {"lib2_album_id": [1]})[0] == "invalid"


def test_track_album_mismatch_is_invalid(db):
    """The client can't pair a track with a foreign album to smuggle a
    different profile context."""
    state, _ = resolve_lib2_grab_context(
        db, {"lib2_track_id": db.ids["track"], "lib2_album_id": 999999})
    assert state == "invalid"


def test_missing_tables_are_invalid_not_crash(tmp_path):
    empty = _Shim(str(tmp_path / "empty.db"))
    state, ctx = resolve_lib2_grab_context(empty, {"lib2_track_id": 1})
    assert (state, ctx) == ("invalid", None)
