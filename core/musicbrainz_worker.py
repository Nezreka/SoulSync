import threading
import time
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
from utils.logging_config import get_logger
from database.music_database import MusicDatabase
from core.musicbrainz_service import MusicBrainzService

logger = get_logger("musicbrainz_worker")

class MusicBrainzWorker:
    """Background worker for enriching library with MusicBrainz IDs"""

    def __init__(self, database: MusicDatabase, app_name: str = "SoulSync", app_version: str = "1.0", contact_email: str = ""):
        self.db = database
        self.mb_service = MusicBrainzService(database, app_name, app_version, contact_email)

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
        self.retry_days = 30  # Retry 'not_found' items after 30 days

        logger.info("MusicBrainz background worker initialized")

    def start(self):
        """Start the background worker"""
        if self.running:
            logger.warning("Worker already running")
            return

        self.running = True
        self.should_stop = False
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        logger.info("MusicBrainz background worker started")

    def stop(self):
        """Stop the background worker"""
        if not self.running:
            return

        logger.info("Stopping MusicBrainz worker...")
        self.should_stop = True
        self.running = False

        if self.thread:
            self.thread.join(timeout=5)

        logger.info("Music Brainz worker stopped")

    def pause(self):
        """Pause the worker"""
        if not self.running:
            logger.warning("Worker not running, cannot pause")
            return

        self.paused = True
        logger.info("MusicBrainz worker paused")

    def resume(self):
        """Resume the worker"""
        if not self.running:
            logger.warning("Worker not running, start it first")
            return

        self.paused = False
        logger.info("MusicBrainz worker resumed")

    def get_stats(self) -> Dict[str, Any]:
        """Get current statistics"""
        # Update pending count
        self.stats['pending'] = self._count_pending_items()

        # Get progress breakdown by entity type
        progress = self._get_progress_breakdown()

        # Check if thread is actually alive (in case it crashed)
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
        logger.info("MusicBrainz worker thread started")

        while not self.should_stop:
            try:
                # Check if paused
                if self.paused:
                    time.sleep(1)
                    continue

                # Clear previous item before getting next
                self.current_item = None

                # Get next item to process
                item = self._get_next_item()

                if not item:
                    # No more items - sleep for a bit
                    logger.debug("No pending items, sleeping...")
                    time.sleep(10)
                    continue

                # Set current item for UI tracking
                self.current_item = item

                # Process the item
                self._process_item(item)

                # Keep current_item set during sleep so UI can see what was just processed
                # Rate limit: 1 request per second
                time.sleep(1)

            except Exception as e:
                logger.error(f"Error in worker loop: {e}")
                time.sleep(5)  # Back off on errors

        logger.info("MusicBrainz worker thread finished")

    def _get_next_item(self) -> Optional[Dict[str, Any]]:
        """Get next item to process from priority queue"""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            # Priority 1: Unattempted artists
            cursor.execute("""
                SELECT id, name
                FROM artists
                WHERE musicbrainz_match_status IS NULL
                ORDER BY id ASC
                LIMIT 1
            """)
            row = cursor.fetchone()
            if row:
                return {'type': 'artist', 'id': row[0], 'name': row[1]}

            # Priority 2: Unattempted albums
            cursor.execute("""
                SELECT a.id, a.title, ar.name AS artist_name
                FROM albums a
                JOIN artists ar ON a.artist_id = ar.id
                WHERE a.musicbrainz_match_status IS NULL
                ORDER BY a.id ASC
                LIMIT 1
            """)
            row = cursor.fetchone()
            if row:
                return {'type': 'album', 'id': row[0], 'name': row[1], 'artist': row[2]}

            # Priority 3: Unattempted tracks
            cursor.execute("""
                SELECT t.id, t.title, ar.name AS artist_name
                FROM tracks t
                JOIN artists ar ON t.artist_id = ar.id
                WHERE t.musicbrainz_match_status IS NULL
                ORDER BY t.id ASC
                LIMIT 1
            """)
            row = cursor.fetchone()
            if row:
                return {'type': 'track', 'id': row[0], 'name': row[1], 'artist': row[2]}

            # Priority 4: Retry 'not_found' artists after retry_days
            cutoff_date = datetime.now() - timedelta(days=self.retry_days)
            cursor.execute("""
                SELECT id, name
                FROM artists
                WHERE musicbrainz_match_status = 'not_found'
                  AND musicbrainz_last_attempted < ?
                ORDER BY musicbrainz_last_attempted ASC
                LIMIT 1
            """, (cutoff_date,))
            row = cursor.fetchone()
            if row:
                logger.info(f"Retrying artist '{row[1]}' (last attempted: {cutoff_date})")
                return {'type': 'artist', 'id': row[0], 'name': row[1]}

            # Priority 5: Retry 'not_found' albums
            cursor.execute("""
                SELECT a.id, a.title, ar.name AS artist_name
                FROM albums a
                JOIN artists ar ON a.artist_id = ar.id
                WHERE a.musicbrainz_match_status = 'not_found'
                  AND a.musicbrainz_last_attempted < ?
                ORDER BY a.musicbrainz_last_attempted ASC
                LIMIT 1
            """, (cutoff_date,))
            row = cursor.fetchone()
            if row:
                return {'type': 'album', 'id': row[0], 'name': row[1], 'artist': row[2]}

            # Priority 6: Retry 'not_found' tracks
            cursor.execute("""
                SELECT t.id, t.title, ar.name AS artist_name
                FROM tracks t
                JOIN artists ar ON t.artist_id = ar.id
                WHERE t.musicbrainz_match_status = 'not_found'
                  AND t.musicbrainz_last_attempted < ?
                ORDER BY t.musicbrainz_last_attempted ASC
                LIMIT 1
            """, (cutoff_date,))
            row = cursor.fetchone()
            if row:
                return {'type': 'track', 'id': row[0], 'name': row[1], 'artist': row[2]}

            return None

        except Exception as e:
            logger.error(f"Error getting next item: {e}")
            return None
        finally:
            if conn:
                conn.close()

    def _process_item(self, item: Dict[str, Any]):
        """Process a single item (artist, album, or track)"""
        try:
            item_type = item['type']
            item_id = item['id']
            item_name = item['name']

            logger.debug(f"Processing {item_type} #{item_id}: {item_name}")

            if item_type == 'artist':
                result = self.mb_service.match_artist(item_name)
                if result and result.get('mbid'):
                    self.mb_service.update_artist_mbid(item_id, result['mbid'], 'matched')
                    self.stats['matched'] += 1
                    logger.info(f"✅ Matched artist '{item_name}' → MBID: {result['mbid']}")
                else:
                    self.mb_service.update_artist_mbid(item_id, None, 'not_found')
                    self.stats['not_found'] += 1
                    logger.debug(f"❌ No match for artist '{item_name}'")

            elif item_type == 'album':
                artist_name = item.get('artist')
                result = self.mb_service.match_release(item_name, artist_name)
                if result and result.get('mbid'):
                    self.mb_service.update_album_mbid(item_id, result['mbid'], 'matched')
                    self.stats['matched'] += 1
                    logger.info(f"✅ Matched album '{item_name}' → MBID: {result['mbid']}")
                else:
                    self.mb_service.update_album_mbid(item_id, None, 'not_found')
                    self.stats['not_found'] += 1
                    logger.debug(f"❌ No match for album '{item_name}'")

            elif item_type == 'track':
                artist_name = item.get('artist')
                result = self.mb_service.match_recording(item_name, artist_name)
                if result and result.get('mbid'):
                    self.mb_service.update_track_mbid(item_id, result['mbid'], 'matched')
                    self.stats['matched'] += 1
                    logger.info(f"✅ Matched track '{item_name}' → MBID: {result['mbid']}")
                else:
                    self.mb_service.update_track_mbid(item_id, None, 'not_found')
                    self.stats['not_found'] += 1
                    logger.debug(f"❌ No match for track '{item_name}'")

        except Exception as e:
            logger.error(f"Error processing {item['type']} #{item['id']}: {e}")
            self.stats['errors'] += 1

            # Mark as error in database
            try:
                if item['type'] == 'artist':
                    self.mb_service.update_artist_mbid(item['id'], None, 'error')
                elif item['type'] == 'album':
                    self.mb_service.update_album_mbid(item['id'], None, 'error')
                elif item['type'] == 'track':
                    self.mb_service.update_track_mbid(item['id'], None, 'error')
            except Exception as e2:
                logger.error(f"Error updating item status: {e2}")

    def _count_pending_items(self) -> int:
        """Count how many items still need processing"""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            # Count unattempted items
            cursor.execute("""
                SELECT
                    (SELECT COUNT(*) FROM artists WHERE musicbrainz_match_status IS NULL) +
                    (SELECT COUNT(*) FROM albums WHERE musicbrainz_match_status IS NULL) +
                    (SELECT COUNT(*) FROM tracks WHERE musicbrainz_match_status IS NULL)
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
                    SUM(CASE WHEN musicbrainz_match_status IS NOT NULL THEN 1 ELSE 0 END) AS processed
                FROM artists
            """)
            row = cursor.fetchone()
            if row:
                total, processed = row[0], row[1] or 0
                progress['artists'] = {
                    'matched': processed,  # Actually "processed" count for UI
                    'total': total,
                    'percent': int((processed / total * 100) if total > 0 else 0)
                }

            # Albums progress
            cursor.execute("""
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN musicbrainz_match_status IS NOT NULL THEN 1 ELSE 0 END) AS processed
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
                    SUM(CASE WHEN musicbrainz_match_status IS NOT NULL THEN 1 ELSE 0 END) AS processed
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
