"""Cross-script SKIP before the "expected artist not found" FAIL (#1628).

``_find_best_title_artist_match`` ranks on title_sim*0.6 + artist_sim*0.4, so a
Latin stray credit ('They Die', 0.20) outranks the real non-Latin artist
(宇多田ヒカル, 0.00 cross-script) on the same matching title. The winner's
0.20 then reads as a CLEAR mismatch (< 0.30) and the correct file is
quarantined — even though a same-title recording with an UNREADABLE artist is
exactly the shape the module already treats as "unknown, never failed".

The fix scans ALL recordings immediately before that FAIL: if any recording
names the expected title (same TITLE_MATCH_THRESHOLD) with a
cross-script-incomparable artist, the verdict is SKIP, not FAIL.
"""

from __future__ import annotations

import pytest

from core.matching.audio_verification import Decision, evaluate


def _rec(title: str, artist: str):
    return {"title": title, "artist": artist, "score": 1.0}


def _evaluate(recordings, expected_title="Hikari", expected_artist="Hikaru Utada"):
    return evaluate(expected_title, expected_artist, recordings,
                    fingerprint_score=0.9)


def test_stray_latin_credit_outranked_by_cross_script_artist_skips():
    """The reported shape: three recordings, the Latin stray ('They Die')
    wins the ranking on 0.20 artist sim, but a same-title recording names
    宇多田ヒカル — cross-script-incomparable with 'Hikaru Utada'. Pre-fix this
    was FAIL ('expected artist not found'); the FAIL rested on a ranking
    accident, so it must be SKIP."""
    recordings = [
        _rec("Hikari", "They Die"),
        _rec("Hikari", "宇多田ヒカル"),
        _rec("Unrelated Song", "Someone Else"),
    ]

    out = _evaluate(recordings)

    assert out.decision is Decision.SKIP
    assert "different script" in out.reason


def test_two_recordings_winner_itself_cross_script_still_skips():
    """Unchanged pre-existing behaviour: when the winning recording's own
    artist is cross-script-incomparable, the existing branch skips."""
    recordings = [
        _rec("Hikari", "宇多田ヒカル"),
        _rec("Different Song", "They Die"),
    ]

    out = _evaluate(recordings)

    assert out.decision is Decision.SKIP


def test_genuine_latin_mismatch_still_fails():
    """Control: no cross-script recording anywhere, both artists comparable
    Latin — a genuinely wrong artist must still FAIL."""
    recordings = [
        _rec("Hikari", "They Die"),
        _rec("Hikari", "Wrong Band"),
    ]

    out = _evaluate(recordings)

    assert out.decision is Decision.FAIL
    assert "expected artist not found" in out.reason


def test_single_cross_script_recording_still_skips():
    """Pin the existing behaviour: one recording, cross-script artist, title
    matches — SKIP, never FAIL."""
    out = _evaluate([_rec("Hikari", "宇多田ヒカル")])

    assert out.decision is Decision.SKIP


def test_cross_script_recording_with_different_title_does_not_skip():
    """The scan requires a MATCHING title: a cross-script artist on some
    other song is not evidence about this file, so the FAIL stands."""
    recordings = [
        _rec("Hikari", "They Die"),
        _rec("Some Other Song", "宇多田ヒカル"),
    ]

    out = _evaluate(recordings)

    assert out.decision is Decision.FAIL
