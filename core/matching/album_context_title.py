"""Strip redundant album-context suffixes from track titles.

Issue #589 — MTV Unplugged albums (and similar live-concert / session
releases) have source-side track titles like ``"Shy Away (MTV Unplugged
Live)"`` while the local DB stored title is just ``"Shy Away"``. The
album-scoped library check at ``core/downloads/master.py`` compares
the two with raw string similarity, the length asymmetry tanks the
score, and tracks the user already owns get marked missing.

This helper normalizes a track title by stripping the parenthetical
or dash suffix when its tokens are fully subsumed by the album
context: at least one version marker (live / unplugged / acoustic /
session / etc) is present in BOTH the suffix AND the album title, and
every other suffix token is either a known marker, a year, a
connecting noise word, or a word that appears in the album title.

Pure function. No I/O. Tests at the function boundary.
"""

from __future__ import annotations

import re
from typing import Iterable, Tuple

# Version-marker keywords. When the album title contains any of these,
# stripping is enabled. Singular forms — plurals get matched separately
# via stem expansion below.
_VERSION_MARKERS = (
    'live',
    'unplugged',
    'acoustic',
    'session',
    'concert',
    'tour',
)

# Markers that are implied "live" context — when the album mentions any
# of these, a bare ``live`` token in the suffix counts as album context
# even if the album title doesn't literally say "live". MTV Unplugged
# albums are live recordings; same for "in concert" / "tour" releases.
_IMPLIES_LIVE = ('unplugged', 'concert', 'tour', 'session')

# Connecting / filler words that don't carry meaning by themselves.
_NOISE_TOKENS = frozenset({
    'version', 'edition', 'recording', 'recordings', 'remaster',
    'remastered', 'mix',
    'the', 'a', 'an', 'from', 'at', 'in', 'on', 'for', 'of',
    'and', 'or', 'with', 'by',
    'vol', 'pt', 'part', 'no',
})

_SUFFIX_PATTERNS: Tuple[re.Pattern, ...] = (
    re.compile(r'\s*\(([^()]+)\)\s*$'),
    re.compile(r'\s*\[([^\[\]]+)\]\s*$'),
    re.compile(r'\s+-\s+(.+?)\s*$'),
)

_YEAR_RE = re.compile(r'^(?:19|20)\d{2}$')
_TOKEN_RE = re.compile(r'\w+')


def _normalize(text: str) -> str:
    return (text or '').lower().strip()


def _tokenize(text: str) -> set:
    return set(_TOKEN_RE.findall(_normalize(text)))


def _expand_marker_set(markers: Iterable[str]) -> set:
    """Expand each marker into its singular + plural forms."""
    out = set()
    for marker in markers:
        out.add(marker)
        if not marker.endswith('s'):
            out.add(marker + 's')
    return out


_EXPANDED_MARKERS = _expand_marker_set(_VERSION_MARKERS)


def album_context_markers(album_title: str) -> Tuple[str, ...]:
    """Return the version markers present in the album title (singular form)."""
    if not album_title:
        return ()
    album_tokens = _tokenize(album_title)
    found = []
    for marker in _VERSION_MARKERS:
        if marker in album_tokens or (marker + 's') in album_tokens:
            found.append(marker)
    return tuple(found)


def _suffix_is_album_redundant(
    inner: str,
    album_tokens: set,
    album_markers: Tuple[str, ...],
) -> bool:
    """Decide whether a suffix's tokens are all subsumed by album context.

    Three requirements:
      1. The suffix contains at least one version-marker token. Stops
         a generic "feat. X" suffix from being stripped because the
         album happened to be live.
      2. The shared marker matches one the album implies — either
         literally in the album title, OR via the implied-live set
         (unplugged/concert/tour albums imply "live").
      3. Every other suffix token is either a marker, a year, a
         tolerated noise word, or a word that appears in the album
         title. If any token falls outside, the suffix carries
         info beyond album context (featured artist, different
         version, etc) — keep it on.
    """
    if not inner:
        return False

    suffix_tokens = _tokenize(inner)
    if not suffix_tokens:
        return False

    # Markers the album effectively implies (literal + implied-live).
    implied_markers = set(album_markers)
    if any(m in implied_markers for m in _IMPLIES_LIVE):
        implied_markers.add('live')

    suffix_markers = suffix_tokens & _EXPANDED_MARKERS
    if not suffix_markers:
        return False

    # At least one marker must overlap with album-implied set. Plural
    # tolerance — strip trailing 's' for the comparison.
    def _stem(tok: str) -> str:
        return tok[:-1] if tok.endswith('s') and len(tok) > 1 else tok

    if not any(_stem(t) in implied_markers for t in suffix_markers):
        return False

    # Every remaining suffix token must be subsumed.
    for tok in suffix_tokens:
        if tok in _EXPANDED_MARKERS:
            continue
        if _YEAR_RE.match(tok):
            continue
        if tok in _NOISE_TOKENS:
            continue
        if tok in album_tokens:
            continue
        return False

    return True


def strip_redundant_album_suffix(track_title: str, album_title: str) -> str:
    """Strip a trailing parenthetical/bracket/dash suffix from `track_title`
    when the suffix duplicates context already implied by `album_title`.

    Examples:
      - ("Shy Away (MTV Unplugged Live)", "MTV Unplugged") → "Shy Away"
      - ("Only If For A Night (MTV Unplugged, 2012 / Live)",
         "Ceremonials (Live At MTV Unplugged)") → "Only If For A Night"
      - ("In My Feelings (Instrumental)", "Scorpion")
         → unchanged (instrumental NOT implied by studio album)
      - ("Hello (Live - feat. Other)", "Live At Wembley")
         → unchanged (suffix carries featured-artist beyond album context)
      - ("Shy Away", "MTV Unplugged") → unchanged (no suffix)

    Pure function — never raises, returns the input unchanged on any
    edge / unexpected input.
    """
    if not track_title:
        return track_title or ''
    album_markers = album_context_markers(album_title)
    if not album_markers:
        return track_title

    album_tokens = _tokenize(album_title)
    stripped = track_title

    # Stacked suffixes ("Track (MTV Unplugged) [Live]") — peel one at a
    # time. Bound the loop defensively.
    for _ in range(4):
        peeled = None
        for pattern in _SUFFIX_PATTERNS:
            m = pattern.search(stripped)
            if not m:
                continue
            inner = m.group(1)
            if _suffix_is_album_redundant(inner, album_tokens, album_markers):
                peeled = stripped[: m.start()].rstrip()
                break
        if peeled is None:
            return stripped
        stripped = peeled
    return stripped
