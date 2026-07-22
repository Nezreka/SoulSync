"""Phase C re-tag: lib2 → tag_writer db_data shaping, preview, batch write."""

from __future__ import annotations

import json
import sqlite3

from core.library2 import retag


def _seed_album_with_files(conn, *, path: str | None = "/nope/track.flac"):
    cur = conn.cursor()
    cur.execute("INSERT INTO lib2_artists(name) VALUES('Drake')")
    artist_id = cur.lastrowid
    cur.execute(
        """INSERT INTO lib2_albums(primary_artist_id, title, year, release_date,
               genres, expected_track_count) VALUES(?, 'Views', 2016, '2016-04-29',
               '["rap","pop"]', 2)""", (artist_id,))
    album_id = cur.lastrowid
    cur.execute("INSERT INTO lib2_album_artists(album_id, artist_id) VALUES(?,?)",
                (album_id, artist_id))
    cur.execute(
        """INSERT INTO lib2_tracks(album_id, title, track_number, spotify_id)
           VALUES(?, 'One Dance', 1, 'sp1')""", (album_id,))
    track_id = cur.lastrowid
    cur.execute("INSERT INTO lib2_track_artists(track_id, artist_id, position) VALUES(?,?,0)",
                (track_id, artist_id))
    # Featured credit → artists_list should appear in db_data.
    cur.execute("INSERT INTO lib2_artists(name) VALUES('Wizkid')")
    feat_id = cur.lastrowid
    cur.execute(
        "INSERT INTO lib2_track_artists(track_id, artist_id, role, position) "
        "VALUES(?,?, 'featured', 1)", (track_id, feat_id))
    if path:
        cur.execute("INSERT INTO lib2_track_files(track_id, path) VALUES(?,?)",
                    (track_id, path))
    conn.commit()
    return artist_id, album_id, track_id


def test_db_data_shape(imported_conn):
    """The db_data handed to core/tag_writer carries lib2's full metadata."""
    conn = imported_conn
    _, album_id, track_id = _seed_album_with_files(conn)
    row = retag._track_rows(conn, [track_id])[0]
    data = retag._db_data_for_row(conn, row)
    assert data["title"] == "One Dance"
    assert data["album_title"] == "Views"
    assert data["artist_name"] == "Drake"
    assert data["track_artist"] == "Drake; Wizkid"
    assert data["artists_list"] == ["Drake", "Wizkid"]
    assert data["genres"] == ["rap", "pop"]
    assert data["release_date"] == "2016-04-29"
    assert data["track_count"] == 2
    assert data["spotify_track_id"] == "sp1"


def test_preview_reports_missing_file(imported_conn):
    conn = imported_conn
    _, _, track_id = _seed_album_with_files(conn, path=None)
    out = retag.tag_preview(retag.track_contexts(conn, [track_id]))
    assert len(out) == 1
    assert out[0]["error"] == "No file"
    assert out[0]["has_changes"] is False


def test_preview_reports_unreadable_file(imported_conn):
    """A path that doesn't exist yields a per-track error, never an exception."""
    conn = imported_conn
    _, _, track_id = _seed_album_with_files(conn)  # /nope/track.flac
    out = retag.tag_preview(retag.track_contexts(conn, [track_id]))
    assert len(out) == 1
    assert out[0]["error"]
    assert out[0]["has_changes"] is False


def test_write_counts_unreadable_as_failed(imported_conn, legacy_db):
    conn = imported_conn
    _, _, track_id = _seed_album_with_files(conn)
    stats = retag.write_tags(legacy_db, [track_id], embed_cover=False)
    assert stats["failed"] == 1
    assert stats["written"] == 0
    assert stats["errors"][0]["track_id"] == track_id


def test_scope_helpers(imported_conn):
    conn = imported_conn
    artist_id, album_id, track_id = _seed_album_with_files(conn)
    assert track_id in retag.album_track_ids(conn, album_id)
    assert track_id in retag.artist_track_ids(conn, artist_id)


def test_artist_scope_helper_includes_linked_alias_release(imported_conn):
    conn = imported_conn
    canonical = conn.execute(
        "INSERT INTO lib2_artists(name) VALUES('Canonical')"
    ).lastrowid
    alias, _album_id, track_id = _seed_album_with_files(conn)
    from core.library2.artist_aliases import link_artist_alias
    link_artist_alias(conn, alias, canonical)

    assert track_id in retag.artist_track_ids(conn, canonical)


def test_unchanged_retag_refreshes_stale_gap_cache(
        imported_conn, legacy_db, tmp_path, monkeypatch):
    conn = imported_conn
    file_path = tmp_path / "track.flac"
    file_path.write_bytes(b"fake")
    _, _, track_id = _seed_album_with_files(conn, path="/mapped/track.flac")
    file_tags = {
        "title": "One Dance", "artist": "Drake; Wizkid",
        "album_artist": "Drake", "album": "Views", "year": "2016-04-29",
        "genre": "rap, pop", "track_number": 1, "disc_number": 1,
        "has_cover_art": True, "error": None,
    }
    monkeypatch.setattr("core.library2.paths.resolve_lib2_path", lambda _path: str(file_path))
    monkeypatch.setattr("core.tag_writer.read_file_tags", lambda _path: file_tags)
    monkeypatch.setattr("core.tag_writer.build_tag_diff", lambda *_args: [])

    stats = retag.write_tags(legacy_db, [track_id], embed_cover=False)

    cache = conn.execute(
        "SELECT tags_json, missing_tags_json FROM lib2_track_files WHERE track_id=?",
        (track_id,),
    ).fetchone()
    assert stats["skipped"] == 1
    assert json.loads(cache["tags_json"])["cover"] is True
    assert json.loads(cache["missing_tags_json"]) == []


def test_successful_retag_reloads_written_tags_instead_of_leaving_old_gaps(
        imported_conn, legacy_db, tmp_path, monkeypatch):
    conn = imported_conn
    file_path = tmp_path / "track.flac"
    file_path.write_bytes(b"fake")
    _, _, track_id = _seed_album_with_files(conn, path="/mapped/track.flac")
    reads = iter([
        {"title": None, "error": None},
        {
            "title": "One Dance", "artist": "Drake; Wizkid",
            "album_artist": "Drake", "album": "Views", "year": "2016-04-29",
            "genre": "rap, pop", "track_number": 1, "disc_number": 1,
            "has_cover_art": True, "error": None,
        },
    ])
    monkeypatch.setattr("core.library2.paths.resolve_lib2_path", lambda _path: str(file_path))
    monkeypatch.setattr("core.tag_writer.read_file_tags", lambda _path: next(reads))
    monkeypatch.setattr(
        "core.tag_writer.build_tag_diff",
        lambda *_args: [{"changed": True}],
    )
    monkeypatch.setattr(
        "core.tag_writer.write_tags_to_file",
        lambda *_args, **_kwargs: {"success": True},
    )

    stats = retag.write_tags(legacy_db, [track_id], embed_cover=False)

    cache = conn.execute(
        "SELECT missing_tags_json FROM lib2_track_files WHERE track_id=?", (track_id,)
    ).fetchone()
    assert stats["written"] == 1
    assert json.loads(cache["missing_tags_json"]) == []


def test_force_cover_embeds_even_when_text_tags_are_unchanged(
        imported_conn, legacy_db, tmp_path, monkeypatch):
    """A1: a picked cover must reach the file even when every text tag already
    matches — build_tag_diff never compares cover art, so without force_cover
    the unchanged-fastpath would skip the file and the new cover would never
    be embedded."""
    conn = imported_conn
    file_path = tmp_path / "track.flac"
    file_path.write_bytes(b"fake")
    _, album_id, track_id = _seed_album_with_files(conn, path="/mapped/track.flac")

    from core.library2.artwork import artwork_file
    cover_path = artwork_file(legacy_db, "album", album_id)
    cover_path.write_bytes(b"new-cover-bytes")

    file_tags = {
        "title": "One Dance", "artist": "Drake; Wizkid",
        "album_artist": "Drake", "album": "Views", "year": "2016-04-29",
        "genre": "rap, pop", "track_number": 1, "disc_number": 1,
        "has_cover_art": True, "error": None,
    }
    monkeypatch.setattr("core.library2.paths.resolve_lib2_path", lambda _path: str(file_path))
    monkeypatch.setattr("core.tag_writer.read_file_tags", lambda _path: file_tags)
    monkeypatch.setattr("core.tag_writer.build_tag_diff", lambda *_args: [])
    captured = {}

    def _fake_write(path, db_data, *, embed_cover, cover_data):
        captured["embed_cover"] = embed_cover
        captured["cover_data"] = cover_data
        return {"success": True}

    monkeypatch.setattr("core.tag_writer.write_tags_to_file", _fake_write)

    stats = retag.write_tags(legacy_db, [track_id], embed_cover=True, force_cover=True)

    assert stats["written"] == 1
    assert stats["skipped"] == 0
    assert captured["embed_cover"] is True
    assert captured["cover_data"] == (b"new-cover-bytes", "image/jpeg")


def test_force_cover_without_a_cache_file_still_skips_unchanged(
        imported_conn, legacy_db, tmp_path, monkeypatch):
    """force_cover has nothing to embed if the album has no cached artwork
    yet — must fall back to the normal skip instead of a pointless write."""
    conn = imported_conn
    file_path = tmp_path / "track.flac"
    file_path.write_bytes(b"fake")
    _, _, track_id = _seed_album_with_files(conn, path="/mapped/track.flac")

    file_tags = {
        "title": "One Dance", "artist": "Drake; Wizkid",
        "album_artist": "Drake", "album": "Views", "year": "2016-04-29",
        "genre": "rap, pop", "track_number": 1, "disc_number": 1,
        "has_cover_art": True, "error": None,
    }
    monkeypatch.setattr("core.library2.paths.resolve_lib2_path", lambda _path: str(file_path))
    monkeypatch.setattr("core.tag_writer.read_file_tags", lambda _path: file_tags)
    monkeypatch.setattr("core.tag_writer.build_tag_diff", lambda *_args: [])

    stats = retag.write_tags(legacy_db, [track_id], embed_cover=True, force_cover=True)

    assert stats["skipped"] == 1
    assert stats["written"] == 0


def test_write_closes_snapshot_connection_before_file_io(
        imported_conn, legacy_db, tmp_path, monkeypatch):
    conn = imported_conn
    file_path = tmp_path / "track.flac"
    file_path.write_bytes(b"fake")
    _, _, track_id = _seed_album_with_files(conn, path="/mapped/track.flac")
    state = {"active": 0, "opened": 0}

    class _TrackedConnection:
        def __init__(self):
            self._conn = sqlite3.connect(legacy_db.path)
            self._conn.row_factory = sqlite3.Row
            state["active"] += 1
            state["opened"] += 1

        def __getattr__(self, name):
            return getattr(self._conn, name)

        def close(self):
            self._conn.close()
            state["active"] -= 1

    class _Shim:
        def _get_connection(self):
            return _TrackedConnection()

    def _assert_closed(_path):
        assert state["active"] == 0
        return str(file_path)

    def _read_tags(_path):
        assert state["active"] == 0
        return {"error": None}

    monkeypatch.setattr("core.library2.paths.resolve_lib2_path", _assert_closed)
    monkeypatch.setattr("core.tag_writer.read_file_tags", _read_tags)
    monkeypatch.setattr("core.tag_writer.build_tag_diff", lambda *_args: [])

    stats = retag.write_tags(_Shim(), [track_id], embed_cover=False)

    assert stats["skipped"] == 1
    assert state == {"active": 0, "opened": 2}
