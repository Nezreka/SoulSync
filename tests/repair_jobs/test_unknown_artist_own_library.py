"""#1504 — Unknown Artist Fixer must not migrate own-library tracks to shared.

``_apply_fix`` used to join the template-built relative path onto the shared
transfer folder, silently moving an own-library track across libraries.
Now the destination root comes from ``library_root_for_profile`` per track,
and the post-move parent climb can never rmdir the own-library root.

Hermetic: the per-profile root lookup is faked; moves happen on tmp_path.
"""

from __future__ import annotations

import os

import pytest

import core.imports.paths as paths
from core.repair_jobs.unknown_artist_fixer import UnknownArtistFixerJob


@pytest.fixture
def own_root(monkeypatch):
    """profile 2 owns /own; every other profile (incl. unknown) is shared.

    The destination root is derived from the file's PATH (ground truth),
    never the DB row — so the db fake only lists profile 2's root."""
    import database.music_database as mdb

    class _FakeDb:
        def get_own_library_profiles(self):
            return [{"id": 2, "name": "u2", "root": "/own"}]

    monkeypatch.setattr(mdb, "get_database", lambda: _FakeDb())
    monkeypatch.setattr(
        paths, "library_root_for_profile",
        lambda pid, announce=True: "/own" if int(pid) == 2 else None,
    )


# ── _destination_root: the PATH decides, never the DB row ──────────────────

def test_destination_root_own_library(own_root):
    job = UnknownArtistFixerJob()
    assert job._destination_root("/own/Old Artist/x.mp3", "/Transfer") == "/own"


def test_destination_root_shared_for_shared_path(own_root):
    job = UnknownArtistFixerJob()
    assert job._destination_root("/Transfer/Artist/x.mp3", "/Transfer") == "/Transfer"


def test_destination_root_path_boundary_is_exact(own_root):
    job = UnknownArtistFixerJob()
    # /owns is NOT under /own
    assert job._destination_root("/owns/Artist/x.mp3", "/Transfer") == "/Transfer"


def test_destination_root_fail_open_when_lookup_raises(monkeypatch):
    def _boom():
        raise RuntimeError("db gone")
    import database.music_database as mdb
    monkeypatch.setattr(mdb, "get_database", _boom)
    job = UnknownArtistFixerJob()
    assert job._destination_root("/own/Artist/x.mp3", "/Transfer") == "/Transfer"


# ── _apply_fix moves inside the own library, never past its root ──────────

def _track():
    return {
        "id": 7, "title": "Song",
        "artist_id": "a1", "album_id": "b1", "album_title": "Old Album",
    }


def _corrected():
    return {
        "artist": "Real Artist", "album": "Real Album", "title": "Song",
        "track_number": 1, "disc_number": 1, "year": "2020",
    }


def _run_apply(tmp_path, monkeypatch, *, src_under_own):
    """Run _apply_fix (tags off, reorganize on) and return (job, expected dir).

    The source file is placed under the own-library root or the shared
    transfer folder; the destination root must follow the PATH, not any
    row-level owner (the track dict carries no owner at all — the NULL-row
    case from the issue, where SoulSync downloads never stamp one)."""
    import database.music_database as mdb

    transfer = tmp_path / "Transfer"
    own = tmp_path / "own"

    class _FakeDb:
        def get_own_library_profiles(self):
            return [{"id": 2, "name": "u2", "root": str(own)}]

    monkeypatch.setattr(mdb, "get_database", lambda: _FakeDb())
    monkeypatch.setattr(
        paths, "library_root_for_profile",
        lambda pid, announce=True: str(own) if int(pid) == 2 else None,
    )
    base = own if src_under_own else transfer
    src_dir = base / "Old Artist" / "Old Album"
    src_dir.mkdir(parents=True)
    src = src_dir / "01 - Song.mp3"
    src.write_bytes(b"\x00" * 64)

    job = UnknownArtistFixerJob()
    ok = job._apply_fix(
        context=None,
        track=_track(),
        corrected=_corrected(),
        resolved_path=str(src),
        expected_rel=os.path.join("Real Artist", "Real Artist - Real Album",
                                  "01 - Song.mp3"),
        transfer=str(transfer),
        fix_tags=False,
        reorganize_files=True,
    )
    return job, own, transfer, src, ok


def test_apply_fix_moves_into_own_library(tmp_path, monkeypatch):
    _job, own, _transfer, src, _ok = _run_apply(
        tmp_path, monkeypatch, src_under_own=True)
    dest = own / "Real Artist" / "Real Artist - Real Album" / "01 - Song.mp3"
    assert dest.is_file()          # moved under the OWN root, not Transfer
    assert not src.exists()
    assert not (tmp_path / "Transfer").exists() or not any(
        (tmp_path / "Transfer").rglob("*.mp3"))


def test_apply_fix_shared_track_stays_in_shared(tmp_path, monkeypatch):
    _job, _own, transfer, _src, _ok = _run_apply(
        tmp_path, monkeypatch, src_under_own=False)
    dest = transfer / "Real Artist" / "Real Artist - Real Album" / "01 - Song.mp3"
    # shared path → shared folder, exactly today's behavior
    assert dest.is_file()


def test_apply_fix_never_removes_own_library_root(tmp_path, monkeypatch):
    _job, own, _transfer, _src, _ok = _run_apply(
        tmp_path, monkeypatch, src_under_own=True)
    # the emptied Old Artist/Old Album parents may go, but the own root stays
    assert own.is_dir()
