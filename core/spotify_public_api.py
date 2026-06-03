"""Anonymous full-playlist fetch for the public 'Spotify link' path.

The embed scraper (``spotify_public_scraper.scrape_spotify_embed``) only ever
sees the ~100 tracks Spotify bakes into the embed widget — a public playlist
added by link gets truncated. This module gets the *full* track list without
any app credentials by:

  1. reading the anonymous web-player ``accessToken`` Spotify ships in its
     ``open.spotify.com/embed/playlist/{id}`` page (server-minted — nothing for
     us to sign or maintain, unlike the rotating TOTP secret the now-dead
     get_access_token endpoint demanded), then
  2. paging the public Web API (`/v1/playlists/{id}/tracks`, 100 at a time)
     until the whole playlist is pulled.

Every failure path raises. The only caller
(``spotify_public_scraper.fetch_spotify_public``) catches that and falls back to
the embed scraper, so the worst case is exactly today's behaviour — this never
makes the link path *worse*, only (when Spotify cooperates) better.

This rides Spotify's undocumented page-embedded token and is expected to break
when they change their page; it degrades to the embed fallback, it does not
crash. Pure helpers (token extraction, normalisation, pagination) take an
injected ``http_get`` so they're unit-testable without the network.
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any, Callable, Dict, List, Optional

import requests

# 'soulsync.*' so these lines land in app.log (the bare module name isn't captured).
logger = logging.getLogger('soulsync.spotify_public')

_BROWSER_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept-Language': 'en-US,en;q=0.9',
}

# Spotify embeds the anonymous token as "accessToken":"BQ..." in the page's
# session/config blob.
_TOKEN_RE = re.compile(r'"accessToken"\s*:\s*"([^"]+)"')

_PAGE_LIMIT = 100          # Web API max page size
_MAX_TRACKS = 10000        # safety cap so a bad `total` can't loop forever
_TIMEOUT = 20


def extract_access_token(html: str) -> Optional[str]:
    """Return the anonymous accessToken embedded in a Spotify page, or None."""
    if not html:
        return None
    m = _TOKEN_RE.search(html)
    token = m.group(1) if m else None
    # A truncated/empty token isn't usable.
    return token if token and len(token) > 20 else None


def normalize_api_track(item: Any, index: int) -> Optional[Dict[str, Any]]:
    """Convert a Web API playlist item to the embed scraper's track shape.

    Returns None for items without a usable track id (local files, podcast
    episodes, removed tracks) so the caller can skip them.
    """
    track = (item or {}).get('track') or {}
    track_id = track.get('id')
    if not track_id:
        return None
    artists = [{'name': a.get('name', '')} for a in (track.get('artists') or []) if a.get('name')]
    return {
        'id': track_id,
        'name': track.get('name', 'Unknown Track'),
        'artists': artists or [{'name': 'Unknown Artist'}],
        'duration_ms': track.get('duration_ms', 0),
        'is_explicit': bool(track.get('explicit', False)),
        'track_number': index + 1,
    }


def _paginate_api_tracks(http_get: Callable, spotify_id: str, token: str) -> List[Dict[str, Any]]:
    """Pull the full track list from the Web API, 100 at a time."""
    headers = {'Authorization': f'Bearer {token}', **_BROWSER_HEADERS}
    tracks: List[Dict[str, Any]] = []
    offset = 0
    while offset < _MAX_TRACKS:
        resp = http_get(
            f'https://api.spotify.com/v1/playlists/{spotify_id}/tracks',
            headers=headers,
            params={
                'limit': _PAGE_LIMIT,
                'offset': offset,
                'fields': 'items(track(id,name,artists(name),duration_ms,explicit))',
            },
            timeout=_TIMEOUT,
        )
        if resp.status_code != 200:
            logger.warning("public API tracks offset=%s -> HTTP %s", offset, resp.status_code)
        resp.raise_for_status()
        items = (resp.json() or {}).get('items') or []
        if not items:
            break
        for item in items:
            t = normalize_api_track(item, len(tracks))
            if t:
                tracks.append(t)
        offset += _PAGE_LIMIT
        if len(items) < _PAGE_LIMIT:
            break
    return tracks


def fetch_public_playlist_full(
    spotify_id: str,
    *,
    http_get: Callable = requests.get,
) -> Dict[str, Any]:
    """Pull a public playlist's FULL track list with no app credentials.

    Single embed-page fetch yields the anonymous token + name + first-page
    tracks; the token then paginates the Web API for the whole list. If the API
    is unavailable (e.g. the anonymous token gets rate-limited / 401s), we fall
    back to the tracks the embed page already gave us (≤100) — so this is never
    worse than the embed scraper. Returns ``scrape_spotify_embed``'s shape;
    raises only when we get neither a token nor any embed tracks (caller then
    drops to the embed scraper)."""
    from core.spotify_public_scraper import parse_embed_html

    page = http_get(
        f'https://open.spotify.com/embed/playlist/{spotify_id}',
        headers=_BROWSER_HEADERS, timeout=_TIMEOUT,
    )
    page.raise_for_status()
    html = page.text

    token = extract_access_token(html)
    base = parse_embed_html(html, 'playlist', spotify_id)
    embed_ok = isinstance(base, dict) and 'error' not in base
    name = base.get('name', 'Unknown') if embed_ok else 'Unknown'
    subtitle = base.get('subtitle', '') if embed_ok else ''
    embed_tracks = base.get('tracks', []) if embed_ok else []
    logger.info("public fetch %s: token=%s, embed_parsed=%s, embed_tracks=%d",
                spotify_id, 'yes' if token else 'NO', embed_ok, len(embed_tracks))

    tracks: List[Dict[str, Any]] = []
    if token:
        try:
            tracks = _paginate_api_tracks(http_get, spotify_id, token)
            logger.info("public API pagination ok: %d tracks", len(tracks))
        except Exception as e:
            logger.warning("public API pagination failed (%s); using embed tracks (≤100)", e)

    if not tracks:
        tracks = embed_tracks          # graceful: at least the embed's ≤100
    if not tracks:
        raise RuntimeError('no anonymous token usable and no embed tracks')

    source_url = f'https://open.spotify.com/playlist/{spotify_id}'
    return {
        'id': spotify_id,
        'type': 'playlist',
        'name': name,
        'subtitle': subtitle,
        'tracks': tracks,
        'url': source_url,
        'url_hash': hashlib.md5(source_url.encode()).hexdigest()[:12],
    }


__all__ = ['extract_access_token', 'normalize_api_track', 'fetch_public_playlist_full']
