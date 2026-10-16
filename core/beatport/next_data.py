"""parse the ``__NEXT_DATA__`` json out of a beatport page.

every list page (home, top 100, hype 100, genre, chart, release) carries its
tracks in ``props.pageProps.dehydratedState.queries``, one query per list,
each named by its ``queryKey``. queries are found by that key, never by
position: beatport puts more or fewer lists on a page from week to week, and
the old scraper's "first 10 are the top 10, the rest are hype" broke whenever
it did.

every list track has the same shape everywhere, so one normaliser covers all
of them. the track page itself is the odd one out (``track_id`` /
``track_name``), it gets its own.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

BASE_URL = "https://www.beatport.com"

_NEXT_DATA_RE = re.compile(
    r'<script[^>]*id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', re.S)

# list kinds and how their query key reads. hype lists carry hype=true in the
# key, the plain ones either leave it out or say hype=false.
LIST_KINDS = ('top-10', 'hype-10', 'top-100', 'hype-100', 'chart', 'release', 'tracks')


def extract_next_data(html: Any) -> Optional[Dict[str, Any]]:
    """the page's next.js json, or None when it has none (a cloudflare page,
    a redesign, an error page)."""
    if isinstance(html, bytes):
        html = html.decode('utf-8', errors='replace')
    if not isinstance(html, str) or not html:
        return None
    m = _NEXT_DATA_RE.search(html)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _key_text(query_key: Any) -> str:
    """a queryKey as one string. keys are lists of strings and dicts, e.g.
    ['genre-7-top-10-tracks-per_page=10&hype=true'] or
    ['tracks', {'release_id': 7473727, 'per_page': 100}]"""
    if not isinstance(query_key, list):
        query_key = [query_key]
    parts = []
    for part in query_key:
        if isinstance(part, dict):
            parts.append(json.dumps(part, sort_keys=True))
        else:
            parts.append(str(part))
    return ' '.join(parts)


def queries(next_data: Any) -> List[Tuple[str, Any]]:
    """(key text, data) for every query on the page."""
    try:
        raw = next_data['props']['pageProps']['dehydratedState']['queries']
    except (KeyError, TypeError):
        return []
    out = []
    for q in raw if isinstance(raw, list) else []:
        if not isinstance(q, dict):
            continue
        state = q.get('state') if isinstance(q.get('state'), dict) else {}
        out.append((_key_text(q.get('queryKey')), state.get('data')))
    return out


def _results(data: Any) -> Optional[list]:
    if isinstance(data, dict) and isinstance(data.get('results'), list):
        return data['results']
    return None


def _is_hype(key: str) -> bool:
    return 'hype=true' in key


def _list_matcher(kind: str) -> Callable[[str], bool]:
    if kind == 'top-10':
        return lambda k: 'top-10-tracks' in k and not _is_hype(k)
    if kind == 'hype-10':
        return lambda k: 'top-10-tracks' in k and _is_hype(k)
    if kind == 'top-100':
        return lambda k: 'top-100-tracks' in k and not _is_hype(k)
    if kind == 'hype-100':
        return lambda k: 'top-100-tracks' in k and _is_hype(k)
    if kind == 'chart':
        return lambda k: k.startswith('chart-') and '-tracks' in k
    if kind == 'release':
        return lambda k: k.startswith('tracks ') and '"release_id"' in k
    if kind == 'tracks':
        # a genre's plain track browser
        return lambda k: k.startswith('tracks ') and '"release_id"' not in k
    raise ValueError(f"unknown beatport list kind: {kind}")


def raw_list(next_data: Any, kind: str) -> Optional[list]:
    """the raw track objects of one list on the page, or None when the page
    doesn't have that list. an empty list means the list is there and empty."""
    match = _list_matcher(kind)
    for key, data in queries(next_data):
        results = _results(data)
        if results is not None and match(key):
            return results
    return None


def _name(obj: Any) -> str:
    if isinstance(obj, dict):
        return str(obj.get('name') or '').strip()
    return str(obj or '').strip()


def _image(image: Any, size: str = '1400x1400', prefer_size: bool = False) -> str:
    """an image object's url. dynamic_uri is a {w}x{h} template; prefer_size
    takes it at ``size`` over the fixed uri (a 1400px cover is a lot for a card)."""
    if isinstance(image, str):
        return image.replace('{w}x{h}', size)
    if not isinstance(image, dict):
        return ''
    if prefer_size and image.get('dynamic_uri'):
        return str(image['dynamic_uri']).replace('{w}x{h}', size)
    uri = image.get('uri') or ''
    if not uri and image.get('dynamic_uri'):
        uri = str(image['dynamic_uri']).replace('{w}x{h}', size)
    return str(uri or '')


def track_url(slug: Any, track_id: Any) -> str:
    if not track_id:
        return ''
    return f"{BASE_URL}/track/{slug or 'track'}/{track_id}"


def track_key(beatport_id: Any) -> str:
    """the id a beatport track goes by everywhere downstream (sync cache,
    wishlist, mirrors). the real beatport id, so two charts can never share
    one the way beatport_chart_<position> did."""
    text = str(beatport_id or '').strip()
    return f"beatport_{text}" if text else ''


def beatport_id_from_url(url: Any) -> str:
    """the numeric id at the end of /track/<slug>/<id>, or ''."""
    m = re.search(r'/track/[^/]+/(\d+)', str(url or ''))
    return m.group(1) if m else ''


def _artists(raw: Any) -> List[Dict[str, Any]]:
    out = []
    for a in raw if isinstance(raw, list) else []:
        name = _name(a)
        if not name:
            continue
        entry = {'name': name}
        if isinstance(a, dict):
            if a.get('id'):
                entry['id'] = a['id']
            if a.get('slug') and a.get('id'):
                entry['url'] = f"{BASE_URL}/artist/{a['slug']}/{a['id']}"
        out.append(entry)
    return out


def normalize_track(raw: Any, position: int, list_name: str = '') -> Optional[Dict[str, Any]]:
    """one list track in the shape the rest of soulsync reads.

    keeps every key the old scraper produced (title, artist, mix_name, label,
    release_*, duration in seconds) so nothing downstream has to change, and
    adds what the old one never had: the real id, isrc, artists as a list,
    remixers, duration_ms."""
    if not isinstance(raw, dict):
        return None
    beatport_id = raw.get('id')
    title = str(raw.get('name') or '').strip()
    if not beatport_id or not title:
        return None

    artists = _artists(raw.get('artists'))
    release = raw.get('release') if isinstance(raw.get('release'), dict) else {}
    label = release.get('label') if isinstance(release.get('label'), dict) else {}
    genre = raw.get('genre') if isinstance(raw.get('genre'), dict) else {}
    sub_genre = raw.get('sub_genre') if isinstance(raw.get('sub_genre'), dict) else {}
    length_ms = raw.get('length_ms') or 0
    try:
        length_ms = int(length_ms)
    except (TypeError, ValueError):
        length_ms = 0
    release_image = _image(release.get('image'))
    release_id = release.get('id')

    return {
        'position': position,
        'rank': position,
        'beatport_id': beatport_id,
        'id': track_key(beatport_id),
        'title': title,
        'mix_name': str(raw.get('mix_name') or '').strip(),
        'artist': ', '.join(a['name'] for a in artists) or 'Unknown Artist',
        'artists': artists,
        'remixers': [a['name'] for a in _artists(raw.get('remixers'))],
        'list_name': list_name,
        'url': track_url(raw.get('slug'), beatport_id),
        'label': _name(label) or 'Unknown Label',
        'bpm': raw.get('bpm'),
        'key': _name(raw.get('key')) or None,
        'genre': _name(genre) or None,
        'sub_genre': _name(sub_genre) or None,
        'duration': length_ms // 1000,
        'duration_ms': length_ms,
        'isrc': str(raw.get('isrc') or '').strip().upper(),
        'catalog_number': str(raw.get('catalog_number') or '').strip(),
        'type': 'track',
        'release_name': _name(release),
        'release_id': str(release_id) if release_id else '',
        'release_url': (f"{BASE_URL}/release/{release.get('slug') or 'release'}/{release_id}"
                        if release_id else ''),
        'release_image': release_image,
        'release_date': str(raw.get('publish_date') or raw.get('new_release_date') or ''),
        'artwork_url': release_image,
        'source': 'next_data',
    }


def normalize_tracks(raw_tracks: Any, list_name: str = '', limit: Optional[int] = None) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for raw in raw_tracks if isinstance(raw_tracks, list) else []:
        track = normalize_track(raw, len(out) + 1, list_name)
        if track:
            out.append(track)
        if limit is not None and len(out) >= limit:
            break
    return out


def list_tracks(next_data: Any, kind: str, list_name: str = '',
                limit: Optional[int] = None) -> Optional[List[Dict[str, Any]]]:
    """the normalised tracks of one list, or None when the page lacks it."""
    raw = raw_list(next_data, kind)
    if raw is None:
        return None
    return normalize_tracks(raw, list_name, limit)


def track_details(next_data: Any) -> Optional[Dict[str, Any]]:
    """the track a /track/<slug>/<id> page is about, normalised like a list
    track. the page's other queries are "charts with this track" and
    "recommendations": never the track, which is exactly what the old
    enrichment picked up instead."""
    for key, data in queries(next_data):
        if not key.startswith('track-details-') or not isinstance(data, dict):
            continue
        track_id = data.get('track_id')
        if not track_id:
            continue
        release = data.get('release') if isinstance(data.get('release'), dict) else {}
        # reshape into the list shape so there is one normaliser to trust
        raw = {
            'id': track_id,
            'name': data.get('track_name'),
            'mix_name': data.get('mix_name'),
            'slug': data.get('slug') or '',
            'artists': data.get('artists'),
            'remixers': data.get('remixers'),
            'length_ms': data.get('track_length_ms'),
            'bpm': data.get('bpm'),
            'key': data.get('key'),
            'isrc': data.get('isrc'),
            'genre': data.get('genre'),
            'sub_genre': (data.get('genre') or {}).get('sub_genre') if isinstance(data.get('genre'), dict) else None,
            'catalog_number': release.get('catalog_number'),
            'publish_date': str(release.get('release_date') or '')[:10],
            'release': {
                'id': release.get('id'),
                'name': release.get('name'),
                'slug': release.get('slug'),
                'image': _image(release.get('image_url')),
                'label': data.get('label'),
            },
        }
        track = normalize_track(raw, int(data.get('track_number') or 1))
        if track and not raw['slug']:
            track['url'] = ''
        return track
    return None


def chart_meta(next_data: Any) -> Optional[Dict[str, Any]]:
    """name, image and owner of a /chart/<slug>/<id> page."""
    for _key, data in queries(next_data):
        if isinstance(data, dict) and 'track_count' in data and 'person' in data and data.get('id'):
            person = data.get('person') if isinstance(data.get('person'), dict) else {}
            return {
                'id': data['id'],
                'name': str(data.get('name') or '').strip(),
                'slug': data.get('slug') or '',
                'image': _image(data.get('image')),
                'track_count': data.get('track_count') or 0,
                'creator': str(person.get('owner_name') or _name(data.get('artist')) or '').strip(),
                'url': f"{BASE_URL}/chart/{data.get('slug') or 'chart'}/{data['id']}",
            }
    return None


def release_meta(next_data: Any) -> Optional[Dict[str, Any]]:
    """a /release/<slug>/<id> page's release: name, artists, label, art."""
    for key, data in queries(next_data):
        if (isinstance(data, dict) and key.startswith('release-') and data.get('id')
                and 'catalog_number' in data):
            return {
                'id': data['id'],
                'name': str(data.get('name') or '').strip(),
                'slug': data.get('slug') or '',
                'artists': _artists(data.get('artists')),
                'remixers': [a['name'] for a in _artists(data.get('remixers'))],
                'label': _name(data.get('label')),
                'image': _image(data.get('image')),
                'release_date': str(data.get('new_release_date') or data.get('publish_date') or ''),
                'catalog_number': str(data.get('catalog_number') or ''),
                'upc': str(data.get('upc') or ''),
                'type': _name(data.get('type')).lower(),
                'track_count': data.get('track_count') or 0,
                'url': f"{BASE_URL}/release/{data.get('slug') or 'release'}/{data['id']}",
            }
    return None


# -- browse sections (page modules) ------------------------------------------
#
# a page's curated rows (hype picks, new releases, the hero slideshow, featured
# and dj charts) are "page modules". their ids change every week, their TYPE
# names don't, so a section is found by type. the html scrape for these had
# gone quiet: new releases, both chart rows and the genre hero all came back
# empty, and hero titles were glued to the artist ("Jinx (Club Mix)Imbermind").

def page_modules(next_data: Any) -> List[Tuple[str, list]]:
    """(module type name, its items) for every module on the page, in page order."""
    types: Dict[str, str] = {}
    order: List[str] = []
    items: Dict[str, list] = {}
    for key, data in queries(next_data):
        if key.startswith('page-modules') and isinstance(data, dict):
            for m in data.get('results') or []:
                if isinstance(m, dict) and m.get('id') is not None:
                    mid = str(m['id'])
                    types[mid] = _name(m.get('type'))
                    order.append(mid)
        m = re.match(r'^page-module-(\d+)-items', key)
        if m and isinstance(data, dict):
            items[m.group(1)] = data.get('results') or []
    return [(types[mid], items[mid]) for mid in order if mid in items]


def module_items(next_data: Any, type_name: str) -> Optional[list]:
    """the raw items of the first module of this type, or None."""
    for mtype, items in page_modules(next_data):
        if mtype == type_name:
            return items
    return None


def normalize_release(raw: Any, rank: Optional[int] = None,
                      image_override: str = '') -> Optional[Dict[str, Any]]:
    """a release object as the browse cards read it (title, artist, label,
    url, image_url), plus the ids the release download needs."""
    if not isinstance(raw, dict) or not raw.get('id') or not raw.get('name'):
        return None
    artists = _artists(raw.get('artists'))
    names = ', '.join(a['name'] for a in artists)
    out = {
        'title': str(raw['name']).strip(),
        'artist': names or 'Various Artists',
        'artists_string': names or 'Various Artists',
        'label': _name(raw.get('label')) or 'Unknown Label',
        'url': f"{BASE_URL}/release/{raw.get('slug') or 'release'}/{raw['id']}",
        'image_url': image_override or _image(raw.get('image'), '500x500', prefer_size=True),
        'release_id': raw['id'],
        'catalog_number': str(raw.get('catalog_number') or ''),
        'release_date': str(raw.get('new_release_date') or raw.get('publish_date') or ''),
        'type': 'release',
        'source': 'next_data',
    }
    if rank is not None:
        out['rank'] = rank
    return out


def normalize_chart(raw: Any) -> Optional[Dict[str, Any]]:
    """a chart object as the chart cards read it (name, creator, url, image)."""
    if not isinstance(raw, dict) or not raw.get('id') or not raw.get('name'):
        return None
    person = raw.get('person') if isinstance(raw.get('person'), dict) else {}
    return {
        'name': str(raw['name']).strip(),
        'creator': str(person.get('owner_name') or _name(raw.get('artist')) or 'Beatport').strip(),
        'url': f"{BASE_URL}/chart/{raw.get('slug') or 'chart'}/{raw['id']}",
        'image': _image(raw.get('image'), '500x500', prefer_size=True),
        'track_count': raw.get('track_count') or 0,
        'source': 'next_data',
    }


def module_releases(next_data: Any, type_name: str, limit: Optional[int] = None,
                    banner_art: bool = False) -> Optional[List[Dict[str, Any]]]:
    """a release module as cards. banner_art takes the module item's own image
    (the wide slideshow art) over the release cover."""
    items = module_items(next_data, type_name)
    if items is None:
        return None
    out: List[Dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        art = _image(it.get('image'), '920x642') if banner_art else ''
        release = normalize_release(it.get('item'), image_override=art)
        if release:
            out.append(release)
        if limit and len(out) >= limit:
            break
    return out


def module_charts(next_data: Any, type_name: str, limit: Optional[int] = None) -> Optional[List[Dict[str, Any]]]:
    items = module_items(next_data, type_name)
    if items is None:
        return None
    out: List[Dict[str, Any]] = []
    for it in items:
        chart = normalize_chart(it.get('item') if isinstance(it, dict) else None)
        if chart:
            out.append(chart)
        if limit and len(out) >= limit:
            break
    return out


def top_releases(next_data: Any, limit: int = 10) -> Optional[List[Dict[str, Any]]]:
    """the page's top 10 releases query (homepage or genre), ranked."""
    for key, data in queries(next_data):
        if 'top-10-releases' in key:
            results = _results(data)
            if results is None:
                continue
            out = []
            for raw in results:
                rel = normalize_release(raw, rank=len(out) + 1)
                if rel:
                    out.append(rel)
                if len(out) >= limit:
                    break
            return out
    return None
