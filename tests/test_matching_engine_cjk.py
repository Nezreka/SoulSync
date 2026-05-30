"""Tests for ``MusicMatchingEngine.normalize_string`` CJK preservation.

Issue #722 (@Sokhii): downloading a Japanese OST through Apple Music
metadata + Tidal download produced duplicate tracks — same audio
landed under multiple track positions in the album.

Root cause: ``normalize_string`` detected CJK presence and SKIPPED
unidecode (correct — kanji→pinyin would have been gibberish), but
then ran ``re.sub(r'[^a-z0-9\\s$]', '', text)`` which stripped EVERY
CJK character. Every Japanese title normalised to ``''``. The
``similarity_score`` guard at ``if not str1 or not str2: return 0.0``
made every CJK-vs-CJK comparison return ``0.000``, so the matcher
fell back to duration+artist alone — multiple iTunes tracks mapped
to the same Tidal candidate, and the user got duplicate downloads
under different track positions.

These tests pin the new behaviour: CJK characters survive
normalisation, identical CJK titles score 1.0, disjoint CJK titles
score low, mixed CJK+Latin titles work, and Latin-only titles are
completely unaffected.
"""

from __future__ import annotations

import pytest

from core.matching_engine import MusicMatchingEngine


@pytest.fixture
def engine() -> MusicMatchingEngine:
    return MusicMatchingEngine()


# ---------------------------------------------------------------------------
# Normalisation preserves CJK ranges.
# ---------------------------------------------------------------------------


def test_normalize_preserves_kanji_characters(engine: MusicMatchingEngine):
    """Japanese kanji must survive normalisation, not get stripped."""
    assert engine.normalize_string('命の灯火') == '命の灯火'


def test_normalize_preserves_hiragana_characters(engine: MusicMatchingEngine):
    """Hiragana also survives."""
    assert engine.normalize_string('あいうえお') == 'あいうえお'


def test_normalize_preserves_katakana_characters(engine: MusicMatchingEngine):
    """Katakana — common in Japanese song titles for foreign loanwords —
    survives. Pre-fix this was the most-visible failure since OST titles
    are often Katakana."""
    assert engine.normalize_string('ハッピーデイズ') == 'ハッピーデイズ'


def test_normalize_preserves_hangul_characters(engine: MusicMatchingEngine):
    """Korean Hangul survives (same root cause hits K-Pop OST tracks)."""
    assert engine.normalize_string('안녕하세요') == '안녕하세요'


def test_normalize_preserves_simplified_chinese_characters(engine: MusicMatchingEngine):
    """Chinese hanzi survives (same root cause hits Mandarin / Cantonese
    releases). All three CJK ideograph users were broken together; the
    fix covers all three."""
    assert engine.normalize_string('你好世界') == '你好世界'


def test_normalize_lowercases_cjk_branch_does_not_uppercase_ascii(engine: MusicMatchingEngine):
    """Mixed CJK + Latin string — CJK branch was supposed to keep CJK and
    only lowercase; verify the Latin half also gets lowercased and isn't
    accidentally left as-is."""
    assert engine.normalize_string('Happy 命') == 'happy 命'


def test_normalize_strips_latin_punctuation_in_cjk_branch(engine: MusicMatchingEngine):
    """The CJK branch must still strip Latin punctuation — only CJK
    ranges are preserved, not random symbols. ``!`` should still go,
    same as in the Latin branch."""
    assert engine.normalize_string('命の灯火!') == '命の灯火'


# ---------------------------------------------------------------------------
# Similarity scoring on CJK titles.
# ---------------------------------------------------------------------------


def test_identical_cjk_titles_score_perfect_match(engine: MusicMatchingEngine):
    """Same Japanese title twice → 1.0. Pre-fix this was 0.0 because
    both normalised to '' and the empty-string guard short-circuited."""
    a = engine.clean_title('命の灯火')
    b = engine.clean_title('命の灯火')
    assert engine.similarity_score(a, b) == 1.0


def test_completely_disjoint_cjk_titles_score_low(engine: MusicMatchingEngine):
    """Two unrelated Japanese titles share no characters → similarity
    near 0. The point is that they're DIFFERENT — pre-fix they both
    normalised to '' so were treated the same as "identical"."""
    a = engine.clean_title('命の灯火')
    b = engine.clean_title('無職転生')
    score = engine.similarity_score(a, b)
    assert score < 0.3


def test_partially_overlapping_cjk_titles_score_partial(engine: MusicMatchingEngine):
    """Sequential matching gives a midrange score for partial overlap —
    proves the comparator is actually looking at the characters, not
    just returning 0 or 1."""
    a = engine.clean_title('命の灯火')
    b = engine.clean_title('命の音')
    score = engine.similarity_score(a, b)
    assert 0.3 < score < 1.0


def test_cjk_title_does_not_falsely_match_unrelated_latin_title(engine: MusicMatchingEngine):
    """Pre-fix bug: a CJK title normalised to '' would short-circuit
    similarity scoring against ANY Latin title (also returning 0
    because of the empty guard). That's still 0 in both directions
    so the symptom isn't directly observable here — but pin that a
    real CJK string vs a real Latin string returns a meaningful low
    score, not a coincidental match."""
    a = engine.clean_title('命の灯火')
    b = engine.clean_title('Happy Days')
    score = engine.similarity_score(a, b)
    assert score < 0.2


# ---------------------------------------------------------------------------
# Regression: Latin-only titles are untouched.
# ---------------------------------------------------------------------------


def test_latin_normalisation_unchanged_for_simple_title(engine: MusicMatchingEngine):
    """No CJK in input → unidecode + lowercase path, exactly as before."""
    assert engine.normalize_string('Happy Days') == 'happy days'


def test_latin_normalisation_unchanged_for_unidecode_target(engine: MusicMatchingEngine):
    """Cyrillic / accented Latin still goes through unidecode."""
    assert engine.normalize_string('Björk') == 'bjork'


def test_latin_normalisation_unchanged_for_dollar_sign(engine: MusicMatchingEngine):
    """The ``$`` preservation rule still applies in the Latin branch
    (A$AP Rocky etc.) — pinned so the CJK refactor doesn't accidentally
    drop it."""
    norm = engine.normalize_string('A$AP Rocky')
    assert '$' in norm


def test_latin_similarity_unchanged_for_baseline_comparison(engine: MusicMatchingEngine):
    """Sanity: the existing Latin-Latin scoring behaviour didn't shift.
    Identical strings still score 1.0; different strings score below
    1.0. Pin a specific pair from the regression report so a future
    normaliser tweak doesn't quietly change Latin-side semantics."""
    a = engine.clean_title('Happy Days')
    b = engine.clean_title('Happy Days')
    assert engine.similarity_score(a, b) == 1.0

    c = engine.clean_title('Happy Days')
    d = engine.clean_title('My Past Self')
    assert engine.similarity_score(c, d) < 0.5


# ---------------------------------------------------------------------------
# Real-world scenarios from issue #722.
# ---------------------------------------------------------------------------


def test_mushoku_tensei_ost_track_titles_distinguishable():
    """End-to-end: the exact scenario from #722. Two different iTunes
    tracks from Mushoku Tensei Original Soundtrack II — pre-fix both
    normalised to '' so the matcher couldn't tell them apart and
    routed both to the same Tidal candidate. Post-fix they're
    distinguishable.

    Track titles taken from the iTunes album response for id 1753240110;
    when the storefront returns Japanese titles instead of the English
    romanisations (depends on user's region + storefront config), this
    is the comparator the matcher will use."""
    engine = MusicMatchingEngine()
    # Two distinct OST tracks rendered in Japanese.
    track_a = engine.clean_title('幸せの日々')   # 'Happy Days'
    track_b = engine.clean_title('家探し')        # 'Home Search'
    score = engine.similarity_score(track_a, track_b)
    # The match must be well below the 0.7+ threshold the candidate
    # scorer uses to accept a match — otherwise both iTunes tracks
    # would still pick the same Tidal candidate and duplicate.
    assert score < 0.5


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
