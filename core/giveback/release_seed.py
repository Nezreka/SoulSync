"""albums musicbrainz doesn't have, and musicbrainz's add release form filled
in for them (#2335).

an album soulsync identified on spotify, deezer, apple music, tidal, qobuz or
bandcamp, that the musicbrainz worker looked for and didn't find, can be
added by the person. soulsync never adds it: it opens musicbrainz's release
editor with the fields already filled in (release editor seeding,
https://musicbrainz.org/doc/Development/Release_Editor_Seeding), the person
checks it against the source, fixes what is wrong, and submits it as their
own edit. the source page goes in as an external link so other editors can
check it too.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from core.giveback import common

RELEASE_ADD_URL = 'https://musicbrainz.org/release/add'

# where each source's album page is, and the musicbrainz link type for it
# where it's certain (85: free streaming). the rest are left for the release
# editor, which recognises these sites and picks the type itself
_SOURCES: Tuple[Tuple[str, str, str, Optional[int]], ...] = (
    ('spotify', 'Spotify', 'https://open.spotify.com/album/{id}', 85),
    ('deezer', 'Deezer', 'https://www.deezer.com/album/{id}', 85),
    ('itunes', 'Apple Music', 'https://music.apple.com/album/{id}', None),
    ('tidal', 'Tidal', 'https://tidal.com/browse/album/{id}', None),
    ('qobuz', 'Qobuz', 'https://open.qobuz.com/album/{id}', None),
)

_TYPES = {
    'album': ('album',),
    'single': ('single',),
    'ep': ('ep',),
    'compilation': ('album', 'compilation'),
    'live': ('album', 'live'),
    'soundtrack': ('album', 'soundtrack'),
    'remix': ('album', 'remix'),
}


def _ids(raw: Any) -> Dict[str, str]:
    try:
        data = json.loads(raw or '{}') if not isinstance(raw, dict) else raw
    except (TypeError, ValueError):
        return {}
    return {str(k).lower(): str(v).strip() for k, v in (data or {}).items()
            if isinstance(v, (str, int)) and str(v).strip()} if isinstance(data, dict) else {}


def source_of(album: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """the first source the album was identified from: {'name', 'url',
    'link_type'}, or None"""
    ids = _ids(album.get('external_ids'))
    if album.get('spotify_id'):
        ids['spotify'] = str(album['spotify_id'])
    for key, name, pattern, link_type in _SOURCES:
        value = ids.get(key)
        if value and re.match(r'^[A-Za-z0-9_-]+$', value):
            return {'name': name, 'url': pattern.format(id=value), 'link_type': link_type}
    bandcamp = ids.get('bandcamp') or ids.get('bandcamp_url') or album.get('bandcamp_url')
    if bandcamp and re.match(r'^https://[a-z0-9-]+\.bandcamp\.com/album/', str(bandcamp)):
        return {'name': 'Bandcamp', 'url': str(bandcamp), 'link_type': None}
    return None


_MISSING_SQL = """
SELECT al.id, al.title, al.release_date, al.year, al.label, al.upc, al.album_type,
       al.spotify_id, al.external_ids, al.tracklist_json, al.track_count,
       ar.name AS artist, ar.musicbrainz_id AS artist_mbid
  FROM lib2_provider_attempts pa
  JOIN lib2_albums al ON al.id = pa.entity_id
  JOIN lib2_artists ar ON ar.id = al.primary_artist_id
 WHERE pa.entity_type = 'album' AND pa.service = 'musicbrainz' AND pa.status = 'not_found'
   AND COALESCE(al.musicbrainz_id, '') = ''
 ORDER BY al.id DESC
 LIMIT ?
"""

_COLS = ('id', 'title', 'release_date', 'year', 'label', 'upc', 'album_type', 'spotify_id',
         'external_ids', 'tracklist_json', 'track_count', 'artist', 'artist_mbid')


def _album_row(conn: Any, album_id: int) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT al.id, al.title, al.release_date, al.year, al.label, al.upc, al.album_type, "
        "al.spotify_id, al.external_ids, al.tracklist_json, al.track_count, "
        "ar.name AS artist, ar.musicbrainz_id AS artist_mbid "
        "FROM lib2_albums al JOIN lib2_artists ar ON ar.id = al.primary_artist_id "
        "WHERE al.id = ?", (int(album_id),)).fetchone()
    return dict(zip(_COLS, tuple(row), strict=True)) if row else None


def missing_releases(conn: Any, limit: int = 50) -> List[Dict[str, Any]]:
    """albums musicbrainz didn't have when soulsync looked, with a source"""
    out = []
    for row in conn.execute(_MISSING_SQL, (int(limit) * 4,)).fetchall():
        album = dict(zip(_COLS, tuple(row), strict=True))
        source = source_of(album)
        if not source:
            continue
        out.append({
            'id': album['id'], 'title': album['title'], 'artist': album['artist'],
            'year': album['year'] or (str(album['release_date'] or '')[:4] or None),
            'source': source['name'], 'source_url': source['url'],
        })
        if len(out) >= limit:
            break
    return out


def _tracks(conn: Any, album: Dict[str, Any]) -> List[Dict[str, Any]]:
    """the full track list the source gave, else the tracks soulsync has"""
    listed: List[Dict[str, Any]] = []
    try:
        parsed = json.loads(album.get('tracklist_json') or '[]')
    except (TypeError, ValueError):
        parsed = []
    for t in parsed if isinstance(parsed, list) else []:
        if isinstance(t, dict) and (t.get('title') or t.get('name')):
            artists = t.get('artists') if isinstance(t.get('artists'), list) else []
            listed.append({
                'title': str(t.get('title') or t.get('name')),
                'number': t.get('track_number'),
                'disc': t.get('disc_number') or 1,
                'length': t.get('duration_ms'),
                'artist': ', '.join(str(a.get('name') if isinstance(a, dict) else a)
                                    for a in artists if a) or '',
            })
    if listed:
        return listed
    rows = conn.execute(
        "SELECT title, track_number, disc_number, duration, track_artist FROM lib2_tracks "
        "WHERE album_id = ? ORDER BY COALESCE(disc_number, 1), track_number, id",
        (int(album['id']),)).fetchall()
    return [{'title': r[0], 'number': r[1], 'disc': r[2] or 1, 'length': r[3], 'artist': r[4] or ''}
            for r in rows]


def _int(value: Any) -> Optional[int]:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def seed_fields(conn: Any, album_id: int) -> Optional[Dict[str, Any]]:
    """{'action', 'fields': [[name, value], ...], 'source'} for the album, or
    None when it isn't one musicbrainz is missing that soulsync has a source for"""
    album = _album_row(conn, album_id)
    if not album:
        return None
    source = source_of(album)
    if not source:
        return None
    f: List[List[str]] = []

    def add(name: str, value: Any) -> None:
        if value is not None and str(value) != '':
            f.append([name, str(value)])

    add('name', album['title'])
    add('artist_credit.names.0.name', album['artist'])
    add('artist_credit.names.0.mbid', common.mbid(album.get('artist_mbid')) or None)
    for kind in _TYPES.get(str(album.get('album_type') or '').lower(), ('album',)):
        add('type', kind)
    add('status', 'official')
    date = str(album.get('release_date') or '')
    m = re.match(r'^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?', date)
    if m:
        add('events.0.date.year', m.group(1))
        add('events.0.date.month', int(m.group(2)) if m.group(2) else None)
        add('events.0.date.day', int(m.group(3)) if m.group(3) else None)
    elif _int(album.get('year')):
        add('events.0.date.year', album['year'])
    if album.get('label'):
        add('labels.0.name', album['label'])
    barcode = re.sub(r'\D', '', str(album.get('upc') or ''))
    if len(barcode) in (12, 13, 14):
        add('barcode', barcode)

    discs: Dict[int, List[Dict[str, Any]]] = {}
    for t in _tracks(conn, album):
        discs.setdefault(_int(t.get('disc')) or 1, []).append(t)
    for m_i, disc in enumerate(sorted(discs)):
        add(f'mediums.{m_i}.format', 'Digital Media')
        tracks = sorted(discs[disc], key=lambda t: _int(t.get('number')) or 10**6)
        for t_i, t in enumerate(tracks):
            pre = f'mediums.{m_i}.track.{t_i}'
            add(f'{pre}.name', t['title'])
            add(f'{pre}.number', _int(t.get('number')) or t_i + 1)
            # milliseconds, which the release editor takes as a plain integer
            add(f'{pre}.length', _int(t.get('length')))
            artist = str(t.get('artist') or '').strip()
            if artist and artist.lower() != str(album['artist'] or '').lower():
                add(f'{pre}.artist_credit.names.0.name', artist)

    add('urls.0.url', source['url'])
    add('urls.0.link_type', source['link_type'])
    add('edit_note', (f"Seeded by SoulSync (https://github.com/Nezreka/SoulSync) from "
                      f"{source['name']}: {source['url']} . The title, track list, lengths "
                      "and date come from that page."))
    return {'action': RELEASE_ADD_URL, 'fields': f, 'source': source['url']}
