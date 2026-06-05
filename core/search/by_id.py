"""Resolve a pasted metadata link or ID to a single album/track result.

This backs the Search page's "Link / ID" mode (#775): instead of a fuzzy
name search, the user pastes a provider URL (or a bare ID) and we look the
entity up *directly* on the owning source — no scoring, no guessing.

Design notes
------------
- **Source-agnostic input.** A full URL carries its source in the domain
  (``open.spotify.com`` → spotify, ``musicbrainz.org`` → musicbrainz, …)
  and its kind in the path (``/album/`` vs ``/track/``), so it resolves to
  exactly one lookup. A *bare* ID is disambiguated by format where we can
  (UUID → MusicBrainz, 22-char base62 → Spotify) and otherwise fanned out
  across the numeric-ID sources (Deezer, iTunes); the first source that
  returns a hit wins. Paste the full link to remove that ambiguity.

- **Reuses existing per-source get-by-id.** Spotify/iTunes/MusicBrainz all
  expose ``get_album``; Deezer exposes ``get_album_metadata``; all four
  expose ``get_track_details``. Those already normalize to a common
  "Spotify-shaped" dict, so a single adapter projects them onto the same
  card shape the enhanced-search dropdown renders (see
  ``core/search/sources.py``).

- **Purely additive.** Nothing here mutates existing search behavior; the
  route layer calls :func:`resolve_identifier` only for the new mode.

The module is import-safe and side-effect free: clients are resolved through
an injected ``client_resolver`` (defaulting to the orchestrator's
``resolve_client``) so the seam is unit-testable with fakes.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable, NamedTuple, Optional
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)

# Sources we can resolve a link/ID against. These are exactly the metadata
# providers whose public links a user would paste AND whose get-by-id returns
# the common Spotify-shaped dict. Streaming download backends (Tidal/Qobuz)
# return raw API shapes and aren't metadata-link sources, so they're omitted.
SUPPORTED_SOURCES = ('spotify', 'itunes', 'musicbrainz', 'deezer')

_UUID_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I
)
_SPOTIFY_ID_RE = re.compile(r'^[0-9A-Za-z]{22}$')
_NUMERIC_RE = re.compile(r'^\d+$')

# Numeric-ID sources, tried in this order when a bare number is pasted.
_NUMERIC_SOURCES = ('deezer', 'itunes')

# Domains we recognize — used to detect a pasted URL even when the user
# omitted the scheme (e.g. "open.spotify.com/album/…").
_KNOWN_HOSTS = (
    'open.spotify.com', 'music.apple.com', 'itunes.apple.com',
    'musicbrainz.org', 'deezer.com',
)


class LookupTarget(NamedTuple):
    """One (source, kind, id) lookup to attempt.

    ``kind`` is ``'album'`` or ``'track'`` when the input pins it (a URL path
    or URI type), or ``None`` when unknown — the resolver then tries album
    first, then track.
    """

    source: str
    kind: Optional[str]
    id: str


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

def _kind_from_keyword(keyword: str) -> Optional[str]:
    """Map a URL/URI path keyword to a lookup kind."""
    if keyword in ('album', 'release', 'release-group'):
        return 'album'
    if keyword in ('track', 'recording', 'song'):
        return 'track'
    return None


def _parse_spotify_uri(raw: str) -> Optional[LookupTarget]:
    """``spotify:album:ID`` / ``spotify:track:ID``."""
    parts = raw.split(':')
    if len(parts) >= 3 and parts[0] == 'spotify':
        kind = _kind_from_keyword(parts[1])
        if kind:
            return LookupTarget('spotify', kind, parts[-1])
    return None


def _parse_url(raw: str) -> list[LookupTarget]:
    """Parse a provider URL into lookup targets (empty if unrecognized)."""
    parsed = urlparse(raw)
    host = (parsed.netloc or '').lower()
    segs = [s for s in (parsed.path or '').split('/') if s]

    def _by_keyword(source: str) -> list[LookupTarget]:
        """Find the first album/track-style keyword and take the next seg as id."""
        for i, seg in enumerate(segs):
            kind = _kind_from_keyword(seg.lower())
            if kind and i + 1 < len(segs):
                return [LookupTarget(source, kind, segs[i + 1])]
        return []

    if 'open.spotify.com' in host:
        return _by_keyword('spotify')

    if 'music.apple.com' in host or 'itunes.apple.com' in host:
        # Apple track links are an album URL with ?i=<track-id>; otherwise the
        # trailing path segment is the album/song id.
        qs = parse_qs(parsed.query or '')
        track_id = (qs.get('i') or [None])[0]
        if track_id:
            return [LookupTarget('itunes', 'track', track_id)]
        for i, seg in enumerate(segs):
            kind = _kind_from_keyword(seg.lower())
            if kind and i + 1 < len(segs):
                # Apple's id is the last segment, not necessarily i+1.
                return [LookupTarget('itunes', kind, segs[-1])]
        return []

    if 'musicbrainz.org' in host:
        return _by_keyword('musicbrainz')

    if 'deezer.com' in host:
        # link.deezer.com short links can't be resolved without a network
        # redirect; only handle canonical /album/ /track/ paths.
        return _by_keyword('deezer')

    return []


def parse_metadata_identifier(
    raw: str, preferred_source: Optional[str] = None
) -> list[LookupTarget]:
    """Parse a pasted link/ID into an ordered list of lookup targets.

    Returns the candidates to try in order; the first that resolves wins.
    ``preferred_source`` (the user's active source) only reorders the
    fan-out for ambiguous *bare numeric* IDs.
    """
    raw = (raw or '').strip()
    if not raw:
        return []

    if raw.lower().startswith('spotify:'):
        uri = _parse_spotify_uri(raw)
        return [uri] if uri else []

    lowered = raw.lower()
    looks_like_url = (
        '://' in raw
        or lowered.startswith('www.')
        or any(host in lowered for host in _KNOWN_HOSTS)
    )
    if looks_like_url:
        url = raw if '://' in raw else f'https://{raw}'
        targets = _parse_url(url)
        if targets:
            return targets
        # Fall through: maybe a bare id slipped in with a stray scheme.

    # Bare identifier — disambiguate by format.
    if _UUID_RE.match(raw):
        # MusicBrainz MBID: could be release / release-group (album) or
        # recording (track). Try album first, then track.
        return [LookupTarget('musicbrainz', 'album', raw),
                LookupTarget('musicbrainz', 'track', raw)]

    if _NUMERIC_RE.match(raw):
        # Numeric IDs collide across Deezer/iTunes — fan out, first hit wins.
        order = list(_NUMERIC_SOURCES)
        if preferred_source in order:
            order.remove(preferred_source)
            order.insert(0, preferred_source)
        targets: list[LookupTarget] = []
        for src in order:
            targets.append(LookupTarget(src, 'album', raw))
            targets.append(LookupTarget(src, 'track', raw))
        return targets

    if _SPOTIFY_ID_RE.match(raw):
        return [LookupTarget('spotify', 'album', raw),
                LookupTarget('spotify', 'track', raw)]

    return []


# --------------------------------------------------------------------------
# Shaping — project a get-by-id dict onto the dropdown's card shape
# --------------------------------------------------------------------------

def _join_artists(artists: Any) -> str:
    """Normalize an artists field (list of str OR list of {'name': ...}) to a
    display string."""
    names: list[str] = []
    for a in artists or []:
        if isinstance(a, dict):
            n = a.get('name')
        else:
            n = a
        if n:
            names.append(str(n))
    return ', '.join(names) if names else 'Unknown Artist'


def _first_image(d: dict) -> str:
    """Pull the first image URL from a Spotify-shaped images list."""
    imgs = d.get('images') or []
    if imgs and isinstance(imgs[0], dict):
        return imgs[0].get('url', '') or ''
    return d.get('image_url', '') or ''


def album_dict_to_card(d: dict) -> dict:
    """Project a get_album / get_album_metadata dict onto the album card shape
    (mirrors ``core/search/sources.py`` ``search_kind('albums')``)."""
    return {
        'id': str(d.get('id', '')),
        'name': d.get('name', ''),
        'artist': _join_artists(d.get('artists')),
        'image_url': _first_image(d),
        'release_date': d.get('release_date', ''),
        'total_tracks': d.get('total_tracks', 0),
        'album_type': d.get('album_type', 'album'),
        'format': d.get('format'),
        'country': d.get('country'),
        'status': d.get('status'),
        'label': d.get('label'),
        'disambiguation': d.get('disambiguation'),
        'release_group_id': d.get('release_group_id'),
        'external_urls': d.get('external_urls') or {},
    }


def track_dict_to_card(d: dict) -> dict:
    """Project a get_track_details dict onto the track card shape (mirrors
    ``core/search/sources.py`` ``search_kind('tracks')``)."""
    album = d.get('album')
    if isinstance(album, dict):
        album_name = album.get('name', '')
        image_url = _first_image(album)
        release_date = album.get('release_date', '')
    else:
        album_name = album or ''
        image_url = _first_image(d)
        release_date = d.get('release_date', '')
    return {
        'id': str(d.get('id', '')),
        'name': d.get('name', ''),
        'artist': _join_artists(d.get('artists')),
        'album': album_name,
        'duration_ms': d.get('duration_ms', 0),
        'image_url': image_url or _first_image(d),
        'release_date': release_date,
        'external_urls': d.get('external_urls') or {},
    }


# --------------------------------------------------------------------------
# Fetch dispatch — per-source method names differ slightly
# --------------------------------------------------------------------------

def _fetch_album(client: Any, source: str, identifier: str) -> Optional[dict]:
    """Fetch album metadata by id. Deezer names the method differently; the
    rest share ``get_album``. ``include_tracks=False`` keeps the lookup cheap
    (the modal re-fetches the full tracklist on open)."""
    if source == 'deezer':
        return client.get_album_metadata(identifier, include_tracks=False)
    if source in ('itunes', 'musicbrainz'):
        return client.get_album(identifier, include_tracks=False)
    return client.get_album(identifier)  # spotify


def _fetch_track(client: Any, source: str, identifier: str) -> Optional[dict]:
    """Fetch track metadata by id — uniform across all supported sources."""
    return client.get_track_details(identifier)


def _empty_result(raw: str, source: str = '') -> dict:
    return {
        'source': source,
        'albums': [],
        'tracks': [],
        'available': False,
        'query': raw,
    }


def resolve_identifier(
    raw: str,
    deps: Any,
    preferred_source: Optional[str] = None,
    client_resolver: Optional[Callable[[str], Any]] = None,
) -> dict:
    """Resolve a pasted link/ID to a single album or track card.

    Returns a dropdown-compatible dict:
    ``{source, albums, tracks, available, query}``. ``available`` is True iff
    a source returned a hit. The first resolving target wins, so the result
    carries exactly one card (and the ``source`` that owns it).

    ``client_resolver`` maps a source name to a client (or None). It defaults
    to the orchestrator's ``resolve_client``; tests inject fakes.
    """
    if client_resolver is None:
        from core.search.orchestrator import resolve_client

        def client_resolver(source: str) -> Any:  # noqa: E306
            return resolve_client(source, deps)[0]

    targets = parse_metadata_identifier(raw, preferred_source=preferred_source)
    if not targets:
        logger.info(f"Link/ID resolve: unrecognized identifier {raw!r}")
        return _empty_result(raw, preferred_source or '')

    for target in targets:
        try:
            client = client_resolver(target.source)
        except Exception as e:
            logger.debug(f"Link/ID resolve: client for {target.source} failed: {e}")
            client = None
        if client is None:
            continue

        kinds = (target.kind,) if target.kind else ('album', 'track')
        for kind in kinds:
            try:
                if kind == 'album':
                    data = _fetch_album(client, target.source, target.id)
                    if data:
                        return {
                            'source': target.source,
                            'albums': [album_dict_to_card(data)],
                            'tracks': [],
                            'available': True,
                            'query': raw,
                        }
                else:
                    data = _fetch_track(client, target.source, target.id)
                    if data:
                        return {
                            'source': target.source,
                            'albums': [],
                            'tracks': [track_dict_to_card(data)],
                            'available': True,
                            'query': raw,
                        }
            except Exception as e:
                logger.debug(
                    f"Link/ID resolve: {target.source} {kind} {target.id} failed: {e}"
                )

    logger.info(f"Link/ID resolve: no source resolved {raw!r}")
    return _empty_result(raw, preferred_source or '')
