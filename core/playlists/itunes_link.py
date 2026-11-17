"""iTunes / Apple Music link parsing, shared by the paste route and refresh.

the /api/itunes-link/parse route and the itunes_link playlist adapter both
call ``fetch_itunes_link``. the refresh used to get a stub that returned
nothing, so an Apple Music mirror never refreshed (#2147).
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from typing import Any, Callable, Dict, Optional
from urllib.parse import parse_qs, urlparse

import requests

from utils.logging_config import get_logger

logger = get_logger("playlists.itunes_link")

_apple_music_token_cache = {'token': None, 'fetched_at': 0}
_apple_music_token_lock = threading.Lock()
_APPLE_MUSIC_TOKEN_TTL = 6 * 60 * 60  # seconds
_APPLE_MUSIC_JWT_RE = re.compile(r'eyJ[A-Za-z0-9_\-]+\.eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+')
_APPLE_MUSIC_BUNDLE_SCRAPE_CAP = 8

def _parse_itunes_link_url(url):
    """Return {'type': 'album'|'track'|'playlist', 'id': str} for supported Apple links."""
    import re

    raw = (url or '').strip()
    if not raw:
        return None

    uri_match = re.match(r'^(?:itunes|applemusic):(album|track|playlist):([A-Za-z0-9._-]+)$', raw, re.IGNORECASE)
    if uri_match:
        return {'type': uri_match.group(1).lower(), 'id': uri_match.group(2), 'country': 'us'}

    parsed = urlparse(raw)
    host = (parsed.netloc or '').lower()
    path = parsed.path or ''
    query = parse_qs(parsed.query or '')

    if 'itunes.apple.com' not in host and 'music.apple.com' not in host:
        return None

    # Apple Music track links are album URLs with ?i=<trackId>.
    track_id = (query.get('i') or [None])[0]
    if track_id and str(track_id).isdigit():
        return {'type': 'track', 'id': str(track_id), 'country': _apple_music_country_from_path(path)}

    song_match = re.search(r'/song(?:/[^/]+)?/(\d+)', path)
    if song_match:
        return {'type': 'track', 'id': song_match.group(1), 'country': _apple_music_country_from_path(path)}

    album_match = re.search(r'/album(?:/[^/]+)?/(\d+)', path)
    if album_match:
        return {'type': 'album', 'id': album_match.group(1), 'country': _apple_music_country_from_path(path)}

    playlist_match = re.search(r'/playlist(?:/[^/]+)?/(pl\.[A-Za-z0-9._-]+)', path)
    if playlist_match:
        return {'type': 'playlist', 'id': playlist_match.group(1), 'country': _apple_music_country_from_path(path)}

    return None


def _apple_music_country_from_path(path):
    import re
    match = re.match(r'^/([a-z]{2})(?:/|$)', path or '', re.IGNORECASE)
    return match.group(1).lower() if match else 'us'


def _itunes_album_image_url(album_data):
    images = album_data.get('images') or []
    if images and isinstance(images[0], dict):
        return images[0].get('url', '')
    return ''


def _itunes_track_to_link_track(track_data, fallback_album=None):
    artists = track_data.get('artists') or []
    if artists and isinstance(artists[0], dict):
        artists = [a.get('name', '') for a in artists if a.get('name')]
    elif isinstance(artists, str):
        artists = [artists]

    album = track_data.get('album') or fallback_album or {}
    if isinstance(album, str):
        album = {'name': album, 'images': []}

    return {
        'id': str(track_data.get('id') or track_data.get('trackId') or ''),
        'name': track_data.get('name') or track_data.get('trackName') or '',
        'artists': artists,
        'album': album,
        'duration_ms': track_data.get('duration_ms') or track_data.get('trackTimeMillis') or 0,
        'explicit': track_data.get('explicit') or track_data.get('trackExplicitness') == 'explicit',
        'track_number': track_data.get('track_number') or track_data.get('trackNumber') or 0,
        'disc_number': track_data.get('disc_number') or track_data.get('discNumber') or 1,
        'external_urls': track_data.get('external_urls') or {'itunes': track_data.get('trackViewUrl', '')},
        '_source': 'itunes'
    }


def _apple_music_artwork_images(artwork):
    if not isinstance(artwork, dict):
        return []
    template = artwork.get('url') or ''
    if not template:
        return []
    sizes = [600, 300, 100]
    images = []
    for size in sizes:
        url = template.replace('{w}', str(size)).replace('{h}', str(size))
        url = url.replace('{f}', 'jpg').replace('{c}', '')
        images.append({'url': url, 'height': size, 'width': size})
    return images


def _apple_music_song_to_link_track(song):
    attrs = (song or {}).get('attributes') or {}
    album_images = _apple_music_artwork_images(attrs.get('artwork'))
    album = {
        'id': attrs.get('albumId') or '',
        'name': attrs.get('albumName') or '',
        'images': album_images,
        'release_date': attrs.get('releaseDate') or '',
        'album_type': 'album',
    }
    return {
        'id': str((song or {}).get('id') or ''),
        'name': attrs.get('name') or '',
        'artists': [attrs.get('artistName') or 'Unknown Artist'],
        'album': album,
        'duration_ms': attrs.get('durationInMillis') or 0,
        'explicit': attrs.get('contentRating') == 'explicit',
        'track_number': attrs.get('trackNumber') or 0,
        'disc_number': attrs.get('discNumber') or 1,
        'external_urls': {'itunes': attrs.get('url') or ''},
        '_source': 'itunes'
    }


def _looks_like_apple_music_token(token):
    """Decode JWT payload and confirm Apple media-api claims before trusting."""
    import base64

    if not token or token.count('.') != 2:
        return False
    try:
        payload_b64 = token.split('.')[1]
        padding = '=' * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64 + padding))
    except Exception:
        return False
    # Apple media-api tokens carry root_https_origin (distinctive) or are
    # Apple-issued JWTs with iss + iat + exp claims.
    if payload.get('root_https_origin'):
        return True
    if payload.get('iss') and payload.get('iat') and payload.get('exp'):
        return True
    return False


def _extract_apple_music_web_token(html_text, session=None):
    import html
    from urllib.parse import unquote, urljoin

    if not html_text:
        return None

    meta_match = re.search(
        r'<meta[^>]+name=["\']desktop-music-app/config/environment["\'][^>]+content=["\']([^"\']+)["\']',
        html_text,
        re.IGNORECASE,
    )
    if meta_match:
        try:
            raw = html.unescape(meta_match.group(1))
            data = json.loads(unquote(raw))
            token = ((data.get('MEDIA_API') or {}).get('token')
                     or (data.get('media-api') or {}).get('token'))
            if token and _looks_like_apple_music_token(token):
                return token
        except Exception as e:
            logger.debug(f"Apple Music token meta parse failed: {e}")

    inline_match = re.search(r'"token"\s*:\s*"(' + _APPLE_MUSIC_JWT_RE.pattern + r')"', html_text)
    if inline_match and _looks_like_apple_music_token(inline_match.group(1)):
        return inline_match.group(1)

    if session is None:
        return None

    script_srcs = re.findall(
        r'<script[^>]+src=["\']([^"\']+\.js)["\']',
        html_text,
        re.IGNORECASE,
    )
    prioritized = sorted(
        script_srcs,
        key=lambda s: 0 if re.search(r'(index|chunk|main|app)[^/]*\.js$', s, re.IGNORECASE) else 1,
    )
    attempted = 0
    for src in prioritized:
        if attempted >= _APPLE_MUSIC_BUNDLE_SCRAPE_CAP:
            break
        attempted += 1
        try:
            js_url = urljoin('https://music.apple.com/', src)
            js_resp = session.get(js_url, timeout=15)
            js_resp.raise_for_status()
            for match in _APPLE_MUSIC_JWT_RE.finditer(js_resp.text):
                candidate = match.group(0)
                if _looks_like_apple_music_token(candidate):
                    return candidate
        except Exception as e:
            logger.debug(f"Apple Music bundle scrape failed for {src}: {e}")
            continue
    return None


def _get_apple_music_web_token(session, page_text, force_refresh=False):
    with _apple_music_token_lock:
        if not force_refresh:
            cached = _apple_music_token_cache.get('token')
            fetched_at = _apple_music_token_cache.get('fetched_at', 0)
            if cached and (time.time() - fetched_at) < _APPLE_MUSIC_TOKEN_TTL:
                return cached
        token = _extract_apple_music_web_token(page_text, session=session)
        if token:
            _apple_music_token_cache['token'] = token
            _apple_music_token_cache['fetched_at'] = time.time()
        elif force_refresh:
            _apple_music_token_cache['token'] = None
            _apple_music_token_cache['fetched_at'] = 0
        return token


def _invalidate_apple_music_token():
    with _apple_music_token_lock:
        _apple_music_token_cache['token'] = None
        _apple_music_token_cache['fetched_at'] = 0


def _apple_music_amp_get(session, page_text_provider, web_headers, page_url, api_url, params):
    """GET amp-api with current cached token; on 401 invalidate + refetch token + retry once."""
    def build_headers(tok):
        return {
            'Accept': 'application/json',
            'Origin': 'https://music.apple.com',
            'Referer': page_url,
            'User-Agent': web_headers['User-Agent'],
            'Authorization': f"Bearer {tok}",
        }

    token = _get_apple_music_web_token(session, page_text_provider())
    if not token:
        raise ValueError("Could not read Apple Music web token")
    resp = session.get(api_url, headers=build_headers(token), params=params, timeout=20)
    if resp.status_code == 401:
        _invalidate_apple_music_token()
        page = session.get(page_url, headers=web_headers, timeout=20)
        page.raise_for_status()
        token = _get_apple_music_web_token(session, page.text, force_refresh=True)
        if not token:
            raise ValueError("Could not read Apple Music web token")
        resp = session.get(api_url, headers=build_headers(token), params=params, timeout=20)
    resp.raise_for_status()
    return resp


def _fetch_apple_music_playlist(url, playlist_id, country):
    from urllib.parse import urljoin

    session = requests.Session()
    web_headers = {
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15',
        'Accept-Language': 'en-US,en;q=0.9',
    }
    cached_page_text = {'value': None}

    def page_text_provider():
        if cached_page_text['value'] is None:
            page = session.get(url, headers=web_headers, timeout=20)
            page.raise_for_status()
            cached_page_text['value'] = page.text
        return cached_page_text['value']

    country = (country or 'us').lower()
    api_url = f"https://amp-api.music.apple.com/v1/catalog/{country}/playlists/{playlist_id}"
    playlist_resp = _apple_music_amp_get(session, page_text_provider, web_headers, url, api_url, {'l': 'en-US'})
    playlist_payload = playlist_resp.json()
    playlist_item = (playlist_payload.get('data') or [{}])[0]
    playlist_attrs = playlist_item.get('attributes') or {}

    tracks = []
    tracks_url = f"{api_url}/tracks"
    params = {'l': 'en-US'}
    while tracks_url:
        tracks_resp = _apple_music_amp_get(session, page_text_provider, web_headers, url, tracks_url, params)
        tracks_payload = tracks_resp.json()
        tracks.extend(_apple_music_song_to_link_track(song) for song in tracks_payload.get('data') or [])
        next_path = tracks_payload.get('next')
        tracks_url = urljoin('https://amp-api.music.apple.com', next_path) if next_path else None
        params = None

    images = _apple_music_artwork_images(playlist_attrs.get('artwork'))
    return {
        'id': playlist_id,
        'type': 'playlist',
        'name': playlist_attrs.get('name') or 'Apple Music Playlist',
        'subtitle': playlist_attrs.get('curatorName') or playlist_attrs.get('editorialNotes', {}).get('standard') or 'Apple Music',
        'url': url,
        'track_count': len(tracks),
        'image_url': images[0]['url'] if images else '',
        'tracks': tracks,
    }


def itunes_link_url_hash(parsed: Dict[str, Any]) -> str:
    """the stable key a pasted link is stored under (source_playlist_id)."""
    canonical = f"itunes:{parsed['type']}:{parsed['id']}"
    return hashlib.md5(canonical.encode()).hexdigest()[:12]


def fetch_itunes_link(url: str, itunes_client_getter: Callable[[], Any]) -> Dict[str, Any]:
    """Parse an iTunes / Apple Music album, track or playlist link into the
    virtual playlist the paste route returns.

    raises ``ValueError`` for a link it can't read and ``LookupError`` when
    the album or track isn't found; network errors pass through."""
    url = (url or '').strip()
    if not url:
        raise ValueError("iTunes URL is required")
    parsed = _parse_itunes_link_url(url)
    if not parsed:
        raise ValueError("Invalid iTunes or Apple Music link. Album and track links are supported.")

    tracks = []
    image_url = ''
    subtitle = 'iTunes'
    name = ''
    if parsed['type'] == 'playlist':
        response_data = _fetch_apple_music_playlist(url, parsed['id'], parsed.get('country') or 'us')
        tracks = response_data.get('tracks') or []
    elif parsed['type'] == 'album':
        album = itunes_client_getter().get_album(parsed['id'], include_tracks=True)
        if not album:
            raise LookupError("iTunes album not found")
        album_tracks = ((album.get('tracks') or {}).get('items') or [])
        tracks = [_itunes_track_to_link_track(t, fallback_album={
            'id': album.get('id'),
            'name': album.get('name', ''),
            'images': album.get('images') or [],
            'release_date': album.get('release_date', ''),
            'album_type': album.get('album_type', 'album')
        }) for t in album_tracks]
        name = album.get('name', '')
        artists = album.get('artists') or []
        subtitle = ', '.join(a.get('name', '') for a in artists if isinstance(a, dict)) or 'iTunes'
        image_url = _itunes_album_image_url(album)
    else:
        track = itunes_client_getter().get_track_details(parsed['id'])
        if not track:
            raise LookupError("iTunes track not found")
        tracks = [_itunes_track_to_link_track(track)]
        name = track.get('name', '')
        subtitle = ', '.join(track.get('artists') or []) or 'iTunes'
        album = track.get('album') or {}
        if isinstance(album, dict):
            name = f"{track.get('name', '')} - {subtitle}".strip(' -')

    if not tracks:
        raise ValueError("No tracks found for this iTunes link")

    url_hash = itunes_link_url_hash(parsed)
    if parsed['type'] == 'playlist':
        response_data['url_hash'] = url_hash
        response_data['track_count'] = len(tracks)
        return response_data
    return {
        'id': parsed['id'],
        'type': parsed['type'],
        'name': name or f"iTunes {parsed['type'].title()}",
        'subtitle': subtitle,
        'url': url,
        'url_hash': url_hash,
        'track_count': len(tracks),
        'image_url': image_url,
        'tracks': tracks
    }
