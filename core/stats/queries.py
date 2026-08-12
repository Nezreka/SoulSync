"""Stats API query helpers.

Lifted from web_server.py /api/stats/* and /api/listening-stats/* routes.
Pure-ish functions: take dependencies as args, return data dicts/lists. Route
handlers stay in web_server.py and are responsible for request parsing,
jsonify, and error responses.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import traceback
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

ImageUrlFixer = Callable[[Optional[str]], Optional[str]]


# The stats page shows names the media server reported for a play; the catalogue
# has to be found by name because there is no id in a listening-history row.
# Three things about doing that against Library v2 (docs §50.4.4.13, corrected
# in §50.4.4.22):
#
# **The id is the lib2 one.** Every id here is handed to the artist-detail
# link, and that route redirects `/artist-detail/library/<id>` into Library V2
# as `?artist=<id>` (ldp-01) — where the number is read as `lib2_artists.id`.
# §50.4.4.13 kept the legacy id because the page it knew resolved against the
# legacy table; that page is gone, so a legacy id there opens a different
# artist or none at all. A row without a legacy twin is therefore no longer a
# row without a link, and the ``ORDER BY`` prefers the canonical row (an alias
# member folds into it anyway) rather than a linked one.
#
# **Artists match on ``name_key``, not ``LOWER(name)``.** It is the indexed
# dedup key, and SQLite's ``lower()`` is ASCII-only — the old comparison missed
# every Cyrillic/Greek/Turkish name it was supposed to find (iss29-D13).
#
# **A path is a file row.** lib2 keeps paths and bitrate on
# ``lib2_track_files`` (ADR-03), so "has a playable file" is a join, and the
# stored path is returned as stored — resolving it to disk is the caller's job,
# as it was when the column lived on the track.
_ARTIST_BY_NAME_SQL = """
    SELECT image_url,
           json_extract(enrichment, '$.lastfm.listeners'),
           json_extract(enrichment, '$.lastfm.playcount'),
           soul_id,
           COALESCE(canonical_artist_id, id)
      FROM lib2_artists
     WHERE name_key = ?
     ORDER BY (canonical_artist_id IS NOT NULL), id
     LIMIT 1
"""

_ALBUM_BY_TITLE_SQL = """
    SELECT al.image_url, al.id, COALESCE(ar.canonical_artist_id, ar.id)
      FROM lib2_albums al
      JOIN lib2_artists ar ON ar.id = al.primary_artist_id
     WHERE LOWER(al.title) = LOWER(?)
       AND al.image_url IS NOT NULL AND al.image_url != ''
     ORDER BY al.id
     LIMIT 1
"""

_TRACK_BY_TITLE_AND_ARTIST_SQL = """
    SELECT al.image_url, t.id, COALESCE(ar.canonical_artist_id, ar.id)
      FROM lib2_tracks t
      JOIN lib2_albums al ON al.id = t.album_id
      JOIN lib2_artists ar ON ar.id = al.primary_artist_id
     WHERE LOWER(t.title) = LOWER(?) AND ar.name_key = ?
     ORDER BY t.id
     LIMIT 1
"""

_PLAYABLE_TRACK_SQL = """
    SELECT t.id, t.title, f.path, f.bitrate, t.duration,
           ar.name, al.title, al.image_url,
           COALESCE(ar.canonical_artist_id, ar.id), al.id
      FROM lib2_tracks t
      JOIN lib2_albums al ON al.id = t.album_id
      JOIN lib2_artists ar ON ar.id = al.primary_artist_id
      JOIN lib2_track_files f ON f.track_id = t.id
     WHERE LOWER(t.title) = LOWER(?) AND ar.name_key = ?
       AND f.path IS NOT NULL AND f.path != ''
       AND COALESCE(f.file_state, 'active') = 'active'
     ORDER BY f.is_primary DESC, f.id
     LIMIT 1
"""


def _name_key(name: Any) -> str:
    from core.library2.importer import normalize_name

    return normalize_name(str(name or ""))


def get_cached_stats(database, image_url_fixer: ImageUrlFixer, time_range: str) -> dict:
    """Read pre-computed stats cache for a time range. Instant response."""
    conn = database._get_connection()
    try:
        cursor = conn.cursor()

        cursor.execute("SELECT value FROM metadata WHERE key = ?", (f'stats_cache_{time_range}',))
        row = cursor.fetchone()
        data = json.loads(row[0]) if row and row[0] else {}

        cursor.execute("SELECT value FROM metadata WHERE key = 'stats_cache_recent'")
        row = cursor.fetchone()
        recent = json.loads(row[0]) if row and row[0] else []

        cursor.execute("SELECT value FROM metadata WHERE key = 'stats_cache_health'")
        row = cursor.fetchone()
        health = json.loads(row[0]) if row and row[0] else {}
    finally:
        conn.close()

    for item in (data.get('top_artists') or []) + (data.get('top_albums') or []) + (data.get('top_tracks') or []):
        if item.get('image_url'):
            item['image_url'] = image_url_fixer(item['image_url'])

    return {
        'cached': True,
        **data,
        'recent': recent,
        'health': health,
    }


def get_overview(database, time_range: str) -> dict:
    """Aggregate listening stats for a time range."""
    return database.get_listening_stats(time_range)


def get_top_artists(database, image_url_fixer: ImageUrlFixer, time_range: str, limit: int) -> list[dict]:
    """Top artists by play count, enriched with image / Last.fm stats / soul_id."""
    artists = database.get_top_artists(time_range, limit)

    for artist in artists:
        try:
            conn = database._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(_ARTIST_BY_NAME_SQL, (_name_key(artist['name']),))
                row = cursor.fetchone()
                if row:
                    artist['image_url'] = image_url_fixer(row[0]) if row[0] else None
                    artist['global_listeners'] = row[1]
                    artist['global_playcount'] = row[2]
                    artist['soul_id'] = row[3]
                    artist['id'] = row[4]
            finally:
                conn.close()
        except Exception as e:
            logger.debug("top artists enrich failed: %s", e)

    return artists


def get_top_albums(database, image_url_fixer: ImageUrlFixer, time_range: str, limit: int) -> list[dict]:
    """Top albums by play count, enriched with album thumb."""
    albums = database.get_top_albums(time_range, limit)

    for album in albums:
        try:
            conn = database._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(_ALBUM_BY_TITLE_SQL, (album['name'],))
                row = cursor.fetchone()
                if row:
                    album['image_url'] = image_url_fixer(row[0]) if row[0] else None
                    album['id'] = row[1]
                    album['artist_id'] = row[2]
            finally:
                conn.close()
        except Exception as e:
            logger.debug("top albums enrich failed: %s", e)

    return albums


def get_top_tracks(database, image_url_fixer: ImageUrlFixer, time_range: str, limit: int) -> list[dict]:
    """Top tracks by play count, enriched with album thumb."""
    tracks = database.get_top_tracks(time_range, limit)

    for track in tracks:
        try:
            conn = database._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(_TRACK_BY_TITLE_AND_ARTIST_SQL,
                               (track['name'], _name_key(track['artist'])))
                row = cursor.fetchone()
                if row:
                    track['image_url'] = image_url_fixer(row[0]) if row[0] else None
                    track['id'] = row[1]
                    track['artist_id'] = row[2]
            finally:
                conn.close()
        except Exception as e:
            logger.debug("top tracks enrich failed: %s", e)

    return tracks


def get_timeline(database, time_range: str, granularity: str) -> Any:
    """Play count per time period for chart rendering."""
    return database.get_listening_timeline(time_range, granularity)


def get_genres(database, time_range: str) -> Any:
    """Genre distribution by play count."""
    return database.get_genre_breakdown(time_range)


def get_library_health(database) -> dict:
    """Library health metrics."""
    return database.get_library_health()


def get_db_storage(database) -> dict:
    """Database storage breakdown by table."""
    return database.get_db_storage_stats()


def get_library_disk_usage(database) -> dict:
    """On-disk size of the library, with per-format breakdown.

    Backed by `tracks.file_size` populated during the deep scan from
    media-server-reported sizes (Plex MediaPart.size, Jellyfin
    MediaSources[].Size, Navidrome <song size="...">,
    SoulSync standalone os.path.getsize).
    """
    return database.get_library_disk_usage()


def get_recent_tracks(database, limit: int) -> list[dict]:
    """Recently played tracks from listening_history."""
    conn = database._get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT title, artist, album, played_at, duration_ms
            FROM listening_history
            ORDER BY played_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = cursor.fetchall()
    finally:
        conn.close()

    return [
        {
            'title': row[0],
            'artist': row[1],
            'album': row[2],
            'played_at': row[3],
            'duration_ms': row[4],
        }
        for row in rows
    ]


def resolve_track(database, image_url_fixer: ImageUrlFixer, title: str, artist: str) -> Optional[dict]:
    """Resolve a track by title+artist to its file_path / metadata. Returns None if not found."""
    conn = database._get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(_PLAYABLE_TRACK_SQL, (title.strip(), _name_key(artist)))
        row = cursor.fetchone()
    finally:
        conn.close()

    if not row:
        return None

    return {
        'id': row[0],
        'title': row[1],
        'file_path': row[2],
        'bitrate': row[3],
        'duration': row[4],
        'artist_name': row[5],
        'album_title': row[6],
        'image_url': image_url_fixer(row[7]) if row[7] else None,
        'artist_id': row[8],
        'album_id': row[9],
        # The player takes the v2 ids by their own names (iss29-B08): its
        # "Go to artist" button then routes straight into the Library page
        # instead of going through the artist-detail redirect.
        'lib2_track_id': row[0],
        'lib2_artist_id': row[8],
    }


def trigger_listening_sync(worker) -> None:
    """Spawn a daemon thread that runs the worker's poll loop once.

    Caller is responsible for verifying worker is not None before calling.
    """
    def _do_sync():
        try:
            logger.info("[Stats Sync] Starting manual poll...")
            worker._poll()
            worker.stats['polls_completed'] += 1
            worker.stats['last_poll'] = time.strftime('%Y-%m-%d %H:%M:%S')
            logger.info("[Stats Sync] Manual poll completed")
        except Exception as e:
            logger.error(f"[Stats Sync] Manual poll failed: {e}")
            traceback.print_exc()
            logger.error(f"Manual stats sync failed: {e}")

    threading.Thread(target=_do_sync, daemon=True).start()


def get_listening_status(worker) -> dict:
    """Worker status dict. Returns disabled-state shape if worker is None."""
    if worker is None:
        return {
            'enabled': False,
            'running': False,
            'paused': False,
            'idle': False,
            'current_item': None,
            'stats': {},
        }
    return worker.get_stats()
