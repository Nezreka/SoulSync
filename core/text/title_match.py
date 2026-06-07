"""Guard against char-level title false positives in track matching.

Issue #769: playlist sync matched tracks that aren't in the library to a
DIFFERENT song by the SAME artist, with high confidence — e.g. "Dani
California" -> "Californication" (Red Hot Chili Peppers), "Under The Bridge"
-> "Around the World". The confidence formula is ``0.5*title + 0.5*artist``,
and a same-artist comparison always yields ``artist = 1.0``, so the title score
is the only thing that can tell two of an artist's songs apart. But the title
score is a ``difflib.SequenceMatcher`` character ratio, which over-credits
unrelated titles that happen to share a long substring ("californi…") or only a
stopword ("the"): 0.67 and 0.62 respectively. With the flat 0.5 artist term
that lands at 0.83 / 0.81 — well over the 0.7 sync threshold.

``titles_plausibly_same`` adds a cheap word-level sanity check on top of the
char ratio: accept a pair only when it's near-identical char-wise (so typos and
punctuation/casing variants — "Beleive"/"Believe", "HUMBLE."/"Humble" — still
match) OR the two titles share at least one significant (non-stopword) token.
Two genuinely different songs by the same artist share no content word, so they
get rejected; the real track is then correctly reported missing.
"""

from __future__ import annotations

import re

# Articles / prepositions / conjunctions only. Deliberately NOT pronouns
# ("you", "me", "i") — those carry meaning in song titles and dropping them
# could strip the only shared word from a real match. "the" MUST stay here:
# without it "Under The Bridge" and "Around the World" would falsely share it.
_TITLE_STOPWORDS = frozenset({
    "the", "a", "an", "of", "and", "or", "to", "in", "on",
    "for", "with", "at", "by", "from",
})

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Char ratio at/above which two titles are treated as the same regardless of
# shared words — covers typos, punctuation, casing, accents. Tuned so single-
# word typos ("Beleive"/"Believe" = 0.857) pass while the #769 false positives
# ("Dani California"/"Californication" = 0.667) do not.
_NEAR_IDENTICAL = 0.85


def _content_tokens(text: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall((text or "").lower()) if t not in _TITLE_STOPWORDS}


def titles_plausibly_same(
    title_a: str,
    title_b: str,
    char_similarity: float,
    *,
    near_identical: float = _NEAR_IDENTICAL,
) -> bool:
    """Whether two titles could be the same track, given their char similarity.

    ``title_a`` / ``title_b`` should already be normalised/cleaned (lowercased,
    brackets stripped) the same way the caller computed ``char_similarity``.

    Returns ``True`` when the pair is near-identical char-wise OR shares at
    least one significant (non-stopword) token. Returns ``False`` for two
    titles that are only moderately char-similar and share no content word —
    i.e. different songs the char ratio over-credited (#769)."""
    if char_similarity >= near_identical:
        return True
    ta = _content_tokens(title_a)
    tb = _content_tokens(title_b)
    # Word-overlap is only a reliable "different song" signal when at least one
    # side has 2+ content words — that's the #769 case where the char ratio
    # over-credits a shared substring ("Dani California"/"Californication") or
    # a stopword ("Under The Bridge"/"Around the World"). For single-word
    # titles there's no other word to share, so applying it would wrongly fail
    # legitimate stylized spellings ("Grey"/"Gray", "Tonite"/"Tonight",
    # "Thru"/"Through") that the char ratio rightly accepts. In that case defer
    # to the caller's existing char-similarity floor instead of force-failing.
    if max(len(ta), len(tb)) < 2 or not ta or not tb:
        return True
    return not ta.isdisjoint(tb)


_QUALIFIER_RE = re.compile(r"[\(\[]([^\)\]]*)[\)\]]")


def strip_redundant_context_qualifiers(title: str, *context_texts: str) -> str:
    """Remove parenthetical/bracket qualifiers that merely restate known context.

    A qualifier whose text appears (word-bounded) in one of ``context_texts``
    — typically the release's album title, or the other side of a comparison —
    is album context, not a version difference. #808: the wishlist held
    'Champagne Supernova (OurVinyl Sessions)' while the library track was the
    bare 'Champagne Supernova' on the album '… (OurVinyl Sessions)'; the
    qualifier restated the album, but the length-ratio penalty treated the
    pair as different songs and the cleanup never recognised the owned
    edition. Version markers that do NOT appear in any context ('(Live)',
    '(Remix)' on a studio album) are kept, so their mismatch penalty stands.
    """
    if not title:
        return title

    contexts = [c.casefold() for c in context_texts if c]
    if not contexts:
        return title

    def _drop(match: re.Match) -> str:
        inner = match.group(1).strip().casefold()
        if not inner:
            return " "
        pattern = r"\b" + re.escape(inner) + r"\b"
        for ctx in contexts:
            if re.search(pattern, ctx):
                return " "
        return match.group(0)

    out = _QUALIFIER_RE.sub(_drop, title)
    return re.sub(r"\s+", " ", out).strip()


def numeric_tokens_differ(title_a: str, title_b: str) -> bool:
    """True when the digit-bearing tokens of two titles differ — 'Vol.4' vs
    'Vol.4.5', 'Album' vs 'Album 2'. A numeric difference is a different
    release (volume / part / sequel), never a '(Deluxe)'-style suffix:
    string similarity ('Vol.4' vs 'Vol.4.5' = 0.97) and token-subset checks
    both wave these through, which hung volume 4.5's cover art on volume 4
    (Sokhi). Shared digits on both sides ('1989' vs '1989 (Deluxe)') are
    fine."""
    def _digit_tokens(text: str) -> frozenset:
        tokens = re.sub(r"[^a-z0-9]+", " ", (text or "").casefold()).split()
        return frozenset(t for t in tokens if any(c.isdigit() for c in t))

    return _digit_tokens(title_a) != _digit_tokens(title_b)


__all__ = [
    "titles_plausibly_same",
    "strip_redundant_context_qualifiers",
    "numeric_tokens_differ",
]
