"""#1504 review findings — own-library cleanup guards.

1. ``_protected_root_dirs`` must include every active own-library root, and
   ``_cleanup_empty_parents`` must never rmdir one (a repair fix emptying an
   own library must not delete the configured library folder itself).
2. ``_fix_orphan_file`` with ``fix_action='staging'`` must leave a
   fingerprinted profile sidecar next to the staged file, so the auto-import
   worker routes an own-library orphan back into its own library.

Hermetic: real sqlite db on tmp_path; the own-library root is a tmp dir.
"""

from __future__ import annotations

import json
import os

from database.music_database import MusicDatabase
from core.repair_worker import RepairWorker
from core.repair_jobs.relocate import (
    PROFILE_SIDECAR_SUFFIX, profile_sidecar_path,
)


class _Config:
    def __init__(self, mapping):
        self._mapping = mapping

    def get(self, key, default=None):
        return self._mapping.get(key, default)


def _worker(tmp_path, monkeypatch, own_root):
    db = MusicDatabase(str(tmp_path / "music.db"))
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO profiles (name, library_mode, library_root) "
            "VALUES ('u2', 'own', ?)", (str(own_root),))
        conn.commit()
    # own_library_roots()/library_root_for_profile() resolve through the
    # global get_database() — point it at this test's db
    import database.music_database as mdb
    monkeypatch.setattr(mdb, "get_database", lambda: db)
    w = RepairWorker(database=db)
    transfer = tmp_path / "Transfer"
    transfer.mkdir()
    staging = tmp_path / "Staging"
    w._config_manager = _Config({
        'import.staging_path': str(staging),
        'soulseek.transfer_path': str(transfer),
        'soulseek.download_path': str(tmp_path / "Downloads"),
        'auto_import.enabled': False,
    })
    w.transfer_folder = str(transfer)
    return db, w


# ── _protected_root_dirs / _cleanup_empty_parents ──────────────────────────

def test_protected_roots_include_own_library(tmp_path, monkeypatch):
    own = tmp_path / "own"
    own.mkdir()
    _db, w = _worker(tmp_path, monkeypatch, own)
    protected = w._protected_root_dirs()
    assert os.path.normpath(os.path.realpath(str(own))) in {
        os.path.normpath(os.path.realpath(p)) for p in protected}


def test_cleanup_empty_parents_never_removes_own_root(tmp_path, monkeypatch):
    own = tmp_path / "own"
    nested = own / "Artist" / "Album"
    nested.mkdir(parents=True)
    victim = nested / "song.flac"
    victim.write_bytes(b"\x00" * 16)
    _db, w = _worker(tmp_path, monkeypatch, own)

    victim.unlink()
    w._cleanup_empty_parents(str(victim))

    # emptied album/artist parents may go, the library root itself must stay
    assert own.is_dir()


# ── orphan → staging carries the owner across ────────────────────────────

def test_orphan_staging_writes_profile_sidecar(tmp_path, monkeypatch):
    own = tmp_path / "own"
    src_dir = own / "Artist"
    src_dir.mkdir(parents=True)
    src = src_dir / "orphan.flac"
    src.write_bytes(b"\x00" * 32)
    _db, w = _worker(tmp_path, monkeypatch, own)

    res = w._fix_orphan_file('track', '1', str(src), {'_fix_action': 'staging'})
    assert res['success'] is True, res

    staging = tmp_path / "Staging"
    staged = staging / "orphan.flac"
    assert staged.is_file()
    sidecar = staging / ("orphan.flac" + PROFILE_SIDECAR_SUFFIX)
    assert sidecar.is_file()
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    assert data["profile_id"] == 2
    # fingerprinted to the staged file
    assert data["size"] == os.stat(str(staged)).st_size


def test_orphan_staging_shared_file_writes_no_sidecar(tmp_path, monkeypatch):
    own = tmp_path / "own"
    own.mkdir()
    _db, w = _worker(tmp_path, monkeypatch, own)
    transfer = tmp_path / "Transfer"
    src = transfer / "orphan.flac"
    src.write_bytes(b"\x00" * 32)

    res = w._fix_orphan_file('track', '1', str(src), {'_fix_action': 'staging'})
    assert res['success'] is True, res

    staged = tmp_path / "Staging" / "orphan.flac"
    assert staged.is_file()
    assert not os.path.exists(profile_sidecar_path(str(staged)))
