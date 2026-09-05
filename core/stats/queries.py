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

# INT-03: a play resolves on the TRACK's artist. Matching only the album's
# primary artist meant a Muse track on a Various Artists compilation could not
# be resolved from a listening event that correctly named Muse — the local file
# was there and the stats view could not find it. The album artist stays as the
# fallback it always was; it is simply no longer the only credit consulted.
_PLAYABLE_TRACK_SQL = """
    SELECT t.id, t.title, f.path, f.bitrate, t.duration,
           ar.name, al.title, al.image_url,
           COALESCE(ar.canonical_artist_id, ar.id), al.id
      FROM lib2_tracks t
      JOIN lib2_albums al ON al.id = t.album_id
      JOIN lib2_artists ar ON ar.id = al.primary_artist_id
      JOIN lib2_track_files f ON f.track_id = t.id
     WHERE LOWER(t.title) = LOWER(?)
       AND (ar.name_key = ?
            OR EXISTS (SELECT 1 FROM lib2_track_artists ta
                        JOIN lib2_artists credit ON credit.id = ta.artist_id
                       WHERE ta.track_id = t.id AND credit.name_key = ?))
       AND f.path IS NOT NULL AND f.path != ''
       AND COALESCE(f.file_state, 'active') = 'active'
     ORDER BY (ar.name_key = ?) DESC, f.is_primary DESC, f.id
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

    Tracks with no active file row are EXCLUDED here rather than filtered in
    the player: a row the player would skip is not a track you own, and
    counting it would make "play album" look like it lost songs. One preferred
    file is selected per recording and the result follows album track order.
    """
    conn = None
    try:
        conn = database._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT t.id, t.title,
                   COALESCE(NULLIF(t.track_artist, ''), ar.name), al.title,
                   f.path, f.bitrate,
                   COALESCE(ar.canonical_artist_id, ar.id), t.album_id,
                   al.image_url
            FROM lib2_tracks t
            JOIN lib2_albums al ON al.id = t.album_id
            JOIN lib2_artists ar ON ar.id = al.primary_artist_id
            JOIN lib2_track_files f ON f.id = (
                SELECT preferred.id
                FROM lib2_track_files preferred
                WHERE preferred.track_id = t.id
                  AND preferred.path IS NOT NULL
                  AND TRIM(preferred.path) != ''
                  AND COALESCE(preferred.file_state, 'active') = 'active'
                ORDER BY preferred.is_primary DESC, preferred.id
                LIMIT 1
            )
            WHERE t.album_id = ?
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


def get_recent_tracks(database, limit: int, image_url_fixer: Optional[ImageUrlFixer] = None) -> list[dict]:
    """Recently played tracks from listening_history.

    Joins album art through lib2_track_id when the play was matched to a
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
                   lh.server_source, al.image_url,
                   COALESCE(ar.canonical_artist_id, ar.id)
            FROM listening_history lh
            LEFT JOIN lib2_tracks t ON t.id = lh.lib2_track_id
            LEFT JOIN lib2_albums al ON al.id = t.album_id
            LEFT JOIN lib2_artists ar ON ar.id = al.primary_artist_id
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
            -- INT-02: `lib2_track_id` is the catalogue link. Both writers of
            -- the catalogue id — the media-server importer and the Last.fm one
            -- — fill that column; `db_track_id` is the media server's OWN id
            -- namespace. Joining the catalogue on it left every chart detail
            -- without cover, artist link and track link, and on a numeric
            -- collision pointed at somebody else's row. `get_recent_tracks`
            -- already reads the right column; this is the same contract.
            SELECT lh.title, lh.artist, lh.album, lh.played_at, lh.duration_ms,
                   lh.server_source, al.image_url,
                   COALESCE(ar.canonical_artist_id, ar.id), t.id AS db_track_id
            FROM picked
            JOIN listening_history lh ON lh.id = picked.id
            LEFT JOIN lib2_tracks t ON t.id = lh.lib2_track_id
            LEFT JOIN lib2_albums al ON al.id = t.album_id
            LEFT JOIN lib2_artists ar ON ar.id = al.primary_artist_id
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
        artist_key = _name_key(artist)
        cursor.execute(
            _PLAYABLE_TRACK_SQL,
            (title.strip(), artist_key, artist_key, artist_key))
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

