"""Tighten the AcoustID "language/script" skip exemption.

User report (Mr. Morale download): three different track requests
(Rich Interlude, Savior Interlude, Savior) each received the same
WRONG audio file (Kendrick's R.O.T.C Interlude from his 2010 mixtape).
AcoustID flagged the title mismatch but the verification logic
SKIPPED rather than FAILED with the reason "likely same song in
different language/script."

The old condition was:
    best_score >= 0.95 AND (title_sim >= 0.55 OR artist_sim >= match)

That OR-clause fired for English-vs-English titles by the same artist
that share NO actual content — same artist + word "interlude" in both
titles cleared the bar. The skip then trusted the wrong file as
correct.

New condition: only skip when there's positive evidence the mismatch
is a transliteration / language-script case:
- (a) Either side of the comparison contains non-ASCII characters AND
      artist matches strongly. Real cases: Japanese kanji ↔ romaji,
      Korean hangul ↔ romaji, etc.
- (b) BOTH title AND artist similarity are very high (>=0.80, ARTIST
      threshold). Real cases: title differs only by punctuation /
      casing that fell below strict-match thresholds.

For English-vs-English with very different titles by the same artist,
the skip no longer fires — verification correctly returns FAIL,
quarantining the wrong file.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from core.acoustid_verification import (
    AcoustIDVerification,
    VerificationResult,
)


@pytest.fixture
def verifier(monkeypatch):
    """A verifier with the network/fingerprint side stubbed so we can
    drive the title/artist comparison logic directly."""
    v = AcoustIDVerification()

    # Stub availability check to avoid touching real chromaprint
    class _StubClient:
        def is_available(self):
            return True, 'available'

        def fingerprint_and_lookup(self, path):
            # Each test injects its own desired return value via
            # monkeypatch on this method; default is empty.
            return None

    v.acoustid_client = _StubClient()
    return v


def _stub_lookup(verifier, *, recordings, best_score):
    """Make `fingerprint_and_lookup` return a fabricated AcoustID result."""
    verifier.acoustid_client.fingerprint_and_lookup = lambda path: {
        'recordings': recordings,
        'best_score': best_score,
        'recording_mbids': [r.get('id') for r in recordings if r.get('id')],
    }


# ---------------------------------------------------------------------------
# The headline regression — Rich Interlude vs R.O.T.C Interlude
# ---------------------------------------------------------------------------


def test_english_titles_same_artist_no_longer_skipped(verifier):
    """User's actual case: requested 'Rich (Interlude)' by Kendrick
    Lamar, AcoustID identified the file as 'R.O.T.C. (interlude)' by
    Kendrick Lamar. Same artist, same word 'interlude', but completely
    different songs. Old skip-logic let it pass; new logic must FAIL
    so the file gets quarantined."""
    _stub_lookup(verifier, recordings=[
        {'title': 'R.O.T.C. (interlude)', 'artist': 'Kendrick Lamar feat. BJ the Chicago Kid'},
    ], best_score=0.96)

    result, msg = verifier.verify_audio_file(
        '/fake/path.flac',
        'Rich (Interlude)',
        'Kendrick Lamar',
    )
    assert result == VerificationResult.FAIL
    # Message should be the wrong-file message, NOT the language/script skip
    assert 'mismatch' in msg.lower()
    assert 'language/script' not in msg.lower()


def test_savior_request_returning_rotc_no_longer_skipped(verifier):
    """Same bug surface, different track. Confirms the fix isn't
    Rich-Interlude-specific."""
    _stub_lookup(verifier, recordings=[
        {'title': 'R.O.T.C. (interlude)', 'artist': 'Kendrick Lamar feat. BJ the Chicago Kid'},
    ], best_score=0.96)

    result, _msg = verifier.verify_audio_file(
        '/fake/path.flac',
        'Savior',
        'Kendrick Lamar',
    )
    assert result == VerificationResult.FAIL


# ---------------------------------------------------------------------------
# The legitimate skip cases — must STILL fire
# ---------------------------------------------------------------------------


def test_japanese_kanji_to_romaji_still_skipped(verifier):
    """Real language/script case: AcoustID's database has the kanji
    title, the user requested the romaji version. Same artist (in
    Latin script), high fingerprint confidence. Skip should still
    fire so a correct file isn't false-quarantined."""
    _stub_lookup(verifier, recordings=[
        {'title': '残酷な天使のテーゼ', 'artist': 'Yoko Takahashi'},
    ], best_score=0.97)

    result, msg = verifier.verify_audio_file(
        '/fake/path.flac',
        'Zankoku na Tenshi no Theze',
        'Yoko Takahashi',
    )
    assert result == VerificationResult.SKIP
    assert 'language/script' in msg.lower()


def test_minor_punctuation_difference_passes_outright(verifier):
    """Punctuation-only difference: both 'MAAD' and 'M.A.A.D' normalize
    similarly enough that the strict TITLE_MATCH_THRESHOLD is met and
    verification PASSES (better outcome than SKIP). Pin this so a
    future tightening of the strict thresholds doesn't accidentally
    push these into the FAIL bucket."""
    _stub_lookup(verifier, recordings=[
        {'title': 'M.A.A.D City', 'artist': 'Kendrick Lamar'},
    ], best_score=0.97)

    result, _msg = verifier.verify_audio_file(
        '/fake/path.flac',
        'MAAD City',
        'Kendrick Lamar',
    )
    # PASS or SKIP both fine — the critical assertion is "not FAIL".
    assert result != VerificationResult.FAIL


def test_low_fingerprint_score_never_skipped(verifier):
    """Below the 0.95 confidence floor, the skip exemption should
    never fire — even for plausibly-real language/script cases. We
    don't have enough signal to be sure the audio matches."""
    _stub_lookup(verifier, recordings=[
        {'title': '残酷な天使のテーゼ', 'artist': 'Yoko Takahashi'},
    ], best_score=0.80)  # below 0.95 floor

    result, _msg = verifier.verify_audio_file(
        '/fake/path.flac',
        'Zankoku na Tenshi no Theze',
        'Yoko Takahashi',
    )
    assert result == VerificationResult.FAIL


def test_high_score_but_artist_mismatch_no_longer_skipped(verifier):
    """Even with high fingerprint AND non-ASCII chars present, if the
    artist DOESN'T match well, we don't have enough signal to skip.
    Could be a cover by a different artist."""
    _stub_lookup(verifier, recordings=[
        {'title': '残酷な天使のテーゼ', 'artist': 'Some Other Singer'},
    ], best_score=0.97)

    result, _msg = verifier.verify_audio_file(
        '/fake/path.flac',
        'Zankoku na Tenshi no Theze',
        'Yoko Takahashi',
    )
    assert result == VerificationResult.FAIL


def test_old_loose_threshold_no_longer_fires_for_unrelated_titles(verifier):
    """Pin the negative case for the old loose threshold (title_sim
    >= 0.55). 'Crown' vs 'Crown of Thorns' had similarity around 0.6
    in some normalizations — under old logic with high confidence
    and matching artist that would skip. New logic requires title_sim
    >= 0.80 OR non-ASCII presence."""
    _stub_lookup(verifier, recordings=[
        {'title': 'Crown of Thorns', 'artist': 'Kendrick Lamar'},
    ], best_score=0.96)

    result, _msg = verifier.verify_audio_file(
        '/fake/path.flac',
        'Crown',
        'Kendrick Lamar',
    )
    # User asked for 'Crown', got 'Crown of Thorns' — should FAIL now
    assert result == VerificationResult.FAIL
