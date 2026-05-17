import re
import threading
import time
from difflib import SequenceMatcher
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
from utils.logging_config import get_logger
from database.music_database import MusicDatabase
from core.amazon_client import AmazonClient
from core.worker_utils import interruptible_sleep, set_album_api_track_count
from core.enrichment.manual_match_honoring import honor_stored_match

logger = get_logger("amazon_worker")


class AmazonWorker:
    """Background worker for enriching library artists, albums, and tracks with Amazon Music metadata."""

    def __init__(self, database: MusicDatabase):
        self.db = database
        self.client = AmazonClient()

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

        logger.info("Amazon background worker initialized")

    def start(self):
        if self.running:
            logger.warning("Worker already running")
            return

        self.running = True
        self.should_stop = False
        self._stop_event.clear()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        logger.info("Amazon background worker started")

    def stop(self):
        if not self.running:
            return

        logger.info("Stopping Amazon worker...")
        self.should_stop = True
        self.running = False
        self._stop_event.set()

        if self.thread:
            self.thread.join(timeout=1)

        logger.info("Amazon worker stopped")

    def pause(self):
        if not self.running:
            logger.warning("Worker not running, cannot pause")
            return
        self.paused = True
        logger.info("Amazon worker paused")

    def resume(self):
        if not self.running:
            logger.warning("Worker not running, start it first")
            return
        self.paused = False
        logger.info("Amazon worker resumed")

    def get_stats(self) -> Dict[str, Any]:
        self.stats['pending'] = self._count_pending_items()
        progress = self._get_progress_breakdown()
        is_actually_running = self.running and (self.thread is not None and self.thread.is_alive())
        is_idle = is_actually_running and not self.paused and self.stats['pending'] == 0 and self.current_item is None
        return {
            'enabled': True,
            'running': is_actually_running and not self.paused,
            'paused': self.paused,
            'idle': is_idle,
            'current_item': self.current_item,
            'stats': self.stats.copy(),
            'progress': progress,
        }

    def _run(self):
        logger.info("Amazon worker thread started")
        while not self.should_stop:
            try:
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
                    logger.warning(f"Skipping {item.get('type', 'unknown')} with NULL id: {item.get('name', '?')}")
                    continue

                self._process_item(item)
                interruptible_sleep(self._stop_event, 2)

            except Exception as e:
                logger.error(f"Error in worker loop: {e}")
                interruptible_sleep(self._stop_event, 5)

        logger.info("Amazon worker thread finished")

    def _get_next_item(self) -> Optional[Dict[str, Any]]:
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            # Priority 1: Unattempted artists
            cursor.execute("""
                SELECT id, name FROM artists
                WHERE amazon_match_status IS NULL AND id IS NOT NULL
                ORDER BY id ASC LIMIT 1
            """)
            row = cursor.fetchone()
            if row:
                return {'type': 'artist', 'id': row[0], 'name': row[1]}

            # Priority 2: Unattempted albums
            cursor.execute("""
                SELECT a.id, a.title, ar.name AS artist_name, ar.amazon_id AS artist_amazon_id
                FROM albums a
                JOIN artists ar ON a.artist_id = ar.id
                WHERE a.amazon_match_status IS NULL AND a.id IS NOT NULL
                ORDER BY a.id ASC LIMIT 1
            """)
            row = cursor.fetchone()
            if row:
                return {'type': 'album', 'id': row[0], 'name': row[1], 'artist': row[2], 'artist_amazon_id': row[3]}

            # Priority 3: Unattempted tracks
            cursor.execute("""
                SELECT t.id, t.title, ar.name AS artist_name, ar.amazon_id AS artist_amazon_id
                FROM tracks t
                JOIN artists ar ON t.artist_id = ar.id
                WHERE t.amazon_match_status IS NULL AND t.id IS NOT NULL
                ORDER BY t.id ASC LIMIT 1
            """)
            row = cursor.fetchone()
            if row:
                return {'type': 'track', 'id': row[0], 'name': row[1], 'artist': row[2], 'artist_amazon_id': row[3]}

            not_found_cutoff = datetime.now() - timedelta(days=self.retry_days)

            # Priority 4: Retry not_found artists
            cursor.execute("""
                SELECT id, name FROM artists
                WHERE amazon_match_status = 'not_found' AND amazon_last_attempted < ?
                ORDER BY amazon_last_attempted ASC LIMIT 1
            """, (not_found_cutoff,))
            row = cursor.fetchone()
            if row:
                logger.info(f"Retrying artist '{row[1]}' (last attempted before cutoff)")
                return {'type': 'artist', 'id': row[0], 'name': row[1]}

            # Priority 5: Retry not_found albums
            cursor.execute("""
                SELECT a.id, a.title, ar.name AS artist_name, ar.amazon_id AS artist_amazon_id
                FROM albums a
                JOIN artists ar ON a.artist_id = ar.id
                WHERE a.amazon_match_status = 'not_found' AND a.amazon_last_attempted < ?
                ORDER BY a.amazon_last_attempted ASC LIMIT 1
            """, (not_found_cutoff,))
            row = cursor.fetchone()
            if row:
                return {'type': 'album', 'id': row[0], 'name': row[1], 'artist': row[2], 'artist_amazon_id': row[3]}

            # Priority 6: Retry not_found tracks
            cursor.execute("""
                SELECT t.id, t.title, ar.name AS artist_name, ar.amazon_id AS artist_amazon_id
                FROM tracks t
                JOIN artists ar ON t.artist_id = ar.id
                WHERE t.amazon_match_status = 'not_found' AND t.amazon_last_attempted < ?
                ORDER BY t.amazon_last_attempted ASC LIMIT 1
            """, (not_found_cutoff,))
            row = cursor.fetchone()
            if row:
                return {'type': 'track', 'id': row[0], 'name': row[1], 'artist': row[2], 'artist_amazon_id': row[3]}

            return None

        except Exception as e:
            logger.error(f"Error getting next item: {e}")
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
        logger.debug(f"Name similarity: '{query_name}' vs '{result_name}' = {similarity:.2f}")
        return similarity >= self.name_similarity_threshold

    def _process_item(self, item: Dict[str, Any]):
        try:
            item_type = item['type']
            item_id = item['id']
            item_name = item['name']
            logger.debug(f"Processing {item_type} #{item_id}: {item_name}")

            if item_type == 'artist':
                self._process_artist(item_id, item_name)
            elif item_type == 'album':
                self._process_album(item_id, item_name, item.get('artist', ''), item)
            elif item_type == 'track':
                self._process_track(item_id, item_name, item.get('artist', ''), item)

        except Exception as e:
            logger.error(f"Error processing {item['type']} #{item['id']}: {e}")
            self.stats['errors'] += 1
            try:
                self._mark_status(item['type'], item['id'], 'error')
            except Exception as e2:
                logger.error(f"Error updating item status: {e2}")

    def _get_existing_id(self, entity_type: str, entity_id: int) -> Optional[str]:
        table_map = {'artist': 'artists', 'album': 'albums', 'track': 'tracks'}
        table = table_map.get(entity_type)
        if not table:
            return None
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute(f"SELECT amazon_id FROM {table} WHERE id = ?", (entity_id,))
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
            logger.debug(f"Preserving existing Amazon ID for artist '{artist_name}': {existing_id}")
            return

        results = self.client.search_artists(artist_name, limit=5)
        if results:
            result = results[0]
            if self._name_matches(artist_name, result.name):
                self._update_artist(artist_id, result)
                self.stats['matched'] += 1
                logger.info(f"Matched artist '{artist_name}' -> Amazon ID: {result.id}")
            else:
                self._mark_status('artist', artist_id, 'not_found')
                self.stats['not_found'] += 1
                logger.debug(f"Name mismatch for artist '{artist_name}' (got '{result.name}')")
        else:
            self._mark_status('artist', artist_id, 'not_found')
            self.stats['not_found'] += 1
            logger.debug(f"No match for artist '{artist_name}'")

    def _refresh_album_via_stored_id(self, album_id, stored_id, api_data):
        self._update_album(album_id, api_data, stored_id)

    def _refresh_track_via_stored_id(self, track_id, stored_id, api_data):
        self._update_track(track_id, api_data, stored_id)

    def _process_album(self, album_id: int, album_name: str, artist_name: str, item: Dict[str, Any]):
        if honor_stored_match(
            db=self.db, entity_table='albums', entity_id=album_id,
            id_column='amazon_id',
            client_fetch_fn=lambda asin: self.client.get_album(asin, include_tracks=False),
            on_match_fn=self._refresh_album_via_stored_id,
            log_prefix='Amazon',
        ):
            self.stats['matched'] += 1
            return

        query = f"{artist_name} {album_name}"
        results = self.client.search_albums(query, limit=10)
        if results:
            result = results[0]
            if self._name_matches(album_name, result.name):
                full_album = None
                if result.id:
                    try:
                        full_album = self.client.get_album(result.id, include_tracks=False)
                    except Exception as e:
                        logger.warning(f"Failed to fetch full album '{album_name}' (ASIN: {result.id}): {e}")

                if full_album is None:
                    self._mark_status('album', album_id, 'error')
                    self.stats['errors'] += 1
                    logger.warning(f"Album '{album_name}' matched but full details unavailable, will retry")
                    return

                self._update_album(album_id, full_album, result.id)
                self.stats['matched'] += 1
                logger.info(f"Matched album '{album_name}' -> Amazon ASIN: {result.id}")
            else:
                self._mark_status('album', album_id, 'not_found')
                self.stats['not_found'] += 1
                logger.debug(f"Name mismatch for album '{album_name}' (got '{result.name}')")
        else:
            self._mark_status('album', album_id, 'not_found')
            self.stats['not_found'] += 1
            logger.debug(f"No match for album '{album_name}'")

    def _process_track(self, track_id: int, track_name: str, artist_name: str, item: Dict[str, Any]):
        if honor_stored_match(
            db=self.db, entity_table='tracks', entity_id=track_id,
            id_column='amazon_id',
            client_fetch_fn=self.client.get_track_details,
            on_match_fn=self._refresh_track_via_stored_id,
            log_prefix='Amazon',
        ):
            self.stats['matched'] += 1
            return

        query = f"{artist_name} {track_name}"
        results = self.client.search_tracks(query, limit=10)
        if results:
            result = results[0]
            if self._name_matches(track_name, result.name):
                full_track = None
                if result.id:
                    try:
                        full_track = self.client.get_track_details(result.id)
                    except Exception as e:
                        logger.warning(f"Failed to fetch full track '{track_name}' (ASIN: {result.id}): {e}")

                if full_track is None:
                    self._mark_status('track', track_id, 'error')
                    self.stats['errors'] += 1
                    logger.warning(f"Track '{track_name}' matched but full details unavailable, will retry")
                    return

                self._update_track(track_id, full_track, result.id)
                self.stats['matched'] += 1
                logger.info(f"Matched track '{track_name}' -> Amazon ASIN: {result.id}")
            else:
                self._mark_status('track', track_id, 'not_found')
                self.stats['not_found'] += 1
                logger.debug(f"Name mismatch for track '{track_name}' (got '{result.name}')")
        else:
            self._mark_status('track', track_id, 'not_found')
            self.stats['not_found'] += 1
            logger.debug(f"No match for track '{track_name}'")

    def _update_artist(self, artist_id: int, result):
        """Store Amazon metadata for an artist. ``result`` is an Artist dataclass."""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                UPDATE artists SET
                    amazon_id = ?,
                    amazon_match_status = 'matched',
                    amazon_last_attempted = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (str(result.id), artist_id))

            # Backfill thumb_url from album cover stand-in when artist has no image
            image_url = result.image_url
            if not image_url:
                try:
                    image_url = self.client._get_artist_image_from_albums(result.id)
                except Exception:
                    pass
            if image_url:
                cursor.execute("""
                    UPDATE artists SET thumb_url = ?
                    WHERE id = ? AND (thumb_url IS NULL OR thumb_url = '')
                """, (image_url, artist_id))

            conn.commit()

        except Exception as e:
            logger.error(f"Error updating artist #{artist_id} with Amazon data: {e}")
            raise
        finally:
            if conn:
                conn.close()

    def _update_album(self, album_id: int, full_data: Dict[str, Any], asin: str):
        """Store Amazon metadata for an album. ``full_data`` is a get_album() dict."""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                UPDATE albums SET
                    amazon_id = ?,
                    amazon_match_status = 'matched',
                    amazon_last_attempted = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (asin, album_id))

            # Backfill label when missing
            label = full_data.get('label')
            if label:
                cursor.execute("""
                    UPDATE albums SET label = ?
                    WHERE id = ? AND (label IS NULL OR label = '')
                """, (label, album_id))

            # Backfill thumb_url
            images = full_data.get('images') or []
            thumb_url = images[0].get('url') if images else None
            if thumb_url:
                cursor.execute("""
                    UPDATE albums SET thumb_url = ?
                    WHERE id = ? AND (thumb_url IS NULL OR thumb_url = '')
                """, (thumb_url, album_id))

            # Cache authoritative track count for completeness repair
            total_tracks = full_data.get('total_tracks') or (
                full_data.get('tracks', {}).get('total') if isinstance(full_data.get('tracks'), dict) else None
            )
            set_album_api_track_count(cursor, album_id, total_tracks)

            conn.commit()

        except Exception as e:
            logger.error(f"Error updating album #{album_id} with Amazon data: {e}")
            raise
        finally:
            if conn:
                conn.close()

    def _update_track(self, track_id: int, full_data: Dict[str, Any], asin: str):
        """Store Amazon metadata for a track. ``full_data`` is a get_track_details() dict."""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                UPDATE tracks SET
                    amazon_id = ?,
                    amazon_match_status = 'matched',
                    amazon_last_attempted = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (asin, track_id))

            conn.commit()

        except Exception as e:
            logger.error(f"Error updating track #{track_id} with Amazon data: {e}")
            raise
        finally:
            if conn:
                conn.close()

    def _mark_status(self, entity_type: str, entity_id: int, status: str):
        table_map = {'artist': 'artists', 'album': 'albums', 'track': 'tracks'}
        table = table_map.get(entity_type)
        if not table:
            logger.error(f"Unknown entity type: {entity_type}")
            return

        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute(f"""
                UPDATE {table} SET
                    amazon_match_status = ?,
                    amazon_last_attempted = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (status, entity_id))
            conn.commit()
        except Exception as e:
            logger.error(f"Error marking {entity_type} #{entity_id} status: {e}")
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
                    (SELECT COUNT(*) FROM artists WHERE amazon_match_status IS NULL AND id IS NOT NULL) +
                    (SELECT COUNT(*) FROM albums  WHERE amazon_match_status IS NULL AND id IS NOT NULL) +
                    (SELECT COUNT(*) FROM tracks  WHERE amazon_match_status IS NULL AND id IS NOT NULL)
                AS pending
            """)
            row = cursor.fetchone()
            return row[0] if row else 0
        except Exception as e:
            logger.error(f"Error counting pending items: {e}")
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

            for table in ('artists', 'albums', 'tracks'):
                cursor.execute(f"""
                    SELECT
                        COUNT(*) AS total,
                        SUM(CASE WHEN amazon_match_status IS NOT NULL THEN 1 ELSE 0 END) AS processed
                    FROM {table}
                """)
                row = cursor.fetchone()
                if row:
                    total, processed = row[0], row[1] or 0
                    progress[table] = {
                        'matched': processed,
                        'total': total,
                        'percent': int((processed / total * 100) if total > 0 else 0),
                    }

            return progress

        except Exception as e:
            logger.error(f"Error getting progress breakdown: {e}")
            return {}
        finally:
            if conn:
                conn.close()
