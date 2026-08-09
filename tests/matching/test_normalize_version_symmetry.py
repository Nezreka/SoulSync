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


# --- a marker INSIDE the tail is not a version tag (PR #1121 review) ---
#
# Nezreka: a rule of "the qualifier CONTAINS a version token" eats real titles,
# because 'radio', 'piano', 'single', 'live' and 'take' are all markers. These
# are "Artist - Title" strings whose title must survive intact.
@pytest.mark.parametrize(
    "combined,expected",
    [
        ('Queen - Radio Ga Ga', 'queen radio ga ga'),
        ('Billy Joel - Piano Man', 'billy joel piano man'),
        ('Beyonce - Single Ladies', 'beyonce single ladies'),
        ('Wings - Live and Let Die', 'wings live and let die'),
        ('a-ha - Take On Me', 'aha take on me'),
        ('Bruce Springsteen - Radio Nowhere', 'bruce springsteen radio nowhere'),
        ('Prince - Little Red Corvette', 'prince little red corvette'),
    ],
)
def test_leading_marker_in_a_real_title_is_not_a_version_tag(combined, expected):
    assert normalize(combined) == expected


@pytest.mark.parametrize(
    "combined",
    [
        "Spider-Man",
        # 'remix'/'live' are markers, but an UNSPACED hyphen is part of a word,
        # not Spotify's ' - ' version separator — the strip must not fire.
        "Post-Remix",
        "Alt-Live",
    ],
)
def test_hyphenated_word_not_treated_as_version_tail(combined):
    assert normalize(combined) == combined.lower().replace("-", "")


# --- Spotify's canonical tails must normalize like their bracket twin ---
#
# The two most common version tags in the catalogue both END in a non-marker
# word ('Remastered 2011', 'Live at Wembley Stadium'), so a last-token-only
# rule left the dash form and the bracket form with different identities —
# the drift this whole helper exists to prevent. Bare title on the right is
# what MusicBrainz/AcoustID actually returns for these recordings.
@pytest.mark.parametrize(
    "dashed,bracketed",
    [
        ("Bohemian Rhapsody - Remastered 2011", "Bohemian Rhapsody (Remastered 2011)"),
        ("Hey Jude - Remastered 2015", "Hey Jude (Remastered 2015)"),
        ("Baba O'Riley - Remaster 2009", "Baba O'Riley (Remaster 2009)"),
        ("Wish You Were Here - Live at Wembley 1974",
         "Wish You Were Here (Live at Wembley 1974)"),
        ("Redemption Song - Live From Paris", "Redemption Song (Live From Paris)"),
        ("Nothing Else Matters - Sped Up", "Nothing Else Matters (Sped Up)"),
    ],
)
def test_spotify_canonical_tails_match_the_bracket_form(dashed, bracketed):
    assert normalize(dashed) == normalize(bracketed)
    assert similarity(dashed, bracketed) == 1.0


@pytest.mark.parametrize(
    "dashed,bare",
    [
        ("Bohemian Rhapsody - Remastered 2011", "Bohemian Rhapsody"),
        ("Hotel California - 2013 Remaster", "Hotel California"),
        ("Comfortably Numb - Live at Earls Court", "Comfortably Numb"),
    ],
)
def test_version_tail_matches_the_bare_recording_title(dashed, bare):
    assert similarity(dashed, bare) == 1.0


# --- a tail naming a DIFFERENT track is never dropped ---
#
# Collapsing 'Song - Pt. 1' and 'Song - Pt. 2' (or a song and its interlude)
# onto one identity scores the WRONG file at 1.00, which is a verification
# PASS for a file that should FAIL.
@pytest.mark.parametrize(
    "a,b",
    [
        ("Sicko Mode - Pt. 2", "Sicko Mode - Pt. 1"),
        ("Runaway - Part II", "Runaway - Part I"),
        ("Yellow - Interlude", "Yellow"),
        ("Blinding Lights - Intro", "Blinding Lights"),
    ],
)
def test_distinct_track_tails_keep_their_identity(a, b):
    assert normalize(a) != normalize(b)
    assert similarity(a, b) < 1.0


# --- an over-stripped tail must never crater a genuine comparison ---
#
# No token rule can tell 'Taylor Swift - Long Live' (artist + title) from
# 'Halo - Long Live' (title + version tag) — both end in a marker. So the
# strip is not allowed to be load-bearing: similarity() also scores the
# un-stripped form and keeps the better result, which turns a wrong strip
# from a quarantined track into a slightly lower score.
@pytest.mark.parametrize(
    "combined,title",
    [
        ("Taylor Swift - Long Live", "Long Live"),
        ("Portugal. The Man - Live in the Moment", "Live in the Moment"),
        ("The Prodigy - Smack My Bitch Up", "Smack My Bitch Up"),
    ],
)
def test_overstripped_tail_still_scores_against_the_real_title(combined, title):
    # Without the un-stripped fallback these collapse to the artist name and
    # score ~0.1 against the recording title.
    assert similarity(combined, title) > 0.5


# --- existing behaviour must still hold ---

@pytest.mark.parametrize(
    "suffix",
    ["Instrumental", "Vocal", "Clean", "Explicit", "Original Mix"],
)
def test_whitelisted_dash_suffix_still_stripped(suffix):
    assert normalize(f"In My Feelings - {suffix}") == "in my feelings"


def test_genuinely_different_song_still_fails():
    out = evaluate(
        "Yellow", "Coldplay",
        [{'title': "Rich Interlude", 'artist': "Kendrick Lamar"}],
        fingerprint_score=0.85,
    )
    assert out.decision == Decision.FAIL


# --- the dash needs whitespace on ONE side, not both ---
#
# Real catalogue rows write ']- Single' (the bracket group absorbs the space
# before the dash), so demanding a space on both sides left 'single' glued to
# the title. Demanding one is still enough to keep 'Spider-Man' intact.
@pytest.mark.parametrize(
    "title,expected",
    [
        ("Cold Water [Anirudh Diwali Edition]- Single", "cold water"),
        ("Some Song -Remastered 2011", "some song"),
        ("Some Song- Live at Wembley", "some song"),
        ("Spider-Man", "spiderman"),
        ("Post-Remix", "postremix"),
    ],
)
def test_dash_separator_needs_one_adjacent_space(title, expected):
    assert normalize(title) == expected


# --- CJK identity must survive normalization ---

@pytest.mark.parametrize(
    "title,expected",
    [
        ("夜に駆ける", "夜に駆ける"),
        ("紅蓮華 - Instrumental", "紅蓮華"),
        ("残酷な天使のテーゼ - TV Size", "残酷な天使のテーゼ"),
        ("僕の戦争 - 僕の戦争", "僕の戦争 僕の戦争"),
    ],
)
def test_cjk_titles_keep_their_identity(title, expected):
    assert normalize(title) == expected


# --- separators used by Japanese and provider metadata ---

@pytest.mark.parametrize(
    "title,expected",
    [
        # ideographic space around an ASCII dash
        ("残酷な天使のテーゼ　-　Instrumental", "残酷な天使のテーゼ"),
        # fullwidth hyphen-minus (U+FF0D) — what a JP tagger writes
        ("残酷な天使のテーゼ － Instrumental", "残酷な天使のテーゼ"),
        ("紅蓮華 － ライブ", "紅蓮華"),
        # unicode hyphen (U+2010) and minus sign (U+2212)
        ("Bohemian Rhapsody ‐ Remastered 2011", "bohemian rhapsody"),
        ("Bohemian Rhapsody − Remastered 2011", "bohemian rhapsody"),
        # a CJK title is never emptied by any of this
        ("紅蓮華 － 紅蓮華", "紅蓮華 紅蓮華"),
    ],
)
def test_unicode_dash_separators(title, expected):
    assert normalize(title) == expected


@pytest.mark.parametrize(
    "dashed,bare",
    [
        ("残酷な天使のテーゼ - Remastered 2019", "残酷な天使のテーゼ"),
        ("紅蓮華 - TV Size", "紅蓮華"),
        ("YOASOBI - Instrumental Ver.", "YOASOBI"),
        ("この街 - Off Vocal", "この街"),
        ("Vogel im Käfig - Live at Budokan", "Vogel im Käfig"),
        ("Barricades - 2013 Remaster", "Barricades"),
    ],
)
def test_japanese_and_mixed_script_version_tails(dashed, bare):
    assert similarity(dashed, bare) == 1.0


def test_a_version_strip_never_empties_a_title():
    """The strip removes a SUFFIX; if it ever consumed the whole string the
    title would score 0.0 against everything and a correct file would be
    reported unverifiable."""
    for title in ["Live - Live", "Instrumental - Instrumental",
                  "ライブ - ライブ", "Remix - Remix", "EP - EP"]:
        assert normalize(title), f"{title!r} normalized to empty"
