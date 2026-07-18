"""Server-side entity resolution for manual grabs (audit P1-16/P1-17).

The browser names a lib2 entity; the server decides whether it exists and
which quality profile applies. A named-but-invalid entity must fail the
grab, not degrade to a context-free download.
"""

from __future__ import annotations

import sqlite3

import pytest

from core.library2.grab_context import (
    build_lib2_import_pipeline_fields,
    build_lib2_track_info,
    names_lib2_entity,
    resolve_lib2_grab_context,
)
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
    cur.executemany(
        "INSERT INTO quality_profiles(id, name) VALUES(?, ?)",
        [(7, "Album Profile"), (9, "Track Profile")],
    )
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


def test_only_non_null_ids_name_a_lib2_entity():
    assert names_lib2_entity({"lib2_track_id": 1}) is True
    assert names_lib2_entity({"lib2_album_id": "2"}) is True
    assert names_lib2_entity({"lib2_track_id": None}) is False
    assert names_lib2_entity({}) is False


def test_pipeline_metadata_uses_server_resolved_profile():
    request_data = {
        "title": "Track",
        "artist": "Artist",
        "quality_profile_id": 999,
    }

    info = build_lib2_track_info(
        request_data,
        {"track_id": 3, "album_id": 2, "quality_profile_id": 9},
        album_name="Album",
    )

    assert info["name"] == "Track"
    assert info["artists"] == [{"name": "Artist"}]
    assert info["album"] == {"name": "Album"}
    assert info["quality_profile_id"] == 9
    assert request_data["quality_profile_id"] == 999


def test_pipeline_metadata_is_absent_without_lib2_context():
    assert build_lib2_track_info({"title": "Normal download"}, None) is None


def test_import_pipeline_fields_ground_metadata_in_the_resolved_entity():
    """docs §69.2/§71.2: a grab naming a resolved track routes through the
    full import pipeline, trusting the entity's own DB row over whatever
    (possibly stale) title/artist the browser's search-result card sent."""
    request_data = {"title": "Stale Title", "artist": "Stale Artist"}
    lib2_context = {
        "track_id": 3, "album_id": 2, "quality_profile_id": 9,
        "artist_name": "Real Artist", "album_name": "Real Album",
        "track_title": "Real Title", "track_number": 4, "disc_number": 1,
    }

    fields = build_lib2_import_pipeline_fields(request_data, lib2_context)

    assert fields["is_simple_download"] is False
    assert fields["artist"] == {"name": "Real Artist"}
    assert fields["album"] == {"name": "Real Album"}
    assert fields["track_info"]["name"] == "Real Title"
    assert fields["track_info"]["track_number"] == 4
    assert fields["track_info"]["disc_number"] == 1
    assert fields["track_info"]["album"] == {"name": "Real Album"}


def test_import_pipeline_fields_fall_back_to_request_title_for_album_scope():
    """An album-scoped grab's lib2_context has no per-track title (only one
    context is resolved for the whole album) — each track's own request
    data still supplies it."""
    request_data = {"title": "Track From Listing", "artist": "ignored"}
    lib2_context = {
        "album_id": 2, "quality_profile_id": 7,
        "artist_name": "Real Artist", "album_name": "Real Album",
    }

    fields = build_lib2_import_pipeline_fields(request_data, lib2_context)

    assert fields["is_simple_download"] is False
    assert fields["artist"] == {"name": "Real Artist"}
    assert fields["track_info"]["name"] == "Track From Listing"


def test_import_pipeline_fields_empty_without_lib2_context():
    """No resolved entity (plain search-page download) => stay on the
    metadata-free simple-download shortcut, unchanged."""
    assert build_lib2_import_pipeline_fields({"title": "x"}, None) == {}
    assert build_lib2_import_pipeline_fields({"title": "x"}, {}) == {}


def test_track_resolves_with_own_profile(db):
    state, ctx = resolve_lib2_grab_context(db, {"lib2_track_id": db.ids["track"]})
    assert state == "ok"
    assert ctx == {
        "track_id": db.ids["track"], "album_id": db.ids["album"],
        "quality_profile_id": 9,
        "artist_name": "A", "album_name": "Alb", "track_title": "T",
        "track_number": 1, "disc_number": 1,
    }


def test_album_resolves_with_album_profile_not_artist(db):
    """P1-17: an album grab carries the ALBUM's own profile."""
    state, ctx = resolve_lib2_grab_context(db, {"lib2_album_id": db.ids["album"]})
    assert state == "ok"
    assert ctx == {
        "album_id": db.ids["album"], "quality_profile_id": 7,
        "artist_name": "A", "album_name": "Alb",
    }


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
