"""Reusing a folder that already exists in a different case (#1091).

TomOdellSheetMusic: reorganize/download build every destination from metadata,
so metadata casing that differs from the folder on disk produces a second
folder. On his case-sensitive Linux setup that is two real directories and a
split album in Jellyfin; on Windows/macOS the write lands in the first folder
but SoulSync records a path that does not exist as spelled.

These tests run on a case-sensitive filesystem (Linux CI), which is the harder
half and the one that produces genuine duplicates.
"""

from __future__ import annotations

import os

import pytest

from core.library.case_folding import (
    resolve_existing_case_dir,
    resolve_existing_case_path,
)


@pytest.fixture()
def root(tmp_path):
    return str(tmp_path)


def _mk(root, *parts):
    path = os.path.join(root, *parts)
    os.makedirs(path, exist_ok=True)
    return path


# ── the reported bug ─────────────────────────────────────────────────────────

def test_an_existing_folder_is_reused_whatever_case_was_asked_for(root):
    _mk(root, 'Pink Floyd', 'Pink Floyd - The Wall')

    resolved = resolve_existing_case_dir(root, 'pink floyd/pink floyd - the wall')

    assert resolved == os.path.join(root, 'Pink Floyd', 'Pink Floyd - The Wall')


def test_it_works_the_other_way_round_too(root):
    _mk(root, 'pink floyd', 'pink floyd - the wall')

    resolved = resolve_existing_case_dir(root, 'Pink Floyd/Pink Floyd - The Wall')

    assert resolved == os.path.join(root, 'pink floyd', 'pink floyd - the wall')


def test_only_the_artist_level_differing_still_resolves(root):
    """The album folder is new; the artist folder is not. Each component is
    resolved independently."""
    _mk(root, 'Radiohead')

    resolved = resolve_existing_case_dir(root, 'radiohead/Radiohead - In Rainbows')

    assert resolved == os.path.join(root, 'Radiohead', 'Radiohead - In Rainbows')


def test_an_exact_match_is_returned_unchanged(root):
    _mk(root, 'Radiohead', 'Radiohead - Kid A')

    resolved = resolve_existing_case_dir(root, 'Radiohead/Radiohead - Kid A')

    assert resolved == os.path.join(root, 'Radiohead', 'Radiohead - Kid A')


def test_a_wholly_new_path_keeps_the_casing_it_was_given(root):
    resolved = resolve_existing_case_dir(root, 'New Artist/New Artist - New Album')

    assert resolved == os.path.join(root, 'New Artist', 'New Artist - New Album')


def test_resolution_stops_at_the_first_missing_component(root):
    """Nothing under a missing directory can exist, so the rest is the
    caller's to name — and we must not scandir our way down a tree that
    isn't there."""
    _mk(root, 'Artist')

    resolved = resolve_existing_case_dir(root, 'artist/Album/Disc 1')

    assert resolved == os.path.join(root, 'Artist', 'Album', 'Disc 1')


# ── safety ───────────────────────────────────────────────────────────────────

def test_the_root_itself_is_never_rewritten(root):
    """The root is a configured library path. Rewriting its case could point
    writes at a different mount entirely."""
    resolved = resolve_existing_case_dir(root, 'Artist')

    assert resolved.startswith(root)


def test_a_missing_root_does_not_raise(tmp_path):
    missing = str(tmp_path / 'not-here')

    resolved = resolve_existing_case_dir(missing, 'Artist/Album')

    assert resolved == os.path.join(missing, 'Artist', 'Album')


def test_a_file_where_a_directory_was_expected_is_not_matched(root):
    """Only directories count. A stray file named like the folder must not
    capture the path."""
    open(os.path.join(root, 'artist'), 'w').close()

    resolved = resolve_existing_case_dir(root, 'Artist/Album')

    assert resolved == os.path.join(root, 'Artist', 'Album')


def test_empty_inputs_do_not_explode(root):
    assert resolve_existing_case_dir(root, '') == root
    assert resolve_existing_case_dir('', 'Artist') == 'Artist'


def test_several_existing_cases_resolve_deterministically(root):
    """An install that ALREADY split needs every pass to choose the same
    survivor, or a reorganize would keep moving files back and forth."""
    _mk(root, 'Artist')
    _mk(root, 'artist')

    first = resolve_existing_case_dir(root, 'ARTIST/Album')
    second = resolve_existing_case_dir(root, 'ArTiSt/Album')

    assert first == second


# ── the file variant ─────────────────────────────────────────────────────────

def test_a_files_parent_folders_resolve_but_its_name_does_not(root):
    """Two tracks differing only in case are two files. Folding the filename
    would overwrite one with the other and lose audio."""
    _mk(root, 'Artist', 'Artist - Album')
    open(os.path.join(root, 'Artist', 'Artist - Album', '01 - Song.flac'), 'w').close()

    resolved = resolve_existing_case_path(root, 'artist/artist - album/01 - SONG.flac')

    assert resolved == os.path.join(root, 'Artist', 'Artist - Album', '01 - SONG.flac')


def test_a_bare_filename_has_no_parents_to_resolve(root):
    assert resolve_existing_case_path(root, 'song.flac') == os.path.join(root, 'song.flac')


def test_windows_separators_are_understood(root):
    _mk(root, 'Artist', 'Artist - Album')

    resolved = resolve_existing_case_dir(root, r'artist\artist - album')

    assert resolved == os.path.join(root, 'Artist', 'Artist - Album')


# ── the case-INSENSITIVE half (Windows / macOS) ──────────────────────────────

def test_it_does_not_trust_isdir_for_the_exact_case(root, monkeypatch):
    """On Windows/macOS `os.path.isdir('Pink Floyd')` returns True even when
    the folder on disk is 'pink floyd'. An earlier version of this resolver
    used isdir as a fast path and therefore kept the CALLER's casing on
    exactly the filesystems where the recorded path then fails to match disk.

    Simulated by making isdir case-insensitive, which is what those
    filesystems do.
    """
    _mk(root, 'pink floyd', 'pink floyd - the wall')

    real_isdir = os.path.isdir

    def insensitive_isdir(path):
        if real_isdir(path):
            return True
        parent, name = os.path.split(path)
        try:
            return any(e.lower() == name.lower() for e in os.listdir(parent))
        except OSError:
            return False

    monkeypatch.setattr('core.library.case_folding.os.path.isdir', insensitive_isdir)

    resolved = resolve_existing_case_dir(root, 'Pink Floyd/Pink Floyd - The Wall')

    assert resolved == os.path.join(root, 'pink floyd', 'pink floyd - the wall')


def test_a_directory_created_mid_run_is_seen(root):
    """The listing is cached; a folder created after the first lookup must
    still be found, or two tracks of one album could disagree."""
    resolve_existing_case_dir(root, 'Artist/Album')      # warms the cache
    _mk(root, 'artist')

    resolved = resolve_existing_case_dir(root, 'Artist/Album')

    assert resolved == os.path.join(root, 'artist', 'Album')
