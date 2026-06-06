"""Writing-system (script) compatibility helpers for metadata comparison.

Issue #797 — AcoustID returns a recording's title/artist in their
*original* script (e.g. ``久石譲`` for Joe Hisaishi) while SoulSync's
expected metadata is romanized / English (``Joe Hisaishi``). A raw
string-similarity comparison between two different writing systems
scores ~0 even when they name the very same artist, so correct
downloads of non-English artists get false-quarantined.

These pure helpers let callers DETECT that situation — "one side is
written in a non-Latin script, the other in Latin" — so the comparison
logic can stop treating an untranslatable title/artist as evidence the
file is wrong.

Deliberately conservative: a single accented Latin character (``é``,
``ñ``, ``ü``) is still Latin, NOT a script mismatch. Only genuinely
different writing systems (CJK, Hangul, Cyrillic, Greek, Arabic,
Hebrew, Thai, …) count as "non-Latin".
"""

from __future__ import annotations

# Unicode ranges for non-Latin writing systems we treat as a "hard"
# script difference. Latin (incl. Latin-1 Supplement / Extended with
# diacritics) is intentionally absent — accented Latin is still Latin.
# CJK ranges mirror core.matching_engine's issue #722 detection so the
# two stay consistent.
_NONLATIN_RANGES = (
    ('Ͱ', 'Ͽ'),  # Greek and Coptic
    ('Ѐ', 'ӿ'),  # Cyrillic
    ('Ԁ', 'ԯ'),  # Cyrillic Supplement
    ('֐', '׿'),  # Hebrew
    ('؀', 'ۿ'),  # Arabic
    ('ݐ', 'ݿ'),  # Arabic Supplement
    ('฀', '๿'),  # Thai
    ('⺀', '⻿'),  # CJK Radicals Supplement
    ('぀', 'ゟ'),  # Hiragana
    ('゠', 'ヿ'),  # Katakana
    ('㐀', '䶿'),  # CJK Unified Ideographs Extension A
    ('一', '鿿'),  # CJK Unified Ideographs
    ('가', '힯'),  # Hangul Syllables
    ('豈', '﫿'),  # CJK Compatibility Ideographs
    ('ｦ', 'ￜ'),  # Halfwidth Katakana / Hangul
)


def _is_nonlatin_char(c: str) -> bool:
    """True when ``c`` belongs to a non-Latin writing system."""
    for lo, hi in _NONLATIN_RANGES:
        if lo <= c <= hi:
            return True
    return False


def has_strong_nonlatin(text: str) -> bool:
    """True when ``text`` contains at least one non-Latin-script letter.

    Accented Latin (``Beyoncé``, ``Sigur Rós``, ``Mötley Crüe``) returns
    False — those are Latin. ``久石譲``, ``Дмитрий``, ``방탄소년단`` return True.
    """
    if not text:
        return False
    return any(_is_nonlatin_char(c) for c in text)


def _has_latin_letter(text: str) -> bool:
    """True when ``text`` contains an ASCII A–Z / a–z letter."""
    if not text:
        return False
    return any(('a' <= c <= 'z') or ('A' <= c <= 'Z') for c in text)


def is_cross_script_mismatch(a: str, b: str) -> bool:
    """True when ``a`` and ``b`` are written in different scripts.

    Specifically: exactly one side uses a non-Latin writing system while
    the other is genuine Latin text. This is the signal that a raw
    similarity score between the two is meaningless (a romanized name vs
    its native-script form), NOT that they name different things.

    Symmetric. Returns False when:
      - both sides are Latin (ordinary English-vs-English comparison),
      - both sides are non-Latin (same-script comparison still works),
      - either side is empty / has no comparable letters.
    """
    a_nonlatin = has_strong_nonlatin(a)
    b_nonlatin = has_strong_nonlatin(b)
    if a_nonlatin == b_nonlatin:
        # Same script class on both sides (or neither has non-Latin) —
        # similarity comparison is meaningful, no script bridge needed.
        return False
    # Exactly one side is non-Latin. It's only a true cross-script case
    # if the OTHER side is real Latin text (not punctuation / digits).
    if a_nonlatin:
        return _has_latin_letter(b)
    return _has_latin_letter(a)
