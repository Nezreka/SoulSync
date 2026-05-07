"""AcoustID verification rejects version mismatches (instrumental / live / etc).

Discord report (corruption [BWC]): downloads coming through as instrumentals
when the user expected vocal versions. Slipped past AcoustID verification
because the ``_normalize`` step strips parentheticals and version-suffix
tags ("(Instrumental)", "- Live", etc) so that legitimate name variations
don't fail the title-similarity check. Side effect: "Song Name" and
"Song Name (Instrumental)" both normalize to "song name", title sim is
1.0, file passes verification despite being the wrong cut.

Fix: detect the version label on each side BEFORE normalization runs.
If the expected and matched versions disagree, return FAIL — the
fingerprint did identify a real song, just not the version the caller
asked for.

Reuses ``MusicMatchingEngine.detect_version_type`` so the same patterns
that the pre-download Soulseek matcher uses also drive post-download
verification (no duplicated regex tables).
"""

from __future__ import annotations

import pytest

from core.acoustid_verification import (
    AcoustIDVerification,
    VerificationResult,
)


@pytest.fixture
def verifier():
    """Verifier with the network/fingerprint side stubbed so tests can
    drive the title/artist comparison logic directly."""
    v = AcoustIDVerification()

    class _StubClient:
        def is_available(self):
            return True, "available"

        def fingerprint_and_lookup(self, path):
            return None  # tests inject via _stub_lookup below

    v.acoustid_client = _StubClient()
    return v


def _stub_lookup(verifier, *, recordings, best_score=0.95):
    verifier.acoustid_client.fingerprint_and_lookup = lambda path: {
        "recordings": recordings,
        "best_score": best_score,
        "recording_mbids": [r.get("id") for r in recordings if r.get("id")],
    }


# ---------------------------------------------------------------------------
# The headline bug — instrumental returned where vocal was expected.
# ---------------------------------------------------------------------------


def test_instrumental_returned_for_vocal_request_fails(verifier):
    """User asked for a vocal track; file's fingerprint matched an
    instrumental version of the same song. Old normalizer stripped
    "(Instrumental)" and let it pass. Must FAIL now."""
    _stub_lookup(verifier, recordings=[
        {"title": "In My Feelings (Instrumental)", "artist": "Drake"},
    ])

    result, msg = verifier.verify_audio_file(
        "/fake/path.flac",
        "In My Feelings",
        "Drake",
    )
    assert result == VerificationResult.FAIL
    assert "version mismatch" in msg.lower()
    assert "instrumental" in msg.lower()


def test_instrumental_request_with_vocal_file_fails(verifier):
    """Symmetric case: user asked for the instrumental cut explicitly,
    file's fingerprint matched the regular vocal version. Also FAIL —
    they're different recordings."""
    _stub_lookup(verifier, recordings=[
        {"title": "In My Feelings", "artist": "Drake"},
    ])

    result, _ = verifier.verify_audio_file(
        "/fake/path.flac",
        "In My Feelings (Instrumental)",
        "Drake",
    )
    assert result == VerificationResult.FAIL


# ---------------------------------------------------------------------------
# Different-version mismatches (live vs acoustic, etc) — also FAIL.
# ---------------------------------------------------------------------------


def test_different_versions_disagree_fails(verifier):
    """Caller asked for the live cut; file is the acoustic cut. Both
    are non-original versions, but they're different non-original
    versions — must FAIL."""
    _stub_lookup(verifier, recordings=[
        {"title": "Hello (Acoustic)", "artist": "Adele"},
    ])

    result, _ = verifier.verify_audio_file(
        "/fake/path.flac",
        "Hello (Live at Wembley)",
        "Adele",
    )
    assert result == VerificationResult.FAIL


# ---------------------------------------------------------------------------
# Regression guards — version match must NOT cause false-FAIL.
# ---------------------------------------------------------------------------


def test_original_to_original_passes(verifier):
    """Plain track to plain track — no version on either side. Verify
    the version gate doesn't get in the way of the normal happy path."""
    _stub_lookup(verifier, recordings=[
        {"title": "Bohemian Rhapsody", "artist": "Queen"},
    ])

    result, _ = verifier.verify_audio_file(
        "/fake/path.flac",
        "Bohemian Rhapsody",
        "Queen",
    )
    assert result == VerificationResult.PASS


def test_matching_versions_pass(verifier):
    """Both expected and matched are the live version of the same song.
    Versions agree — must PASS."""
    _stub_lookup(verifier, recordings=[
        {"title": "Hello (Live at Wembley)", "artist": "Adele"},
    ])

    result, _ = verifier.verify_audio_file(
        "/fake/path.flac",
        "Hello (Live at Wembley)",
        "Adele",
    )
    assert result == VerificationResult.PASS


# ---------------------------------------------------------------------------
# Secondary scan path — version gate must apply to fallback recordings too.
# ---------------------------------------------------------------------------


def test_secondary_scan_skips_wrong_version_recordings(verifier):
    """When the best AcoustID recording's title doesn't match strongly
    enough, verify scans through the rest of the recordings list looking
    for a better candidate. That fallback path must also reject
    wrong-version variants — otherwise an instrumental from the same
    fingerprint cluster could win the scan and pass verification."""
    _stub_lookup(verifier, recordings=[
        # Best by combined score: a different track entirely. Same
        # artist, completely different title. Original version (no
        # version mismatch on this one).
        {"title": "Some Other Song", "artist": "Drake"},
        # Fallback candidate: instrumental version of the requested
        # song. Without the scan-loop version gate, this would PASS
        # (title matches after stripping "(Instrumental)", artist
        # matches). With the gate, it gets skipped and the loop falls
        # through.
        {"title": "In My Feelings (Instrumental)", "artist": "Drake"},
    ])

    result, _ = verifier.verify_audio_file(
        "/fake/path.flac",
        "In My Feelings",
        "Drake",
    )
    # Best-recording version check passes (both 'original'), then the
    # main pass/fail bucket misses (title doesn't match), fallback scan
    # skips the instrumental, no other valid recording → falls through
    # to the final unmatched logic. Either FAIL or SKIP is acceptable
    # here; the critical assertion is "did NOT pass via the
    # instrumental-version recording".
    assert result != VerificationResult.PASS
