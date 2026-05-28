"""Tests for ``core/downloads/file_finder.py``.

Real-world regression these tests pin: the Soulseek album-bundle
post-process previously had its own three-candidate probe for the
local downloaded file. When slskd was configured to nest downloads
under a username subdir (a common config) NONE of the three
candidates matched, the poll silently timed out 22 minutes later,
and the batch went to "failed" even though slskd had successfully
downloaded every track of the album. The per-track download path
already used the recursive-walk finder and worked fine — these
tests pin the lifted shared finder so both paths now find files
no matter what layout slskd writes.

Issue: #715 (Billy Ocean — Download album task fails after slskd
finishes downloading release).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from core.downloads.file_finder import find_completed_audio_file


def _touch(path: Path, content: bytes = b'\x00\x00'):
    """Create a small placeholder file at ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


# ---------------------------------------------------------------------------
# Layout coverage — slskd writes downloads to many different paths
# depending on its config. The bundle resolver must find the file in
# all of these layouts; the pre-lift code only handled the first two.
# ---------------------------------------------------------------------------


def test_finds_file_in_flat_slskd_layout(tmp_path):
    """Default slskd config: ``<download_dir>/<basename>``."""
    downloads = tmp_path / 'downloads'
    target = downloads / '01 - Suddenly.flac'
    _touch(target)

    found, location = find_completed_audio_file(
        str(downloads), r'shared\Billy Ocean\Very Best Of\01 - Suddenly.flac',
    )

    assert found == str(target)
    assert location == 'downloads'


def test_finds_file_under_username_subdir(tmp_path):
    """Regression for #715. slskd configured with
    ``directories.downloads.username = true`` writes to
    ``<download_dir>/<username>/<filename>``. Pre-lift the bundle
    resolver missed this layout because it only probed
    ``download_dir/filename`` / ``download_dir/basename`` /
    ``download_dir/normalized_remote_path`` — none included a
    username segment, so every file looked missing."""
    downloads = tmp_path / 'downloads'
    target = downloads / '3opgkrpokgreg' / '01 - Suddenly.flac'
    _touch(target)

    found, location = find_completed_audio_file(
        str(downloads), r'shared\Billy Ocean\Very Best Of\01 - Suddenly.flac',
    )

    assert found == str(target)
    assert location == 'downloads'


def test_finds_file_when_slskd_preserves_remote_tree(tmp_path):
    """slskd config where the remote sharer's folder tree is
    mirrored locally — ``<download_dir>/shared/<artist>/<album>/<file>``."""
    downloads = tmp_path / 'downloads'
    target = downloads / 'shared' / 'Billy Ocean' / 'Very Best Of' / '01 - Suddenly.flac'
    _touch(target)

    found, location = find_completed_audio_file(
        str(downloads), r'shared\Billy Ocean\Very Best Of\01 - Suddenly.flac',
    )

    assert found == str(target)
    assert location == 'downloads'


def test_finds_file_in_deeply_nested_user_tree(tmp_path):
    """Some slskd setups combine username + preserved tree. Finder
    walks recursively so depth doesn't matter."""
    downloads = tmp_path / 'downloads'
    target = (downloads / '3opgkrpokgreg' / 'Music'
                        / 'Billy Ocean' / 'Very Best Of'
                        / '01 - Suddenly.flac')
    _touch(target)

    found, location = find_completed_audio_file(
        str(downloads), r'shared\Billy Ocean\Very Best Of\01 - Suddenly.flac',
    )

    assert found == str(target)


def test_returns_none_when_file_missing(tmp_path):
    """File genuinely not on disk → both elements ``None``."""
    downloads = tmp_path / 'downloads'
    downloads.mkdir()
    _touch(downloads / '02 - Caribbean Queen.flac')  # different file

    found, location = find_completed_audio_file(
        str(downloads), r'shared\Billy Ocean\Very Best Of\01 - Suddenly.flac',
    )

    assert found is None
    assert location is None


# ---------------------------------------------------------------------------
# Disambiguation — when multiple files share a basename, the path
# whose components mirror the remote tree wins.
# ---------------------------------------------------------------------------


def test_path_confirms_against_remote_dirs_when_basename_collides(tmp_path):
    """Two albums both contain ``01 - Intro.flac``. The finder
    picks the one whose path carries the most remote-dir components
    — keeps two simultaneous bundle downloads from claiming each
    other's file."""
    downloads = tmp_path / 'downloads'
    other_album = downloads / 'Some Other Artist' / 'Other Album' / '01 - Intro.flac'
    target_album = downloads / 'Billy Ocean' / 'Very Best Of' / '01 - Intro.flac'
    _touch(other_album)
    _touch(target_album)

    found, _ = find_completed_audio_file(
        str(downloads), r'shared\Billy Ocean\Very Best Of\01 - Intro.flac',
    )

    assert found == str(target_album)


def test_returns_only_match_when_no_disambiguation_possible(tmp_path):
    """Single basename match in the tree → return it regardless of
    whether the remote dir components line up."""
    downloads = tmp_path / 'downloads'
    target = downloads / 'random' / 'subdir' / '01 - Suddenly.flac'
    _touch(target)

    found, _ = find_completed_audio_file(
        str(downloads), r'shared\Billy Ocean\Very Best Of\01 - Suddenly.flac',
    )

    assert found == str(target)


# ---------------------------------------------------------------------------
# slskd dedup suffix — when a file with the same name already exists,
# slskd appends ``_<timestamp>`` to the new file. The finder must
# strip this and still match the original API filename.
# ---------------------------------------------------------------------------


def test_matches_slskd_dedup_suffix(tmp_path):
    """``Song.flac`` requested → ``Song_639067852665564677.flac``
    on disk (slskd dedup). Finder strips the timestamp suffix and
    returns the deduped file."""
    downloads = tmp_path / 'downloads'
    target = downloads / '01 - Suddenly_639067852665564677.flac'
    _touch(target)

    found, _ = find_completed_audio_file(
        str(downloads), r'shared\Billy Ocean\Very Best Of\01 - Suddenly.flac',
    )

    assert found == str(target)


def test_dedup_suffix_short_digits_not_treated_as_dedup(tmp_path):
    """Year-style ``_2007`` (4 digits) is NOT slskd's dedup format —
    that's a 10+ digit timestamp. A file like ``Greatest Hits_2007.flac``
    that exists alongside the requested ``Greatest Hits.flac`` must
    not be returned as a tier-1 dedup match. (Fuzzy may still grab
    it as a lower-confidence fallback when no real match exists,
    which is intentional — the strict-dedup tier is what we're
    pinning here.)"""
    downloads = tmp_path / 'downloads'
    # Real match exists too — so the dedup-incorrect file doesn't
    # win by default. Tests the priority order.
    real_match = downloads / 'Billy Ocean' / '01 - Suddenly.flac'
    near_miss = downloads / '01 - Suddenly_2007.flac'
    _touch(real_match)
    _touch(near_miss)

    found, _ = find_completed_audio_file(
        str(downloads), r'shared\Billy Ocean\Very Best Of\01 - Suddenly.flac',
    )

    # Real exact-basename + path-confirmed match must win over the
    # year-suffixed file.
    assert found == str(real_match)


# ---------------------------------------------------------------------------
# Special inputs — YouTube/Tidal encoded filenames, quarantine,
# empty paths, transfer-dir fallback.
# ---------------------------------------------------------------------------


def test_skips_quarantine_subdir(tmp_path):
    """Files under ``ss_quarantine/`` are known-wrong AcoustID
    rejects — the finder must ignore them so a quarantined-but-still-
    present file doesn't get re-claimed."""
    downloads = tmp_path / 'downloads'
    quarantined = downloads / 'ss_quarantine' / '01 - Suddenly.flac'
    real = downloads / 'Billy Ocean' / '01 - Suddenly.flac'
    _touch(quarantined)
    _touch(real)

    found, _ = find_completed_audio_file(
        str(downloads), r'shared\Billy Ocean\Very Best Of\01 - Suddenly.flac',
    )

    assert found == str(real)


def test_handles_youtube_tidal_encoded_filename(tmp_path):
    """YouTube / Tidal downloads encode the API filename as
    ``id||title``. The finder strips the id and matches against
    the title half."""
    downloads = tmp_path / 'downloads'
    target = downloads / 'My Song.mp3'
    _touch(target)

    found, _ = find_completed_audio_file(str(downloads), 'abc123||My Song.mp3')

    assert found == str(target)


def test_falls_back_to_transfer_dir_when_download_dir_misses(tmp_path):
    """File has already moved into the transfer dir. The finder
    falls through to the second search root rather than returning
    None — covers the post-process race where a file is mid-move."""
    downloads = tmp_path / 'downloads'
    downloads.mkdir()
    transfer = tmp_path / 'transfer'
    moved = transfer / 'Billy Ocean' / '01 - Suddenly.flac'
    _touch(moved)

    found, location = find_completed_audio_file(
        str(downloads),
        r'shared\Billy Ocean\Very Best Of\01 - Suddenly.flac',
        transfer_dir=str(transfer),
    )

    assert found == str(moved)
    assert location == 'transfer'


def test_returns_none_when_neither_dir_has_file(tmp_path):
    """Missing in both download AND transfer → ``(None, None)``."""
    downloads = tmp_path / 'downloads'
    transfer = tmp_path / 'transfer'
    downloads.mkdir()
    transfer.mkdir()

    found, location = find_completed_audio_file(
        str(downloads),
        r'shared\Billy Ocean\Very Best Of\01 - Suddenly.flac',
        transfer_dir=str(transfer),
    )

    assert found is None
    assert location is None


def test_ignores_non_audio_files(tmp_path):
    """Cover art, NFO, and text files in the slskd dir must not be
    surfaced as audio matches even when their stem aligns."""
    downloads = tmp_path / 'downloads'
    _touch(downloads / '01 - Suddenly.txt')   # wrong extension
    _touch(downloads / '01 - Suddenly.nfo')
    _touch(downloads / 'cover.jpg')
    target = downloads / '01 - Suddenly.flac'
    _touch(target)

    found, _ = find_completed_audio_file(
        str(downloads), r'shared\Billy Ocean\Very Best Of\01 - Suddenly.flac',
    )

    assert found == str(target)


def test_empty_api_filename_returns_none(tmp_path):
    """Defensive — empty / None filename can't resolve to anything."""
    downloads = tmp_path / 'downloads'
    downloads.mkdir()
    _touch(downloads / '01 - Suddenly.flac')

    found, location = find_completed_audio_file(str(downloads), '')

    assert found is None
    assert location is None


# ---------------------------------------------------------------------------
# Fuzzy fallback — when no exact / dedup match lands, the finder
# falls back to the closest-basename fuzzy match above 0.85.
# ---------------------------------------------------------------------------


def test_fuzzy_matches_punctuation_variant(tmp_path):
    """Soulseek shares vary in separator style — underscore vs
    dash vs period. The normaliser collapses all three to spaces
    so the fuzzy comparator can match across the variation."""
    downloads = tmp_path / 'downloads'
    # On disk: underscore. API filename: dash. Both normalise to
    # the same token stream.
    target = downloads / "01_Caribbean_Queen.flac"
    _touch(target)

    found, _ = find_completed_audio_file(
        str(downloads),
        r"shared\Billy Ocean\Very Best Of\01 - Caribbean Queen.flac",
    )

    assert found == str(target)


def test_fuzzy_rejects_low_similarity(tmp_path):
    """A completely different filename in the same dir must not
    fuzzy-match — the 0.85 floor keeps unrelated files out."""
    downloads = tmp_path / 'downloads'
    _touch(downloads / 'random-unrelated-file.flac')

    found, _ = find_completed_audio_file(
        str(downloads), r'shared\Billy Ocean\Very Best Of\01 - Suddenly.flac',
    )

    assert found is None


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
