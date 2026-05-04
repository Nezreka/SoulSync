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
                cursor.execute(
                    """
                    SELECT thumb_url, id, lastfm_listeners, lastfm_playcount, soul_id
                    FROM artists
                    WHERE LOWER(name) = LOWER(?)
                    LIMIT 1
                    """,
                    (artist['name'],),
                )
                row = cursor.fetchone()
                if row:
                    artist['image_url'] = image_url_fixer(row[0]) if row[0] else None
                    artist['id'] = row[1]
                    artist['global_listeners'] = row[2]
                    artist['global_playcount'] = row[3]
                    artist['soul_id'] = row[4]
            finally:
                conn.close()
        except Exception:
            pass

    return artists


def get_top_albums(database, image_url_fixer: ImageUrlFixer, time_range: str, limit: int) -> list[dict]:
    """Top albums by play count, enriched with album thumb."""
    albums = database.get_top_albums(time_range, limit)

    for album in albums:
        try:
            conn = database._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT al.thumb_url, al.id, al.artist_id FROM albums al
                    WHERE LOWER(al.title) = LOWER(?) AND al.thumb_url IS NOT NULL AND al.thumb_url != ''
                    LIMIT 1
                    """,
                    (album['name'],),
                )
                row = cursor.fetchone()
                if row:
                    album['image_url'] = image_url_fixer(row[0]) if row[0] else None
                    album['id'] = row[1]
                    album['artist_id'] = row[2]
            finally:
                conn.close()
        except Exception:
            pass

    return albums


def get_top_tracks(database, image_url_fixer: ImageUrlFixer, time_range: str, limit: int) -> list[dict]:
    """Top tracks by play count, enriched with album thumb."""
    tracks = database.get_top_tracks(time_range, limit)

    for track in tracks:
        try:
            conn = database._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT al.thumb_url, t.id, t.artist_id FROM tracks t
                    JOIN albums al ON al.id = t.album_id
                    JOIN artists ar ON ar.id = t.artist_id
                    WHERE LOWER(t.title) = LOWER(?) AND LOWER(ar.name) = LOWER(?)
                    LIMIT 1
                    """,
                    (track['name'], track['artist']),
                )
                row = cursor.fetchone()
                if row:
                    track['image_url'] = image_url_fixer(row[0]) if row[0] else None
                    track['id'] = row[1]
                    track['artist_id'] = row[2]
            finally:
                conn.close()
        except Exception:
            pass

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
        cursor.execute(
            """
            SELECT t.id, t.title, t.file_path, t.bitrate, t.duration,
                   ar.name as artist_name, al.title as album_title,
                   al.thumb_url, t.artist_id, t.album_id
            FROM tracks t
            JOIN artists ar ON ar.id = t.artist_id
            LEFT JOIN albums al ON al.id = t.album_id
            WHERE LOWER(t.title) = LOWER(?) AND LOWER(ar.name) = LOWER(?)
              AND t.file_path IS NOT NULL AND t.file_path != ''
            LIMIT 1
            """,
            (title.strip(), artist.strip()),
        )
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
