"""A similarity score across two writing systems is not evidence.

Real findings from the user's production library (2026-08-25), both on the
same album, both reported as "Wrong download" by the AcoustID scan while the
download-time check had passed the very same files:

    Wrong download: "Apetitan" is actually "APETITAN"
    Expected "Apetitan" by Sawano Hiroyuki, but audio fingerprint matches
    "APETITAN" by 澤野弘之 (fingerprint: 100%, title 100%, artist 0%)

    Wrong download: "You See Big Girl / T:T" is actually "YouSeeBIGGIRL/T:T"
    (fingerprint: 100%, title 92%, artist 0%)

`artist_sim` is 0.00 there because romaji and kanji cannot be string-compared,
not because the artist is wrong — yet `evaluate` read "below
CLEAR_MISMATCH_THRESHOLD" as "clear wrong song". The rule these tests pin is
the general one: a dimension whose two sides are written in different scripts
is UNKNOWN, never FAILED, so it can never be the evidence a FAIL rests on.
Applies to Cyrillic / Greek / Hangul / Arabic exactly as it does to Japanese,
and to titles exactly as it does to artists.
"""

from core.matching.audio_verification import Decision, evaluate


def _rec(title, artist, score=1.0):
    return {"title": title, "artist": artist, "score": score, "mbid": "mb-1"}


# --- the two production findings -------------------------------------------


def test_kanji_artist_with_matching_title_is_not_a_wrong_download():
    out = evaluate(
        "Apetitan", "Sawano Hiroyuki",
        [_rec("APETITAN", "澤野弘之")],
        fingerprint_score=1.0,
        aliases_provider=None,          # alias bridge unavailable, as in prod
    )
    assert out.decision is not Decision.FAIL
    assert out.artist_sim == 0.0        # still reported honestly


def test_kanji_artist_with_near_matching_title_is_not_a_wrong_download():
    out = evaluate(
        "You See Big Girl / T:T", "Sawano Hiroyuki",
        [_rec("YouSeeBIGGIRL/T:T", "澤野弘之")],
        fingerprint_score=1.0,
        aliases_provider=None,
    )
    assert out.decision is not Decision.FAIL


# --- the same rule, other scripts ------------------------------------------


def test_cyrillic_artist_with_matching_title_is_not_a_wrong_download():
    out = evaluate(
        "Nocturne", "Dmitry Yablonsky",
        [_rec("Nocturne", "Дмитрий Яблонский")],
        fingerprint_score=0.88,
        aliases_provider=None,
    )
    assert out.decision is not Decision.FAIL


def test_hangul_artist_with_matching_title_is_not_a_wrong_download():
    out = evaluate(
        "Spring Day", "BTS",
        [_rec("Spring Day", "방탄소년단")],
        fingerprint_score=0.86,
        aliases_provider=None,
    )
    assert out.decision is not Decision.FAIL


# --- cross-script TITLE, artist agrees -------------------------------------
#
# The old code only rescued this above a fingerprint of 0.95, so an ordinary
# 0.90 match on a Japanese-titled track was quarantined. The script signal
# says the title is incomparable regardless of how the fingerprint scored.


def test_kanji_title_with_matching_artist_is_not_a_wrong_download():
    out = evaluate(
        "Zankoku na Tenshi no Thesis", "Yoko Takahashi",
        [_rec("残酷な天使のテーゼ", "Yoko Takahashi", score=0.90)],
        fingerprint_score=0.90,
        aliases_provider=None,
    )
    assert out.decision is not Decision.FAIL


def test_both_sides_cross_script_is_not_a_wrong_download():
    out = evaluate(
        "Zankoku na Tenshi no Thesis", "Yoko Takahashi",
        [_rec("残酷な天使のテーゼ", "高橋洋子", score=0.90)],
        fingerprint_score=0.90,
        aliases_provider=None,
    )
    assert out.decision is not Decision.FAIL


# --- and it must NOT blunt a real mismatch ---------------------------------


def test_same_script_wrong_artist_still_fails():
    out = evaluate(
        "Apetitan", "Sawano Hiroyuki",
        [_rec("Apetitan", "Taylor Swift")],
        fingerprint_score=1.0,
        aliases_provider=None,
    )
    assert out.decision is Decision.FAIL


def test_same_script_wrong_song_still_fails():
    out = evaluate(
        "Barricades", "Sawano Hiroyuki",
        [_rec("Wanna Be Startin' Somethin'", "Michael Jackson")],
        fingerprint_score=1.0,
        aliases_provider=None,
    )
    assert out.decision is Decision.FAIL


def test_two_different_kanji_artists_still_fail():
    # Both sides non-Latin: same script class, so the comparison IS meaningful
    # and a disagreement is real evidence.
    out = evaluate(
        "残酷な天使のテーゼ", "澤野弘之",
        [_rec("残酷な天使のテーゼ", "米津玄師")],
        fingerprint_score=1.0,
        aliases_provider=None,
    )
    assert out.decision is Decision.FAIL


def test_cross_script_artist_but_genuinely_different_song_still_fails():
    # Artist incomparable, but the title IS comparable and disagrees — one
    # comparable dimension in disagreement is enough to fail.
    out = evaluate(
        "Barricades", "Sawano Hiroyuki",
        [_rec("Wanna Be Startin' Somethin'", "澤野弘之")],
        fingerprint_score=1.0,
        aliases_provider=None,
    )
    assert out.decision is Decision.FAIL


# --- the accepted cost of the rule above -----------------------------------


def test_a_wrong_download_in_another_script_is_knowingly_unreportable():
    """The blind spot, pinned so nobody "fixes" it by accident.

    Both dimensions are unreadable, so nothing disagrees and nothing agrees —
    and a Latin expectation against a Japanese recording looks identical whether
    it is the same song or a completely different one. A fingerprint bar was
    tried here and reverted: the score is not evidence about the NAMES, so all
    it does is quarantine the correct files in the test above. Closing this
    needs a signal that actually distinguishes the two — a duration or an MBID
    the import already recorded — not a stricter reading of a silence.
    """
    out = evaluate(
        "Blinding Lights", "The Weeknd",
        [_rec("残酷な天使のテーゼ", "高橋洋子")],
        fingerprint_score=0.90,
        aliases_provider=None,
    )
    assert out.decision is Decision.SKIP


def test_an_agreeing_artist_corroborates_an_unreadable_title():
    """One comparable dimension that AGREES is evidence; two silences are not."""
    out = evaluate(
        "Zankoku na Tenshi no These", "Yoko Takahashi",
        [_rec("残酷な天使のテーゼ", "Yoko Takahashi")],
        fingerprint_score=0.82,
        aliases_provider=None,
    )
    assert out.decision is Decision.SKIP


def test_a_readable_artist_that_disagrees_is_still_a_wrong_download():
    """The rule only ever protects UNREADABLE dimensions."""
    out = evaluate(
        "Zankoku na Tenshi no These", "Yoko Takahashi",
        [_rec("残酷な天使のテーゼ", "Metallica")],
        fingerprint_score=0.90,
        aliases_provider=None,
    )
    assert out.decision is Decision.FAIL
