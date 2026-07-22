"""Version-tag formatting symmetry in audio_verification.normalize().

Regression for a class of AcoustID false-positives: a downloaded file labels its
remix/edit/slowed version in PARENTHESES ("Title (Foo Remix)") while the expected
metadata uses a DASH ("Title - Foo Remix"). normalize() stripped parens wholesale
but only stripped a hardcoded whitelist of dash suffixes, so the two forms diverged
→ title similarity fell below threshold → the correct file was quarantined as an
"Audio mismatch".

These are the exact real-world tracks the user reported as wrongly quarantined.
After the fix both forms normalize to the same bare title and evaluate() must NOT
return FAIL (PASS, or SKIP for the extra-artist cases — both import the file).

Remix DISCRIMINATION (Don Diablo vs Tom Staar edit) is intentionally NOT this
function's job — it lives in the download-time matcher (test_divergent_version.py)
and the version-category gate, which reads the RAW titles, not normalize() output.
"""

from __future__ import annotations

import pytest

from core.matching.audio_verification import normalize, similarity, evaluate, Decision


# (expected_title, expected_artist, matched_title, matched_artist)
_REPORTED = [
    ('King Of My Castle - Don Diablo Edit', 'Keanu Silva',
     'King of My Castle (Don Diablo edit)', 'Keanu Silva'),
    ('Her Eyes - Slowed', 'Narvent',
     'Her Eyes (slowed)', 'Narvent'),
    ('void - super slowed', 'isq',
     'void (super slowed)', 'ISQ'),
    ('Is There Anybody out There - Jon Campbell Radio Edit', 'Michael Oakley',
     'Is There Anybody Out There (Jon Campbell Radio Edit)', 'Michael Oakley'),
    ('Monster - Robin Schulz Remix', 'LUM!X',
     'Monster (Robin Schulz remix)', 'LUM!X, Gabry Ponte'),
    ('In The End - Mellen Gi Remix', 'Tommee Profitt',
     'In the End (Mellen Gi remix)', 'Tommee Profitt feat. Fleurie'),
    ('SLAY! - Slowed + Reverb', 'Eternxlkz',
     'SLAY! (slowed + reverb)', 'Eternxlkz'),
]


@pytest.mark.parametrize('exp_t,_exp_a,mat_t,_mat_a', _REPORTED)
def test_dash_and_paren_version_forms_normalize_equal(exp_t, _exp_a, mat_t, _mat_a):
    assert normalize(exp_t) == normalize(mat_t)
    assert similarity(exp_t, mat_t) == 1.0


@pytest.mark.parametrize('exp_t,exp_a,mat_t,mat_a', _REPORTED)
def test_reported_tracks_are_not_quarantined(exp_t, exp_a, mat_t, mat_a):
    out = evaluate(
        exp_t, exp_a,
        [{'title': mat_t, 'artist': mat_a}],
        fingerprint_score=0.9,
    )
    assert out.decision != Decision.FAIL, (
        f"{exp_t!r} vs {mat_t!r} → {out.decision} ({out.reason})"
    )


# --- the strip must stay version-aware: real dashed titles survive ---

def test_non_version_dash_tail_preserved():
    # "Bad Girl" carries no version keyword — it is part of the title, keep it.
    assert normalize("Marvin's Room - Bad Girl") == "marvins room bad girl"


def test_hyphenated_word_not_treated_as_version_tail():
    # No space before the hyphen → the version-tail strip must not fire; the
    # bare hyphen is then dropped as punctuation (existing behaviour).
    assert normalize("Spider-Man") == "spiderman"


# --- existing behaviour must still hold ---

def test_whitelisted_dash_suffix_still_stripped():
    assert normalize("In My Feelings - Instrumental") == "in my feelings"


def test_genuinely_different_song_still_fails():
    out = evaluate(
        "Yellow", "Coldplay",
        [{'title': "Rich Interlude", 'artist': "Kendrick Lamar"}],
        fingerprint_score=0.85,
    )
    assert out.decision == Decision.FAIL
