"""Empty Folder Cleaner (corruption's request) — pure removable decision + the
apply handler's re-check safety (never deletes a folder that gained content, the
library root, or a symlink)."""

from __future__ import annotations

import os

from core.repair_jobs.base import JobContext
from core.repair_jobs.empty_folder_cleaner import (
    EmptyFolderCleanerJob, dir_is_removable, is_junk, remove_empty_folder,
)


# ── pure decision ───────────────────────────────────────────────────────────
def test_empty_dir_is_removable():
    assert dir_is_removable([], []) is True


def test_dir_with_real_file_is_not_removable():
    assert dir_is_removable(['cover.jpg'], []) is False
    assert dir_is_removable(['song.flac'], []) is False


def test_dir_with_surviving_subdir_is_not_removable():
    assert dir_is_removable([], ['Album']) is False


def test_junk_only_dir_removable_when_ignore_junk():
    assert dir_is_removable(['.DS_Store', 'Thumbs.db'], []) is True
    assert dir_is_removable(['.DS_Store'], [], ignore_junk=False) is False  # strict mode keeps it


def test_junk_plus_real_file_not_removable():
    assert dir_is_removable(['.DS_Store', 'cover.jpg'], []) is False


def test_is_junk():
    assert is_junk('.DS_Store') and is_junk('thumbs.db') and not is_junk('cover.jpg')


# ── #891: residual (image / sidecar only) folders ───────────────────────────
def test_image_only_dir_kept_by_default_removed_with_residual_opt():
    # Default (junk only): a cover.jpg keeps the folder (the conservative behavior).
    assert dir_is_removable(['cover.jpg'], []) is False
    # Opt-in: image/sidecar-only folders become removable.
    assert dir_is_removable(['cover.jpg'], [], ignore_disposable=True) is True
    assert dir_is_removable(['back.jpg', 'lyrics.lrc', '.DS_Store'], [], ignore_disposable=True) is True
    assert dir_is_removable(['folder.png', 'album.nfo'], [], ignore_disposable=True) is True


def test_residual_opt_still_keeps_real_content():
    # Audio, or anything not recognized as a leftover (a booklet pdf), still blocks.
    assert dir_is_removable(['cover.jpg', 'song.flac'], [], ignore_disposable=True) is False
    assert dir_is_removable(['cover.jpg', 'booklet.pdf'], [], ignore_disposable=True) is False
    assert dir_is_removable([], ['Album'], ignore_disposable=True) is False  # surviving subdir


# ── apply re-check (real FS) ────────────────────────────────────────────────
def _fx():
    return dict(listdir=os.listdir, isdir=os.path.isdir, islink=os.path.islink,
                remove_file=os.remove, rmdir=os.rmdir)


def test_apply_removes_truly_empty_folder(tmp_path):
    root = tmp_path / 'lib'; root.mkdir()
    empty = root / 'Artist' / 'Album'; empty.mkdir(parents=True)
    res = remove_empty_folder(str(empty), junk_files=[], remove_junk=True, root=str(root), **_fx())
    assert res['removed'] is True
    assert not empty.exists()


def test_apply_deletes_junk_then_folder(tmp_path):
    root = tmp_path / 'lib'; root.mkdir()
    d = root / 'Empty'; d.mkdir()
    (d / '.DS_Store').write_text('x')
    res = remove_empty_folder(str(d), junk_files=['.DS_Store'], remove_junk=True, root=str(root), **_fx())
    assert res['removed'] is True and not d.exists()


def test_apply_refuses_folder_that_gained_a_file(tmp_path):
    root = tmp_path / 'lib'; root.mkdir()
    d = root / 'NowFull'; d.mkdir()
    (d / 'new.flac').write_text('audio')         # appeared between scan and apply
    res = remove_empty_folder(str(d), junk_files=[], remove_junk=True, root=str(root), **_fx())
    assert res['removed'] is False and 'no longer empty' in res['error'].lower()
    assert d.exists()                             # left untouched


def test_apply_refuses_library_root(tmp_path):
    root = tmp_path / 'lib'; root.mkdir()
    res = remove_empty_folder(str(root), junk_files=[], remove_junk=True, root=str(root), **_fx())
    assert res['removed'] is False and 'root' in res['error'].lower()
    assert root.exists()


def test_apply_sweeps_residual_then_folder_when_enabled(tmp_path):
    root = tmp_path / 'lib'; root.mkdir()
    d = root / 'Artist' / 'Old Album'; d.mkdir(parents=True)
    (d / 'cover.jpg').write_text('img')
    (d / 'back.jpg').write_text('img')
    (d / 'lyrics.lrc').write_text('la')
    res = remove_empty_folder(str(d), junk_files=[], remove_junk=True,
                              remove_disposable=True, root=str(root), **_fx())
    assert res['removed'] is True and not d.exists()


def test_apply_without_residual_opt_leaves_image_folder(tmp_path):
    # The default apply (no residual opt) must NOT delete a cover.jpg folder.
    root = tmp_path / 'lib'; root.mkdir()
    d = root / 'HasCover'; d.mkdir()
    (d / 'cover.jpg').write_text('img')
    res = remove_empty_folder(str(d), junk_files=[], remove_junk=True, root=str(root), **_fx())
    assert res['removed'] is False and d.exists()


def test_apply_residual_opt_still_refuses_real_content(tmp_path):
    root = tmp_path / 'lib'; root.mkdir()
    d = root / 'Mixed'; d.mkdir()
    (d / 'cover.jpg').write_text('img')
    (d / 'booklet.pdf').write_text('pdf')        # unrecognized → real content
    res = remove_empty_folder(str(d), junk_files=[], remove_junk=True,
                              remove_disposable=True, root=str(root), **_fx())
    assert res['removed'] is False and d.exists()
    assert (d / 'booklet.pdf').exists() and (d / 'cover.jpg').exists()  # nothing deleted


# ── #912: scan() must read the opt-in from where the UI SAVES it ─────────────
class _Cfg:
    """Mimics ConfigManager: job settings live as a nested dict under
    `repair.jobs.<id>.settings` (RepairWorker.set_job_settings writes there)."""

    def __init__(self, settings):
        self._settings = settings

    def get(self, key, default=None):
        if key == 'repair.jobs.empty_folder_cleaner.settings':
            return self._settings
        return default


def _run_scan(tmp_path, settings):
    root = tmp_path / 'lib'; root.mkdir()
    res_dir = root / 'Artist' / 'Old Album'; res_dir.mkdir(parents=True)
    (res_dir / 'cover.jpg').write_text('img')       # image + lyric only — the #912 case
    (res_dir / 'lyrics.lrc').write_text('la')
    keep = root / 'Artist2' / 'Real Album'; keep.mkdir(parents=True)
    (keep / 'song.flac').write_text('audio')        # has audio — must never be flagged

    flagged = []
    ctx = JobContext(
        db=None, transfer_folder=str(root), config_manager=_Cfg(settings),
        create_finding=lambda **kw: (flagged.append(kw.get('file_path')), True)[1],
    )
    EmptyFolderCleanerJob().scan(ctx)
    return str(res_dir), str(keep), set(flagged)


def test_scan_flags_residual_folder_when_opt_in_saved_under_settings(tmp_path):
    # The toggle is stored at repair.jobs.<id>.settings.remove_residual_files. The scan must
    # read it from THERE — the old flat-key read missed it, so the option did nothing (#912).
    res_dir, keep, flagged = _run_scan(
        tmp_path, {'remove_junk_files': True, 'remove_residual_files': True})
    assert res_dir in flagged          # the cover.jpg + .lrc folder is now found
    assert keep not in flagged         # the audio folder is never touched


def test_scan_keeps_residual_folder_when_opt_off(tmp_path):
    # Opt-off preserves the conservative default: an image/.lrc folder is left alone.
    res_dir, keep, flagged = _run_scan(
        tmp_path, {'remove_junk_files': True, 'remove_residual_files': False})
    assert res_dir not in flagged
    assert keep not in flagged
