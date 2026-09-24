"""Recognize a pasted streaming-source track/album link in the manual download
search (#813).

A user pastes e.g. ``https://tidal.com/track/434945950/u`` instead of typing a
query, to grab the exact version. We recognize sources that download by
track ID (Tidal, Qobuz, Deezer) — the manual search then resolves the link to
that track and runs the source's own search so the result is a normal,
downloadable candidate (no hand-built download encoding).

Deezer also accepts album URLs (``deezer.com/{locale}/album/<id>``): remix
singles are published as one-track albums, which is what users actually
paste. Album → track resolution is done by the caller after a network fetch.

Pure + import-safe: parsing only, no network.
"""

from __future__ import annotations

import re
from typing import Any, List, NamedTuple, Optional, Tuple
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


def inject_linked_track_first(
    tracks: List[Any], linked_result: Any, link_track_id: str
) -> List[Any]:
    """Put the EXACT linked track first.

    When ``linked_result`` is the track fetched directly by id, prepend it and
    drop any search duplicate of it — so an obscure track a text search never
    surfaced is still present and downloadable (#932). When it's None (the source
    can't fetch one), fall back to bubbling a matching search result. Pure."""
    if not link_track_id:
        return tracks
    target = str(link_track_id)
    if linked_result is not None:
        return [linked_result] + [t for t in tracks if linked_track_id(t) != target]
    return bubble_linked_track_first(tracks, target)

# host substring → download source id. Only ID-downloadable streaming sources.
_HOSTS = (
    ('tidal.com', 'tidal'),
    ('qobuz.com', 'qobuz'),
    ('deezer.com', 'deezer'),
)

# Path keywords this source will resolve. Tidal/Qobuz download by track id
# only; Deezer remix singles live on album pages, so album is accepted too.
_KINDS_BY_SOURCE = {
    'tidal': ('track',),
    'qobuz': ('track',),
    'deezer': ('track', 'album'),
}


class ParsedDownloadLink(NamedTuple):
    """A pasted streaming-source URL the manual search can resolve."""

    source: str
    entity_id: str
    kind: str  # 'track' | 'album'


def parse_download_link(raw: str) -> Optional[ParsedDownloadLink]:
    """Parse a pasted Tidal/Qobuz/Deezer URL into source + kind + id.

    Returns None when the input isn't a recognized download link (so the
    caller falls back to a normal text search). Handles the common shapes:
    ``tidal.com/track/<id>[/u]``, ``listen.tidal.com/track/<id>``,
    ``tidal.com/browse/track/<id>``, ``open.qobuz.com/track/<id>``,
    ``play.qobuz.com/track/<id>``, ``deezer.com/track/<id>``,
    ``deezer.com/{locale}/track/<id>``, ``deezer.com/{locale}/album/<id>``
    — with or without the scheme.
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

    allowed = _KINDS_BY_SOURCE.get(source, ('track',))
    segs = [s for s in (parsed.path or '').split('/') if s]
    for i, seg in enumerate(segs):
        kind = seg.lower()
        if kind in allowed and i + 1 < len(segs):
            m = re.match(r'(\d+)', segs[i + 1])   # id may carry a slug/suffix
            if m:
                return ParsedDownloadLink(source, m.group(1), kind)
    return None


def parse_download_track_link(raw: str) -> Optional[Tuple[str, str]]:
    """Parse a pasted Tidal/Qobuz/Deezer *track* URL into ``(source, track_id)``.

    Album URLs return None here (use :func:`parse_download_link` for those).
    Kept as a thin wrapper so existing track-only callers stay 2-tuple.
    """
    parsed = parse_download_link(raw)
    if parsed and parsed.kind == 'track':
        return (parsed.source, parsed.entity_id)
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
    - Deezer: public-API track dict (``title`` + ``artist`` / ``artists``,
      optional ``title_version`` for remixes).
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
    elif source == 'deezer':
        version = (raw.get('title_version') or '').strip()
        if version and version.lower() not in title.lower():
            title = f"{title} ({version})" if title else version
        artist = _first_artist_name(raw.get('artist') or raw.get('artists'))

    query = f"{artist} {title}".strip()
    return query or (title or None)


def _norm_title(value: Any) -> str:
    """Loose title compare so 'Raccoons (Remix)' matches 'raccoons remix'."""
    return re.sub(r'[^a-z0-9]+', '', str(value or '').lower())


def query_from_album_payload(raw: Any) -> Optional[str]:
    """``"artist title"`` from a Deezer (or similarly shaped) album payload."""
    if not isinstance(raw, dict):
        return None
    title = (raw.get('title') or '').strip()
    artist = _first_artist_name(raw.get('artist') or raw.get('artists'))
    query = f"{artist} {title}".strip()
    return query or (title or None)


def _album_track_list(raw: Any) -> List[dict]:
    """Tracks from a Deezer album payload (``tracks.data`` or a bare list)."""
    if not isinstance(raw, dict):
        return []
    nested = raw.get('tracks')
    if isinstance(nested, dict):
        items = nested.get('data') or []
    elif isinstance(nested, list):
        items = nested
    else:
        items = []
    return [t for t in items if isinstance(t, dict)]


def track_id_from_album_payload(raw: Any, prefer_title: str = '') -> Optional[str]:
    """Pick a track id from an album payload for a pasted album link.

    Single-track albums (typical Deezer remix single) → that track.
    Multi-track: prefer a title match against ``prefer_title`` (the failed
    download's track name), else the first track.
    """
    tracks = _album_track_list(raw)
    if not tracks:
        return None

    def _id_of(track: dict) -> Optional[str]:
        tid = track.get('id')
        return str(tid) if tid not in (None, '') else None

    if len(tracks) == 1:
        return _id_of(tracks[0])

    want = _norm_title(prefer_title)
    if want:
        for track in tracks:
            if _norm_title(track.get('title')) == want:
                return _id_of(track)
    return _id_of(tracks[0])
