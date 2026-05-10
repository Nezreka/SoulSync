"""Pure-function artist-name comparison with alias awareness.

Issue #442 — cross-script artist quarantines
-----------------------------------------------------

A file tagged with one spelling of an artist's name (e.g. the
Japanese kanji `澤野弘之`) was being quarantined when SoulSync's
expected-artist metadata used the romanized spelling
(`Hiroyuki Sawano`). Raw similarity comparison scores 0% across
scripts even though MusicBrainz already knows both names belong to
the same artist (its alias list).

This module is the shared resolution helper. Given an expected
artist name, an actual artist name, and an iterable of known
aliases, it returns whether they should be treated as the same
artist + the highest similarity score across the candidate set.

Pure function design:
- No I/O, no DB access, no network
- Caller supplies aliases (looked up from library DB or live MB)
- Caller supplies normalize + similarity functions to keep the
  helper provider-neutral (the verifier and the matching engine
  use slightly different normalizers — let each pass its own)
- Returns ``(matched: bool, score: float)`` so callers can log
  the score they made the decision on

Backward compat: when ``aliases`` is empty (or the looking-up
caller hasn't been wired yet), the helper degrades to a plain
direct similarity comparison — identical to the pre-fix behaviour.
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Callable, Iterable, Optional, Tuple


# Default threshold matches the existing ARTIST_MATCH_THRESHOLD in
# core/acoustid_verification.py. Callers can override but the helper
# defaults are tuned to preserve current verifier behaviour.
DEFAULT_ARTIST_MATCH_THRESHOLD = 0.6


def _default_normalize(text: str) -> str:
    """Lowercase + strip whitespace. Minimal — caller's normaliser
    almost always replaces this with something stricter (parenthetical
    stripping, punctuation removal). Used only when the caller
    doesn't pass a custom one."""
    if not text:
        return ''
    return str(text).strip().lower()


def _default_similarity(a: str, b: str) -> float:
    """SequenceMatcher ratio after the default normaliser. Matches
    the verifier's existing ``_similarity`` semantics for the no-
    custom-callable path."""
    na = _default_normalize(a)
    nb = _default_normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


def _coerce_aliases(aliases: Optional[Iterable[str]]) -> Tuple[str, ...]:
    """Normalise the aliases input to a tuple of clean strings.

    Accepts ``None``, empty iterables, lists, tuples, sets. Drops
    None / empty / non-string entries silently — callers feeding us
    raw MusicBrainz response dicts shouldn't have to clean first.
    """
    if not aliases:
        return ()
    cleaned = []
    for value in aliases:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            cleaned.append(text)
    return tuple(cleaned)


def artist_names_match(
    expected: str,
    actual: str,
    *,
    aliases: Optional[Iterable[str]] = None,
    threshold: float = DEFAULT_ARTIST_MATCH_THRESHOLD,
    similarity: Optional[Callable[[str, str], float]] = None,
) -> Tuple[bool, float]:
    """Compare ``expected`` and ``actual`` artist names with alias
    awareness.

    Args:
        expected: The artist name the caller expected (typically from
            metadata-source data — Spotify / iTunes / Deezer track
            payload).
        actual: The artist name the caller observed (typically from
            an AcoustID recording or a downloaded file's tag).
        aliases: Iterable of known alternate spellings for ``expected``.
            Each one gets compared against ``actual``; the best score
            wins. Empty or omitted → plain direct comparison
            (backward-compat with pre-fix behaviour).
        threshold: Score at or above which we consider the names a
            match. Defaults to 0.6 to match the verifier's existing
            ``ARTIST_MATCH_THRESHOLD``.
        similarity: Optional caller-supplied similarity function
            ``(a, b) -> float in [0, 1]``. Lets the verifier pass its
            stricter normaliser (parenthetical stripping etc.) without
            this module having to know about it. Defaults to a
            lowercase + SequenceMatcher comparison.

    Returns:
        ``(matched, best_score)`` where ``matched`` is True iff the
        best score across (actual, *aliases) ≥ threshold and
        ``best_score`` is that maximum. ``best_score`` is informative
        for callers that want to log "matched at 0.83" or similar.
    """
    sim = similarity or _default_similarity

    # Direct compare first — both for the fast path and so the
    # returned score reflects the actual-vs-expected baseline (callers
    # may want it for logging even when an alias is the actual winner).
    direct_score = sim(expected, actual)
    best_score = direct_score
    if direct_score >= threshold:
        return True, direct_score

    # Alias compare: each alias is a known alternate spelling of the
    # EXPECTED artist; match it against the ACTUAL name we observed.
    # Highest score wins.
    for alias in _coerce_aliases(aliases):
        score = sim(alias, actual)
        if score > best_score:
            best_score = score
        if score >= threshold:
            return True, score

    return False, best_score


def best_alias_match(
    expected: str,
    actual: str,
    aliases: Optional[Iterable[str]] = None,
    *,
    similarity: Optional[Callable[[str, str], float]] = None,
) -> Tuple[Optional[str], float]:
    """Return the alias that best matched ``actual`` (or None for the
    direct expected-vs-actual comparison) and its score.

    Companion to ``artist_names_match`` for callers that want to
    surface which alias triggered the match (debug logging, UI
    explanations). Doesn't apply a threshold — purely informative.

    Returns:
        ``(winner, score)`` where ``winner`` is the alias string when
        an alias outscored the direct comparison, ``None`` when the
        direct comparison won (or both tied at zero).
    """
    sim = similarity or _default_similarity
    direct_score = sim(expected, actual)
    winner: Optional[str] = None
    best = direct_score

    for alias in _coerce_aliases(aliases):
        score = sim(alias, actual)
        if score > best:
            best = score
            winner = alias

    return winner, best
