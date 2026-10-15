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

# the services a track can carry an id from, and the library column each one's
# enrichment worker fills
LIBRARY_ID_COLUMNS = {
    'spotify': 'spotify_track_id',
    'deezer': 'deezer_id',
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
    """the ids the library has for this song, by its normalised title and
    artist. empty when the library doesn't have it."""
    found: Dict[str, str] = {}
    try:
        title_norm = db._normalize_for_comparison(title or '')
        artist_norm = db._normalize_for_comparison(artist or '')
        if not title_norm or not artist_norm:
            return {}
        with db._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(tracks)")
            cols = {r[1] for r in cursor.fetchall()}
            wanted = [c for c in LIBRARY_ID_COLUMNS.values() if c in cols]
            if not wanted or 'title_norm' not in cols:
                return {}
            artist_clause = "t.artist_id IN (SELECT id FROM artists WHERE name_norm = ?)"
            params = [title_norm, artist_norm]
            if 'track_artist_norm' in cols:
                artist_clause = f"(t.track_artist_norm = ? OR {artist_clause})"
                params = [title_norm, artist_norm, artist_norm]
            cursor.execute(
                f"SELECT {', '.join('t.' + c for c in wanted)} FROM tracks t "
                f"WHERE t.title_norm = ? AND {artist_clause} LIMIT 5",
                params,
            )
            for row in cursor.fetchall():
                for service, column in LIBRARY_ID_COLUMNS.items():
                    if column in wanted and service not in found:
                        value = row[column]
                        if value:
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
