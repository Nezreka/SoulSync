"""HD artist artwork for the media server: a square photo and a wide background.

Plex shows an artist with a square photo (its "thumb") and, behind the page in
Plexamp and the web app, a wide background (its "art"). Jellyfin has the same
pair as Primary and Backdrop. Spotify only has a 640px square, and nothing
SoulSync pushed ever filled the background (#1957).

Sources, best first, each tied to the artist by an id so a namesake's face
never lands on the wrong page:

- fanart.tv, by MusicBrainz id: 1000px photos and 1920x1080 backgrounds made
  for exactly this (the source Kodi and Lidarr use). Needs a free key.
- TheAudioDB, by MusicBrainz id (by name only when the name is an exact match):
  photos, fanart and wide thumbs.
- Deezer, by the Deezer id enrichment stored: a 1000px photo.

Each picture is checked after download: a photo must be near square, a
background must be landscape, and both must be big enough to be an upgrade.
"""

from __future__ import annotations

import io
from typing import Any, Dict, List, Optional, Tuple

import requests

from utils.logging_config import get_logger

logger = get_logger("metadata.artist_artwork")

FANART_URL = "https://webservice.fanart.tv/v3/music/{mbid}"
DEEZER_ARTIST = "https://api.deezer.com/artist/{id}"

MIN_PHOTO_PX = 500
MIN_BACKGROUND_WIDTH = 1000
TIMEOUT = 15


def fanart_api_key() -> str:
    """the music side's fanart.tv key, else the one the video side already has."""
    try:
        from core.settings import config_manager
        key = str(config_manager.get('fanart.api_key', '') or '').strip()
        if key:
            return key
    except Exception as e:  # noqa: BLE001 - fall back to the video side's key
        logger.debug("fanart.tv key unreadable from config: %s", e)
    try:
        from api.video import get_video_db
        return str(get_video_db().get_setting('fanart_api_key') or '').strip()
    except Exception:  # noqa: BLE001 - no video side, no key
        return ''


def _by_likes(images: Any) -> List[str]:
    rows = [i for i in images or [] if isinstance(i, dict) and i.get('url')]
    rows.sort(key=lambda i: -int(i.get('likes') or 0))
    return [str(i['url']) for i in rows]


def fanart_images(mbid: str, api_key: str) -> Dict[str, List[str]]:
    """{'photos': [...], 'backgrounds': [...]} from fanart.tv, most liked first."""
    if not mbid or not api_key:
        return {'photos': [], 'backgrounds': []}
    try:
        response = requests.get(FANART_URL.format(mbid=mbid), params={'api_key': api_key},
                                timeout=TIMEOUT)
        if response.status_code == 404:
            return {'photos': [], 'backgrounds': []}
        response.raise_for_status()
        data = response.json() or {}
    except Exception as e:  # noqa: BLE001 - one source down leaves the others
        logger.debug("fanart.tv lookup failed for %s: %s", mbid, e)
        return {'photos': [], 'backgrounds': []}
    return {'photos': _by_likes(data.get('artistthumb')),
            'backgrounds': _by_likes(data.get('artistbackground'))}


def _norm(name: Any) -> str:
    return ''.join(ch for ch in str(name or '').casefold() if ch.isalnum())


def audiodb_images(name: str, mbid: str) -> Dict[str, List[str]]:
    """photos and backgrounds from TheAudioDB, by MusicBrainz id first. a name
    search only counts when it found exactly this name."""
    data = None
    try:
        from core.audiodb_client import AudioDBClient
        client = AudioDBClient()
        if mbid:
            data = client.lookup_artist_by_mbid(mbid)
        if not data and name:
            found = client.search_artist(name)
            if found and _norm(found.get('strArtist')) == _norm(name) and (
                    not mbid or not found.get('strMusicBrainzID')
                    or found.get('strMusicBrainzID') == mbid):
                data = found
    except Exception as e:  # noqa: BLE001
        logger.debug("TheAudioDB lookup failed for %r: %s", name, e)
    if not data:
        return {'photos': [], 'backgrounds': []}
    photos = [data.get('strArtistThumb')]
    backgrounds = [data.get('strArtistFanart'), data.get('strArtistFanart2'),
                   data.get('strArtistFanart3'), data.get('strArtistWideThumb')]
    return {'photos': [u for u in photos if u], 'backgrounds': [u for u in backgrounds if u]}


def deezer_photo(deezer_id: str) -> List[str]:
    if not deezer_id:
        return []
    try:
        response = requests.get(DEEZER_ARTIST.format(id=deezer_id), timeout=TIMEOUT)
        response.raise_for_status()
        data = response.json() or {}
    except Exception as e:  # noqa: BLE001
        logger.debug("Deezer artist lookup failed for %s: %s", deezer_id, e)
        return []
    url = data.get('picture_xl') or data.get('picture_big')
    # deezer's stand-in for "no photo" is an empty-hash image path
    return [url] if url and '/artist//' not in url else []


def candidates(name: str, *, mbid: str = '', deezer_id: str = '',
               fallback_photo: str = '') -> Dict[str, List[Tuple[str, str]]]:
    """every photo and background worth trying, best first, as (url, source)."""
    photos: List[Tuple[str, str]] = []
    backgrounds: List[Tuple[str, str]] = []
    fan = fanart_images(mbid, fanart_api_key())
    photos += [(u, 'fanart.tv') for u in fan['photos']]
    backgrounds += [(u, 'fanart.tv') for u in fan['backgrounds']]
    adb = audiodb_images(name, mbid)
    photos += [(u, 'TheAudioDB') for u in adb['photos']]
    backgrounds += [(u, 'TheAudioDB') for u in adb['backgrounds']]
    photos += [(u, 'Deezer') for u in deezer_photo(deezer_id)]
    if fallback_photo:
        photos.append((fallback_photo, 'Spotify'))
    return {'photos': photos, 'backgrounds': backgrounds}


def _size(data: bytes) -> Optional[Tuple[int, int]]:
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as image:
            return image.size
    except Exception:  # noqa: BLE001 - not an image
        return None


def fits_photo(data: bytes) -> bool:
    size = _size(data)
    if not size:
        return False
    w, h = size
    return min(w, h) >= MIN_PHOTO_PX and 0.75 <= w / h <= 1.34


def fits_background(data: bytes) -> bool:
    size = _size(data)
    if not size:
        return False
    w, h = size
    return w >= MIN_BACKGROUND_WIDTH and w >= 1.3 * h


def as_jpeg(data: bytes) -> bytes:
    """JPEG for the media server (fanart.tv serves some as PNG)."""
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as image:
            if image.format == 'JPEG':
                return data
            out = io.BytesIO()
            image.convert('RGB').save(out, format='JPEG', quality=92)
            return out.getvalue()
    except Exception:  # noqa: BLE001
        return data


def first_that_fits(options: List[Tuple[str, str]], check) -> Optional[Tuple[bytes, str]]:
    """download the options in order and return the first that passes ``check``
    as (jpeg bytes, source)."""
    for url, source in options:
        try:
            response = requests.get(url, timeout=TIMEOUT)
            response.raise_for_status()
        except Exception as e:  # noqa: BLE001 - try the next one
            logger.debug("artwork download failed (%s): %s", url, e)
            continue
        if check(response.content):
            return as_jpeg(response.content), source
    return None


# Radiohead: always on fanart.tv, so a failure means the key, not the artist
_CHECK_MBID = "a74b1b7f-71a5-4011-9441-d0b5e4122711"


def check_fanart_key(api_key: str) -> Tuple[bool, str]:
    """(ok, message) for Settings' Test Connection."""
    api_key = str(api_key or '').strip()
    if not api_key:
        return False, "Add a fanart.tv API key (free at fanart.tv)."
    try:
        response = requests.get(FANART_URL.format(mbid=_CHECK_MBID), params={'api_key': api_key},
                                timeout=TIMEOUT)
    except Exception as e:  # noqa: BLE001
        return False, f"Couldn't reach fanart.tv: {e}"
    if response.status_code in (401, 403):
        return False, "fanart.tv refused that key."
    if not response.ok:
        return False, f"fanart.tv answered {response.status_code}."
    return True, "Connected to fanart.tv!"


__all__ = ["check_fanart_key", "candidates", "fanart_api_key", "first_that_fits", "fits_background", "fits_photo"]
