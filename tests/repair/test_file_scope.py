"""Exact file scoping for destructive/moving repair jobs."""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace
from unittest.mock import MagicMock

from core.repair_jobs.base import (
    build_artist_file_scope,
    file_path_in_scope,
    get_scope_file_paths,
    JobContext,
)
from core.repair_jobs.single_album_dedup import SingleAlbumDedupJob


class _DB:
    def __init__(self, conn):
        self.conn = conn

    def _get_connection(self):
        return self.conn


class _NonClosingConnection:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, *args, **kwargs):
        return self.conn.execute(*args, **kwargs)

    def close(self):
        pass


def test_build_artist_file_scope_uses_lib2_links_and_keeps_empty_scope_explicit():
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE lib2_artists(id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE lib2_album_artists(album_id INTEGER, artist_id INTEGER);
        CREATE TABLE lib2_tracks(id INTEGER PRIMARY KEY, album_id INTEGER);
        CREATE TABLE lib2_track_files(track_id INTEGER, path TEXT);
        INSERT INTO lib2_artists VALUES(1, 'Artist'), (2, 'Empty');
        INSERT INTO lib2_album_artists VALUES(10, 1);
        INSERT INTO lib2_tracks VALUES(100, 10), (101, 10);
        INSERT INTO lib2_track_files VALUES
            (100, '/music/Artist/Album/a.flac'),
            (101, '/music/Artist/Album/b.flac');
    """)
    db = _DB(_NonClosingConnection(conn))

    scope = build_artist_file_scope(db, 1)
    assert scope == {
        "artist_id": 1,
        "artist_name": "Artist",
        "file_paths": [
            "/music/Artist/Album/a.flac",
            "/music/Artist/Album/b.flac",
        ],
    }
    assert build_artist_file_scope(db, 2)["file_paths"] == []


def test_file_scope_is_exact_and_empty_never_means_library_wide():
    context = SimpleNamespace(scope={"file_paths": [r"C:\\Music\\Artist\\one.flac"]})
    allowed = get_scope_file_paths(context)
    assert file_path_in_scope("C:/Music/Artist/one.flac", allowed)
    assert not file_path_in_scope("C:/Music/Artist/two.flac", allowed)
    assert not file_path_in_scope("/any/file.flac", frozenset())
    assert file_path_in_scope("/any/file.flac", None)


def test_dedup_scopes_actionable_single_path_but_keeps_album_candidates_global():
    rows = [
        (1, "Song", "Artist", "Song", "single", 1,
         "/music/Artist/Song/single.flac", 1000, 180, None, None, 1),
        (2, "Song", "Artist", "Other Single", "single", 1,
         "/music/Other/Song/single.flac", 1000, 180, None, None, 1),
        (3, "Song", "Artist", "Full Album", "album", 10,
         "/music/Other/Album/song.flac", 1000, 180, None, None, 4),
    ]
    cursor = MagicMock()
    cursor.fetchall.return_value = rows
    conn = MagicMock()
    conn.cursor.return_value = cursor
    db = MagicMock()
    db._get_connection.return_value = conn
    findings = []
    context = JobContext(
        db=db,
        transfer_folder="/music",
        config_manager=None,
        scope={"file_paths": ["/music/Artist/Song/single.flac"]},
        create_finding=lambda **finding: findings.append(finding) or True,
    )

    result = SingleAlbumDedupJob().scan(context)

    assert result.findings_created == 1
    assert findings[0]["entity_id"] == "1"
    assert findings[0]["details"]["album_track"]["id"] == 3
