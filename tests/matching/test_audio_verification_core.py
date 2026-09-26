"""Shared audio-verification decision core: normalize() + evaluate().

One place for normalization + the PASS/SKIP/FAIL decision used by BOTH import-time
verification and the library AcoustID scan, so the two paths can't drift apart.
"""

from core.matching.audio_verification import normalize, evaluate, Decision


def _rec(title, artist, duration=None):
    return {"title": title, "artist": artist, "duration": duration}


def test_cross_script_vocal_credit_clean_match_passes():
    # Sawano / 澤野弘之 <Vocal: ...> — <> stripped, alias bridges the artist,
    # title matches too -> clean PASS (must never FAIL/quarantine).
    out = evaluate(
        "Call Your Name", "Sawano Hiroyuki",
        [_rec("call your name", "澤野弘之 <Vocal: mpi & CASG>")],
        fingerprint_score=0.95,
        aliases_provider=lambda: ["澤野弘之"],
    )
    assert out.decision == Decision.PASS


def test_cross_script_ipa_title_skips_not_fails():
    # AcoustID returns an IPA-transcribed title that can't string-match, but the
    # artist bridges cross-script -> SKIP (import anyway), never FAIL.
    out = evaluate(
        "Attack on Titan", "Sawano Hiroyuki",
        [_rec("ətˈæk 0N tάɪtn", "澤野弘之")],
        fingerprint_score=0.95,
        aliases_provider=lambda: ["澤野弘之"],
    )
    assert out.decision == Decision.SKIP


def test_clean_cross_script_match_passes():
    out = evaluate(
        "Xl-Tt", "Sawano Hiroyuki",
        [_rec("xl-tt", "澤野弘之")],
        fingerprint_score=0.95,
        aliases_provider=lambda: ["澤野弘之"],
    )
    assert out.decision == Decision.PASS


def test_genuine_wrong_song_fails():
    out = evaluate(
        "Yellow", "Coldplay",
        [_rec("Rich Interlude", "Kendrick Lamar")],
        fingerprint_score=0.85,
    )
    assert out.decision == Decision.FAIL


def test_no_recordings_skips():
    out = evaluate("Whatever", "Someone", [], fingerprint_score=0.9)
    assert out.decision == Decision.SKIP


def test_normalize_strips_paren_bracket_angle_and_keeps_cjk():
    assert normalize("澤野弘之 <Vocal: MIKA KOBAYASHI>") == "澤野弘之"
    assert normalize("Clarity (Live at X) [Remastered]") == "clarity"
    assert normalize("Attack on Titan <TV Size>") == "attack on titan"


def test_normalize_strips_version_and_featuring():
    assert normalize("In My Feelings - Instrumental") == "in my feelings"
    assert normalize("Song feat. Someone") == "song"


def test_normalize_keeps_plain_text():
    assert normalize("Sawano Hiroyuki") == "sawano hiroyuki"


def test_empty_expected_artist_does_not_fail():
    # Old scanner treated a missing expected artist as artist-match=1.0
    # (compare title only). The unified core must not FAIL a track just
    # because the DB has no artist value.
    out = evaluate("Some Track", "", [_rec("Some Track", "Whoever")],
                   fingerprint_score=0.95)
    assert out.decision == Decision.PASS


def test_cluster_duration_disambiguation_picks_correct_mix():
    """When AcoustID cluster has multiple recordings (e.g. Radio Version [216s] and 3 AM Mix [416s]),
    duration awareness must pick the 3 AM mix that matches the file length (417s) instead of
    failing on the radio version."""
    recordings = [
        _rec("Behind the Cow (radio version)", "Scooter", duration=216),
        _rec("Behind the Cow (3 AM mix)", "Scooter", duration=416),
    ]
    out = evaluate(
        "Behind The Cow (3 AM Mix)", "Scooter",
        recordings,
        fingerprint_score=0.99,
        expected_duration_s=417.44,
    )
    assert out.decision == Decision.PASS
    assert out.matched_title == "Behind the Cow (3 AM mix)"


def test_verbatim_title_tie_breaker_prefers_matching_mix_name():
    """When durations are not available, verbatim title similarity breaks ties
    between candidate recordings sharing the same normalized base title."""
    recordings = [
        _rec("Song (Radio Edit)", "Artist"),
        _rec("Song (Club Mix)", "Artist"),
    ]
    out = evaluate(
        "Song (Club Mix)", "Artist",
        recordings,
        fingerprint_score=0.95,
    )
    assert out.decision == Decision.PASS
    assert out.matched_title == "Song (Club Mix)"
