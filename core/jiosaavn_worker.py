import re
import threading
from difflib import SequenceMatcher
from typing import Optional, Dict, Any
from datetime import datetime, timedelta

from utils.logging_config import get_logger
from database.music_database import MusicDatabase
from core.metadata.registry import get_jiosaavn_client, is_jiosaavn_enabled
from core.worker_utils import (
    accept_artist_match,
    artist_name_matches,
    interruptible_sleep,
    set_album_api_track_count,
)
from core.enrichment.manual_match_honoring import honor_stored_match

logger = get_logger("jiosaavn_worker")


class JioSaavnWorker:
    """Background worker for enriching library artists, albums, and tracks with JioSaavn metadata."""

    def __init__(self, database: MusicDatabase):
        self.db = database
        self._client = None

        self.running = False
        self.paused = False
        self.should_stop = False
        self.thread = None
        self._stop_event = threading.Event()

        self.current_item = None

        self.stats = {
            'matched': 0,
            'not_found': 0,
            'pending': 0,
            'errors': 0,
        }

        self.retry_days = 30
        self.name_similarity_threshold = 0.80

        logger.info("JioSaavn background worker initialized")

    @property
    def client(self):
        if self._client is None:
            self._client = get_jiosaavn_client()
        return self._client

    def start(self):
        if self.running:
            logger.warning("Worker already running")
            return

        self.running = True
        self.should_stop = False
        self._stop_event.clear()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        logger.info("JioSaavn background worker started")

    def stop(self):
        if not self.running:
            return

        logger.info("Stopping JioSaavn worker...")
        self.should_stop = True
        self.running = False
        self._stop_event.set()

        if self.thread:
            self.thread.join(timeout=1)

        logger.info("JioSaavn worker stopped")

    def pause(self):
        if not self.running:
            logger.warning("Worker not running, cannot pause")
            return

        self.paused = True
        logger.info("JioSaavn worker paused")

    def resume(self):
        if not self.running:
            logger.warning("Worker not running, start it first")
            return

        self.paused = False
        logger.info("JioSaavn worker resumed")

    def get_stats(self) -> Dict[str, Any]:
        self.stats['pending'] = self._count_pending_items()
        progress = self._get_progress_breakdown()

        is_actually_running = self.running and (self.thread is not None and self.thread.is_alive())
        is_idle = (
            is_actually_running
            and not self.paused
            and self.stats['pending'] == 0
            and self.current_item is None
        )

        return {
            'enabled': is_jiosaavn_enabled(),
            'running': is_actually_running and not self.paused and is_jiosaavn_enabled(),
            'paused': self.paused,
            'idle': is_idle,
            'current_item': self.current_item,
            'stats': self.stats.copy(),
            'progress': progress,
        }

    def _run(self):
        logger.info("JioSaavn worker thread started")

        while not self.should_stop:
            try:
                if not is_jiosaavn_enabled():
                    interruptible_sleep(self._stop_event, 10)
                    continue

                if self.paused:
                    interruptible_sleep(self._stop_event, 1)
                    continue

                self.current_item = None
                item = self._get_next_item()

                if not item:
                    logger.debug("No pending items, sleeping...")
                    interruptible_sleep(self._stop_event, 10)
                    continue

                self.current_item = item
                item_id = item.get('id') or item.get('artist_id') or item.get('album_id')
                if item_id is None:
                    logger.warning(
                        "Skipping %s with NULL id: %s",
                        item.get('type', 'unknown'),
                        item.get('name', '?'),
                    )
                    continue

                self._process_item(item)
                interruptible_sleep(self._stop_event, 1)

            except Exception as e:
                logger.error("Error in worker loop: %s", e)
                interruptible_sleep(self._stop_event, 5)

        logger.info("JioSaavn worker thread finished")

    def _get_next_item(self) -> Optional[Dict[str, Any]]:
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            from core.worker_utils import read_enrichment_priority, priority_pending_item
            _prio = read_enrichment_priority('jiosaavn')
            if _prio:
                _pi = priority_pending_item(cursor, 'jiosaavn', _prio)
                if _pi:
                    return _pi

            cursor.execute("""
                SELECT id, name
                FROM artists
                WHERE jiosaavn_match_status IS NULL AND id IS NOT NULL
                ORDER BY id ASC
                LIMIT 1
            """)
            row = cursor.fetchone()
            if row:
                return {'type': 'artist', 'id': row[0], 'name': row[1]}

            cursor.execute("""
                SELECT a.id, a.title, ar.name AS artist_name, ar.jiosaavn_id AS artist_jiosaavn_id
                FROM albums a
                JOIN artists ar ON a.artist_id = ar.id
                WHERE a.jiosaavn_match_status IS NULL AND a.id IS NOT NULL
                ORDER BY a.id ASC
                LIMIT 1
            """)
            row = cursor.fetchone()
            if row:
                return {
                    'type': 'album',
                    'id': row[0],
                    'name': row[1],
                    'artist': row[2],
                    'artist_jiosaavn_id': row[3],
                }

            cursor.execute("""
                SELECT t.id, t.title, ar.name AS artist_name, ar.jiosaavn_id AS artist_jiosaavn_id
                FROM tracks t
                JOIN artists ar ON t.artist_id = ar.id
                WHERE t.jiosaavn_match_status IS NULL AND t.id IS NOT NULL
                ORDER BY t.id ASC
                LIMIT 1
            """)
            row = cursor.fetchone()
            if row:
                return {
                    'type': 'track',
                    'id': row[0],
                    'name': row[1],
                    'artist': row[2],
                    'artist_jiosaavn_id': row[3],
                }

            not_found_cutoff = datetime.now() - timedelta(days=self.retry_days)

            cursor.execute("""
                SELECT id, name
                FROM artists
                WHERE jiosaavn_match_status = 'not_found' AND jiosaavn_last_attempted < ?
                ORDER BY jiosaavn_last_attempted ASC
                LIMIT 1
            """, (not_found_cutoff,))
            row = cursor.fetchone()
            if row:
                logger.info("Retrying artist '%s' (last attempted before cutoff)", row[1])
                return {'type': 'artist', 'id': row[0], 'name': row[1]}

            cursor.execute("""
                SELECT a.id, a.title, ar.name AS artist_name, ar.jiosaavn_id AS artist_jiosaavn_id
                FROM albums a
                JOIN artists ar ON a.artist_id = ar.id
                WHERE a.jiosaavn_match_status = 'not_found' AND a.jiosaavn_last_attempted < ?
                ORDER BY a.jiosaavn_last_attempted ASC
                LIMIT 1
            """, (not_found_cutoff,))
            row = cursor.fetchone()
            if row:
                return {
                    'type': 'album',
                    'id': row[0],
                    'name': row[1],
                    'artist': row[2],
                    'artist_jiosaavn_id': row[3],
                }

            cursor.execute("""
                SELECT t.id, t.title, ar.name AS artist_name, ar.jiosaavn_id AS artist_jiosaavn_id
                FROM tracks t
                JOIN artists ar ON t.artist_id = ar.id
                WHERE t.jiosaavn_match_status = 'not_found' AND t.jiosaavn_last_attempted < ?
                ORDER BY t.jiosaavn_last_attempted ASC
                LIMIT 1
            """, (not_found_cutoff,))
            row = cursor.fetchone()
            if row:
                return {
                    'type': 'track',
                    'id': row[0],
                    'name': row[1],
                    'artist': row[2],
                    'artist_jiosaavn_id': row[3],
                }

            return None

        except Exception as e:
            logger.error("Error getting next item: %s", e)
            return None
        finally:
            if conn:
                conn.close()

    def _normalize_name(self, name: str) -> str:
        name = name.lower().strip()
        name = re.sub(r'\s+[-–—]\s+.*$', '', name)
        name = re.sub(r'\s*\(.*?\)\s*', ' ', name)
        name = re.sub(r'[^\w\s]', '', name)
        name = re.sub(r'\s+', ' ', name).strip()
        return name

    def _name_matches(self, query_name: str, result_name: str) -> bool:
        norm_query = self._normalize_name(query_name)
        norm_result = self._normalize_name(result_name)
        similarity = SequenceMatcher(None, norm_query, norm_result).ratio()
        logger.debug("Name similarity: '%s' vs '%s' = %.2f", query_name, result_name, similarity)
        return similarity >= self.name_similarity_threshold

    def _artist_matches_result(self, artist_name: str, result_artists: list) -> bool:
        if not result_artists:
            return False
        return any(artist_name_matches(artist_name, a) for a in result_artists)

    def _process_item(self, item: Dict[str, Any]):
        try:
            item_type = item['type']
            item_id = item['id']
            item_name = item['name']

            logger.debug("Processing %s #%s: %s", item_type, item_id, item_name)

            if item_type == 'artist':
                self._process_artist(item_id, item_name)
            elif item_type == 'album':
                self._process_album(item_id, item_name, item.get('artist', ''))
            elif item_type == 'track':
                self._process_track(item_id, item_name, item.get('artist', ''))

        except Exception as e:
            logger.error("Error processing %s #%s: %s", item['type'], item['id'], e)
            self.stats['errors'] += 1
            try:
                self._mark_status(item['type'], item['id'], 'error')
            except Exception as e2:
                logger.error("Error updating item status: %s", e2)

    def _get_existing_id(self, entity_type: str, entity_id: int) -> Optional[str]:
        table_map = {'artist': 'artists', 'album': 'albums', 'track': 'tracks'}
        table = table_map.get(entity_type)
        if not table:
            return None
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute(f"SELECT jiosaavn_id FROM {table} WHERE id = ?", (entity_id,))
            row = cursor.fetchone()
            return row[0] if row and row[0] else None
        except Exception:
            return None
        finally:
            if conn:
                conn.close()

    def _process_artist(self, artist_id: int, artist_name: str):
        existing_id = self._get_existing_id('artist', artist_id)
        if existing_id:
            logger.debug("Preserving existing JioSaavn ID for artist '%s': %s", artist_name, existing_id)
            return

        results = self.client.search_artists(artist_name, limit=5)
        gated = [a for a in (results or []) if artist_name_matches(artist_name, getattr(a, 'name', ''))]
        chosen = gated[0] if gated else None

        if chosen:
            ok, reason = accept_artist_match(
                self.db, 'jiosaavn_id', chosen.id, artist_id,
                artist_name, chosen.name,
            )
            if ok:
                self._update_artist(artist_id, chosen)
                self.stats['matched'] += 1
                logger.info("Matched artist '%s' -> JioSaavn ID: %s", artist_name, chosen.id)
            else:
                self._mark_status('artist', artist_id, 'not_found')
                self.stats['not_found'] += 1
                logger.debug("Artist '%s' not matched: %s", artist_name, reason)
        else:
            self._mark_status('artist', artist_id, 'not_found')
            self.stats['not_found'] += 1
            logger.debug("No match for artist '%s'", artist_name)

    def _refresh_album_via_stored_id(self, album_id, stored_id, full_album_dict):
        self._update_album(album_id, full_album_dict)

    def _refresh_track_via_stored_id(self, track_id, stored_id, full_track_dict):
        self._update_track(track_id, full_track_dict)

    def _process_album(self, album_id: int, album_name: str, artist_name: str):
        if honor_stored_match(
            db=self.db, entity_table='albums', entity_id=album_id,
            id_column='jiosaavn_id',
            client_fetch_fn=self.client.get_album,
            on_match_fn=self._refresh_album_via_stored_id,
            log_prefix='JioSaavn',
        ):
            self.stats['matched'] += 1
            return

        query = f"{artist_name} {album_name}".strip()
        results = self.client.search_albums(query, limit=5)
        chosen = None
        for candidate in results or []:
            if self._name_matches(album_name, candidate.name) and self._artist_matches_result(
                artist_name, candidate.artists
            ):
                chosen = candidate
                break

        if chosen:
            full_album = self.client.get_album(chosen.id)
            if full_album is None:
                self._mark_status('album', album_id, 'error')
                self.stats['errors'] += 1
                logger.warning("Album '%s' matched but full details unavailable, will retry", album_name)
                return

            self._update_album(album_id, full_album)
            self.stats['matched'] += 1
            logger.info("Matched album '%s' -> JioSaavn ID: %s", album_name, chosen.id)
        else:
            self._mark_status('album', album_id, 'not_found')
            self.stats['not_found'] += 1
            logger.debug("No match for album '%s'", album_name)

    def _process_track(self, track_id: int, track_name: str, artist_name: str):
        if honor_stored_match(
            db=self.db, entity_table='tracks', entity_id=track_id,
            id_column='jiosaavn_id',
            client_fetch_fn=self.client.get_track_details,
            on_match_fn=self._refresh_track_via_stored_id,
            log_prefix='JioSaavn',
        ):
            self.stats['matched'] += 1
            return

        query = f"{artist_name} {track_name}".strip()
        results = self.client.search_tracks(query, limit=5)
        chosen = None
        for candidate in results or []:
            if self._name_matches(track_name, candidate.name) and self._artist_matches_result(
                artist_name, candidate.artists
            ):
                chosen = candidate
                break

        if chosen:
            full_track = self.client.get_track_details(chosen.id)
            if full_track is None:
                self._mark_status('track', track_id, 'error')
                self.stats['errors'] += 1
                logger.warning("Track '%s' matched but full details unavailable, will retry", track_name)
                return

            self._update_track(track_id, full_track)
            self.stats['matched'] += 1
            logger.info("Matched track '%s' -> JioSaavn ID: %s", track_name, chosen.id)
        else:
            self._mark_status('track', track_id, 'not_found')
            self.stats['not_found'] += 1
            logger.debug("No match for track '%s'", track_name)

    def _update_artist(self, artist_id: int, data) -> None:
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            artist_js_id = str(getattr(data, 'id', None) or data.get('id'))
            cursor.execute("""
                UPDATE artists SET
                    jiosaavn_id = ?,
                    jiosaavn_match_status = 'matched',
                    jiosaavn_last_attempted = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (artist_js_id, artist_id))

            thumb_url = getattr(data, 'image_url', None) or (data.get('image_url') if isinstance(data, dict) else None)
            if thumb_url:
                cursor.execute("""
                    UPDATE artists SET thumb_url = ?
                    WHERE id = ? AND (thumb_url IS NULL OR thumb_url = '')
                """, (thumb_url, artist_id))

            conn.commit()

        except Exception as e:
            logger.error("Error updating artist #%s with JioSaavn data: %s", artist_id, e)
            raise
        finally:
            if conn:
                conn.close()

    def _update_album(self, album_id: int, data: Dict[str, Any]) -> None:
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            label = data.get('label')
            album_type = data.get('album_type')

            cursor.execute("""
                UPDATE albums SET
                    jiosaavn_id = ?,
                    jiosaavn_match_status = 'matched',
                    jiosaavn_last_attempted = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (str(data.get('id')), album_id))

            if label:
                cursor.execute("""
                    UPDATE albums SET label = ?
                    WHERE id = ? AND (label IS NULL OR label = '')
                """, (label, album_id))

            if album_type:
                cursor.execute("""
                    UPDATE albums SET record_type = ?
                    WHERE id = ? AND (record_type IS NULL OR record_type = '')
                """, (album_type, album_id))

            thumb_url = data.get('image_url')
            if thumb_url:
                cursor.execute("""
                    UPDATE albums SET thumb_url = ?
                    WHERE id = ? AND (thumb_url IS NULL OR thumb_url = '')
                """, (thumb_url, album_id))

            set_album_api_track_count(cursor, album_id, data.get('total_tracks'))

            conn.commit()

        except Exception as e:
            logger.error("Error updating album #%s with JioSaavn data: %s", album_id, e)
            raise
        finally:
            if conn:
                conn.close()

    def _update_track(self, track_id: int, data: Dict[str, Any]) -> None:
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                UPDATE tracks SET
                    jiosaavn_id = ?,
                    jiosaavn_match_status = 'matched',
                    jiosaavn_last_attempted = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (str(data.get('id')), track_id))

            conn.commit()

        except Exception as e:
            logger.error("Error updating track #%s with JioSaavn data: %s", track_id, e)
            raise
        finally:
            if conn:
                conn.close()

    def _mark_status(self, entity_type: str, entity_id: int, status: str) -> None:
        table_map = {'artist': 'artists', 'album': 'albums', 'track': 'tracks'}
        table = table_map.get(entity_type)
        if not table:
            logger.error("Unknown entity type: %s", entity_type)
            return

        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute(f"""
                UPDATE {table} SET
                    jiosaavn_match_status = ?,
                    jiosaavn_last_attempted = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (status, entity_id))
            conn.commit()
        except Exception as e:
            logger.error("Error marking %s #%s status: %s", entity_type, entity_id, e)
        finally:
            if conn:
                conn.close()

    def _count_pending_items(self) -> int:
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    (SELECT COUNT(*) FROM artists WHERE jiosaavn_match_status IS NULL AND id IS NOT NULL) +
                    (SELECT COUNT(*) FROM albums WHERE jiosaavn_match_status IS NULL AND id IS NOT NULL) +
                    (SELECT COUNT(*) FROM tracks WHERE jiosaavn_match_status IS NULL AND id IS NOT NULL)
                AS pending
            """)
            row = cursor.fetchone()
            return row[0] if row else 0
        except Exception as e:
            logger.error("Error counting pending items: %s", e)
            return 0
        finally:
            if conn:
                conn.close()

    def _get_progress_breakdown(self) -> Dict[str, Dict[str, int]]:
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            progress = {}

            for entity, table in (('artists', 'artists'), ('albums', 'albums'), ('tracks', 'tracks')):
                cursor.execute(f"""
                    SELECT
                        COUNT(*) AS total,
                        SUM(CASE WHEN jiosaavn_match_status IS NOT NULL THEN 1 ELSE 0 END) AS processed
                    FROM {table}
                """)
                row = cursor.fetchone()
                if row:
                    total, processed = row[0], row[1] or 0
                    progress[entity] = {
                        'matched': processed,
                        'total': total,
                        'percent': int((processed / total * 100) if total > 0 else 0),
                    }

            return progress

        except Exception as e:
            logger.error("Error getting progress breakdown: %s", e)
            return {}
        finally:
            if conn:
                conn.close()
