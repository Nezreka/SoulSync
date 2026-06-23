"""Resolve a playlist track's MusicBrainz *recording* MBID, cheapest source first.

A ListenBrainz playlist export needs each track's recording MBID (``jspf_export``). A
SoulSync track can supply it from several places, in increasing cost:

1. **resolution cache** — a prior (artist,title)->mbid result (persistent; reused across
   playlists and runs, so the same song never costs twice).
2. **library DB** — ``tracks.musicbrainz_recording_id`` (set by the MusicBrainz
   enrichment worker).
3. **file tags** — ``MUSICBRAINZ_RECORDING_ID`` written into the audio file on import
   post-processing (catches tracks enriched at import but not via the worker).
4. **MusicBrainz lookup** — a live ``match_recording(artist, title)`` (rate-limited
   ~1 req/s; the slow tail — only hit when 1–3 miss).

This module is the **pure waterfall**: the caller passes ordered ``(label, fn)`` sources,
each ``fn(artist, title) -> mbid | None``, and ``resolve_recording_mbid`` returns the
first valid hit plus its label (for the live status / stats). The actual I/O (DB query,
mutagen read, MB request, cache read/write) lives in the export job that wires the real
sources — so this stays trivially unit-testable and short-circuits correctly.
"""

from __future__ import annotations

import re
from typing import Any, Callable, List, Optional, Tuple

# Source labels (also used in the live-status breakdown).
SRC_CACHE = "cache"
SRC_DB = "db"
SRC_FILE = "file"
SRC_MUSICBRAINZ = "musicbrainz"
SRC_NONE = None

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)

Source = Tuple[str, Callable[[str, str], Optional[str]]]


def _valid(mbid: Any) -> Optional[str]:
    """Return the trimmed MBID if it's a well-formed UUID, else None."""
    if not isinstance(mbid, str):
        return None
    m = mbid.strip()
    return m if _UUID_RE.match(m) else None


def normalize_key(artist: Any, title: Any) -> str:
    """Stable cache key for an (artist, title) pair — lower, punctuation-stripped,
    whitespace-collapsed — so trivial variations share a cache entry."""
    def _n(v: Any) -> str:
        s = re.sub(r"[^\w\s]", "", str(v or "").lower())
        return re.sub(r"\s+", " ", s).strip()
    return f"{_n(artist)}␟{_n(title)}"


def resolve_recording_mbid(
    artist: str,
    title: str,
    sources: List[Source],
) -> Tuple[Optional[str], Optional[str]]:
    """Walk ``sources`` in order; return ``(mbid, label)`` of the first that yields a
    valid recording MBID, or ``(None, None)`` when every source misses.

    Each source is ``(label, fn)`` and ``fn(artist, title)`` returns an MBID or None. A
    source that raises is treated as a miss (never aborts the waterfall) — so one flaky
    lookup (e.g. a MusicBrainz timeout) can't fail the whole export. Short-circuits: a
    later/expensive source isn't called once an earlier one hits.
    """
    for label, fn in sources or []:
        try:
            mbid = _valid(fn(artist, title))
        except Exception:
            mbid = None
        if mbid:
            return (mbid, label)
    return (None, None)


__all__ = [
    "resolve_recording_mbid",
    "normalize_key",
    "SRC_CACHE",
    "SRC_DB",
    "SRC_FILE",
    "SRC_MUSICBRAINZ",
]
