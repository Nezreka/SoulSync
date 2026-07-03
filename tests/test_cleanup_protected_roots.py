"""Regression for #976 — the import/staging folder gets deleted on the host.

Empty-folder cleanups walk UP from a moved file removing empty dirs, stopping
only at the *download* root. When a user's staging folder is nested under the
download folder (common on UnRaid single-share setups), auto-import empties
staging and the cleanup then `rmdir`'d the staging root itself — breaking the
import feature until the folder was manually recreated.

The fix: every empty-folder cleanup treats all configured roots
(staging/download/transfer) as protected and never removes them, however nested.
"""
import os

import core.imports.file_ops as file_ops
from core.imports.file_ops import (
    cleanup_empty_directories,
    ensure_staging_dir,
    protected_root_dirs,
)


def _patch_roots(monkeypatch, mapping):
    def fake_get(key, default=None):
        return mapping.get(key, default)
    monkeypatch.setattr(file_ops.config_manager, 'get', fake_get)


def test_nested_staging_root_is_not_deleted(tmp_path, monkeypatch):
    """The exact #976 case: staging nested under downloads. After the last file
    is moved out of staging, cleanup must NOT delete the staging root."""
    downloads = tmp_path / "downloads"
    staging = downloads / "staging"            # nested under downloads
    album = staging / "Artist - Album"
    os.makedirs(album)
    moved_file = str(album / "01 - song.flac")  # already moved to library (dir empty)

    _patch_roots(monkeypatch, {
        'soulseek.staging_path': str(staging),
        'soulseek.download_path': str(downloads),
        'soulseek.transfer_path': str(tmp_path / "library"),
    })

    cleanup_empty_directories(str(downloads), moved_file)

    assert staging.is_dir(), "staging root must survive cleanup (#976)"
    assert not album.exists(), "the empty album subfolder should still be removed"


def test_transient_subfolders_still_removed(tmp_path, monkeypatch):
    """The cleanup still does its job — empty download subfolders are pruned up
    to (but not including) the protected download root."""
    downloads = tmp_path / "downloads"
    sub = downloads / "Some Artist" / "Some Album"
    os.makedirs(sub)
    moved_file = str(sub / "1.flac")

    _patch_roots(monkeypatch, {
        'soulseek.staging_path': str(tmp_path / "staging"),   # separate, not nested
        'soulseek.download_path': str(downloads),
        'soulseek.transfer_path': str(tmp_path / "library"),
    })

    cleanup_empty_directories(str(downloads), moved_file)

    assert not sub.exists()
    assert not (downloads / "Some Artist").exists()   # walked up, empty → removed
    assert downloads.is_dir()                          # download root protected


def test_ensure_staging_recreates_when_parent_exists(tmp_path, monkeypatch):
    """#976 self-heal: a missing staging folder is recreated so the import
    feature doesn't error until the next Auto-Import scan."""
    downloads = tmp_path / "downloads"
    staging = downloads / "staging"
    downloads.mkdir()                       # parent exists, staging does not
    _patch_roots(monkeypatch, {'soulseek.staging_path': str(staging)})

    assert not staging.exists()
    ensure_staging_dir()
    assert staging.is_dir()


def test_ensure_staging_skips_when_parent_missing(tmp_path, monkeypatch):
    """Mount safety: never fabricate a not-yet-mounted volume path — if the
    parent doesn't exist we leave it alone rather than mask a missing mount."""
    staging = tmp_path / "not_mounted_yet" / "staging"   # parent absent
    _patch_roots(monkeypatch, {'soulseek.staging_path': str(staging)})

    ensure_staging_dir()
    assert not staging.exists()
    assert not staging.parent.exists()      # didn't fabricate the mount path either


def test_ensure_staging_noop_when_present(tmp_path, monkeypatch):
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "keep.flac").write_text("x")
    _patch_roots(monkeypatch, {'soulseek.staging_path': str(staging)})

    ensure_staging_dir()
    assert (staging / "keep.flac").is_file()   # untouched


def test_protected_roots_reads_all_three_configs(tmp_path, monkeypatch):
    _patch_roots(monkeypatch, {
        'soulseek.staging_path': str(tmp_path / "s"),
        'soulseek.download_path': str(tmp_path / "d"),
        'soulseek.transfer_path': str(tmp_path / "t"),
    })
    roots = protected_root_dirs()
    assert os.path.normpath(str(tmp_path / "s")) in roots
    assert os.path.normpath(str(tmp_path / "d")) in roots
    assert os.path.normpath(str(tmp_path / "t")) in roots
