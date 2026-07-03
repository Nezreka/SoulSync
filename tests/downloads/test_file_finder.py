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
import sys
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


# ---------------------------------------------------------------------------
# Issue #835 — a '/' in a YouTube/Tidal title is part of the NAME, not a path
# separator. The encoded ``id||title`` finder previously basename-split the
# title ("YouSeeBIGGIRL/T:T" -> "T:T"), so the real on-disk file (with the
# slash sanitised) never matched and valid downloads got quarantined.
# ---------------------------------------------------------------------------

from core.downloads.file_finder import _extract_basename


def test_encoded_title_with_slash_is_not_basename_split():
    # The Sawano AoT track. The id||title encoding must keep the whole title.
    assert _extract_basename('vy63u2hKoPE||YouSeeBIGGIRL/T:T') == 'YouSeeBIGGIRL/T:T'


def test_finds_youtube_file_whose_title_contains_a_slash(tmp_path):
    downloads = tmp_path / 'downloads'
    # On disk the slash is sanitised to a look-alike and the colon spaced out,
    # exactly as in the issue screenshot. Windows forbids ':' in filenames, so
    # yt-dlp-style sanitisation there leaves a space instead of ': '.
    on_disk = 'YouSeeBIGGIRL∕T T.mp3' if sys.platform == 'win32' else 'YouSeeBIGGIRL∕T: T.mp3'
    target = downloads / on_disk
    _touch(target)

    found, location = find_completed_audio_file(
        str(downloads), 'vy63u2hKoPE||YouSeeBIGGIRL/T:T',
    )

    assert found == str(target), 'pre-#835 this truncated the target to "T:T" and missed the file'
    assert location == 'downloads'


def test_slash_title_does_not_match_an_unrelated_file(tmp_path):
    # Guard against the fix being too loose: a different track must NOT match.
    downloads = tmp_path / 'downloads'
    _touch(downloads / 'Some Totally Different Song.mp3')

    found, _ = find_completed_audio_file(
        str(downloads), 'vy63u2hKoPE||YouSeeBIGGIRL/T:T',
    )
    assert found is None


def test_real_soulseek_path_still_basenamed(tmp_path):
    # Regression: a genuine remote PATH must still resolve to its last segment.
    downloads = tmp_path / 'downloads'
    target = downloads / '01 - Suddenly.flac'
    _touch(target)
    assert _extract_basename(r'shared\Billy Ocean\Very Best Of\01 - Suddenly.flac') == '01 - Suddenly.flac'
    found, _ = find_completed_audio_file(
        str(downloads), r'shared\Billy Ocean\Very Best Of\01 - Suddenly.flac',
    )
    assert found == str(target)


# ---------------------------------------------------------------------------
# Unbalanced bracket — slskd REPORTS "[34 - Title.flac" but SAVES the file as
# "34 - Title.flac" (it sanitises the leading '['). The normaliser's old combined
# bracket-strip r'[\[\(].*?[\]\)]' matched from that lone '[' all the way to the
# next ')', eating the whole title and collapsing the search target to just "flac"
# → 0.40 fuzzy score → "File not found on disk" despite the file sitting right
# there. (Discord: Shdjfgatdif — "You & Me (Flume Remix)".)
# ---------------------------------------------------------------------------


def test_finds_file_when_slskd_strips_a_leading_bracket(tmp_path):
    downloads = tmp_path / 'downloads'
    # On disk: no leading '['. API filename (slskd-reported): has the '['.
    target = downloads / 'Disclosure' / '34 - You & Me (Flume Remix).flac'
    _touch(target)

    found, location = find_completed_audio_file(
        str(downloads), r'Music\Disclosure\[34 - You & Me (Flume Remix).flac',
    )

    assert found == str(target), \
        'the lone "[" used to collapse the target to "flac" and miss the file'
    assert location == 'downloads'


def test_balanced_bracket_tags_still_stripped(tmp_path):
    """No regression: balanced "[FLAC]" / "(Remastered 2016)" tags in a Soulseek
    filename must still be stripped so it matches the clean saved file."""
    downloads = tmp_path / 'downloads'
    target = downloads / 'Song.mp3'
    _touch(target)

    found, _ = find_completed_audio_file(
        str(downloads), r'shared\Artist\Album\Song [FLAC] (Remastered 2016).mp3',
    )

    assert found == str(target)


def test_stray_closing_bracket_does_not_break_match(tmp_path):
    """The other shape in the wild (Discord: "Abort, Retry, Fail_]1-01 …") — a
    stray ']' must not wreck the match either."""
    downloads = tmp_path / 'downloads'
    target = downloads / 'White Town' / "Fail_]1-01 Your Woman.flac"
    _touch(target)

    found, _ = find_completed_audio_file(
        str(downloads), r"@@digadom\Music\White Town\Fail_]1-01 Your Woman.flac",
    )

    assert found == str(target)
