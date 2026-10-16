"""beatport as an enrichment source: bpm, key, label, catalog number and genre
for a track, found by its isrc.

for electronic music beatport is the reference: the label's own bpm and key,
the real mix name, the catalog number djs file by. it only ever answers for
the exact recording. a lookup needs the track's isrc and takes a beatport
track only when its isrc is the same one. no title guessing.

two places to look, cheapest first:
- the metadata cache. every beatport list soulsync reads files its tracks
  there with their isrc, so a track that came in through a beatport chart
  costs nothing.
- beatport's track search, by artist and title. beatport search ignores
  isrcs, but every result carries one, so the right one is picked by isrc.
  rate limited, since every request goes past cloudflare.
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import quote

from core.beatport.next_data import BASE_URL, queries, track_key
from core.discovery.isrc_match import clean_isrc
from utils.logging_config import get_logger

logger = get_logger("beatport.enrich")

SEARCH_MIN_INTERVAL = 2.0   # seconds between beatport searches
MISS_TTL = 6 * 3600         # an isrc beatport didn't have isn't asked again for this long

_lock = threading.Lock()
_last_search = 0.0
_misses: Dict[str, float] = {}

# genres that make a beatport search worth a request when the user hasn't
# asked for every track to be looked up
_ELECTRONIC = re.compile(
    r'house|techno|trance|drum\s*(?:&|and|n)\s*bass|\bdnb\b|dubstep|electro|edm|garage|'
    r'breakbeat|breaks|hardstyle|hardcore|bass music|jungle|minimal|dance|idm|downtempo|'
    r'psytrance|psy-trance|melodic|afro house|amapiano|footwork|grime|uk bass',
    re.I)


def looks_electronic(genres: Iterable[Any]) -> bool:
    return any(_ELECTRONIC.search(str(g or '')) for g in genres or [])


def reset() -> None:
    """test seam: forget the throttle and the misses."""
    global _last_search
    with _lock:
        _last_search = 0.0
        _misses.clear()


def _slug(text: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', (text or '').lower()).strip('-') or 'track'


def normalize_search_result(raw: Any) -> Optional[Dict[str, Any]]:
    """a /search/tracks result in the same shape as next_data.normalize_track.
    the search api names everything differently (track_name, key_name,
    label.label_name, genre as a list)."""
    if not isinstance(raw, dict) or not raw.get('track_id') or not raw.get('track_name'):
        return None
    artists = [{'name': a.get('artist_name'), 'id': a.get('artist_id')}
               for a in raw.get('artists') or [] if isinstance(a, dict) and a.get('artist_name')]
    release = raw.get('release') if isinstance(raw.get('release'), dict) else {}
    label = raw.get('label') if isinstance(raw.get('label'), dict) else {}
    genres = [g.get('genre_name') for g in raw.get('genre') or [] if isinstance(g, dict) and g.get('genre_name')]
    sub_genres = [g.get('sub_genre_name') for g in raw.get('sub_genre') or []
                  if isinstance(g, dict) and g.get('sub_genre_name')] if isinstance(raw.get('sub_genre'), list) else []
    length_ms = raw.get('length') or 0
    try:
        length_ms = int(length_ms)
    except (TypeError, ValueError):
        length_ms = 0
    image = str(release.get('release_image_uri') or '')
    release_id = release.get('release_id')
    return {
        'beatport_id': raw['track_id'],
        'id': track_key(raw['track_id']),
        'title': str(raw['track_name']).strip(),
        'mix_name': str(raw.get('mix_name') or '').strip(),
        'artist': ', '.join(a['name'] for a in artists) or 'Unknown Artist',
        'artists': artists,
        'url': f"{BASE_URL}/track/{_slug(raw['track_name'])}/{raw['track_id']}",
        'label': str(label.get('label_name') or '').strip() or 'Unknown Label',
        'bpm': raw.get('bpm'),
        'key': str(raw.get('key_name') or '').strip() or None,
        'genre': genres[0] if genres else None,
        'sub_genre': sub_genres[0] if sub_genres else None,
        'duration': length_ms // 1000,
        'duration_ms': length_ms,
        'isrc': clean_isrc(raw.get('isrc')),
        'catalog_number': str(raw.get('catalog_number') or '').strip(),
        'type': 'track',
        'release_name': str(release.get('release_name') or '').strip(),
        'release_id': str(release_id) if release_id else '',
        'release_image': image,
        'release_date': str(raw.get('publish_date') or raw.get('release_date') or '')[:10],
        'artwork_url': image,
        'source': 'search',
    }


def search_results(next_data: Any) -> List[Dict[str, Any]]:
    out = []
    for key, data in queries(next_data):
        if not key.startswith('search-tracks') or not isinstance(data, dict):
            continue
        for raw in data.get('data') or []:
            track = normalize_search_result(raw)
            if track:
                out.append(track)
    return out


def find_cached_by_isrc(isrc: str) -> Optional[Dict[str, Any]]:
    """a beatport track the metadata cache already holds for this isrc."""
    code = clean_isrc(isrc)
    if not code:
        return None
    try:
        import json
        from core.metadata.cache import get_metadata_cache
        cache = get_metadata_cache()
        db = cache._get_db()
        conn = db._get_connection()
        try:
            row = conn.execute(
                "SELECT raw_json FROM metadata_cache_entities WHERE source = 'beatport' "
                "AND entity_type = 'track' AND isrc = ? ORDER BY updated_at DESC LIMIT 1",
                (code,)).fetchone()
        finally:
            conn.close()
        if not row:
            return None
        data = json.loads(row[0])
        return data if isinstance(data, dict) and clean_isrc(data.get('isrc')) == code else None
    except Exception as e:  # noqa: BLE001 - a cache miss, not a failure
        logger.debug("beatport isrc cache lookup failed: %s", e)
        return None


def _base_title(title: str) -> str:
    """the title without its bracketed mix / feat parts, which beatport keeps
    in mix_name and would only narrow the search"""
    return re.sub(r'\s*[\(\[][^\)\]]*[\)\]]', '', title or '').strip() or (title or '')


def _throttle() -> None:
    global _last_search
    with _lock:
        wait = SEARCH_MIN_INTERVAL - (time.monotonic() - _last_search)
        if wait > 0:
            time.sleep(wait)
        _last_search = time.monotonic()


def search_by_isrc(isrc: str, artist: str, title: str, scraper: Any = None) -> Optional[Dict[str, Any]]:
    """beatport's track for this isrc, found through its search. None when
    beatport doesn't have it (remembered for a while) or didn't answer."""
    code = clean_isrc(isrc)
    if not code or not title:
        return None
    with _lock:
        missed = _misses.get(code)
    if missed and time.monotonic() - missed < MISS_TTL:
        return None

    from core.beatport.fetch import fetch_next_data, remember_tracks
    first_artist = re.split(r',|&| feat\.? | ft\.? | x ', artist or '', maxsplit=1)[0].strip()
    query = f"{first_artist} {_base_title(title)}".strip()
    _throttle()
    data = fetch_next_data(f"{BASE_URL}/search/tracks?q={quote(query)}", scraper)
    if data is None:
        return None
    results = search_results(data)
    # every result is worth keeping: the next lookup for any of them is free
    remember_tracks([r for r in results if r.get('isrc')])
    for track in results:
        if track.get('isrc') == code:
            return track
    with _lock:
        _misses[code] = time.monotonic()
    return None


def lookup(isrc: Any, artist: str = '', title: str = '', allow_search: bool = True,
           scraper: Any = None) -> Optional[Dict[str, Any]]:
    """the beatport track for this exact recording, or None."""
    code = clean_isrc(isrc)
    if not code:
        return None
    cached = find_cached_by_isrc(code)
    if cached:
        return cached
    if not allow_search:
        return None
    try:
        return search_by_isrc(code, artist, title, scraper)
    except Exception as e:  # noqa: BLE001 - enrichment must never break a download
        logger.debug("beatport search failed for %s: %s", code, e)
        return None


_KEY_RE = re.compile(r'^\s*([A-Ga-g])\s*([#♯b♭]?)\s*(major|minor|maj|min)?\s*$', re.I)


def standard_key(key: Any) -> str:
    """beatport's "F Minor" / "Eb Major" / "C♯ Minor" as the short form the
    TKEY / INITIALKEY tag uses: "Fm", "Eb", "C#m". '' when it isn't a key."""
    m = _KEY_RE.match(str(key or ''))
    if not m:
        return ''
    note = m.group(1).upper()
    accidental = {'♯': '#', '♭': 'b'}.get(m.group(2), m.group(2))
    accidental = '#' if accidental == '#' else ('b' if accidental.lower() == 'b' else '')
    minor = (m.group(3) or '').lower().startswith('min')
    return f"{note}{accidental}{'m' if minor else ''}"
