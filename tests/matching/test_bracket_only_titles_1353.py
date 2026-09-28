"""#1353: bracket-only titles (e.g. "[untitled]") must not false-fail.

Title normalization strips [...] annotations. For a title consisting ENTIRELY
of a bracketed annotation, that collapsed the title to "" and similarity()
scored 0.0 — even against an identical title — so import-time AcoustID
verification quarantined a correct file with the confusing message
"file identified as '[untitled]' by '311', expected '[untitled]' by '311'".

The fix: when stripping would remove the whole string, the annotation content
is kept as the title ("[untitled]" -> "untitled").
"""

from core.matching.audio_verification import (
    TITLE_MATCH_THRESHOLD,
    Decision,
    evaluate,
    normalize,
    similarity,
)


def _rec(title, artist, score=0.9):
    return {"title": title, "artist": artist, "score": score}


def test_identical_bracket_only_titles_match():
    # The reporter's exact case: 311 / Stereolithic track 20.
    assert similarity("[untitled]", "[untitled]") == 1.0


def test_bracket_only_title_normalizes_to_its_content():
    assert normalize("[untitled]") == "untitled"


def test_bracket_only_title_matches_case_insensitively():
    assert similarity("[Untitled]", "[untitled]") == 1.0


def test_bracket_only_title_matches_bare_title():
    # The reporter's attempted workaround (retagging to "Untitled") should
    # also agree with the MusicBrainz "[untitled]" recording.
    assert similarity("[untitled]", "untitled") == 1.0
    assert similarity("[untitled]", "Untitled") == 1.0


def test_paren_only_title_matches():
    assert similarity("(untitled)", "(untitled)") == 1.0
    assert normalize("(untitled)") == "untitled"


def test_different_bracket_only_titles_still_mismatch():
    # The fix must not make every bracket-only title match every other one.
    assert similarity("[untitled]", "[live]") < TITLE_MATCH_THRESHOLD


def test_empty_string_behavior_unchanged():
    # Genuinely empty input still normalizes to "" and scores 0.0.
    assert normalize("") == ""
    assert normalize(None) == ""
    assert similarity("", "") == 0.0
    assert similarity("[untitled]", "") == 0.0


def test_reporter_case_passes_evaluation():
    # End to end: the exact import-time verification from the issue must PASS,
    # not quarantine the file.
    out = evaluate(
        "[untitled]", "311",
        [_rec("[untitled]", "311")],
        fingerprint_score=0.9,
    )
    assert out.decision == Decision.PASS
    assert out.title_sim >= TITLE_MATCH_THRESHOLD


def test_bracket_only_title_still_fails_a_wrong_song():
    # The fix must not let a bracket-only title pass against a different song.
    out = evaluate(
        "[untitled]", "311",
        [_rec("Come Original", "311")],
        fingerprint_score=0.9,
    )
    assert out.decision == Decision.FAIL
