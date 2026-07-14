"""Scope semantics for the file rescan (audit P1-08).

``album_ids=None`` means the whole library; ``[]`` means nothing. The empty
list must never widen into an unscoped full-library scan — an artist without
albums would otherwise probe every file in the database.
"""

from __future__ import annotations

import sqlite3
import json

import pytest

from core.library2.scan import _file_rows_in_scope
from core.library2.schema import ensure_library_v2_schema


@pytest.fixture
def scoped_conn(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "lib2.db"))
    conn.row_factory = sqlite3.Row
    ensure_library_v2_schema(conn)
    cur = conn.cursor()
    cur.execute("INSERT INTO lib2_artists(name) VALUES('A')")
    artist_id = cur.lastrowid
    album_ids = []
    for title, path in (("Album One", "/m/a.flac"), ("Album Two", "/m/b.flac")):
        cur.execute("INSERT INTO lib2_albums(primary_artist_id, title) VALUES(?,?)",
                    (artist_id, title))
        album_id = cur.lastrowid
        cur.execute("INSERT INTO lib2_tracks(album_id, title, track_number) VALUES(?,?,1)",
                    (album_id, title))
        cur.execute("INSERT INTO lib2_track_files(track_id, path) VALUES(?,?)",
                    (cur.lastrowid, path))
        album_ids.append(album_id)
    conn.commit()
    yield conn, album_ids
    conn.close()


def test_none_scope_scans_whole_library(scoped_conn):
    conn, _album_ids = scoped_conn
    rows = _file_rows_in_scope(conn, album_ids=None)
    assert sorted(r["path"] for r in rows) == ["/m/a.flac", "/m/b.flac"]


def test_single_album_scope_stays_scoped(scoped_conn):
    conn, album_ids = scoped_conn
    rows = _file_rows_in_scope(conn, album_ids=[album_ids[0]])
    assert [r["path"] for r in rows] == ["/m/a.flac"]


def test_empty_scope_scans_nothing(scoped_conn):
    """[] must not fall through to the unscoped full-library query."""
    conn, _album_ids = scoped_conn
    assert _file_rows_in_scope(conn, album_ids=[]) == []


def test_rescan_files_with_empty_scope_probes_nothing(scoped_conn, tmp_path):
    from core.library2.scan import rescan_files

    class _Shim:
        def __init__(self, path):
            self.path = path

        def _get_connection(self):
            conn = sqlite3.connect(self.path)
            conn.row_factory = sqlite3.Row
            return conn

    db_path = str(tmp_path / "lib2.db")
    stats = rescan_files(_Shim(db_path), album_ids=[])
    assert stats == {"scanned": 0, "updated": 0, "missing": 0}


def test_rescan_refreshes_tag_and_gap_cache_independently_of_quality(
        scoped_conn, tmp_path, monkeypatch):
    from core.library2.scan import rescan_files

    conn, album_ids = scoped_conn
    db_path = conn.execute("PRAGMA database_list").fetchone()[2]
    file_path = tmp_path / "readable.flac"
    file_path.write_bytes(b"not-real-audio")

    class _Shim:
        def _get_connection(self):
            opened = sqlite3.connect(db_path)
            opened.row_factory = sqlite3.Row
            return opened

    monkeypatch.setattr("core.library2.paths.resolve_lib2_path", lambda _path: str(file_path))
    monkeypatch.setattr("core.imports.file_ops.probe_audio_quality", lambda _path: None)
    monkeypatch.setattr("core.tag_writer.read_file_tags", lambda _path: {
        "title": "Album One",
        "artist": "A",
        "album": "Album One",
        "album_artist": "A",
        "track_number": 1,
        "disc_number": 1,
        "year": "2026",
        "genre": None,
        "has_cover_art": False,
        "error": None,
    })

    stats = rescan_files(_Shim(), album_ids=[album_ids[0]])

    row = conn.execute(
        """SELECT tags_json, missing_tags_json, metadata_gaps_json
             FROM lib2_track_files WHERE path='/m/a.flac'"""
    ).fetchone()
    assert stats == {"scanned": 1, "updated": 0, "missing": 0}
    assert json.loads(row["tags_json"])["title"] == "Album One"
    assert json.loads(row["missing_tags_json"]) == ["genre", "cover"]
    assert json.loads(row["metadata_gaps_json"]) == ["genre", "cover"]


def test_failed_tag_read_invalidates_stale_gap_cache(scoped_conn):
    from core.library2.tag_cache import persist_tag_cache

    conn, _album_ids = scoped_conn
    file_id = conn.execute(
        "SELECT id FROM lib2_track_files WHERE path='/m/a.flac'"
    ).fetchone()[0]
    conn.execute(
        """UPDATE lib2_track_files
              SET tags_json='{"title":"stale"}',
                  missing_tags_json='["cover"]',
                  metadata_gaps_json='["cover"]'
            WHERE id=?""",
        (file_id,),
    )

    assert persist_tag_cache(conn, file_id, {"error": "unreadable"}) is False

    row = conn.execute(
        """SELECT tags_json, missing_tags_json, metadata_gaps_json
             FROM lib2_track_files WHERE id=?""",
        (file_id,),
    ).fetchone()
    assert json.loads(row["tags_json"]) == {}
    assert json.loads(row["missing_tags_json"]) is None
    assert json.loads(row["metadata_gaps_json"]) is None


def test_healthy_consecutive_misses_confirm_and_recovery_resets_lifecycle(
        scoped_conn, tmp_path, monkeypatch):
    from core.library2.scan import rescan_files

    conn, album_ids = scoped_conn
    db_path = conn.execute("PRAGMA database_list").fetchone()[2]

    class _Shim:
        def _get_connection(self):
            opened = sqlite3.connect(db_path)
            opened.row_factory = sqlite3.Row
            return opened

    monkeypatch.setattr("core.library2.paths.resolve_lib2_path", lambda _path: None)
    monkeypatch.setattr(
        "core.library2.paths.missing_path_root_is_healthy", lambda _path: True
    )

    rescan_files(_Shim(), album_ids=[album_ids[0]])
    first = conn.execute(
        """SELECT file_state, missing_scan_count, missing_since
             FROM lib2_track_files WHERE path='/m/a.flac'"""
    ).fetchone()
    assert first["file_state"] == "missing_suspected"
    assert first["missing_scan_count"] == 1
    assert first["missing_since"] is not None

    rescan_files(_Shim(), album_ids=[album_ids[0]])
    second = conn.execute(
        """SELECT file_state, missing_scan_count
             FROM lib2_track_files WHERE path='/m/a.flac'"""
    ).fetchone()
    assert dict(second) == {
        "file_state": "missing_confirmed",
        "missing_scan_count": 2,
    }

    recovered = tmp_path / "recovered.flac"
    recovered.write_bytes(b"fake")
    monkeypatch.setattr(
        "core.library2.paths.resolve_lib2_path", lambda _path: str(recovered)
    )
    monkeypatch.setattr("core.tag_writer.read_file_tags", lambda _path: {"error": "fake"})
    monkeypatch.setattr("core.imports.file_ops.probe_audio_quality", lambda _path: None)
    rescan_files(_Shim(), album_ids=[album_ids[0]])

    final = conn.execute(
        """SELECT file_state, missing_scan_count, missing_since
             FROM lib2_track_files WHERE path='/m/a.flac'"""
    ).fetchone()
    assert dict(final) == {
        "file_state": "active",
        "missing_scan_count": 0,
        "missing_since": None,
    }


def test_unhealthy_root_does_not_advance_missing_lifecycle(
        scoped_conn, monkeypatch):
    from core.library2.scan import rescan_files

    conn, album_ids = scoped_conn
    db_path = conn.execute("PRAGMA database_list").fetchone()[2]

    class _Shim:
        def _get_connection(self):
            opened = sqlite3.connect(db_path)
            opened.row_factory = sqlite3.Row
            return opened

    monkeypatch.setattr("core.library2.paths.resolve_lib2_path", lambda _path: None)
    monkeypatch.setattr(
        "core.library2.paths.missing_path_root_is_healthy", lambda _path: False
    )
    rescan_files(_Shim(), album_ids=[album_ids[0]])

    row = conn.execute(
        """SELECT file_state, missing_scan_count, missing_since
             FROM lib2_track_files WHERE path='/m/a.flac'"""
    ).fetchone()
    assert dict(row) == {
        "file_state": "active",
        "missing_scan_count": 0,
        "missing_since": None,
    }


def test_root_health_requires_every_configured_library_mount(tmp_path):
    from core.library2.paths import missing_path_root_is_healthy

    healthy = tmp_path / "music-a"
    healthy.mkdir()

    class _Config:
        roots = [str(healthy)]

        def get(self, key, default=None):
            assert key == "library.music_paths"
            return self.roots

    config = _Config()
    assert missing_path_root_is_healthy("/remote/Artist/song.flac", config)
    config.roots.append(str(tmp_path / "offline-mount"))
    assert not missing_path_root_is_healthy("/remote/Artist/song.flac", config)
