"""No maintenance job may treat the atomic staging tree as library content.

Boulder, on the staging relocation: "i thought soulsync had an actual staging
folder as well. but i was thinking, if its going in either transfer or staging
folder, would that just create issues."

It would, and this is that issue. Moving atomic-publish staging INSIDE the
transfer dir fixed the Docker permission failure, but the transfer dir is
walked by a family of library-maintenance jobs, and two of them DELETE things:

  * ``orphan_file_detector`` treats any audio file under transfer that is not
    in the database as an orphan. Staged tracks are deliberately not in the
    database until publish, so every one of them qualifies.
  * ``empty_folder_cleaner`` removes empty directories bottom-up. A staging
    tree mid-download is full of album folders whose files have not landed yet.
  * ``duplicate_cleaner`` would see the staged copy and the published copy of
    the same track as duplicates.
  * ``quality_upgrade_scanner`` would probe half-landed files and propose
    upgrades for tracks not yet in the library.

The prune is path-based rather than name-based because ``empty_folder_cleaner``
walks bottom-up, where mutating ``dirs`` in place does nothing at all.
"""

from __future__ import annotations

import os

import pytest

from core.repair_jobs.base import is_internal_transfer_dir, skip_deleted_quarantine

STAGING = '.soulsync_atomic_staging'


# ── the predicate ─────────────────────────────────────────────────────────

def test_the_staging_tree_is_internal(tmp_path):
    transfer = str(tmp_path / 'music')
    assert is_internal_transfer_dir(os.path.join(transfer, STAGING), transfer)
    assert is_internal_transfer_dir(
        os.path.join(transfer, STAGING, 'b1', 'Neil Young', 'Harvest'), transfer)


def test_the_deleted_quarantine_is_still_internal(tmp_path):
    """The original reason this helper exists — must not regress."""
    transfer = str(tmp_path / 'music')
    assert is_internal_transfer_dir(os.path.join(transfer, 'deleted'), transfer)
    assert is_internal_transfer_dir(os.path.join(transfer, 'deleted', 'x'), transfer)


def test_real_library_folders_are_not_internal(tmp_path):
    transfer = str(tmp_path / 'music')
    assert not is_internal_transfer_dir(os.path.join(transfer, 'Neil Young'), transfer)
    assert not is_internal_transfer_dir(
        os.path.join(transfer, 'Neil Young', 'Harvest'), transfer)


def test_a_deeper_folder_named_deleted_is_left_alone(tmp_path):
    """Anchored to the top level: a band could legitimately have an album
    folder called 'deleted'."""
    transfer = str(tmp_path / 'music')
    assert not is_internal_transfer_dir(
        os.path.join(transfer, 'Artist', 'deleted'), transfer)


def test_a_similarly_named_sibling_is_not_matched(tmp_path):
    """Prefix matching must respect path separators, or
    '<transfer>/deleted_archive' gets swallowed too."""
    transfer = str(tmp_path / 'music')
    assert not is_internal_transfer_dir(os.path.join(transfer, 'deleted_archive'), transfer)
    assert not is_internal_transfer_dir(
        os.path.join(transfer, STAGING + '_old'), transfer)


# ── the topdown prune ─────────────────────────────────────────────────────

def test_prune_removes_both_internal_dirs(tmp_path):
    transfer = str(tmp_path / 'music')
    dirs = ['Neil Young', 'deleted', STAGING, 'Pink Floyd']
    skip_deleted_quarantine(transfer, dirs, transfer)
    assert dirs == ['Neil Young', 'Pink Floyd']


def test_prune_is_in_place(tmp_path):
    """os.walk only honours a mutation of the SAME list object."""
    transfer = str(tmp_path / 'music')
    dirs = ['deleted', STAGING]
    original = dirs
    skip_deleted_quarantine(transfer, dirs, transfer)
    assert dirs is original and dirs == []


# ── the walk, end to end ──────────────────────────────────────────────────

def _build(tmp_path):
    transfer = tmp_path / 'music'
    (transfer / 'Neil Young' / 'Harvest').mkdir(parents=True)
    (transfer / 'Neil Young' / 'Harvest' / '01 - real.flac').touch()
    staged = transfer / STAGING / 'b1' / 'Neil Young' / 'Harvest'
    staged.mkdir(parents=True)
    (staged / '08 - Alabama.flac').touch()
    (transfer / 'deleted').mkdir()
    (transfer / 'deleted' / 'removed.flac').touch()
    return str(transfer)


def test_a_pruned_walk_sees_only_real_library_files(tmp_path):
    transfer = _build(tmp_path)
    found = []
    for root, dirs, files in os.walk(transfer):
        skip_deleted_quarantine(root, dirs, transfer)
        found += [f for f in files if f.endswith('.flac')]
    assert found == ['01 - real.flac'], (
        'a maintenance job would have treated a half-downloaded track as '
        'library content'
    )


def test_an_unpruned_walk_would_have_seen_the_staged_track(tmp_path):
    """Pins the danger itself, so the value of the prune is visible."""
    transfer = _build(tmp_path)
    found = []
    for _root, _dirs, files in os.walk(transfer):
        found += [f for f in files if f.endswith('.flac')]
    assert sorted(found) == ['01 - real.flac', '08 - Alabama.flac', 'removed.flac']


def test_bottom_up_walks_need_the_path_check_not_the_prune(tmp_path):
    """empty_folder_cleaner walks topdown=False, where mutating `dirs` has no
    effect — which is exactly why the predicate is path-based."""
    transfer = _build(tmp_path)
    visited_staging = False
    for dirpath, dirnames, _files in os.walk(transfer, topdown=False):
        skip_deleted_quarantine(dirpath, dirnames, transfer)   # no-op here
        if is_internal_transfer_dir(dirpath, transfer):
            continue
        if STAGING in dirpath:
            visited_staging = True
    assert not visited_staging


# ── every transfer-walking job actually uses one of them ──────────────────

# Library v2 moved the file-tool jobs off their own walks: fake_lossless,
# track_number_repair and the retired quality_upgrade_scanner now take their
# subjects from the catalogue (``active_file_subjects``) or from the one shared
# walk in ``filesystem_subjects`` — which is why that module is on this list and
# they are not. Anything staged is by definition not in the catalogue yet.
@pytest.mark.parametrize('path', [
    'core/repair_jobs/orphan_file_detector.py',
    'core/repair_jobs/empty_folder_cleaner.py',
    'core/repair_jobs/filesystem_subjects.py',
    'core/library/duplicate_cleaner.py',
    'core/soulsync_client.py',
])
def test_transfer_walkers_guard_themselves(path):
    """A new job that walks the transfer dir without a guard would quietly
    start eating staged albums, so the whole family is pinned here."""
    with open(path, encoding='utf-8') as fh:
        src = fh.read()
    assert 'os.walk' in src, f'{path} no longer walks — update this list'
    guarded = ('skip_deleted_quarantine' in src
               or 'is_internal_transfer_dir' in src
               or "startswith('.')" in src)
    assert guarded, f'{path} walks the library with no staging/quarantine guard'
