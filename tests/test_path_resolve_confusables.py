"""Confusable-tolerant path resolution (#833, the-hang-man).

The library DB stored a track title with a curly apostrophe (U+2019); the file
was written to disk with an ASCII one (U+0027). Delete rebuilt the unlink path
from the DB value, so os.path.exists missed and the file survived. find_on_disk
resolves the real on-disk name despite typographic confusables — with REAL temp
files, not mocks, so it exercises the actual byte-level mismatch.
"""

from __future__ import annotations

import os

from core.library.path_resolve import fold_confusables, find_on_disk

CURLY = chr(0x2019)   # ’ right single quotation mark
ASCII = chr(0x27)     # ' ascii apostrophe


# ── fold_confusables ────────────────────────────────────────────────────────

def test_curly_and_straight_apostrophe_fold_equal():
    assert fold_confusables(f"I{CURLY}m Upset") == fold_confusables(f"I{ASCII}m Upset")
    assert fold_confusables(f"I{CURLY}m Upset") == "I'm Upset"


def test_other_confusables_fold():
    assert fold_confusables('Rock – Roll') == 'Rock - Roll'   # en dash
    assert fold_confusables('Rock — Roll') == 'Rock - Roll'   # em dash
    assert fold_confusables('A “B” C') == 'A "B" C'       # smart quotes


def test_fold_preserves_case_and_plain_text():
    # Case must NOT be folded — case-sensitive datasets can hold names that
    # differ only by case, and folding could pick the wrong file.
    assert fold_confusables('Track NAME.mp3') == 'Track NAME.mp3'
    assert fold_confusables('') == ''


# ── find_on_disk against real files ─────────────────────────────────────────

def test_finds_ascii_file_from_curly_db_path(tmp_path):
    # On disk: ASCII apostrophe. DB/query: curly. This is the exact #833 case.
    album = tmp_path / 'Drake' / 'Scorpion'
    album.mkdir(parents=True)
    real = album / f"01 - I{ASCII}m Upset.mp3"
    real.write_text('audio')

    suffix = ['Drake', 'Scorpion', f"01 - I{CURLY}m Upset.mp3"]
    found = find_on_disk(str(tmp_path), suffix)
    assert found is not None
    assert os.path.samefile(found, real)


def test_exact_match_still_works(tmp_path):
    real = tmp_path / 'Artist' / 'Album' / 'Track.mp3'
    real.parent.mkdir(parents=True)
    real.write_text('audio')
    found = find_on_disk(str(tmp_path), ['Artist', 'Album', 'Track.mp3'])
    assert found is not None and os.path.samefile(found, real)


def test_confusable_in_folder_component(tmp_path):
    # The apostrophe can be in a folder name (album/artist), not just the file.
    folder = tmp_path / f"Guns N{ASCII} Roses"
    folder.mkdir()
    real = folder / 'track.mp3'
    real.write_text('audio')
    found = find_on_disk(str(tmp_path), [f"Guns N{CURLY} Roses", 'track.mp3'])
    assert found is not None and os.path.samefile(found, real)


def test_returns_none_for_genuinely_missing_file(tmp_path):
    (tmp_path / 'Artist').mkdir()
    assert find_on_disk(str(tmp_path), ['Artist', 'Nope.mp3']) is None


def test_does_not_match_a_different_track(tmp_path):
    # Folding apostrophes must not collapse two genuinely different names.
    (tmp_path / 'Some Other Song.mp3').write_text('x')
    assert find_on_disk(str(tmp_path), [f"I{CURLY}m Upset.mp3"]) is None


def test_exact_wins_over_folded_when_both_present(tmp_path):
    # If the byte-exact file exists, it's chosen even when a folded sibling also
    # exists — no accidental cross-match.
    exact = tmp_path / f"I{CURLY}m Upset.mp3"
    other = tmp_path / f"I{ASCII}m Upset.mp3"
    exact.write_text('curly')
    other.write_text('ascii')
    found = find_on_disk(str(tmp_path), [f"I{CURLY}m Upset.mp3"])
    assert found is not None and os.path.samefile(found, exact)


def test_bad_base_dir_returns_none(tmp_path):
    assert find_on_disk(str(tmp_path / 'does-not-exist'), ['x.mp3']) is None
