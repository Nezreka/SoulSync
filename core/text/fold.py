"""title folding that works for every script (#1306, zikasak).

five matchers normalized with ``[^a-z0-9 ]``: a japanese, hebrew, cyrillic
or greek title folded to an empty string (or to stray latin like "ver"), and
two empty strings compare as a perfect 1.0. so the track number repair
"matched" 君のいない夜を越えて to על הסף at 100% and renumbered it.

``fold_title`` keeps letters and digits of ANY script: accents fold away
(é→e, so latin behaves as before), case folds, punctuation goes. the
similarity helpers never call two emptied names a match.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from functools import lru_cache

_BRACKETS = re.compile(r"\(.*?\)|\[.*?\]|（.*?）|【.*?】")
_SPACES = re.compile(r"\s+")


@lru_cache(maxsize=65536)
def fold_title(text: str, drop_brackets: bool = True) -> str:
    """lowercase letters/digits of any script, single-spaced. qualifiers in
    brackets drop by default (remaster / version noise)."""
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", str(text))
    if drop_brackets:
        t = _BRACKETS.sub(" ", t)
    # accents off: é→e, but a kana/hangul/han letter keeps its base letter
    t = "".join(ch for ch in unicodedata.normalize("NFKD", t) if unicodedata.category(ch) != "Mn")
    t = t.casefold()
    t = "".join(ch if (ch.isalnum() or ch.isspace()) else " " for ch in t)
    return _SPACES.sub(" ", t).strip()


def title_similarity(a: str, b: str, drop_brackets: bool = True) -> float:
    """similarity of two titles after folding; 0.0 when either folds to
    nothing (nothing left to compare is not a match)."""
    fa, fb = fold_title(a or "", drop_brackets), fold_title(b or "", drop_brackets)
    return folded_similarity(fa, fb)


def folded_similarity(fa: str, fb: str) -> float:
    """SequenceMatcher on already-folded strings, with the empty guard."""
    if not fa or not fb:
        return 0.0
    return SequenceMatcher(None, fa, fb).ratio()


__all__ = ["fold_title", "title_similarity", "folded_similarity"]
