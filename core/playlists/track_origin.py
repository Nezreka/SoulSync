"""the ids a playlist track already has, so identify can fetch it instead of
searching for it.

a track on one of your playlists came from somewhere: a search result knows
its spotify or deezer id, a song from your library has whatever ids the
enrichment workers found for it. identify's direct lookup (#1566) takes a
service + id and fetches the exact track, which a search by artist + title
can only do worse than.

`origin_ids` answers "which ids do we have for this track", as
{service: id}. the discovery worker asks it for the active source's id.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from utils.logging_config import get_logger

logger = get_logger("playlists.track_origin")

# the services a track can carry an id from, and where the Library v2 catalogue
# keeps each one's id (spotify has its own column, the rest live in external_ids)
LIBRARY_ID_COLUMNS = {
    'spotify': 't.spotify_id',
    'deezer': "json_extract(t.external_ids, '$.deezer')",
}
ORIGIN_SOURCES = frozenset(LIBRARY_ID_COLUMNS) | {'spotify_public', 'itunes', 'tidal', 'qobuz'}
MAX_ORIGIN_ID_LENGTH = 100


def clean_origin(source: Any, source_id: Any) -> Optional[Dict[str, str]]:
    """a page's (source, id) for a track, or None when it isn't one we know.
    spotify_public ids are spotify ids."""
    service = str(source or '').strip().lower()
    track_id = str(source_id or '').strip()
    if service == 'spotify_public':
        service = 'spotify'
    if service not in ORIGIN_SOURCES or not track_id or len(track_id) > MAX_ORIGIN_ID_LENGTH:
        return None
    if not all(c.isalnum() or c in '-_:.' for c in track_id):
        return None
    return {'source': service, 'id': track_id}


def _extra(track: Dict[str, Any]) -> Dict[str, Any]:
    raw = track.get('extra_data')
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def library_ids(db: Any, title: str, artist: str) -> Dict[str, str]:
    """the ids the library has for this song, by its title and the artist the
    album or the track itself is credited to. empty when the library doesn't
    have it."""
    found: Dict[str, str] = {}
    try:
        from core.library2.importer import normalize_name

        title = (title or '').strip()
        artist_key = normalize_name(artist or '')
        if not title or not artist_key:
            return {}
        with db._get_connection() as conn:
            rows = conn.execute(
                f"SELECT {', '.join(LIBRARY_ID_COLUMNS.values())} FROM lib2_tracks t "
                "WHERE LOWER(t.title) = LOWER(?) AND ("
                " EXISTS (SELECT 1 FROM lib2_albums al JOIN lib2_artists ar ON ar.id = al.primary_artist_id"
                "          WHERE al.id = t.album_id AND ar.name_key = ?)"
                " OR EXISTS (SELECT 1 FROM lib2_track_artists ta JOIN lib2_artists ar ON ar.id = ta.artist_id"
                "             WHERE ta.track_id = t.id AND ar.name_key = ?)) LIMIT 5",
                (title, artist_key, artist_key),
            ).fetchall()
        for row in rows:
            for service, value in zip(LIBRARY_ID_COLUMNS, row):
                if value and service not in found:
                    found[service] = str(value)
    except Exception as e:  # noqa: BLE001 - no ids just means identify searches
        logger.debug("library id lookup failed for %s - %s: %s", artist, title, e)
    return found


def origin_ids(db: Any, playlist_source: str, track: Dict[str, Any]) -> Dict[str, str]:
    """{service: id} for one mirrored playlist track.

    a synced mirror's own source_track_id is its service's id. a user
    playlist's track has the origin its page handed over, then whatever the
    library knows about the same song.
    """
    from core.playlists.user_playlists import USER_PLAYLIST_SOURCE

    source = (playlist_source or '').lower()
    if source != USER_PLAYLIST_SOURCE:
        sid = str(track.get('source_track_id') or '').strip()
        return {source: sid} if sid else {}
    ids: Dict[str, str] = {}
    origin = _extra(track).get('origin')
    if isinstance(origin, dict):
        clean = clean_origin(origin.get('source'), origin.get('id'))
        if clean:
            ids[clean['source']] = clean['id']
    for service, value in library_ids(db, track.get('track_name', ''), track.get('artist_name', '')).items():
        ids.setdefault(service, value)
    return ids
