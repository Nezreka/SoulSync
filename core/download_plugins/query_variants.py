"""Progressive query relaxation for indexer-style search back-ends.

dd28-07: Prowlarr forwards a query to Newznab indexers, which AND every token
together and match against release *filenames*.  A catalog title like::

    Drenchill Freed from Desire (feat. Indiiana) - DNF Extended Remix

therefore matches nothing, even though the indexer holds
``Drenchill-Freed_From_Desire-WEB-2019-FLAC``: "feat", "Indiiana", "DNF",
"Extended" and "Remix" are all required, and none appear in the release name.
Long titles fail the same way — every extra word is another AND term.

Tidal already solved this for itself with a shortened-query ladder
(:meth:`core.tidal_download_client.TidalClient._generate_shortened_queries`),
but the usenet/torrent plugins sent ``buildSearchQuery``'s output verbatim with
no length handling and no bracket fallback anywhere in the chain.

dd28-52: that Tidal ladder strips bracket groups with ``[^\\)\\]]*``, which stops
at the first closing bracket and so mangles genuinely nested groups like
``Song (Live (2015))``.  The strippers here are balance-aware, and Tidal now
uses them too, so both paths share one correct implementation.
"""

from __future__ import annotations

import re
from typing import List

_OPENERS = {"(": ")", "[": "]"}
_CLOSERS = {")", "]"}

# Credit words that a release name almost never carries but a metadata title
# often does. Dropping them widens a query without changing which release it
# means. "with" is the aggressive one — it appears in real titles ("Sleeping
# With Sirens", "Dancing With Myself") as well as in Spotify's "(with Artist)"
# credit form, so it is NOT a pure-metadata word. It stays anyway because
# dropping a token can only WIDEN an indexer's AND-search: the relaxed variant
# is a superset of the exact query, which already ran first and won if it had
# hits. Anything added here needs that same argument — "appears only in
# metadata" is not the bar, and was never true of the whole set.
_NOISE_TOKENS = frozenset({
    "feat", "feat.", "ft", "ft.", "featuring", "with",
    "prod", "prod.", "produced",
})

MAX_QUERY_LENGTH = 100


def _collapse_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def strip_bracket_groups(text: str) -> str:
    """Remove every *balanced* ``(...)``/``[...]`` group, nesting included.

    Everything after an unbalanced opener is KEPT — a stray "(" in a title must
    not swallow the rest of the query. The stray opener itself becomes a space
    (it would only confuse the indexer); an unbalanced closer is left as-is,
    since it can't be told apart from a title's own punctuation.
    """
    source = str(text or "")
    drop = [False] * len(source)
    # (opening index, the closer that would balance it) — matching on the
    # closer is what makes "balanced" true. Popping on ANY closer paired the
    # '(' of "Song (Live] Remix" with the ']' and dropped a group that never
    # existed, returning "Song Remix".
    stack: List[tuple] = []
    for index, char in enumerate(source):
        if char in _OPENERS:
            stack.append((index, _OPENERS[char]))
        elif char in _CLOSERS and stack and stack[-1][1] == char:
            start, _closer = stack.pop()
            for position in range(start, index + 1):
                drop[position] = True
    kept = "".join(
        char for index, char in enumerate(source) if not drop[index]
    )
    return _collapse_whitespace(kept.replace("(", " ").replace("[", " "))


def strip_trailing_bracket_group(text: str) -> str:
    """Remove only a balanced bracket group that ends the string.

    Unlike :func:`strip_bracket_groups` this keeps interior groups, so
    ``"Album (Deluxe) - Track (Live)"`` loses just the ``(Live)``.
    """
    source = _collapse_whitespace(text)
    if not source or source[-1] not in _CLOSERS:
        return source
    closer = source[-1]
    opener = "(" if closer == ")" else "["
    depth = 0
    for index in range(len(source) - 1, -1, -1):
        char = source[index]
        if char == closer:
            depth += 1
        elif char == opener:
            depth -= 1
            if depth == 0:
                return _collapse_whitespace(source[:index])
    return source


def _drop_noise_tokens(text: str) -> str:
    kept = [
        token for token in _collapse_whitespace(text).split(" ")
        if token.lower().strip(",") not in _NOISE_TOKENS
    ]
    return _collapse_whitespace(" ".join(kept))


def _truncate_to_words(text: str, limit: int = MAX_QUERY_LENGTH) -> str:
    source = _collapse_whitespace(text)
    if len(source) <= limit:
        return source
    words = source.split(" ")
    out: List[str] = []
    for word in words:
        candidate = " ".join(out + [word])
        if out and len(candidate) > limit:
            break
        out.append(word)
    return " ".join(out)


def indexer_query_variants(query: str, max_variants: int = 5) -> List[str]:
    """Ordered query ladder, most specific first, for an indexer search.

    The first entry is always the original query — a normalization must never
    cost a hit that the exact query would have found.  Each following entry
    relaxes one more constraint.  Duplicates and empties are dropped, so a
    short plain title yields a single-element list and behaves exactly as
    before.
    """
    original = _collapse_whitespace(query)
    if not original:
        return []

    variants: List[str] = []
    seen: set[str] = set()

    def _add(candidate: str) -> None:
        candidate = _collapse_whitespace(candidate)
        if not candidate or candidate.lower() in seen:
            return
        variants.append(candidate)
        seen.add(candidate.lower())

    _add(original)
    _add(_drop_noise_tokens(original))
    _add(strip_trailing_bracket_group(original))
    _add(strip_bracket_groups(original))
    _add(_drop_noise_tokens(strip_bracket_groups(original)))
    if len(original) > MAX_QUERY_LENGTH:
        _add(_truncate_to_words(strip_bracket_groups(original)))
        _add(_truncate_to_words(original))

    return variants[: max(1, int(max_variants))]


__all__ = [
    "MAX_QUERY_LENGTH",
    "indexer_query_variants",
    "strip_bracket_groups",
    "strip_trailing_bracket_group",
]
