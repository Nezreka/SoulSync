"""#1579: the acoustid version gate quarantined every "explicit" track.

musicbrainz never puts "explicit" in a recording title, and deezer names
whole albums "... (Album Version Explicit)". the gate read the expected title
as version explicit and the matched recording as original, so a correct file
(fingerprint 0.97, title and artist 1.0) failed with "Version mismatch:
expected (explicit) but file is (original)", on every copy from every peer.
"""

from core.matching.audio_verification import Decision, evaluate
from core.matching_engine import MusicMatchingEngine


def _rec(title, artist='Buckcherry'):
    return {'title': title, 'artist': artist}


def test_an_explicit_title_passes_against_the_bare_recording():
    out = evaluate('Frontside (Album Version Explicit)', 'Buckcherry', [_rec('Frontside')],
                   fingerprint_score=0.97)
    assert out.decision == Decision.PASS, out.reason


def test_either_side_and_uncensored_too():
    assert evaluate('Frontside', 'Buckcherry', [_rec('Frontside (Explicit)')],
                    fingerprint_score=0.97).decision == Decision.PASS
    assert evaluate('Frontside (Uncensored)', 'Buckcherry', [_rec('Frontside')],
                    fingerprint_score=0.97).decision == Decision.PASS


def test_a_clean_edit_is_still_a_different_version():
    out = evaluate('Frontside (Album Version Explicit)', 'Buckcherry', [_rec('Frontside (clean)')],
                   fingerprint_score=0.97)
    assert out.decision == Decision.FAIL and 'Version mismatch' in out.reason
    out = evaluate('Frontside (Clean)', 'Buckcherry', [_rec('Frontside')], fingerprint_score=0.97)
    assert out.decision == Decision.FAIL


def test_other_versions_stay_strict():
    for expected, matched in [('Frontside (Album Version Explicit)', 'Frontside (instrumental)'),
                              ('Frontside (Explicit)', 'Frontside (remix)')]:
        out = evaluate(expected, 'Buckcherry', [_rec(matched)], fingerprint_score=0.97)
        assert out.decision == Decision.FAIL, (expected, matched, out.reason)


def test_ranking_still_sees_explicit():
    """#923's candidate ranking reads detect_version_type, untouched"""
    assert MusicMatchingEngine().detect_version_type('Song (Explicit)')[0] == 'explicit'
