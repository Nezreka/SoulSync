import json
import re
import threading
import time
from difflib import SequenceMatcher
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
from utils.logging_config import get_logger
from database.music_database import MusicDatabase
from core.audiodb_client import AudioDBClient

logger = get_logger("audiodb_worker")


class AudioDBWorker:
    """Background worker for enriching library artists, albums, and tracks with AudioDB metadata"""

    def __init__(self, database: MusicDatabase):
        self.db = database
        self.client = AudioDBClient()

        # Worker state
        self.running = False
        self.paused = False
        self.should_stop = False
        self.thread = None

        # Current item being processed (for UI tooltip)
        self.current_item = None

        # Statistics
        self.stats = {
            'matched': 0,
            'not_found': 0,
            'pending': 0,
            'errors': 0
        }

        # Retry configuration
        self.retry_days = 30

        # Name matching threshold
        self.name_similarity_threshold = 0.80

        logger.info("AudioDB background worker initialized")

    def start(self):
        """Start the background worker"""
        if self.running:
            logger.warning("Worker already running")
            return

        self.running = True
        self.should_stop = False
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        logger.info("AudioDB background worker started")

    def stop(self):
        """Stop the background worker"""
        if not self.running:
            return

        logger.info("Stopping AudioDB worker...")
        self.should_stop = True
        self.running = False

        if self.thread:
            self.thread.join(timeout=5)

        logger.info("AudioDB worker stopped")

    def pause(self):
        """Pause the worker"""
        if not self.running:
            logger.warning("Worker not running, cannot pause")
            return

        self.paused = True
        logger.info("AudioDB worker paused")

    def resume(self):
        """Resume the worker"""
        if not self.running:
            logger.warning("Worker not running, start it first")
            return

        self.paused = False
        logger.info("AudioDB worker resumed")

    def get_stats(self) -> Dict[str, Any]:
        """Get current statistics"""
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
            'progress': progress
        }

    def _run(self):
        """Main worker loop"""
        logger.info("AudioDB worker thread started")

        while not self.should_stop:
            try:
                if self.paused:
                    time.sleep(1)
                    continue

                self.current_item = None

                item = self._get_next_item()

                if not item:
                    logger.debug("No pending items, sleeping...")
                    time.sleep(10)
                    continue

                self.current_item = item

                self._process_item(item)

                time.sleep(2)

            except Exception as e:
                logger.error(f"Error in worker loop: {e}")
                time.sleep(5)

        logger.info("AudioDB worker thread finished")

    def _get_next_item(self) -> Optional[Dict[str, Any]]:
        """Get next item to process from priority queue (artists → albums → tracks)"""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            # Priority 1: Unattempted artists
            cursor.execute("""
                SELECT id, name
                FROM artists
                WHERE audiodb_match_status IS NULL
                ORDER BY id ASC
                LIMIT 1
            """)
            row = cursor.fetchone()
            if row:
                return {'type': 'artist', 'id': row[0], 'name': row[1]}

            # Priority 2: Unattempted albums
            cursor.execute("""
                SELECT a.id, a.title, ar.name AS artist_name, ar.audiodb_id AS artist_audiodb_id
                FROM albums a
                JOIN artists ar ON a.artist_id = ar.id
                WHERE a.audiodb_match_status IS NULL
                ORDER BY a.id ASC
                LIMIT 1
            """)
            row = cursor.fetchone()
            if row:
                return {'type': 'album', 'id': row[0], 'name': row[1], 'artist': row[2], 'artist_audiodb_id': row[3]}

            # Priority 3: Unattempted tracks
            cursor.execute("""
                SELECT t.id, t.title, ar.name AS artist_name, ar.audiodb_id AS artist_audiodb_id
                FROM tracks t
                JOIN artists ar ON t.artist_id = ar.id
                WHERE t.audiodb_match_status IS NULL
                ORDER BY t.id ASC
                LIMIT 1
            """)
            row = cursor.fetchone()
            if row:
                return {'type': 'track', 'id': row[0], 'name': row[1], 'artist': row[2], 'artist_audiodb_id': row[3]}

            # Priority 4: Retry 'not_found' artists after retry_days
            cutoff_date = datetime.now() - timedelta(days=self.retry_days)
            cursor.execute("""
                SELECT id, name
                FROM artists
                WHERE audiodb_match_status = 'not_found'
                  AND audiodb_last_attempted < ?
                ORDER BY audiodb_last_attempted ASC
                LIMIT 1
            """, (cutoff_date,))
            row = cursor.fetchone()
            if row:
                logger.info(f"Retrying artist '{row[1]}' (last attempted before {cutoff_date})")
                return {'type': 'artist', 'id': row[0], 'name': row[1]}

            # Priority 5: Retry 'not_found' albums
            cursor.execute("""
                SELECT a.id, a.title, ar.name AS artist_name, ar.audiodb_id AS artist_audiodb_id
                FROM albums a
                JOIN artists ar ON a.artist_id = ar.id
                WHERE a.audiodb_match_status = 'not_found'
                  AND a.audiodb_last_attempted < ?
                ORDER BY a.audiodb_last_attempted ASC
                LIMIT 1
            """, (cutoff_date,))
            row = cursor.fetchone()
            if row:
                return {'type': 'album', 'id': row[0], 'name': row[1], 'artist': row[2], 'artist_audiodb_id': row[3]}

            # Priority 6: Retry 'not_found' tracks
            cursor.execute("""
                SELECT t.id, t.title, ar.name AS artist_name, ar.audiodb_id AS artist_audiodb_id
                FROM tracks t
                JOIN artists ar ON t.artist_id = ar.id
                WHERE t.audiodb_match_status = 'not_found'
                  AND t.audiodb_last_attempted < ?
                ORDER BY t.audiodb_last_attempted ASC
                LIMIT 1
            """, (cutoff_date,))
            row = cursor.fetchone()
            if row:
                return {'type': 'track', 'id': row[0], 'name': row[1], 'artist': row[2], 'artist_audiodb_id': row[3]}

            return None

        except Exception as e:
            logger.error(f"Error getting next item: {e}")
            return None
        finally:
            if conn:
                conn.close()

    def _normalize_name(self, name: str) -> str:
        """Normalize artist name for comparison"""
        name = name.lower().strip()
        name = re.sub(r'\s*\(.*?\)\s*', ' ', name)
        name = re.sub(r'[^\w\s]', '', name)
        name = re.sub(r'\s+', ' ', name).strip()
        return name

    def _verify_artist_id(self, item: Dict[str, Any], result: Dict[str, Any]) -> bool:
        """Verify that the result's artist ID matches the parent artist's stored AudioDB ID.
        If mismatched, the album/track search is more specific (uses artist+title),
        so we trust it and correct the parent artist's audiodb_id."""
        parent_audiodb_id = item.get('artist_audiodb_id')
        if not parent_audiodb_id:
            return True

        result_artist_id = result.get('idArtist')
        if not result_artist_id:
            return True

        if str(result_artist_id) != str(parent_audiodb_id):
            logger.info(
                f"Artist ID correction from {item['type']} '{item['name']}': "
                f"updating parent artist AudioDB ID from {parent_audiodb_id} to {result_artist_id}"
            )
            self._correct_artist_audiodb_id(item, str(result_artist_id))

        return True

    def _correct_artist_audiodb_id(self, item: Dict[str, Any], correct_audiodb_id: str):
        """Correct the parent artist's audiodb_id based on a more specific album/track match"""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            # Find the artist_id from the album/track
            table = 'albums' if item['type'] == 'album' else 'tracks'
            cursor.execute(f"SELECT artist_id FROM {table} WHERE id = ?", (item['id'],))
            row = cursor.fetchone()
            if not row:
                return

            artist_id = row[0]
            cursor.execute("""
                UPDATE artists SET
                    audiodb_id = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (correct_audiodb_id, artist_id))
            conn.commit()

            logger.info(f"Corrected artist #{artist_id} AudioDB ID to {correct_audiodb_id}")

        except Exception as e:
            logger.error(f"Error correcting artist AudioDB ID: {e}")
        finally:
            if conn:
                conn.close()

    def _name_matches(self, query_name: str, result_name: str) -> bool:
        """Check if AudioDB result name matches our query with fuzzy matching"""
        norm_query = self._normalize_name(query_name)
        norm_result = self._normalize_name(result_name)

        similarity = SequenceMatcher(None, norm_query, norm_result).ratio()
        logger.debug(f"Name similarity: '{query_name}' vs '{result_name}' = {similarity:.2f}")
        return similarity >= self.name_similarity_threshold

    def _process_item(self, item: Dict[str, Any]):
        """Process a single item (artist, album, or track)"""
        try:
            item_type = item['type']
            item_id = item['id']
            item_name = item['name']

            logger.debug(f"Processing {item_type} #{item_id}: {item_name}")

            if item_type == 'artist':
                result = self.client.search_artist(item_name)
                if result:
                    result_name = result.get('strArtist', '')
                    if self._name_matches(item_name, result_name):
                        self._update_artist(item_id, result)
                        self.stats['matched'] += 1
                        logger.info(f"Matched artist '{item_name}' -> AudioDB ID: {result.get('idArtist')}")
                    else:
                        self._mark_status('artist', item_id, 'not_found')
                        self.stats['not_found'] += 1
                        logger.debug(f"Name mismatch for artist '{item_name}' (got '{result_name}')")
                else:
                    self._mark_status('artist', item_id, 'not_found')
                    self.stats['not_found'] += 1
                    logger.debug(f"No match for artist '{item_name}'")

            elif item_type == 'album':
                artist_name = item.get('artist', '')
                result = self.client.search_album(artist_name, item_name)
                if result:
                    result_name = result.get('strAlbum', '')
                    if self._name_matches(item_name, result_name):
                        self._verify_artist_id(item, result)
                        self._update_album(item_id, result)
                        self.stats['matched'] += 1
                        logger.info(f"Matched album '{item_name}' -> AudioDB ID: {result.get('idAlbum')}")
                    else:
                        self._mark_status('album', item_id, 'not_found')
                        self.stats['not_found'] += 1
                        logger.debug(f"Name mismatch for album '{item_name}' (got '{result_name}')")
                else:
                    self._mark_status('album', item_id, 'not_found')
                    self.stats['not_found'] += 1
                    logger.debug(f"No match for album '{item_name}'")

            elif item_type == 'track':
                artist_name = item.get('artist', '')
                result = self.client.search_track(artist_name, item_name)
                if result:
                    result_name = result.get('strTrack', '')
                    if self._name_matches(item_name, result_name):
                        self._verify_artist_id(item, result)
                        self._update_track(item_id, result)
                        self.stats['matched'] += 1
                        logger.info(f"Matched track '{item_name}' -> AudioDB ID: {result.get('idTrack')}")
                    else:
                        self._mark_status('track', item_id, 'not_found')
                        self.stats['not_found'] += 1
                        logger.debug(f"Name mismatch for track '{item_name}' (got '{result_name}')")
                else:
                    self._mark_status('track', item_id, 'not_found')
                    self.stats['not_found'] += 1
                    logger.debug(f"No match for track '{item_name}'")

        except Exception as e:
            logger.error(f"Error processing {item['type']} #{item['id']}: {e}")
            self.stats['errors'] += 1
            try:
                self._mark_status(item['type'], item['id'], 'error')
            except Exception as e2:
                logger.error(f"Error updating item status: {e2}")

    def _update_artist(self, artist_id: int, data: Dict[str, Any]):
        """Store AudioDB metadata for an artist using generic column names"""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            # Update AudioDB tracking + generic metadata columns
            cursor.execute("""
                UPDATE artists SET
                    audiodb_id = ?,
                    audiodb_match_status = 'matched',
                    audiodb_last_attempted = CURRENT_TIMESTAMP,
                    style = ?,
                    mood = ?,
                    label = ?,
                    banner_url = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (
                data.get('idArtist'),
                data.get('strStyle'),
                data.get('strMood'),
                data.get('strLabel'),
                data.get('strArtistBanner'),
                artist_id
            ))

            # Backfill thumb_url if artist has no image
            thumb_url = data.get('strArtistThumb')
            if thumb_url:
                cursor.execute("""
                    UPDATE artists SET thumb_url = ?
                    WHERE id = ? AND (thumb_url IS NULL OR thumb_url = '')
                """, (thumb_url, artist_id))

            # Backfill genres if artist has none
            genre = data.get('strGenre')
            if genre:
                cursor.execute("""
                    UPDATE artists SET genres = ?
                    WHERE id = ? AND (genres IS NULL OR genres = '' OR genres = '[]')
                """, (json.dumps([genre]), artist_id))

            conn.commit()

        except Exception as e:
            logger.error(f"Error updating artist #{artist_id} with AudioDB data: {e}")
            raise
        finally:
            if conn:
                conn.close()

    def _update_album(self, album_id: int, data: Dict[str, Any]):
        """Store AudioDB metadata for an album using generic column names"""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                UPDATE albums SET
                    audiodb_id = ?,
                    audiodb_match_status = 'matched',
                    audiodb_last_attempted = CURRENT_TIMESTAMP,
                    style = ?,
                    mood = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (
                data.get('idAlbum'),
                data.get('strStyle'),
                data.get('strMood'),
                album_id
            ))

            # Backfill thumb_url if album has no image
            thumb_url = data.get('strAlbumThumb')
            if thumb_url:
                cursor.execute("""
                    UPDATE albums SET thumb_url = ?
                    WHERE id = ? AND (thumb_url IS NULL OR thumb_url = '')
                """, (thumb_url, album_id))

            # Backfill genres if album has none
            genre = data.get('strGenre')
            if genre:
                cursor.execute("""
                    UPDATE albums SET genres = ?
                    WHERE id = ? AND (genres IS NULL OR genres = '' OR genres = '[]')
                """, (json.dumps([genre]), album_id))

            conn.commit()

        except Exception as e:
            logger.error(f"Error updating album #{album_id} with AudioDB data: {e}")
            raise
        finally:
            if conn:
                conn.close()

    def _update_track(self, track_id: int, data: Dict[str, Any]):
        """Store AudioDB metadata for a track using generic column names"""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                UPDATE tracks SET
                    audiodb_id = ?,
                    audiodb_match_status = 'matched',
                    audiodb_last_attempted = CURRENT_TIMESTAMP,
                    style = ?,
                    mood = ?
                WHERE id = ?
            """, (
                data.get('idTrack'),
                data.get('strStyle'),
                data.get('strMood'),
                track_id
            ))

            conn.commit()

        except Exception as e:
            logger.error(f"Error updating track #{track_id} with AudioDB data: {e}")
            raise
        finally:
            if conn:
                conn.close()

    def _mark_status(self, entity_type: str, entity_id: int, status: str):
        """Mark an entity (artist, album, or track) with a match status"""
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
                    audiodb_match_status = ?,
                    audiodb_last_attempted = CURRENT_TIMESTAMP,
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
        """Count how many items still need processing across all entity types"""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT
                    (SELECT COUNT(*) FROM artists WHERE audiodb_match_status IS NULL) +
                    (SELECT COUNT(*) FROM albums WHERE audiodb_match_status IS NULL) +
                    (SELECT COUNT(*) FROM tracks WHERE audiodb_match_status IS NULL)
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
        """Get progress breakdown by entity type"""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            progress = {}

            # Artists progress
            cursor.execute("""
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN audiodb_match_status IS NOT NULL THEN 1 ELSE 0 END) AS processed
                FROM artists
            """)
            row = cursor.fetchone()
            if row:
                total, processed = row[0], row[1] or 0
                progress['artists'] = {
                    'matched': processed,
                    'total': total,
                    'percent': int((processed / total * 100) if total > 0 else 0)
                }

            # Albums progress
            cursor.execute("""
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN audiodb_match_status IS NOT NULL THEN 1 ELSE 0 END) AS processed
                FROM albums
            """)
            row = cursor.fetchone()
            if row:
                total, processed = row[0], row[1] or 0
                progress['albums'] = {
                    'matched': processed,
                    'total': total,
                    'percent': int((processed / total * 100) if total > 0 else 0)
                }

            # Tracks progress
            cursor.execute("""
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN audiodb_match_status IS NOT NULL THEN 1 ELSE 0 END) AS processed
                FROM tracks
            """)
            row = cursor.fetchone()
            if row:
                total, processed = row[0], row[1] or 0
                progress['tracks'] = {
                    'matched': processed,
                    'total': total,
                    'percent': int((processed / total * 100) if total > 0 else 0)
                }

            return progress

        except Exception as e:
            logger.error(f"Error getting progress breakdown: {e}")
            return {}
        finally:
            if conn:
                conn.close()
