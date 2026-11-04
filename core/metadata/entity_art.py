"""art for cached entities that were stored without any.

the genre deep dive and the discover cache shelves read metadata_cache_entities,
and a lot of it has no image_url: on boulder's cache 36k of 55k deezer tracks,
and all 12k itunes artists (apple's search api never returns artist photos).
so the genre browser was a wall of 🎵 and 🎤.

the art was mostly there all along. every deezer track payload carries its
album cover's md5_image, and the cover url is built from it. an itunes artist
usually has a same-named twin from another source that does have a photo, and
an artist you own has your library's own thumbnail. this fills image_url from
those, on the way out, without touching what's stored.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional, Sequence

from utils.logging_config import get_logger

logger = get_logger("metadata.entity_art")

DEEZER_COVER = "https://cdn-images.dzcdn.net/images/cover/{md5}/500x500-000000-80-0-0.jpg"


def _usable(url: Any) -> Optional[str]:
    from core.metadata.artwork import usable_image_url
    return str(url) if url and usable_image_url(str(url)) else None


def art_from_raw(raw: Any) -> Optional[str]:
    """the best cover a provider payload carries, or None."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return None
    if not isinstance(raw, dict):
        return None
    album = raw.get('album') if isinstance(raw.get('album'), dict) else {}
    for holder in (raw, album):
        # deezer: sized covers, or the md5 the sized urls are built from
        for field in ('cover_xl', 'cover_big', 'cover_medium', 'picture_xl', 'picture_big'):
            url = _usable(holder.get(field))
            if url:
                return url
        md5 = holder.get('md5_image')
        if isinstance(md5, str) and md5 and md5 != 'd41d8cd98f00b204e9800998ecf8427e':
            return DEEZER_COVER.format(md5=md5)
        # spotify: images, biggest first
        images = holder.get('images')
        if isinstance(images, list) and images and isinstance(images[0], dict):
            url = _usable(images[0].get('url'))
            if url:
                return url
        # itunes: the 100px artwork, asked for at 600
        art = holder.get('artworkUrl100')
        if isinstance(art, str) and art:
            return _usable(art.replace('100x100bb', '600x600bb'))
    return None


def _chunks(items: Sequence[Any], n: int) -> Iterable[Sequence[Any]]:
    for i in range(0, len(items), n):
        yield items[i:i + n]


def fill_from_payloads(cursor, rows: List[Dict[str, Any]], entity_type: str) -> int:
    """rows without image_url get one from their stored payload. rows need
    'source' and 'entity_id'. returns how many were filled."""
    missing = [r for r in rows if not r.get('image_url') and r.get('entity_id')]
    if not missing:
        return 0
    ids = sorted({str(r['entity_id']) for r in missing})
    raw_by = {}
    for chunk in _chunks(ids, 900):
        cursor.execute(
            f"SELECT source, entity_id, raw_json FROM metadata_cache_entities "
            f"WHERE entity_type = ? AND entity_id IN ({','.join('?' * len(chunk))})",
            [entity_type, *chunk])
        for source, entity_id, raw in cursor.fetchall():
            raw_by[(source, str(entity_id))] = raw
    filled = 0
    for r in missing:
        url = art_from_raw(raw_by.get((r.get('source'), str(r['entity_id']))))
        if url:
            r['image_url'] = url
            filled += 1
    return filled


def fill_artist_photos(cursor, artists: List[Dict[str, Any]]) -> int:
    """artists without a photo: a same-named artist from another source that
    has one, else the library's own thumbnail for an artist you own."""
    missing = [a for a in artists if not a.get('image_url') and a.get('name')]
    if not missing:
        return 0
    names = sorted({a['name'].strip().lower() for a in missing})
    twin: Dict[str, str] = {}
    for chunk in _chunks(names, 900):
        cursor.execute(
            f"SELECT name, image_url FROM metadata_cache_entities WHERE entity_type = 'artist' "
            f"AND image_url IS NOT NULL AND image_url != '' "
            f"AND name COLLATE NOCASE IN ({','.join('?' * len(chunk))}) "
            f"ORDER BY COALESCE(followers, 0) DESC",
            list(chunk))
        for name, url in cursor.fetchall():
            key = (name or '').strip().lower()
            if key not in twin and _usable(url):
                twin[key] = url
    still = [n for n in names if n not in twin]
    library: Dict[str, str] = {}
    if still:
        from core.metadata import normalize_image_url
        for chunk in _chunks(still, 900):
            cursor.execute(
                f"SELECT name, image_url FROM lib2_artists WHERE image_url IS NOT NULL AND image_url != '' "
                f"AND name COLLATE NOCASE IN ({','.join('?' * len(chunk))})",
                list(chunk))
            for name, thumb in cursor.fetchall():
                key = (name or '').strip().lower()
                if key not in library:
                    url = normalize_image_url(thumb)
                    if url:
                        library[key] = url
    filled = 0
    for a in missing:
        key = a['name'].strip().lower()
        url = twin.get(key) or library.get(key)
        if url:
            a['image_url'] = url
            filled += 1
    return filled
