"""Local relevance re-ranking for metadata-source search results.

Background
----------

Some metadata sources (Deezer notably) return search results in a
relevance order that puts karaoke covers, "originally performed by",
re-recorded versions, tribute compilations, and Vocal/Backing-Track
variants ABOVE the actual studio recording the user is looking for.
Their global popularity ordering means anything that appears across
many compilations outranks the canonical track. Issue #534 is the
canonical example: searching `Dirty White Boy` + `Foreigner` returned
five karaoke / cover variants before the real Foreigner studio cut.

This module is a provider-neutral helper. Given a list of typed
``Track`` results plus an expected title + artist, it re-ranks by
local heuristics that the source's own ranking ignores:

- Hard penalty for known cover/karaoke/tribute patterns (title OR
  album OR artist field). These rarely belong in import / match
  results when the user typed the original artist.
- Soft penalty for variant types (Live, Acoustic, Remix, Demo,
  Instrumental) UNLESS the user's expected title also contains the
  variant tag (so "Track (Live)" search matches Live recordings).
- Boost for exact artist match — the strongest signal that this is
  the canonical recording.
- Title similarity via SequenceMatcher on normalised strings (drop
  parentheticals + punctuation before comparison).
- Album-type weight: album > compilation > single (compilations are
  more likely to be tributes / "best of" repackages).

Pure-function design over the canonical ``Track`` dataclass —
no Deezer-specific assumptions, applies to iTunes / Spotify /
Hydrabase results equally well. Each scoring component is its own
small function so tests can pin them independently.

Usage
-----

>>> from core.metadata.relevance import rerank_tracks
>>> tracks = client.search_tracks(query)
>>> ranked = rerank_tracks(tracks, expected_title='Dirty White Boy', expected_artist='Foreigner')
>>> # ranked[0] is now the most relevant; karaoke variants drop to bottom
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import List, Optional, Sequence

from core.metadata.types import Track


# ---------------------------------------------------------------------------
# Pattern tables — public so tests can introspect, callers can extend
# ---------------------------------------------------------------------------


# Title / album / artist substrings that strongly indicate a cover,
# karaoke, tribute, or "originally performed by" compilation. Multiplier
# applied to the final score when matched. 0.05 effectively buries these
# unless nothing else matches.
COVER_KARAOKE_PATTERNS = (
    'karaoke',
    'originally performed by',
    'in the style of',
    'made famous by',
    'tribute',
    'vocal version',           # karaoke "vocal version" backing tracks
    'backing track',
    'cover version',
    're-recorded',             # artist re-recordings (Taylor's Version notwithstanding)
    're-record',
    'rerecorded',
    'cover by',
    'as performed by',
    'workout mix',             # gym-music compilations
    'study music',
    'music for',               # "Music for Studying", "Music for Sleep" etc
)

COVER_KARAOKE_PENALTY = 0.05  # Multiplicative; effectively bury


# Variant tags — softer penalty since the user MAY want them. Skipped
# when the user's expected_title also contains the same tag (so
# "Track Name (Live)" search matches the Live version cleanly).
VARIANT_TAG_PATTERNS = (
    'live',
    'acoustic',
    'demo',
    'instrumental',
    'remix',
    'edit',
    'extended',
    'radio edit',
    'club mix',
    'a cappella',
    'acapella',
)

VARIANT_TAG_PENALTY = 0.4


# Strong boost when the source's artist field exactly matches the
# user's expected artist (case-insensitive, normalised). The single
# strongest signal that this is the canonical recording.
EXACT_ARTIST_BOOST = 1.5


# Album-type weights. Compilations are more likely to be tributes /
# karaoke repackages; albums are most likely to be the canonical
# studio source.
ALBUM_TYPE_WEIGHT = {
    'album': 1.0,
    'single': 0.85,
    'ep': 0.85,
    'compilation': 0.7,
}
DEFAULT_ALBUM_TYPE_WEIGHT = 0.85


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


_PARENTHETICAL_RE = re.compile(r'[\(\[].*?[\)\]]')
_PUNCT_RE = re.compile(r'[^\w\s]')


def _normalise(text: str) -> str:
    """Lowercase, strip parentheticals + punctuation, collapse spaces.

    Used for similarity scoring AND for variant-tag detection (since
    we want to know if the user typed the variant tag inside their
    own search input)."""
    if not text:
        return ''
    t = text.lower().strip()
    t = _PARENTHETICAL_RE.sub('', t)
    t = _PUNCT_RE.sub('', t)
    return ' '.join(t.split())


def _contains_pattern(haystack: str, patterns: Sequence[str]) -> bool:
    """Case-insensitive substring match across patterns. Read raw
    `haystack` (NOT the parenthetical-stripped version) — patterns
    like "karaoke" most often live INSIDE the parentheticals on
    Deezer's titles."""
    if not haystack:
        return False
    lowered = haystack.lower()
    return any(p in lowered for p in patterns)


# ---------------------------------------------------------------------------
# Scoring components
# ---------------------------------------------------------------------------


def title_similarity(track: Track, expected_title: str) -> float:
    """Normalised SequenceMatcher ratio against the expected title."""
    if not expected_title:
        return 0.0
    return SequenceMatcher(
        None,
        _normalise(track.name),
        _normalise(expected_title),
    ).ratio()


def primary_artist(track: Track) -> str:
    """First entry from track.artists — that's the lead/primary
    credit. Empty when the track has no artist info."""
    if not track.artists:
        return ''
    first = track.artists[0]
    if isinstance(first, dict):
        # Some sources still surface raw dicts during migration; fall
        # back to .get() rather than assume the dataclass is fully
        # normalised.
        return str(first.get('name', '') or '')
    return str(first)


def artist_similarity(track: Track, expected_artist: str) -> float:
    """Normalised SequenceMatcher ratio against the expected artist."""
    if not expected_artist:
        return 0.0
    return SequenceMatcher(
        None,
        _normalise(primary_artist(track)),
        _normalise(expected_artist),
    ).ratio()


def has_exact_artist(track: Track, expected_artist: str) -> bool:
    """True when the primary artist matches expected_artist after
    normalisation. Strict equality on the normalised form (so
    "Foreigner" matches "Foreigner" but not "Foreigner Tribute Band")."""
    if not expected_artist:
        return False
    return _normalise(primary_artist(track)) == _normalise(expected_artist)


def has_cover_pattern(track: Track) -> bool:
    """Any cover/karaoke/tribute pattern in the track title, album
    title, or artist credits."""
    if _contains_pattern(track.name, COVER_KARAOKE_PATTERNS):
        return True
    if _contains_pattern(track.album, COVER_KARAOKE_PATTERNS):
        return True
    if _contains_pattern(primary_artist(track), COVER_KARAOKE_PATTERNS):
        return True
    return False


def has_variant_tag(track: Track) -> bool:
    """Track title contains a variant-version tag (Live, Acoustic,
    Remix, Demo, Instrumental, etc.). Album field is intentionally
    NOT checked — albums named "MTV Unplugged" shouldn't penalise
    every track on them."""
    return _contains_pattern(track.name, VARIANT_TAG_PATTERNS)


def album_type_weight(track: Track) -> float:
    """Weight from track.album_type. Compilations ranked lower since
    they're frequently tribute / karaoke repackages."""
    if not track.album_type:
        return DEFAULT_ALBUM_TYPE_WEIGHT
    return ALBUM_TYPE_WEIGHT.get(track.album_type.lower(), DEFAULT_ALBUM_TYPE_WEIGHT)


# ---------------------------------------------------------------------------
# Combined score
# ---------------------------------------------------------------------------


def score_track(
    track: Track,
    *,
    expected_title: str,
    expected_artist: str,
) -> float:
    """Combined relevance score for a single track. Higher = more
    relevant. Roughly 0.0 - 2.5 in practice (boosts can push above
    1.0; penalties can push below 0.1).

    Composition:

    1. Base = title_sim * 0.6 + artist_sim * 0.4
    2. Multiply by album_type_weight
    3. If exact artist match: multiply by EXACT_ARTIST_BOOST
    4. If cover/karaoke pattern: multiply by COVER_KARAOKE_PENALTY
       (effectively buries unless nothing else matched)
    5. If variant tag (Live, Remix, etc.) AND user did NOT type
       a variant tag in their input: multiply by VARIANT_TAG_PENALTY

    Each rule is its own component above so tests can pin them
    individually without standing up the full pipeline.
    """
    title_sim = title_similarity(track, expected_title)
    artist_sim = artist_similarity(track, expected_artist)
    score = title_sim * 0.6 + artist_sim * 0.4

    score *= album_type_weight(track)

    if has_exact_artist(track, expected_artist):
        score *= EXACT_ARTIST_BOOST

    if has_cover_pattern(track):
        score *= COVER_KARAOKE_PENALTY

    # Variant tag penalty — only when the user didn't ask for a
    # variant. Their input "Track (Live)" should rank Live versions
    # higher, not lower.
    user_wanted_variant = _contains_pattern(expected_title, VARIANT_TAG_PATTERNS)
    if has_variant_tag(track) and not user_wanted_variant:
        score *= VARIANT_TAG_PENALTY

    return score


def rerank_tracks(
    tracks: List[Track],
    *,
    expected_title: str,
    expected_artist: str,
) -> List[Track]:
    """Return a copy of ``tracks`` sorted by descending relevance
    score against the expected title + artist.

    Caller's input list is left untouched. Stable sort preserves the
    source's original ordering as a tiebreaker (which is the right
    fallback when two candidates score identically — the source's
    popularity signal is still useful as a tiebreak).

    No-op when both ``expected_title`` and ``expected_artist`` are
    empty (no signal to rank against — return input order)."""
    if not expected_title and not expected_artist:
        return list(tracks)
    scored = [
        (score_track(t, expected_title=expected_title, expected_artist=expected_artist), idx, t)
        for idx, t in enumerate(tracks)
    ]
    # Sort by score desc; idx asc as tiebreaker preserves stable order.
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [t for _score, _idx, t in scored]


def filter_and_rerank(
    tracks: List[Track],
    *,
    expected_title: str,
    expected_artist: str,
    min_score: Optional[float] = None,
) -> List[Track]:
    """Convenience: rerank then optionally drop everything below a
    score floor. Useful when callers want to hide low-confidence
    matches entirely instead of demoting them.

    Returns reranked-only list when ``min_score`` is None — same as
    ``rerank_tracks``."""
    ranked = rerank_tracks(
        tracks,
        expected_title=expected_title,
        expected_artist=expected_artist,
    )
    if min_score is None:
        return ranked
    return [
        t for t in ranked
        if score_track(t, expected_title=expected_title, expected_artist=expected_artist) >= min_score
    ]
