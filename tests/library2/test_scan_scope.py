"""Scope semantics for the file rescan (audit P1-08).

``album_ids=None`` means the whole library; ``[]`` means nothing. The empty
list must never widen into an unscoped full-library scan — an artist without
albums would otherwise probe every file in the database.
"""

from __future__ import annotations

import sqlite3

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
