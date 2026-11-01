"""Regression tests for issue #1504 — reorganize own-library awareness.

Covers:
- resolve_album_profile_id(): file location wins over DB, DB fallback, shared default
- reorganize_album_rename_only(): the cross-library move guard refuses the move
"""
from __future__ import annotations

import sqlite3
from unittest.mock import patch


import core.library_reorganize as reorg


class _FakeDB:
    def __init__(self):
        self._conn = sqlite3.connect(":memory:")
        self._conn.row_factory = sqlite3.Row

    def _get_connection(self):
        return self._conn


def _make_db_with_album(owner_profile_id=None):
    """Ours: Library v2 keeps the owner per file (lib2_track_files)."""
    db = _FakeDB()
    conn = db._get_connection()
    conn.execute("CREATE TABLE lib2_albums (id INTEGER PRIMARY KEY, title TEXT)")
    conn.execute("CREATE TABLE lib2_tracks (id INTEGER PRIMARY KEY, album_id INTEGER)")
    conn.execute(
        "CREATE TABLE lib2_track_files (id INTEGER PRIMARY KEY, track_id INTEGER, path TEXT,"
        " file_state TEXT DEFAULT 'active', is_primary INTEGER DEFAULT 1, owner_profile_id INTEGER)"
    )
    conn.execute("INSERT INTO lib2_albums (id, title) VALUES (1, 'Test Album')")
    conn.execute("INSERT INTO lib2_tracks (id, album_id) VALUES (1, 1)")
    conn.execute(
        "INSERT INTO lib2_track_files (track_id, path, owner_profile_id) VALUES (1, ?, ?)",
        ("/app/libraries/user1/Artist/Album/01.flac", owner_profile_id),
    )
    conn.commit()
    return db


def test_resolve_album_profile_id_file_location_wins():
    db = _make_db_with_album(owner_profile_id=99)  # DB says 99...
    tracks = [{"file_path": "/app/libraries/user1/Artist/Album/01.flac"}]
    with patch(
        "core.imports.paths.profile_id_for_path", return_value=2
    ):
        # ...but the file is in profile 2's library, so 2 wins
        pid = reorg.resolve_album_profile_id(
            db, 1, tracks=tracks, resolve_file_path_fn=lambda p: p
        )
    assert pid == 2


def test_resolve_album_profile_id_falls_back_to_db():
    db = _make_db_with_album(owner_profile_id=3)
    tracks = [{"file_path": "/app/Transfer/Artist/Album/01.flac"}]
    with patch(
        "core.imports.paths.profile_id_for_path", return_value=None
    ):
        # file not in any own library -> DB owner wins
        pid = reorg.resolve_album_profile_id(
            db, 1, tracks=tracks, resolve_file_path_fn=lambda p: p
        )
    assert pid == 3


def test_resolve_album_profile_id_shared_default():
    db = _make_db_with_album(owner_profile_id=None)
    tracks = [{"file_path": "/app/Transfer/Artist/Album/01.flac"}]
    with patch(
        "core.imports.paths.profile_id_for_path", return_value=None
    ):
        pid = reorg.resolve_album_profile_id(
            db, 1, tracks=tracks, resolve_file_path_fn=lambda p: p
        )
    assert pid is None


def _move_one(tmp_path, src, dst, library_of):
    """Run the mover over a one-track plan from ``src`` to ``dst``."""
    updated = {}

    def preview_fn(**_kwargs):
        return {"success": True, "status": "planned", "tracks": [{
            "track_id": "t1", "title": "Track", "matched": True,
            "current_path_abs": str(src), "new_path_abs": str(dst),
        }]}

    with patch("core.imports.paths.library_containing", side_effect=library_of):
        summary = reorg.reorganize_album_rename_only(
            album_id="1", db=_FakeDB(), transfer_dir=str(tmp_path),
            resolve_file_path_fn=lambda p: p, build_final_path_fn=lambda *a, **k: (None, True),
            update_track_path_fn=lambda tid, p: updated.update({tid: p}),
            preview_fn=preview_fn,
        )
    return summary, updated


def test_the_mover_refuses_a_cross_library_move(tmp_path):
    src = tmp_path / "user1" / "track.flac"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"audio")
    dst = tmp_path / "shared" / "track.flac"

    summary, updated = _move_one(
        tmp_path, src, dst,
        lambda p: "/app/libraries/user1" if "user1" in str(p) else None)

    assert summary["failed"] == 1 and summary["moved"] == 0
    assert any("cross-library" in e["error"] for e in summary["errors"])
    assert src.exists() and not dst.exists()
    assert updated == {}


def test_the_mover_moves_within_one_library(tmp_path):
    src = tmp_path / "user1" / "track.flac"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"audio")
    dst = tmp_path / "user1" / "renamed.flac"

    summary, updated = _move_one(tmp_path, src, dst, lambda p: "/app/libraries/user1")

    assert summary["moved"] == 1 and summary["errors"] == []
    assert dst.exists() and not src.exists()
    assert updated == {"t1": str(dst)}
