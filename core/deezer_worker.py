import json
import re
import threading
import time
from difflib import SequenceMatcher
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
from utils.logging_config import get_logger
from database.music_database import MusicDatabase
from core.deezer_client import DeezerClient

logger = get_logger("deezer_worker")


class DeezerWorker:
    """Background worker for enriching library artists, albums, and tracks with Deezer metadata"""

    def __init__(self, database: MusicDatabase):
        self.db = database
        self.client = DeezerClient()

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

        logger.info("Deezer background worker initialized")

    def start(self):
        """Start the background worker"""
        if self.running:
            logger.warning("Worker already running")
            return

        self.running = True
        self.should_stop = False
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        logger.info("Deezer background worker started")

    def stop(self):
        """Stop the background worker"""
        if not self.running:
            return

        logger.info("Stopping Deezer worker...")
        self.should_stop = True
        self.running = False

        if self.thread:
            self.thread.join(timeout=5)

        logger.info("Deezer worker stopped")

    def pause(self):
        """Pause the worker"""
        if not self.running:
            logger.warning("Worker not running, cannot pause")
            return

        self.paused = True
        logger.info("Deezer worker paused")

    def resume(self):
        """Resume the worker"""
        if not self.running:
            logger.warning("Worker not running, start it first")
            return

        self.paused = False
        logger.info("Deezer worker resumed")

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
        logger.info("Deezer worker thread started")

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

        logger.info("Deezer worker thread finished")

    def _get_next_item(self) -> Optional[Dict[str, Any]]:
        """Get next item to process from priority queue (artists -> albums -> tracks)"""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            # Priority 1: Unattempted artists
            cursor.execute("""
                SELECT id, name
                FROM artists
                WHERE deezer_match_status IS NULL
                ORDER BY id ASC
                LIMIT 1
            """)
            row = cursor.fetchone()
            if row:
                return {'type': 'artist', 'id': row[0], 'name': row[1]}

            # Priority 2: Unattempted albums
            cursor.execute("""
                SELECT a.id, a.title, ar.name AS artist_name, ar.deezer_id AS artist_deezer_id
                FROM albums a
                JOIN artists ar ON a.artist_id = ar.id
                WHERE a.deezer_match_status IS NULL
                ORDER BY a.id ASC
                LIMIT 1
            """)
            row = cursor.fetchone()
            if row:
                return {'type': 'album', 'id': row[0], 'name': row[1], 'artist': row[2], 'artist_deezer_id': row[3]}

            # Priority 3: Unattempted tracks
            cursor.execute("""
                SELECT t.id, t.title, ar.name AS artist_name, ar.deezer_id AS artist_deezer_id
                FROM tracks t
                JOIN artists ar ON t.artist_id = ar.id
                WHERE t.deezer_match_status IS NULL
                ORDER BY t.id ASC
                LIMIT 1
            """)
            row = cursor.fetchone()
            if row:
                return {'type': 'track', 'id': row[0], 'name': row[1], 'artist': row[2], 'artist_deezer_id': row[3]}

            # Priority 4: Retry 'not_found' artists after retry_days
            cutoff_date = datetime.now() - timedelta(days=self.retry_days)
            cursor.execute("""
                SELECT id, name
                FROM artists
                WHERE deezer_match_status = 'not_found'
                  AND deezer_last_attempted < ?
                ORDER BY deezer_last_attempted ASC
                LIMIT 1
            """, (cutoff_date,))
            row = cursor.fetchone()
            if row:
                logger.info(f"Retrying artist '{row[1]}' (last attempted before {cutoff_date})")
                return {'type': 'artist', 'id': row[0], 'name': row[1]}

            # Priority 5: Retry 'not_found' albums
            cursor.execute("""
                SELECT a.id, a.title, ar.name AS artist_name, ar.deezer_id AS artist_deezer_id
                FROM albums a
                JOIN artists ar ON a.artist_id = ar.id
                WHERE a.deezer_match_status = 'not_found'
                  AND a.deezer_last_attempted < ?
                ORDER BY a.deezer_last_attempted ASC
                LIMIT 1
            """, (cutoff_date,))
            row = cursor.fetchone()
            if row:
                return {'type': 'album', 'id': row[0], 'name': row[1], 'artist': row[2], 'artist_deezer_id': row[3]}

            # Priority 6: Retry 'not_found' tracks
            cursor.execute("""
                SELECT t.id, t.title, ar.name AS artist_name, ar.deezer_id AS artist_deezer_id
                FROM tracks t
                JOIN artists ar ON t.artist_id = ar.id
                WHERE t.deezer_match_status = 'not_found'
                  AND t.deezer_last_attempted < ?
                ORDER BY t.deezer_last_attempted ASC
                LIMIT 1
            """, (cutoff_date,))
            row = cursor.fetchone()
            if row:
                return {'type': 'track', 'id': row[0], 'name': row[1], 'artist': row[2], 'artist_deezer_id': row[3]}

            return None

        except Exception as e:
            logger.error(f"Error getting next item: {e}")
            return None
        finally:
            if conn:
                conn.close()

    def _normalize_name(self, name: str) -> str:
        """Normalize name for comparison"""
        name = name.lower().strip()
        name = re.sub(r'\s*\(.*?\)\s*', ' ', name)
        name = re.sub(r'[^\w\s]', '', name)
        name = re.sub(r'\s+', ' ', name).strip()
        return name

    def _name_matches(self, query_name: str, result_name: str) -> bool:
        """Check if Deezer result name matches our query with fuzzy matching"""
        norm_query = self._normalize_name(query_name)
        norm_result = self._normalize_name(result_name)

        similarity = SequenceMatcher(None, norm_query, norm_result).ratio()
        logger.debug(f"Name similarity: '{query_name}' vs '{result_name}' = {similarity:.2f}")
        return similarity >= self.name_similarity_threshold

    def _verify_artist_id(self, item: Dict[str, Any], result_artist_id) -> bool:
        """Verify that the result's artist ID matches the parent artist's stored Deezer ID.
        If mismatched, the album/track search is more specific (uses artist+title),
        so we trust it and correct the parent artist's deezer_id."""
        parent_deezer_id = item.get('artist_deezer_id')
        if not parent_deezer_id:
            return True

        if not result_artist_id:
            return True

        if str(result_artist_id) != str(parent_deezer_id):
            logger.info(
                f"Artist ID correction from {item['type']} '{item['name']}': "
                f"updating parent artist Deezer ID from {parent_deezer_id} to {result_artist_id}"
            )
            self._correct_artist_deezer_id(item, str(result_artist_id))

        return True

    def _correct_artist_deezer_id(self, item: Dict[str, Any], correct_deezer_id: str):
        """Correct the parent artist's deezer_id based on a more specific album/track match"""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            table = 'albums' if item['type'] == 'album' else 'tracks'
            cursor.execute(f"SELECT artist_id FROM {table} WHERE id = ?", (item['id'],))
            row = cursor.fetchone()
            if not row:
                return

            artist_id = row[0]
            cursor.execute("""
                UPDATE artists SET
                    deezer_id = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (correct_deezer_id, artist_id))
            conn.commit()

            logger.info(f"Corrected artist #{artist_id} Deezer ID to {correct_deezer_id}")

        except Exception as e:
            logger.error(f"Error correcting artist Deezer ID: {e}")
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

    def _process_artist(self, artist_id: int, artist_name: str):
        """Process an artist: search Deezer, verify, store metadata"""
        result = self.client.search_artist(artist_name)
        if result:
            result_name = result.get('name', '')
            if self._name_matches(artist_name, result_name):
                self._update_artist(artist_id, result)
                self.stats['matched'] += 1
                logger.info(f"Matched artist '{artist_name}' -> Deezer ID: {result.get('id')}")
            else:
                self._mark_status('artist', artist_id, 'not_found')
                self.stats['not_found'] += 1
                logger.debug(f"Name mismatch for artist '{artist_name}' (got '{result_name}')")
        else:
            self._mark_status('artist', artist_id, 'not_found')
            self.stats['not_found'] += 1
            logger.debug(f"No match for artist '{artist_name}'")

    def _process_album(self, album_id: int, album_name: str, artist_name: str, item: Dict[str, Any]):
        """Process an album: search Deezer, verify, fetch full details, store metadata"""
        result = self.client.search_album(artist_name, album_name)
        if result:
            result_name = result.get('title', '')
            if self._name_matches(album_name, result_name):
                # Verify artist ID
                result_artist = result.get('artist', {})
                result_artist_id = result_artist.get('id') if result_artist else None
                self._verify_artist_id(item, result_artist_id)

                # Fetch full album details for label, genres, explicit
                deezer_album_id = result.get('id')
                full_album = self.client.get_album(deezer_album_id) if deezer_album_id else None

                self._update_album(album_id, result, full_album)
                self.stats['matched'] += 1
                logger.info(f"Matched album '{album_name}' -> Deezer ID: {deezer_album_id}")
            else:
                self._mark_status('album', album_id, 'not_found')
                self.stats['not_found'] += 1
                logger.debug(f"Name mismatch for album '{album_name}' (got '{result_name}')")
        else:
            self._mark_status('album', album_id, 'not_found')
            self.stats['not_found'] += 1
            logger.debug(f"No match for album '{album_name}'")

    def _process_track(self, track_id: int, track_name: str, artist_name: str, item: Dict[str, Any]):
        """Process a track: search Deezer, verify, fetch full details for BPM, store metadata"""
        result = self.client.search_track(artist_name, track_name)
        if result:
            result_name = result.get('title', '')
            if self._name_matches(track_name, result_name):
                # Verify artist ID
                result_artist = result.get('artist', {})
                result_artist_id = result_artist.get('id') if result_artist else None
                self._verify_artist_id(item, result_artist_id)

                # Fetch full track details for BPM
                deezer_track_id = result.get('id')
                full_track = self.client.get_track(deezer_track_id) if deezer_track_id else None

                self._update_track(track_id, result, full_track)
                self.stats['matched'] += 1
                logger.info(f"Matched track '{track_name}' -> Deezer ID: {deezer_track_id}")
            else:
                self._mark_status('track', track_id, 'not_found')
                self.stats['not_found'] += 1
                logger.debug(f"Name mismatch for track '{track_name}' (got '{result_name}')")
        else:
            self._mark_status('track', track_id, 'not_found')
            self.stats['not_found'] += 1
            logger.debug(f"No match for track '{track_name}'")

    def _update_artist(self, artist_id: int, data: Dict[str, Any]):
        """Store Deezer metadata for an artist"""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                UPDATE artists SET
                    deezer_id = ?,
                    deezer_match_status = 'matched',
                    deezer_last_attempted = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (
                str(data.get('id')),
                artist_id
            ))

            # Backfill thumb_url if artist has no image
            thumb_url = data.get('picture_xl')
            if thumb_url:
                cursor.execute("""
                    UPDATE artists SET thumb_url = ?
                    WHERE id = ? AND (thumb_url IS NULL OR thumb_url = '')
                """, (thumb_url, artist_id))

            conn.commit()

        except Exception as e:
            logger.error(f"Error updating artist #{artist_id} with Deezer data: {e}")
            raise
        finally:
            if conn:
                conn.close()

    def _update_album(self, album_id: int, search_data: Dict[str, Any], full_data: Optional[Dict[str, Any]]):
        """Store Deezer metadata for an album"""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            # Use full_data if available, otherwise fall back to search_data
            data = full_data or search_data

            label = data.get('label') if full_data else None
            explicit = 1 if data.get('explicit_lyrics') else 0
            record_type = data.get('record_type')  # album, single, ep, compilation

            cursor.execute("""
                UPDATE albums SET
                    deezer_id = ?,
                    deezer_match_status = 'matched',
                    deezer_last_attempted = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (
                str(search_data.get('id')),
                album_id
            ))

            # Update label if available
            if label:
                cursor.execute("""
                    UPDATE albums SET label = ?
                    WHERE id = ? AND (label IS NULL OR label = '')
                """, (label, album_id))

            # Update explicit flag
            if full_data and 'explicit_lyrics' in full_data:
                cursor.execute("""
                    UPDATE albums SET explicit = ?
                    WHERE id = ? AND explicit IS NULL
                """, (explicit, album_id))

            # Update record_type
            if record_type:
                cursor.execute("""
                    UPDATE albums SET record_type = ?
                    WHERE id = ? AND (record_type IS NULL OR record_type = '')
                """, (record_type, album_id))

            # Backfill thumb_url if album has no image
            thumb_url = search_data.get('cover_xl') or (data.get('cover_xl') if full_data else None)
            if thumb_url:
                cursor.execute("""
                    UPDATE albums SET thumb_url = ?
                    WHERE id = ? AND (thumb_url IS NULL OR thumb_url = '')
                """, (thumb_url, album_id))

            # Backfill genres from full album data
            if full_data:
                genres_data = full_data.get('genres', {}).get('data', [])
                if genres_data:
                    genre_names = [g.get('name') for g in genres_data if g.get('name')]
                    if genre_names:
                        cursor.execute("""
                            UPDATE albums SET genres = ?
                            WHERE id = ? AND (genres IS NULL OR genres = '' OR genres = '[]')
                        """, (json.dumps(genre_names), album_id))

            conn.commit()

        except Exception as e:
            logger.error(f"Error updating album #{album_id} with Deezer data: {e}")
            raise
        finally:
            if conn:
                conn.close()

    def _update_track(self, track_id: int, search_data: Dict[str, Any], full_data: Optional[Dict[str, Any]]):
        """Store Deezer metadata for a track (BPM is the crown jewel)"""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            data = full_data or search_data

            bpm = data.get('bpm') if full_data else None
            explicit = 1 if data.get('explicit_lyrics') else 0

            cursor.execute("""
                UPDATE tracks SET
                    deezer_id = ?,
                    deezer_match_status = 'matched',
                    deezer_last_attempted = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (
                str(search_data.get('id')),
                track_id
            ))

            # Update BPM if available and non-zero
            if bpm and bpm > 0:
                cursor.execute("""
                    UPDATE tracks SET bpm = ?
                    WHERE id = ? AND (bpm IS NULL OR bpm = 0)
                """, (float(bpm), track_id))

            # Update explicit flag
            if full_data and 'explicit_lyrics' in full_data:
                cursor.execute("""
                    UPDATE tracks SET explicit = ?
                    WHERE id = ? AND explicit IS NULL
                """, (explicit, track_id))

            conn.commit()

        except Exception as e:
            logger.error(f"Error updating track #{track_id} with Deezer data: {e}")
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
                    deezer_match_status = ?,
                    deezer_last_attempted = CURRENT_TIMESTAMP,
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
                    (SELECT COUNT(*) FROM artists WHERE deezer_match_status IS NULL) +
                    (SELECT COUNT(*) FROM albums WHERE deezer_match_status IS NULL) +
                    (SELECT COUNT(*) FROM tracks WHERE deezer_match_status IS NULL)
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
                    SUM(CASE WHEN deezer_match_status IS NOT NULL THEN 1 ELSE 0 END) AS processed
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
                    SUM(CASE WHEN deezer_match_status IS NOT NULL THEN 1 ELSE 0 END) AS processed
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
                    SUM(CASE WHEN deezer_match_status IS NOT NULL THEN 1 ELSE 0 END) AS processed
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
