"""Empty Folder Cleaner (corruption's request) — pure removable decision + the
apply handler's re-check safety (never deletes a folder that gained content, the
library root, or a symlink)."""

from __future__ import annotations

import os

from core.repair_jobs.empty_folder_cleaner import dir_is_removable, remove_empty_folder, is_junk


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
