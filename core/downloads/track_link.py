"""Recognize a pasted streaming-source track link in the manual download
search (#813).

A user pastes e.g. ``https://tidal.com/track/434945950/u`` instead of typing a
query, to grab the exact version. We only recognize sources that download by
track ID (Tidal, Qobuz) — the manual search then resolves the link to that
track and runs the source's own search so the result is a normal, downloadable
candidate (no hand-built download encoding).

Pure + import-safe: parsing only, no network.
"""

from __future__ import annotations

import re
from typing import Any, List, Optional, Tuple
from urllib.parse import urlparse


def linked_track_id(track: Any) -> str:
    """The source track id stamped on a search result, read from
    ``_source_metadata['track_id']`` — the field every ID-downloadable source
    (Tidal, Qobuz) records. Empty string when absent. ``TrackResult`` has no
    top-level ``id``, so callers must NOT use ``getattr(t, 'id')`` (that always
    missed and left the pasted-link bubble a silent no-op — #932)."""
    meta = getattr(track, '_source_metadata', None)
    if not isinstance(meta, dict):
        return ''
    return str(meta.get('track_id') or '')


def bubble_linked_track_first(tracks: List[Any], link_track_id: str) -> List[Any]:
    """Float the result whose source id matches a pasted link to the top so the
    user sees the EXACT track they linked, not a fuzzy text-search lookalike
    (#813/#932). Stable + a graceful no-op when no result carries the id."""
    if not link_track_id or not tracks:
        return tracks
    target = str(link_track_id)
    return sorted(tracks, key=lambda t: linked_track_id(t) != target)

# host substring → download source id. Only ID-downloadable streaming sources.
_HOSTS = (
    ('tidal.com', 'tidal'),
    ('qobuz.com', 'qobuz'),
)


def parse_download_track_link(raw: str) -> Optional[Tuple[str, str]]:
    """Parse a pasted Tidal/Qobuz track URL into ``(source, track_id)``.

    Returns None when the input isn't a recognized track link (so the caller
    falls back to a normal text search). Handles the common URL shapes:
    ``tidal.com/track/<id>[/u]``, ``listen.tidal.com/track/<id>``,
    ``tidal.com/browse/track/<id>``, ``open.qobuz.com/track/<id>``,
    ``play.qobuz.com/track/<id>`` — with or without the scheme.
    """
    raw = (raw or '').strip()
    if not raw:
        return None

    lowered = raw.lower()
    if '://' not in raw and not any(h in lowered for h, _ in _HOSTS):
        return None  # not even a URL we care about

    url = raw if '://' in raw else f'https://{raw}'
    parsed = urlparse(url)
    host = (parsed.netloc or '').lower()

    source = next((sid for h, sid in _HOSTS if h in host), None)
    if not source:
        return None

    segs = [s for s in (parsed.path or '').split('/') if s]
    for i, seg in enumerate(segs):
        if seg.lower() == 'track' and i + 1 < len(segs):
            m = re.match(r'(\d+)', segs[i + 1])   # id may carry a slug/suffix
            if m:
                return (source, m.group(1))
    return None


def _first_artist_name(value: Any) -> str:
    """First artist name from a list of {'name': ...}/strings, or a single
    {'name': ...}/string."""
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, dict):
        return str(value.get('name') or '')
    return str(value or '')


def query_from_track_payload(source: str, raw: Any) -> Optional[str]:
    """Build a clean ``"artist title"`` search query from a source ``get_track``
    payload — pure, so the per-source shape parsing is unit-testable without a
    live client.

    - Tidal: attributes dict (``title`` + optional ``version`` + maybe
      ``artists``/``artist``). The version is appended so a remix link searches
      for the remix.
    - Qobuz: track dict (``title`` + ``performer``/``album.artist``).
    """
    if not isinstance(raw, dict):
        return None
    title = (raw.get('title') or '').strip()
    artist = ''

    if source == 'tidal':
        version = (raw.get('version') or '').strip()
        if version and version.lower() not in title.lower():
            title = f"{title} ({version})" if title else version
        artist = _first_artist_name(raw.get('artists') or raw.get('artist'))
    elif source == 'qobuz':
        artist = _first_artist_name(raw.get('performer'))
        if not artist:
            album = raw.get('album') if isinstance(raw.get('album'), dict) else {}
            artist = _first_artist_name(album.get('artist'))

    query = f"{artist} {title}".strip()
    return query or (title or None)
