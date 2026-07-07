"""Client for Bandcamp search and release metadata.

Bandcamp has no general-purpose public API — the real bandcamp.com/developer
OAuth API is gated to label/merch-partner accounts. This client instead uses
two endpoints Bandcamp itself serves to unauthenticated browsers:

  - the public autocomplete search API (JSON, same one the site's own search
    box calls) for search_artists / search_albums / search_tracks
  - each release page's embedded schema.org JSON-LD block (served for SEO)
    for get_release_metadata

Note: bandcamp.com/search (the HTML results page) is now gated behind a JS
client-challenge for non-browser clients and returns a ~3KB stub instead of
results — confirmed by live request during development. Do not scrape it;
the JSON autocomplete endpoint below is unaffected and returns richer data
(real numeric IDs, image URLs) than HTML scraping would anyway.
"""

from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime
from difflib import SequenceMatcher
from functools import wraps
from typing import Any, Dict, List, Optional

import requests

from core.metadata.types import Album, Artist, Track
from utils.logging_config import get_logger

logger = get_logger("bandcamp_client")

# Module-level rate limiting — Bandcamp publishes no documented limits for
# these endpoints, so this is a conservative default, same shape as
# core.genius_client's rate_limited decorator.
_last_call_time = 0.0
_call_lock = threading.Lock()
MIN_CALL_INTERVAL = 1.0
_rate_limit_backoff = 0
_rate_limit_until = 0.0

_JSONLD_LINE_RE = re.compile(r'.*"@id".*')
_JSONLD_SCRIPT_RE = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.DOTALL)
_DURATION_RE = re.compile(r'P(?:(\d+)D)?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?')
_TRAILING_TZ_RE = re.compile(r'\s+[A-Z]{2,5}$')
_MUSIC_GRID_RE = re.compile(r'<ol id="music-grid".*?</ol>', re.DOTALL)
_MUSIC_GRID_ITEM_RE = re.compile(r'<li\s+data-item-id="([^"]+)".*?</li>', re.DOTALL)
_ITEM_HREF_RE = re.compile(r'<a href="([^"]+)"')
_ITEM_TITLE_RE = re.compile(r'<p class="title">\s*([^<\n]+)')
_ITEM_IMG_RE = re.compile(r'<img src="([^"]+)"')


class BandcampRateLimitedError(requests.exceptions.RequestException):
    """Raised immediately while Bandcamp is inside a 429/503 backoff window.

    Subclasses RequestException so callers already treat it as a plain
    network failure: log one line, skip Bandcamp, move on. Metadata garnish
    — nothing is allowed to wait for it."""


def rate_limited(func):
    """Enforce a minimum interval between Bandcamp requests, with a fail-fast
    (never-sleeping) gate during a 429/503 backoff window."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        global _last_call_time, _rate_limit_backoff, _rate_limit_until

        with _call_lock:
            now = time.time()
            if now < _rate_limit_until:
                remaining = _rate_limit_until - now
                raise BandcampRateLimitedError(
                    f"Bandcamp in backoff for another {remaining:.0f}s — skipping"
                )

            # Reserve this call's slot one interval after the previous
            # reservation, then sleep to it OUTSIDE the lock. Sleeping while
            # holding _call_lock would stall a foreground request (e.g. a user
            # clicking a Bandcamp album) ~1s behind the background worker. By
            # advancing _last_call_time to the scheduled time under the lock,
            # concurrent callers still serialize into distinct, correctly-spaced
            # slots without blocking each other during the wait.
            scheduled = max(now, _last_call_time + MIN_CALL_INTERVAL)
            _last_call_time = scheduled

        wait = scheduled - time.time()
        if wait > 0:
            time.sleep(wait)

        try:
            result = func(*args, **kwargs)
            if _rate_limit_backoff > 0:
                _rate_limit_backoff = max(0, _rate_limit_backoff - 5)
            return result
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            if status in (429, 503):
                _rate_limit_backoff = min(120, max(30, _rate_limit_backoff * 2) if _rate_limit_backoff else 30)
                _rate_limit_until = time.time() + _rate_limit_backoff
                logger.warning(f"Bandcamp {status} — gating calls for {_rate_limit_backoff}s")
            raise
    return wrapper


def _extract_jsonld(html: str) -> Optional[dict]:
    """Pull the embedded schema.org JSON-LD block out of a Bandcamp page.

    Bandcamp renders the whole JSON-LD object on a single physical line
    (verified against live album/track pages), so a plain per-line regex
    finds it without needing DOTALL. Falls back to matching the surrounding
    <script type="application/ld+json"> tag in case that ever changes."""
    match = _JSONLD_LINE_RE.search(html)
    if match:
        try:
            return json.loads(match.group())
        except (json.JSONDecodeError, ValueError):
            pass

    match = _JSONLD_SCRIPT_RE.search(html)
    if match:
        try:
            return json.loads(match.group(1))
        except (json.JSONDecodeError, ValueError):
            pass

    return None


def _parse_bandcamp_duration(value: Optional[str]) -> int:
    """Parse Bandcamp's duration string (e.g. 'P00H03M57S') into milliseconds.

    This is NOT valid ISO 8601 — real ISO 8601 durations require a 'T' time
    designator before H/M/S components (e.g. 'PT3M57S'); Bandcamp omits it.
    A standard ISO 8601 parser would silently misparse this, so it's handled
    with a dedicated regex instead."""
    if not value:
        return 0
    m = _DURATION_RE.match(value)
    if not m:
        return 0
    days, hours, minutes, seconds = (int(g) if g else 0 for g in m.groups())
    total_seconds = days * 86400 + hours * 3600 + minutes * 60 + seconds
    return total_seconds * 1000


def _parse_bandcamp_date(value: Optional[str]) -> str:
    """Parse Bandcamp's 'DD Mon YYYY HH:MM:SS TZ' date format into 'YYYY-MM-DD'."""
    if not value:
        return ''
    cleaned = _TRAILING_TZ_RE.sub('', value.strip())
    for fmt in ('%d %b %Y %H:%M:%S', '%d %b %Y'):
        try:
            return datetime.strptime(cleaned, fmt).strftime('%Y-%m-%d')
        except ValueError:
            continue
    return ''


def _normalize_for_match(value: str) -> str:
    return re.sub(r'[^a-z0-9 ]', '', (value or '').lower()).strip()


def _best_match(candidates, artist_name: str, title: str):
    """Pick the best of `candidates` (Track or Album objects — both expose
    `.name`/`.artists`) by combined title/artist similarity.

    Both thresholds must clear their bar independently before a candidate
    is even scored — a high title match with a completely unrelated artist
    (or vice versa) must not win."""
    artist_norm = _normalize_for_match(artist_name)
    title_norm = _normalize_for_match(title)

    best = None
    best_score = 0.0
    for candidate in candidates:
        title_score = SequenceMatcher(None, title_norm, _normalize_for_match(candidate.name)).ratio()
        if title_score < 0.75:
            continue
        candidate_artists = candidate.artists or []
        artist_score = max(
            (SequenceMatcher(None, artist_norm, _normalize_for_match(a)).ratio() for a in candidate_artists),
            default=0.0,
        )
        if artist_score < 0.6:
            continue
        score = (title_score * 0.6) + (artist_score * 0.4)
        if score > best_score:
            best_score = score
            best = candidate
    return best


def _best_name_match(candidates, name: str):
    """Pick the best of `candidates` (Artist objects) by name similarity alone.

    Used where there's no second field to cross-check against (resolving a
    plain artist name to a Bandcamp band/label — Bandcamp has no numeric-ID
    lookup API, only free-text search)."""
    target = _normalize_for_match(name)
    best = None
    best_score = 0.0
    for candidate in candidates:
        score = SequenceMatcher(None, target, _normalize_for_match(candidate.name)).ratio()
        if score > best_score:
            best_score = score
            best = candidate
    return best if best_score >= 0.75 else None


class BandcampClient:
    """Client for Bandcamp search and release metadata. No API key required."""

    BASE_URL = "https://bandcamp.com"
    SEARCH_URL = f"{BASE_URL}/api/bcsearch_public_api/1/autocomplete_elastic"

    # Bandcamp's own item_type codes: 'b' = band/label, 'a' = album, 't' = track
    _ITEM_TYPE = {'artist': 'b', 'album': 'a', 'track': 't'}

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            ),
        })
        logger.info("Bandcamp client initialized")

    # ── Low-level HTTP ──

    @rate_limited
    def _search_raw(self, query: str, item_type: str = '', limit: int = 10) -> List[Dict[str, Any]]:
        payload = {
            'search_text': query,
            'search_filter': item_type,
            'full_page': False,
            'fan_id': None,
        }
        try:
            response = self.session.post(self.SEARCH_URL, json=payload, timeout=10)
            response.raise_for_status()
        except requests.exceptions.Timeout:
            logger.warning(f"Bandcamp search timeout for query: {query}")
            return []
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            if status in (429, 503):
                raise
            logger.warning(f"Bandcamp search error for query {query!r}: {e}")
            return []
        except requests.exceptions.RequestException as e:
            logger.warning(f"Bandcamp search error for query {query!r}: {e}")
            return []

        try:
            data = response.json()
        except ValueError:
            logger.warning("Bandcamp search returned non-JSON response")
            return []

        results = ((data or {}).get('auto') or {}).get('results') or []
        return results[:limit]

    @rate_limited
    def _get_response(self, url: str, timeout: int = 15) -> Optional[requests.Response]:
        try:
            response = self.session.get(url, timeout=timeout)
            response.raise_for_status()
            return response
        except requests.exceptions.Timeout:
            logger.warning(f"Bandcamp page fetch timeout: {url}")
            return None
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            if status in (429, 503):
                raise
            logger.warning(f"Bandcamp page fetch error ({url}): {e}")
            return None
        except requests.exceptions.RequestException as e:
            logger.warning(f"Bandcamp page fetch error ({url}): {e}")
            return None

    def _get_text(self, url: str, timeout: int = 15) -> Optional[str]:
        response = self._get_response(url, timeout=timeout)
        return response.text if response is not None else None

    # ── Search ──

    def search_artists(self, query: str, limit: int = 10) -> List[Artist]:
        raw = self._search_raw(query, item_type=self._ITEM_TYPE['artist'], limit=limit)
        return [Artist.from_bandcamp_dict(r) for r in raw if r.get('type') == 'b']

    def search_albums(self, query: str, limit: int = 10) -> List[Album]:
        raw = self._search_raw(query, item_type=self._ITEM_TYPE['album'], limit=limit)
        return [Album.from_bandcamp_dict(r) for r in raw if r.get('type') == 'a']

    def search_tracks(self, query: str, limit: int = 10) -> List[Track]:
        raw = self._search_raw(query, item_type=self._ITEM_TYPE['track'], limit=limit)
        return [Track.from_bandcamp_dict(r) for r in raw if r.get('type') == 't']

    # ── Release metadata ──

    def get_release_metadata(self, url: str) -> Optional[Dict[str, Any]]:
        """Fetch and normalize a Bandcamp album/track page's embedded JSON-LD."""
        if not url:
            return None
        html = self._get_text(url)
        if not html:
            return None
        data = _extract_jsonld(html)
        if not data:
            logger.debug(f"No JSON-LD metadata found on Bandcamp page: {url}")
            return None
        return self._normalize_release(data, url)

    def _normalize_release(self, data: Dict[str, Any], fallback_url: str) -> Dict[str, Any]:
        is_track = data.get('@type') == 'MusicRecording'
        by_artist = data.get('byArtist') or {}
        publisher = data.get('publisher') or {}

        result: Dict[str, Any] = {
            'url': data.get('@id') or fallback_url,
            'title': data.get('name') or '',
            'artist': by_artist.get('name') or '',
            'artist_url': by_artist.get('@id'),
            'label': publisher.get('name') or None,
            'tags': list(data.get('keywords') or []),
            'release_date': _parse_bandcamp_date(data.get('datePublished')),
            'image_url': data.get('image'),
            'credits': data.get('creditText') or None,
            'description': data.get('description') or None,
            'is_track': is_track,
        }

        if is_track:
            result['duration_ms'] = _parse_bandcamp_duration(data.get('duration'))
            album = data.get('inAlbum') or {}
            result['album'] = album.get('name') or ''
        else:
            result['album'] = result['title']
            tracklist = []
            track_container = data.get('track') or {}
            for entry in track_container.get('itemListElement') or []:
                item = entry.get('item') or {}
                tracklist.append({
                    'position': entry.get('position'),
                    'title': item.get('name') or '',
                    'url': item.get('@id'),
                    'duration_ms': _parse_bandcamp_duration(item.get('duration')),
                })
            result['tracks'] = tracklist
            result['total_tracks'] = data.get('numTracks') or len(tracklist)

        return result

    # ── Enrichment convenience ──

    def search_track(self, artist_name: str, track_title: str) -> Optional[Dict[str, Any]]:
        """Search for a specific track and return metadata enriched with
        tags/label/credits from its release page. Mirrors the search_track
        shape other enrichment clients already expose (e.g. DeezerClient,
        AudioDBClient), so core.metadata.source's per-source hooks can call
        it uniformly."""
        query = f"{artist_name} {track_title}".strip()
        if not query:
            return None

        candidates = self.search_tracks(query, limit=10)
        if not candidates:
            logger.debug(f"No Bandcamp results for: {query}")
            return None

        best = _best_match(candidates, artist_name, track_title)
        if not best:
            logger.debug(f"No confident Bandcamp match for: {artist_name} - {track_title}")
            return None

        track_url = best.external_urls.get('bandcamp')
        release = self.get_release_metadata(track_url) if track_url else None

        merged: Dict[str, Any] = {
            'id': best.id,
            'url': track_url,
            'title': best.name,
            'artist': (best.artists or [artist_name])[0],
            # The release page's own JSON-LD image is live-verified (it's what
            # that exact page renders); the autocomplete search index's cached
            # thumbnail can point at a since-removed CDN size variant (confirmed
            # 404 in production — e.g. .../img/1811014619_3.jpg). Prefer it.
            'image_url': (release.get('image_url') if release else None) or best.image_url,
        }
        if release:
            merged['tags'] = release.get('tags') or []
            merged['label'] = release.get('label')
            merged['credits'] = release.get('credits')
            merged['release_date'] = release.get('release_date')
        return merged

    def search_album(self, artist_name: str, album_title: str) -> Optional[Dict[str, Any]]:
        """Search for a specific album and return metadata enriched with
        tags/label/credits/tracklist from its release page. Albums are
        Bandcamp's primary unit — a release's JSON-LD carries the full
        tracklist plus tags/label/credits in a single fetch, richer than
        any individual track page."""
        query = f"{artist_name} {album_title}".strip()
        if not query:
            return None

        candidates = self.search_albums(query, limit=10)
        if not candidates:
            logger.debug(f"No Bandcamp album results for: {query}")
            return None

        best = _best_match(candidates, artist_name, album_title)
        if not best:
            logger.debug(f"No confident Bandcamp album match for: {artist_name} - {album_title}")
            return None

        album_url = best.external_urls.get('bandcamp')
        release = self.get_release_metadata(album_url) if album_url else None

        merged: Dict[str, Any] = {
            'id': best.id,
            'url': album_url,
            'title': best.name,
            'artist': (best.artists or [artist_name])[0],
            # The release page's own JSON-LD image is live-verified (it's what
            # that exact page renders); the autocomplete search index's cached
            # thumbnail can point at a since-removed CDN size variant (confirmed
            # 404 in production — e.g. .../img/1811014619_3.jpg). Prefer it.
            'image_url': (release.get('image_url') if release else None) or best.image_url,
        }
        if release:
            merged['tags'] = release.get('tags') or []
            merged['label'] = release.get('label')
            merged['credits'] = release.get('credits')
            merged['release_date'] = release.get('release_date')
            merged['tracks'] = release.get('tracks') or []
            merged['total_tracks'] = release.get('total_tracks')
        return merged

    # ── Artist discography ──
    # Bandcamp has no numeric-ID-based lookup API for any entity type —
    # bands/labels, albums, and tracks are all addressed by URL. These
    # methods resolve an artist by name (the one thing every caller in
    # this codebase's generic per-source dispatch always has, even when it
    # doesn't have a source-native ID) and scrape the artist's own /music
    # discography page, which is a stable, site-rendered grid — same
    # "read the page Bandcamp already serves" approach as get_release_metadata.

    def get_artist(self, artist_name: str) -> Optional[Artist]:
        """Resolve an artist by name via search."""
        if not artist_name:
            return None
        candidates = self.search_artists(artist_name, limit=5)
        return _best_name_match(candidates, artist_name)

    def get_artist_releases(self, artist_url: str) -> List[Dict[str, Any]]:
        """List a Bandcamp artist/label's releases from their /music page.

        Falls back to a single-release result when the artist has exactly
        one release — Bandcamp redirects /music straight to it instead of
        rendering a grid (confirmed live: a one-release label's /music URL
        303s to /album/<slug> with no music-grid element on the page at
        all)."""
        if not artist_url:
            return []

        domain = artist_url.split('//', 1)[-1].split('/', 1)[0]
        response = self._get_response(f"https://{domain}/music")
        if response is None:
            return []
        html = response.text

        grid_match = _MUSIC_GRID_RE.search(html)
        if grid_match:
            releases = []
            for item_match in _MUSIC_GRID_ITEM_RE.finditer(grid_match.group()):
                item_id = item_match.group(1)
                block = item_match.group(0)
                href_match = _ITEM_HREF_RE.search(block)
                title_match = _ITEM_TITLE_RE.search(block)
                img_match = _ITEM_IMG_RE.search(block)
                if not href_match or not title_match:
                    continue
                releases.append({
                    'id': item_id,
                    'type': 'track' if item_id.startswith('track-') else 'album',
                    'title': title_match.group(1).strip(),
                    'url': href_match.group(1),
                    'image_url': img_match.group(1) if img_match else None,
                })
            return releases

        # No grid — likely the single-release redirect. Confirm via JSON-LD
        # rather than assuming, so an unrelated page (e.g. a login wall)
        # can't be mistaken for a release.
        data = _extract_jsonld(html)
        if data and data.get('@type') in ('MusicAlbum', 'MusicRecording'):
            kind = 'track' if data.get('@type') == 'MusicRecording' else 'album'
            return [{
                'id': f'{kind}-single',
                'type': kind,
                'title': data.get('name') or '',
                'url': data.get('@id') or response.url,
                'image_url': data.get('image'),
            }]
        return []

    def get_artist_albums(
        self, artist_id: str, artist_name: Optional[str] = None,
        album_type: str = 'album,single', limit: int = 50, **kwargs,
    ) -> List[Dict[str, Any]]:
        """Duck-typed interface expected by
        core.metadata.album_tracks.get_artist_albums_for_source — same
        shape as every other source's get_artist_albums.

        `artist_id` (a Discover search result's numeric band_id) isn't
        independently resolvable on Bandcamp, so it's accepted for
        interface compatibility but ignored; `artist_name` is what this
        actually resolves against, matching the 'artist_name kwarg'
        fallback JioSaavn already uses in the same dispatcher."""
        if not artist_name:
            return []
        artist = self.get_artist(artist_name)
        if not artist:
            return []
        artist_url = artist.external_urls.get('bandcamp')
        if not artist_url:
            return []

        releases = self.get_artist_releases(artist_url)
        result = []
        for release in releases[:limit]:
            result.append({
                'id': release['id'],
                'name': release['title'],
                'title': release['title'],
                'album_type': 'single' if release['type'] == 'track' else 'album',
                'image_url': release.get('image_url'),
                'artists': [artist.name],
            })
        return result


def release_to_spotify_shape(
    release: Dict[str, Any], album_id: str = '',
    fallback_name: str = '', fallback_artist: str = '',
) -> Dict[str, Any]:
    """Reshape a get_release_metadata()/search_album() result into the
    'Spotify-shaped' dict (name/artists/images/tracks with
    name/duration_ms/track_number) the rest of SoulSync's album/track
    pipeline expects via its duck-typed field extraction
    (core.metadata.album_tracks._extract_lookup_value chains like
    'name', 'track_name', 'trackName' — Bandcamp's own field names
    ('title', 'position') don't match any of those aliases, so results
    must be relabeled here rather than passed through raw)."""
    tracks = []
    for i, t in enumerate(release.get('tracks') or []):
        tracks.append({
            'id': t.get('url', ''),
            'name': t.get('title', ''),
            'track_number': t.get('position') or (i + 1),
            'disc_number': 1,
            'duration_ms': t.get('duration_ms', 0),
            'artists': [{'name': release.get('artist') or fallback_artist}],
        })
    return {
        'id': release.get('id') or album_id,
        'name': release.get('title') or fallback_name,
        'artists': [{'name': release.get('artist') or fallback_artist}],
        'release_date': release.get('release_date', ''),
        'total_tracks': release.get('total_tracks', len(tracks)),
        'album_type': 'album',
        'images': [{'url': release['image_url']}] if release.get('image_url') else [],
        'tracks': tracks,
    }
