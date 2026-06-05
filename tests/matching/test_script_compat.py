"""Tests for core/matching/script_compat.py — writing-system detection.

Issue #797 — these pin the exact boundary that the AcoustID verifier
relies on: accented Latin is still Latin (no false cross-script
trigger), but genuinely different writing systems (CJK / Hangul /
Cyrillic / Greek / Arabic / Hebrew / Thai) ARE flagged so a
romanized-vs-native artist comparison isn't treated as a real mismatch.
"""

from __future__ import annotations

import pytest

from core.matching.script_compat import (
    has_strong_nonlatin,
    is_cross_script_mismatch,
)


# ---------------------------------------------------------------------------
# has_strong_nonlatin — accented Latin must NOT count
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('text', [
    'Beyoncé', 'Sigur Rós', 'Mötley Crüe', 'Joe Hisaishi',
    'Kendrick Lamar', 'Dmitry Yablonsky', 'AC/DC', 'P!nk',
    '', '   ', '12345', 'Café del Mar',
])
def test_latin_and_accented_latin_is_not_nonlatin(text):
    assert has_strong_nonlatin(text) is False


@pytest.mark.parametrize('text', [
    '久石譲',                 # kanji (Joe Hisaishi)
    '残酷な天使のテーゼ',        # kana + kanji
    'Дмитрий Яблонский',     # Cyrillic
    '방탄소년단',              # Hangul (BTS)
    'Σωκράτης',              # Greek
    'عمرو دياب',             # Arabic
    'שלום',                  # Hebrew
    'ก้อง สหรัถ',            # Thai
])
def test_real_nonlatin_scripts_detected(text):
    assert has_strong_nonlatin(text) is True


# ---------------------------------------------------------------------------
# is_cross_script_mismatch — the verifier's gate
# ---------------------------------------------------------------------------

def test_romanized_vs_native_is_cross_script():
    # The reported case (#797): expected romanized, AcoustID native.
    assert is_cross_script_mismatch('Joe Hisaishi', '久石譲') is True
    assert is_cross_script_mismatch('Dmitry Yablonsky', 'Дмитрий Яблонский') is True


def test_is_symmetric():
    assert is_cross_script_mismatch('久石譲', 'Joe Hisaishi') is True


def test_same_latin_both_sides_is_not_mismatch():
    # English-vs-English — comparison is meaningful, no bridge. This is
    # the Kendrick R.O.T.C protection surface: must stay False so the
    # verifier keeps its strict FAIL path.
    assert is_cross_script_mismatch('Kendrick Lamar', 'Kendrick Lamar feat. BJ') is False
    assert is_cross_script_mismatch('Crown', 'Crown of Thorns') is False


def test_same_nonlatin_both_sides_is_not_mismatch():
    # Both native — same-script similarity still works, don't relax.
    assert is_cross_script_mismatch('久石譲', '久石譲') is False
    assert is_cross_script_mismatch('久石譲', '坂本龍一') is False


def test_empty_or_letterless_other_side_is_not_mismatch():
    # One side non-Latin but the other has no Latin LETTER to bridge to.
    assert is_cross_script_mismatch('', '久石譲') is False
    assert is_cross_script_mismatch('12345', '久石譲') is False
    assert is_cross_script_mismatch('久石譲', '   ') is False
