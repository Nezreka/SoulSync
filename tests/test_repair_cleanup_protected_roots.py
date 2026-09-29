"""Regression tests: repair cleanups must never remove a configured root folder.

Specialmed (Unraid): "Staging/Import-Folder is still randomly being deleted
from host system."

Root cause: the repair worker's empty-parent cleanups only guarded the
*transfer* folder. On the UnRaid single-share layout the staging folder is
nested under the transfer folder, so when an orphan "delete" fix removed the
last file from the staging dir, ``_cleanup_empty_parents`` rmdir'd the staging
folder itself and kept walking up. The import-side cleanups were already fixed
for this class in #976 via ``protected_root_dirs()``; the repair worker had
its own copies of the guard that never consulted it.

The fix: ``RepairWorker._cleanup_empty_parents`` / ``_cleanup_empty_dirs``
(and the walk-ups they replaced) now break on every protected root.
"""
from __future__ import annotations

import os

from database.music_database import MusicDatabase
from core.repair_worker import RepairWorker


class _FakeCfg:
    def __init__(self, values):
        self._values = values

    def get(self, key, default=None):
        return self._values.get(key, default)


def _worker(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    w = RepairWorker(database=db)
    w._config_manager = None
    w.transfer_folder = str(tmp_path / "Transfer")
    return db, w


def _patch_roots(monkeypatch, transfer, staging, downloads):
    fake = _FakeCfg({
        'import.staging_path': str(staging),
        'soulseek.download_path': str(downloads),
        'soulseek.transfer_path': str(transfer),
    })
    # protected_root_dirs() reads the module-global config_manager in
    # core.imports.file_ops (the same singleton web_server configures).
    monkeypatch.setattr('core.imports.file_ops.config_manager', fake)


def _unraid_layout(tmp_path):
    """Transfer/Staging nested under Transfer — the #976 UnRaid layout."""
    transfer = tmp_path / "Transfer"
    staging = transfer / "Staging"
    downloads = tmp_path / "Downloads"
    album_dir = staging / "SomeAlbum"
    album_dir.mkdir(parents=True)
    track = album_dir / "track.mp3"
    track.write_bytes(b"\x00" * 32)
    return transfer, staging, downloads, album_dir, track


def test_cleanup_empty_parents_keeps_nested_staging_root(tmp_path, monkeypatch):
    transfer, staging, downloads, album_dir, track = _unraid_layout(tmp_path)
    _patch_roots(monkeypatch, transfer, staging, downloads)
    _, w = _worker(tmp_path)

    track.unlink()  # the orphan "delete" fix removed the last file
    w._cleanup_empty_parents(str(track))

    assert not album_dir.exists()  # ordinary empty dir still pruned
    assert staging.is_dir()        # the staging ROOT must survive
    assert transfer.is_dir()       # and the walk must stop there


def test_cleanup_empty_parents_still_prunes_ordinary_dirs(tmp_path, monkeypatch):
    transfer, staging, downloads, album_dir, track = _unraid_layout(tmp_path)
    _patch_roots(monkeypatch, transfer, staging, downloads)
    _, w = _worker(tmp_path)

    other = transfer / "SomeArtist" / "SomeAlbum"
    other.mkdir(parents=True)
    f = other / "song.flac"
    f.write_bytes(b"\x00" * 32)
    f.unlink()

    w._cleanup_empty_parents(str(f))

    assert not other.exists()
    assert not (transfer / "SomeArtist").exists()
    assert transfer.is_dir()


def test_cleanup_empty_dirs_keeps_nested_staging_root(tmp_path, monkeypatch):
    transfer, staging, downloads, album_dir, track = _unraid_layout(tmp_path)
    _patch_roots(monkeypatch, transfer, staging, downloads)
    _, w = _worker(tmp_path)

    track.unlink()
    w._cleanup_empty_dirs(str(album_dir))

    assert not album_dir.exists()
    assert staging.is_dir()
    assert transfer.is_dir()


def test_cleanup_empty_parents_never_removes_transfer_root(tmp_path, monkeypatch):
    transfer = tmp_path / "Transfer"
    transfer.mkdir()
    staging = tmp_path / "Staging"
    downloads = tmp_path / "Downloads"
    _patch_roots(monkeypatch, transfer, staging, downloads)
    _, w = _worker(tmp_path)

    lone = transfer / "lone.mp3"
    lone.write_bytes(b"\x00" * 32)
    lone.unlink()
    w._cleanup_empty_parents(str(lone))

    assert transfer.is_dir()


def test_protected_root_dirs_includes_all_configured_roots(tmp_path, monkeypatch):
    transfer, staging, downloads, _, _ = _unraid_layout(tmp_path)
    _patch_roots(monkeypatch, transfer, staging, downloads)
    _, w = _worker(tmp_path)

    protected = w._protected_root_dirs()
    assert os.path.normpath(str(staging)) in protected
    assert os.path.normpath(str(downloads)) in protected
    assert os.path.normpath(str(transfer)) in protected
