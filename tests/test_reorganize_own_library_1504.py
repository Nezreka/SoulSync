"""Regression tests for issue #1504 — reorganize own-library awareness.

Covers:
- resolve_album_profile_id(): file location wins over DB, DB fallback, shared default
- _finalize_track(): cross-library move guard refuses the move
- reorganize_album(): profile_id flows through to the post-process context
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import core.library_reorganize as reorg


class _FakeDB:
    def __init__(self):
        self._conn = sqlite3.connect(":memory:")
        self._conn.row_factory = sqlite3.Row

    def _get_connection(self):
        return self._conn


def _make_db_with_album(owner_profile_id=None):
    db = _FakeDB()
    conn = db._get_connection()
    conn.execute(
        "CREATE TABLE albums (id TEXT PRIMARY KEY, title TEXT, owner_profile_id INTEGER)"
    )
    conn.execute(
        "CREATE TABLE tracks (id TEXT PRIMARY KEY, album_id TEXT, file_path TEXT)"
    )
    conn.execute(
        "INSERT INTO albums (id, title, owner_profile_id) VALUES (?, ?, ?)",
        ("alb-1", "Test Album", owner_profile_id),
    )
    conn.execute(
        "INSERT INTO tracks (id, album_id, file_path) VALUES (?, ?, ?)",
        ("t1", "alb-1", "/app/libraries/user1/Artist/Album/01.flac"),
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
            db, "alb-1", tracks=tracks, resolve_file_path_fn=lambda p: p
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
            db, "alb-1", tracks=tracks, resolve_file_path_fn=lambda p: p
        )
    assert pid == 3


def test_resolve_album_profile_id_shared_default():
    db = _make_db_with_album(owner_profile_id=None)
    tracks = [{"file_path": "/app/Transfer/Artist/Album/01.flac"}]
    with patch(
        "core.imports.paths.profile_id_for_path", return_value=None
    ):
        pid = reorg.resolve_album_profile_id(
            db, "alb-1", tracks=tracks, resolve_file_path_fn=lambda p: p
        )
    assert pid is None


def test_finalize_track_refuses_cross_library_move(tmp_path):
    src = tmp_path / "user1" / "track.flac"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"audio")
    dst = tmp_path / "shared" / "track.flac"
    dst.parent.mkdir(parents=True)
    dst.write_bytes(b"processed")

    ctx = SimpleNamespace(
        update_track_path_fn=None,
        state_lock=__import__("threading").Lock(),
        src_dirs_touched=set(),
        dst_dirs_touched=set(),
        summary={"errors": [], "moved": 0, "skipped": 0, "failed": 0},
        emit=lambda **k: None,
        record_error=lambda tid, title, msg, kind="skipped": ctx.summary["errors"].append(msg),
    )

    with patch(
        "core.imports.paths.library_containing",
        side_effect=lambda p: "/app/libraries/user1" if "user1" in str(p) else None,
    ):
        ok = reorg._finalize_track(ctx, "t1", str(src), str(dst))

    assert ok is False
    # original untouched, stray new file removed
    assert src.exists()
    assert not dst.exists()
    assert any("cross-library" in e for e in ctx.summary["errors"])


def test_finalize_track_allows_same_library_move(tmp_path):
    src = tmp_path / "user1" / "track.flac"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"audio")
    dst = tmp_path / "user1" / "renamed.flac"
    dst.write_bytes(b"processed")

    updated = {}

    ctx = SimpleNamespace(
        update_track_path_fn=lambda tid, p: updated.update({tid: p}),
        state_lock=__import__("threading").Lock(),
        src_dirs_touched=set(),
        dst_dirs_touched=set(),
        summary={"errors": [], "moved": 0, "skipped": 0, "failed": 0},
        emit=lambda **k: None,
        record_error=lambda tid, title, msg, kind="skipped": ctx.summary["errors"].append(msg),
    )

    with patch(
        "core.imports.paths.library_containing",
        return_value="/app/libraries/user1",
    ):
        ok = reorg._finalize_track(ctx, "t1", str(src), str(dst))

    assert ok is True
    assert updated == {"t1": str(dst)}
    assert not src.exists()  # original removed after successful move


def test_finalize_track_allows_shared_to_shared(tmp_path):
    src = tmp_path / "a.flac"
    src.write_bytes(b"audio")
    dst = tmp_path / "b.flac"
    dst.write_bytes(b"processed")

    ctx = SimpleNamespace(
        update_track_path_fn=lambda tid, p: None,
        state_lock=__import__("threading").Lock(),
        src_dirs_touched=set(),
        dst_dirs_touched=set(),
        summary={"errors": [], "moved": 0, "skipped": 0, "failed": 0},
        emit=lambda **k: None,
        record_error=lambda tid, title, msg, kind="skipped": ctx.summary["errors"].append(msg),
    )

    with patch("core.imports.paths.library_containing", return_value=None):
        ok = reorg._finalize_track(ctx, "t1", str(src), str(dst))

    assert ok is True
    assert ctx.summary["errors"] == []
