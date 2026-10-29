"""Direct lookups for a pasted id or link, per service (#1901).

Ktzenjammer: with several artists called "1905" the search shows a wall of
look-alikes and no way to tell which is which, while the right one's id is
sitting in the service's own address bar. ``direct_id.parse_direct_ref`` spots
the id; these confirm it by fetching that exact entity and hand back one row in
the search's own shape, so the modal shows it for a click like any other hit.

Every lookup answers a list: one row when the id resolved, ``[]`` when it
didn't (or the link points at something this kind of match can't use), and
the caller then runs the normal search. Nothing here raises on a bad id.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import unquote_plus

logger = logging.getLogger(__name__)

PREFIX = 'Direct ID match'


def _row(entity_id: Any, name: str, image: Optional[str], *bits: str, **more) -> Dict[str, Any]:
    detail = ' · '.join(b for b in bits if b)
    row = {
        'id': str(entity_id),
        'name': name or '',
        'image': image or None,
        'extra': f"{PREFIX}{' · ' + detail if detail else ''}",
    }
    row.update(more)
    return row


def _image(d: Dict[str, Any]) -> Optional[str]:
    images = d.get('images')
    if isinstance(images, list) and images:
        first = images[0]
        if isinstance(first, dict) and first.get('url'):
            return first['url']
        if isinstance(first, str):
            return first
    for key in ('image_url', 'image', 'thumb', 'cover_url'):
        value = d.get(key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, dict):
            for size in ('large', 'medium', 'small'):
                if value.get(size):
                    return value[size]
    return None


def _artists(d: Dict[str, Any]) -> str:
    artists = d.get('artists')
    if isinstance(artists, list):
        names = [a.get('name', '') if isinstance(a, dict) else str(a) for a in artists]
        return ', '.join(n for n in names if n)
    for key in ('artist', 'performer'):
        value = d.get(key)
        if isinstance(value, dict):
            return value.get('name', '') or ''
        if isinstance(value, str):
            return value
    return ''


def _album_of(d: Dict[str, Any]) -> Dict[str, Any]:
    album = d.get('album')
    return album if isinstance(album, dict) else {}


def _safe(call: Callable[[], Any]) -> Any:
    try:
        return call()
    except Exception as e:  # noqa: BLE001 - a bad id is a miss, never an error
        logger.debug("direct lookup failed: %s", e)
        return None


def _spotify_shaped(entity_type: str, kind: Optional[str], ref_id: str,
                    get_artist, get_album, get_track, **more) -> List[Dict[str, Any]]:
    """Lookups for the clients that answer Spotify-shaped dicts (Spotify, iTunes,
    JioSaavn, Amazon). A track link while matching an album resolves to that
    track's album, the way Deezer's already did."""
    kind = kind or entity_type
    if entity_type == 'artist':
        if kind != 'artist' or get_artist is None:
            return []
        a = _safe(lambda: get_artist(ref_id))
        if not isinstance(a, dict) or not a.get('name'):
            return []
        genres = a.get('genres') or []
        return [_row(a.get('id') or ref_id, a['name'], _image(a),
                     ', '.join(genres[:3]) if isinstance(genres, list) else '', **more)]

    if entity_type == 'album':
        album_id = ref_id
        if kind == 'track':
            t = _safe(lambda: get_track(ref_id)) if get_track else None
            album_id = _album_of(t).get('id') if isinstance(t, dict) else None
            if not album_id:
                return []
        elif kind != 'album':
            return []
        al = _safe(lambda: get_album(album_id)) if get_album else None
        if not isinstance(al, dict) or not (al.get('name') or al.get('title')):
            return []
        return [_row(al.get('id') or album_id, al.get('name') or al.get('title'), _image(al),
                     _artists(al), str(al.get('release_date') or '')[:4], **more)]

    if entity_type == 'track':
        if kind != 'track' or get_track is None:
            return []
        t = _safe(lambda: get_track(ref_id))
        if not isinstance(t, dict) or not (t.get('name') or t.get('title')):
            return []
        album = _album_of(t)
        return [_row(t.get('id') or ref_id, t.get('name') or t.get('title'),
                     _image(t) or _image(album), _artists(t),
                     album.get('name') or album.get('title') or '', **more)]
    return []


def spotify(client, entity_type, kind, ref_id):
    # allow_fallback=False: a Spotify id must come back as Spotify, never as the
    # fallback source's guess, or a numeric id gets stored in the Spotify column
    return _spotify_shaped(
        entity_type, kind, ref_id,
        lambda i: client.get_artist(i, allow_fallback=False),
        lambda i: client.get_album(i, allow_fallback=False),
        lambda i: client.get_track_details(i, allow_fallback=False),
        provider='spotify',
    )


def itunes(client, entity_type, kind, ref_id):
    return _spotify_shaped(
        entity_type, kind, ref_id, client.get_artist,
        lambda i: client.get_album(i, include_tracks=False), client.get_track_details,
    )


def jiosaavn(client, entity_type, kind, ref_id):
    return _spotify_shaped(
        entity_type, kind, ref_id, client.get_artist, client.get_album, client.get_track_details,
    )


def amazon(client, entity_type, kind, ref_id):
    # amazon's get_artist takes a name, so there's no artist id to look up
    return _spotify_shaped(
        entity_type, kind, ref_id, None,
        lambda i: client.get_album(i, include_tracks=False), client.get_track_details,
    )


def discogs(client, entity_type, kind, ref_id):
    if entity_type == 'artist':
        if (kind or 'artist') != 'artist':
            return []
        a = _safe(lambda: client.get_artist(ref_id))
        if not isinstance(a, dict) or not a.get('name'):
            return []
        return [_row(a.get('id') or ref_id, a['name'], _image(a))]
    if entity_type == 'album':
        if (kind or 'album') != 'album':
            return []
        al = _safe(lambda: client.get_album(ref_id, include_tracks=False))
        if not isinstance(al, dict) or not al.get('name'):
            return []
        # keep the tagged id (m123 / r123): it's what says master or release
        return [_row(ref_id, al['name'], _image(al), _artists(al),
                     str(al.get('release_date') or '')[:4])]
    return []  # discogs tracks have no id of their own


def tidal(client, entity_type, kind, ref_id):
    kind = kind or entity_type
    if kind != entity_type:
        return []
    getter = {'artist': client.get_artist, 'album': client.get_album,
              'track': client.get_track}.get(entity_type)
    d = _safe(lambda: getter(ref_id)) if getter else None
    if not isinstance(d, dict):
        return []
    name = d.get('name') or d.get('title')
    if not name:
        return []
    return [_row(d.get('id') or ref_id, name, _image(d), _artists(d))]


def qobuz(client, entity_type, kind, ref_id):
    kind = kind or entity_type
    if kind != entity_type:
        return []
    getter = {'artist': client.get_artist, 'album': client.get_album,
              'track': client.get_track}.get(entity_type)
    d = _safe(lambda: getter(ref_id)) if getter else None
    if not isinstance(d, dict):
        return []
    name = d.get('name') or d.get('title')
    if not name:
        return []
    album = _album_of(d)
    return [_row(d.get('id') or ref_id, name, _image(d) or _image(album), _artists(d),
                 album.get('title', '') if entity_type == 'track' else '')]


def audiodb(client, entity_type, kind, ref_id):
    kind = kind or entity_type
    if kind != entity_type:
        return []
    if entity_type == 'artist':
        a = _safe(lambda: client.lookup_artist_by_id(ref_id))
        if isinstance(a, dict) and a.get('strArtist'):
            return [_row(a.get('idArtist') or ref_id, a['strArtist'], a.get('strArtistThumb'),
                         a.get('strGenre') or '')]
    elif entity_type == 'album':
        a = _safe(lambda: client.lookup_album_by_id(ref_id))
        if isinstance(a, dict) and a.get('strAlbum'):
            return [_row(a.get('idAlbum') or ref_id, a['strAlbum'], a.get('strAlbumThumb'),
                         a.get('strArtist') or '', str(a.get('intYearReleased') or ''))]
    elif entity_type == 'track':
        t = _safe(lambda: client.lookup_track_by_id(ref_id))
        if isinstance(t, dict) and t.get('strTrack'):
            return [_row(t.get('idTrack') or ref_id, t['strTrack'], None,
                         t.get('strArtist') or '', t.get('strAlbum') or '')]
    return []


def genius(client, entity_type, kind, ref_id):
    if entity_type == 'artist':
        a = _safe(lambda: client.get_artist(int(ref_id)))
        if isinstance(a, dict) and a.get('name'):
            return [_row(a.get('id') or ref_id, a['name'], a.get('image_url'))]
    elif entity_type == 'track':
        s = _safe(lambda: client.get_song(int(ref_id)))
        if isinstance(s, dict) and s.get('title'):
            return [_row(s.get('id') or ref_id, s['title'], s.get('song_art_image_url'),
                         s.get('artist_names') or '')]
    return []


def lastfm(client, entity_type, kind, ref_id):
    """The stored last.fm id IS the url; the lookup confirms the page exists and
    stores last.fm's own spelling of it, the one enrichment writes."""
    if kind != entity_type:
        return []
    parts = ref_id.split('/music/', 1)[1].split('/')
    names = [unquote_plus(p) for p in parts if p and p != '_']
    if entity_type == 'artist' and names:
        info = _safe(lambda: client.get_artist_info(names[0]))
        if isinstance(info, dict) and info.get('name'):
            return [_row(info.get('url') or ref_id, info['name'],
                         client.get_best_image(info.get('image', [])))]
    elif entity_type == 'album' and len(names) >= 2:
        info = _safe(lambda: client.get_album_info(names[0], names[1]))
        if isinstance(info, dict) and info.get('name'):
            return [_row(info.get('url') or ref_id, info['name'],
                         client.get_best_image(info.get('image', [])),
                         info.get('artist') or names[0])]
    elif entity_type == 'track' and len(names) >= 2:
        info = _safe(lambda: client.get_track_info(names[0], names[1]))
        if isinstance(info, dict) and info.get('name'):
            artist = info.get('artist')
            artist = artist.get('name', '') if isinstance(artist, dict) else (artist or names[0])
            return [_row(info.get('url') or ref_id, info['name'], None, artist)]
    return []


def bandcamp(client, entity_type, kind, ref_id):
    """Bandcamp is addressed by release url; that url is what gets stored."""
    if entity_type not in ('album', 'track') or kind != entity_type:
        return []
    meta = _safe(lambda: client.get_release_metadata(ref_id))
    if not isinstance(meta, dict) or not meta.get('title'):
        return []
    return [_row(ref_id, meta['title'], meta.get('image_url'), meta.get('artist') or '')]


LOOKUPS = {
    'spotify': spotify, 'itunes': itunes, 'jiosaavn': jiosaavn, 'amazon': amazon,
    'discogs': discogs, 'tidal': tidal, 'qobuz': qobuz, 'audiodb': audiodb,
    'genius': genius, 'lastfm': lastfm, 'bandcamp': bandcamp,
}
