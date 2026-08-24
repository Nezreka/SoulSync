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


def get_year_in_listening(database, image_url_fixer: ImageUrlFixer) -> dict:
    """The Year in Listening story — cached by the worker, computed on miss.

    The miss path is the one that matters: the worker rebuilds every 30
    minutes, so a fresh install (or one restarted five minutes ago) has no
    cache yet. Serving an empty year there would look exactly like "you have
    not listened to anything", which is the wrong answer and unrecoverable
    from the user's side. Computing it costs one pass over listening_history.
    """
    data = None
    conn = database._get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM metadata WHERE key = 'stats_cache_year'")
        row = cursor.fetchone()
        if row and row[0]:
            data = json.loads(row[0])
    except Exception as e:
        logger.debug("year cache read failed, computing live: %s", e)
    finally:
        conn.close()

    if not data:
        data = database.get_year_in_listening()
        # The cached copy was enriched by the worker; a live one has to earn
        # its artwork here or the story renders name-only on exactly the
        # installs that hit this path.
        try:
            from core.stats.enrich import enrich_stats_items
            enrich_stats_items(database, data)
        except Exception as e:
            logger.debug("year enrichment failed, serving unenriched: %s", e)
        data['cached'] = False
    else:
        data['cached'] = True

    for item in ((data.get('top_artists') or []) + (data.get('top_albums') or [])
                 + (data.get('top_tracks') or []) + (data.get('discoveries') or [])):
        if item.get('image_url'):
            item['image_url'] = image_url_fixer(item['image_url'])

    return data


def get_album_play_tracks(database, album_id, image_url_fixer: ImageUrlFixer) -> list[dict]:
    """An owned album's tracks, shaped for ``window.playTrackList``.

    Rows match what ``/api/library/radio`` returns because that is the shape
    ``npMapRadioTrack`` (media-player.js) maps — anything else silently drops
    out of the queue.

    Tracks with no ``file_path`` are EXCLUDED here rather than filtered in the
    player: a row the player would skip is not a track you own, and counting
    it would make "play album" look like it lost songs. Ordered by track
    number so the album plays as an album.
    """
    conn = None
    try:
        conn = database._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT t.id, t.title, ar.name, al.title, t.file_path, t.bitrate,
                   t.artist_id, t.album_id, al.thumb_url
            FROM tracks t
            JOIN albums al ON al.id = t.album_id
            JOIN artists ar ON ar.id = t.artist_id
            WHERE t.album_id = ?
              AND t.file_path IS NOT NULL AND t.file_path != ''
            ORDER BY COALESCE(t.track_number, 999999), t.title
            """,
            (album_id,),
        )
        rows = cursor.fetchall()
    except Exception as e:
        logger.error("Error loading album tracks for %s: %s", album_id, e)
        return []
    finally:
        if conn:
            conn.close()

    return [
        {
            'id': r[0],
            'title': r[1],
            'artist': r[2],
            'album': r[3],
            'file_path': r[4],
            'bitrate': r[5],
            'artist_id': r[6],
            'album_id': r[7],
            'image_url': image_url_fixer(r[8]) if r[8] else None,
        }
        for r in rows
    ]


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


def get_recent_tracks(database, limit: int, image_url_fixer: Optional[ImageUrlFixer] = None) -> list[dict]:
    """Recently played tracks from listening_history.

    Joins album art through db_track_id when the play was matched to a
    library track (the listening-stats worker sets it; media-server plays it
    couldn't match leave it NULL, and those rows come back with image_url
    None). Art passes through ``image_url_fixer`` because server-synced thumb
    URLs need auth and die in the browser — same treatment as resolve_track.
    """
    conn = database._get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT lh.title, lh.artist, lh.album, lh.played_at, lh.duration_ms,
                   lh.server_source, al.thumb_url, t.artist_id
            FROM listening_history lh
            LEFT JOIN tracks t ON t.id = lh.db_track_id
            LEFT JOIN albums al ON al.id = t.album_id
            ORDER BY lh.played_at DESC
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
            'server_source': row[5],
            'image_url': (image_url_fixer(row[6]) if image_url_fixer else row[6]) if row[6] else None,
            # The library artist PK when the play was matched — lets the
            # dashboard band jump straight to the artist page, no name lookup.
            'artist_db_id': row[7],
        }
        for row in rows
    ]


def get_listening_events(
    database,
    image_url_fixer: Optional[ImageUrlFixer],
    *,
    time_range: str,
    filter_type: str,
    date: Optional[str] = None,
    weekday: Optional[int] = None,
    hour: Optional[int] = None,
    limit: int = 100,
) -> dict:
    """Listening-history rows behind a clicked stats chart segment."""
    limit = max(1, min(int(limit or 100), 250))
    where = database._listening_time_filter(time_range, alias='lh')
    clauses: list[str] = []
    params: list[Any] = []
    title = 'Listening details'

    if filter_type == 'date':
        if not date:
            raise ValueError('date is required')
        if _is_month_bucket(date):
            clauses.append("lh.played_at >= date(? || '-01')")
            clauses.append("lh.played_at < date(? || '-01', '+1 month')")
            params.extend([date, date])
            title = date
        elif _is_day_bucket(date):
            clauses.append('lh.played_at >= date(?)')
            clauses.append("lh.played_at < date(?, '+1 day')")
            params.extend([date, date])
            title = date
        else:
            raise ValueError('date must be YYYY-MM-DD or YYYY-MM')
    elif filter_type == 'weekday_hour':
        if weekday is None or hour is None:
            raise ValueError('weekday and hour are required')
        weekday_i = int(weekday)
        hour_i = int(hour)
        if not (0 <= weekday_i <= 6 and 0 <= hour_i <= 23):
            raise ValueError('weekday/hour out of range')
        clauses.append("CAST(strftime('%w', lh.played_at) AS INTEGER) = ?")
        clauses.append("CAST(strftime('%H', lh.played_at) AS INTEGER) = ?")
        params.extend([weekday_i, hour_i])
        title = f"{['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'][weekday_i]} {hour_i:02d}:00"
    elif filter_type == 'hour':
        if hour is None:
            raise ValueError('hour is required')
        hour_i = int(hour)
        if not (0 <= hour_i <= 23):
            raise ValueError('hour out of range')
        clauses.append("CAST(strftime('%H', lh.played_at) AS INTEGER) = ?")
        params.append(hour_i)
        title = f"{hour_i:02d}:00"
    else:
        raise ValueError('unsupported filter type')

    if clauses:
        where = f"{where} AND {' AND '.join(clauses)}"

    conn = database._get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            WITH picked AS (
                SELECT lh.id
                FROM listening_history lh
                {where}
                ORDER BY lh.played_at DESC
                LIMIT ?
            )
            SELECT lh.title, lh.artist, lh.album, lh.played_at, lh.duration_ms,
                   lh.server_source, al.thumb_url, t.artist_id, t.id AS db_track_id
            FROM picked
            JOIN listening_history lh ON lh.id = picked.id
            LEFT JOIN tracks t ON t.id = CAST(lh.db_track_id AS TEXT)
            LEFT JOIN albums al ON al.id = t.album_id
            ORDER BY lh.played_at DESC
            """,
            params + [limit + 1],
        )
        rows = cursor.fetchall()
        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]
        total = len(rows)
    finally:
        conn.close()

    image_url_cache: dict[str, Optional[str]] = {}

    def _normalize_event_image(url: str | None) -> str | None:
        if not url:
            return None
        if not image_url_fixer:
            return url
        if url not in image_url_cache:
            image_url_cache[url] = image_url_fixer(url)
        return image_url_cache[url]

    items = [
        {
            'title': row[0],
            'artist': row[1],
            'album': row[2],
            'played_at': row[3],
            'duration_ms': row[4],
            'server_source': row[5],
            'image_url': _normalize_event_image(row[6]),
            'artist_db_id': row[7],
            'db_track_id': row[8],
        }
        for row in rows
    ]
    return {'title': title, 'total': total, 'limit': limit, 'has_more': has_more, 'items': items}


def _is_day_bucket(value: str) -> bool:
    return len(value) == 10 and value[4] == '-' and value[7] == '-'


def _is_month_bucket(value: str) -> bool:
    return len(value) == 7 and value[4] == '-'

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

