import json
import re
import threading
import time
from difflib import SequenceMatcher
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
from utils.logging_config import get_logger
from database.music_database import MusicDatabase
from core.qobuz_client import _qobuz_is_rate_limited
from core.worker_utils import interruptible_sleep

logger = get_logger("qobuz_worker")


class QobuzWorker:
    """Background worker for enriching library artists, albums, and tracks with Qobuz metadata"""

    def __init__(self, database: MusicDatabase, client=None):
        self.db = database
        self.client = client  # Set externally or created during init in web_server

        # Worker state
        self.running = False
        self.paused = False
        self.should_stop = False
        self.thread = None
        self._stop_event = threading.Event()

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

        logger.info("Qobuz background worker initialized")

    def start(self):
        """Start the background worker"""
        if self.running:
            logger.warning("Worker already running")
            return

        self.running = True
        self.should_stop = False
        self._stop_event.clear()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        logger.info("Qobuz background worker started")

    def stop(self):
        """Stop the background worker"""
        if not self.running:
            return

        logger.info("Stopping Qobuz worker...")
        self.should_stop = True
        self.running = False
        self._stop_event.set()

        if self.thread:
            self.thread.join(timeout=1)

        logger.info("Qobuz worker stopped")

    def pause(self):
        """Pause the worker"""
        if not self.running:
            logger.warning("Worker not running, cannot pause")
            return
        self.paused = True
        logger.info("Qobuz worker paused")

    def resume(self):
        """Resume the worker"""
        if not self.running:
            logger.warning("Worker not running, start it first")
            return
        self.paused = False
        logger.info("Qobuz worker resumed")

    def get_stats(self) -> Dict[str, Any]:
        """Get current statistics"""
        self.stats['pending'] = self._count_pending_items()

        progress = self._get_progress_breakdown()

        is_actually_running = self.running and (self.thread is not None and self.thread.is_alive())
        is_idle = is_actually_running and not self.paused and self.stats['pending'] == 0 and self.current_item is None

        authenticated = False
        try:
            if self.client:
                authenticated = self.client.is_authenticated()
        except Exception:
            pass

        return {
            'enabled': True,
            'running': is_actually_running and not self.paused,
            'paused': self.paused,
            'idle': is_idle,
            'authenticated': authenticated,
            'current_item': self.current_item,
            'stats': self.stats.copy(),
            'progress': progress
        }

    def _run(self):
        """Main worker loop"""
        logger.info("Qobuz worker thread started")

        while not self.should_stop:
            try:
                if self.paused:
                    interruptible_sleep(self._stop_event, 1)
                    continue

                # Auth guard: sleep if not authenticated
                try:
                    if not self.client or not self.client.is_authenticated():
                        self.current_item = None
                        interruptible_sleep(self._stop_event, 30)
                        continue
                except Exception:
                    interruptible_sleep(self._stop_event, 30)
                    continue

                # Rate limit guard: back off if globally rate limited
                if _qobuz_is_rate_limited():
                    self.current_item = None
                    logger.debug("Qobuz rate limited, backing off...")
                    interruptible_sleep(self._stop_event, 10)
                    continue

                self.current_item = None

                item = self._get_next_item()

                if not item:
                    logger.debug("No pending items, sleeping...")
                    interruptible_sleep(self._stop_event, 10)
                    continue

                self.current_item = item
                # Guard: skip items with None/NULL IDs to prevent infinite enrichment loops
                item_id = item.get('id') or item.get('artist_id') or item.get('album_id')
                if item_id is None:
                    logger.warning(f"Skipping {item.get('type', 'unknown')} with NULL id: {item.get('name', '?')} — marking as error")
                    try:
                        itype = item.get('type', '')
                        table = 'artists' if 'artist' in itype else ('albums' if 'album' in itype else 'tracks')
                        # Can't mark status without an ID — just skip
                    except Exception:
                        pass
                    continue


                self._process_item(item)

                # Throttle between API calls
                interruptible_sleep(self._stop_event, 2)

            except Exception as e:
                logger.error(f"Error in worker loop: {e}")
                interruptible_sleep(self._stop_event, 5)

        logger.info("Qobuz worker thread finished")

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
                WHERE qobuz_match_status IS NULL AND id IS NOT NULL
                ORDER BY id ASC
                LIMIT 1
            """)
            row = cursor.fetchone()
            if row:
                return {'type': 'artist', 'id': row[0], 'name': row[1]}

            # Priority 2: Unattempted albums
            cursor.execute("""
                SELECT a.id, a.title, ar.name AS artist_name, ar.qobuz_id AS artist_qobuz_id
                FROM albums a
                JOIN artists ar ON a.artist_id = ar.id
                WHERE a.qobuz_match_status IS NULL AND a.id IS NOT NULL
                ORDER BY a.id ASC
                LIMIT 1
            """)
            row = cursor.fetchone()
            if row:
                return {'type': 'album', 'id': row[0], 'name': row[1], 'artist': row[2], 'artist_qobuz_id': row[3]}

            # Priority 3: Unattempted tracks
            cursor.execute("""
                SELECT t.id, t.title, ar.name AS artist_name, ar.qobuz_id AS artist_qobuz_id
                FROM tracks t
                JOIN artists ar ON t.artist_id = ar.id
                WHERE t.qobuz_match_status IS NULL AND t.id IS NOT NULL
                ORDER BY t.id ASC
                LIMIT 1
            """)
            row = cursor.fetchone()
            if row:
                return {'type': 'track', 'id': row[0], 'name': row[1], 'artist': row[2], 'artist_qobuz_id': row[3]}

            # Priority 4: Retry 'not_found' artists
            not_found_cutoff = datetime.now() - timedelta(days=self.retry_days)
            cursor.execute("""
                SELECT id, name
                FROM artists
                WHERE qobuz_match_status = 'not_found' AND qobuz_last_attempted < ?
                ORDER BY qobuz_last_attempted ASC
                LIMIT 1
            """, (not_found_cutoff,))
            row = cursor.fetchone()
            if row:
                logger.info(f"Retrying artist '{row[1]}' (last attempted before cutoff)")
                return {'type': 'artist', 'id': row[0], 'name': row[1]}

            # Priority 5: Retry 'not_found' albums
            cursor.execute("""
                SELECT a.id, a.title, ar.name AS artist_name, ar.qobuz_id AS artist_qobuz_id
                FROM albums a
                JOIN artists ar ON a.artist_id = ar.id
                WHERE a.qobuz_match_status = 'not_found' AND a.qobuz_last_attempted < ?
                ORDER BY a.qobuz_last_attempted ASC
                LIMIT 1
            """, (not_found_cutoff,))
            row = cursor.fetchone()
            if row:
                return {'type': 'album', 'id': row[0], 'name': row[1], 'artist': row[2], 'artist_qobuz_id': row[3]}

            # Priority 6: Retry 'not_found' tracks
            cursor.execute("""
                SELECT t.id, t.title, ar.name AS artist_name, ar.qobuz_id AS artist_qobuz_id
                FROM tracks t
                JOIN artists ar ON t.artist_id = ar.id
                WHERE t.qobuz_match_status = 'not_found' AND t.qobuz_last_attempted < ?
                ORDER BY t.qobuz_last_attempted ASC
                LIMIT 1
            """, (not_found_cutoff,))
            row = cursor.fetchone()
            if row:
                return {'type': 'track', 'id': row[0], 'name': row[1], 'artist': row[2], 'artist_qobuz_id': row[3]}

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
        name = re.sub(r'\s+[-–—]\s+.*$', '', name)
        name = re.sub(r'\s*\(.*?\)\s*', ' ', name)
        name = re.sub(r'[^\w\s]', '', name)
        name = re.sub(r'\s+', ' ', name).strip()
        return name

    def _name_matches(self, query_name: str, result_name: str) -> bool:
        """Check if Qobuz result name matches our query with fuzzy matching"""
        norm_query = self._normalize_name(query_name)
        norm_result = self._normalize_name(result_name)

        similarity = SequenceMatcher(None, norm_query, norm_result).ratio()
        logger.debug(f"Name similarity: '{query_name}' vs '{result_name}' = {similarity:.2f}")
        return similarity >= self.name_similarity_threshold

    def _verify_artist_id(self, item: Dict[str, Any], result_artist_id) -> bool:
        """Verify/correct parent artist's Qobuz ID based on album/track match"""
        parent_qobuz_id = item.get('artist_qobuz_id')
        if not parent_qobuz_id or not result_artist_id:
            return True

        if str(result_artist_id) != str(parent_qobuz_id):
            logger.info(
                f"Artist ID correction from {item['type']} '{item['name']}': "
                f"updating parent artist Qobuz ID from {parent_qobuz_id} to {result_artist_id}"
            )
            self._correct_artist_qobuz_id(item, str(result_artist_id))

        return True

    def _correct_artist_qobuz_id(self, item: Dict[str, Any], correct_qobuz_id: str):
        """Correct the parent artist's qobuz_id based on a more specific album/track match"""
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
                    qobuz_id = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (correct_qobuz_id, artist_id))
            conn.commit()

            logger.info(f"Corrected artist #{artist_id} Qobuz ID to {correct_qobuz_id}")

        except Exception as e:
            logger.error(f"Error correcting artist Qobuz ID: {e}")
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
            error_str = str(e).lower()
            if '429' in error_str or 'rate limit' in error_str:
                logger.warning(f"Rate limited while processing {item['type']} #{item['id']}, backing off 30s")
                interruptible_sleep(self._stop_event, 30)
                return
            logger.error(f"Error processing {item['type']} #{item['id']}: {e}")
            self.stats['errors'] += 1
            try:
                self._mark_status(item['type'], item['id'], 'error')
            except Exception as e2:
                logger.error(f"Error updating item status: {e2}")

    def _get_existing_id(self, entity_type: str, entity_id: int) -> Optional[str]:
        """Check if an entity already has a qobuz_id (e.g. from manual match)."""
        table_map = {'artist': 'artists', 'album': 'albums', 'track': 'tracks'}
        table = table_map.get(entity_type)
        if not table:
            return None
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute(f"SELECT qobuz_id FROM {table} WHERE id = ?", (entity_id,))
            row = cursor.fetchone()
            return row[0] if row and row[0] else None
        except Exception:
            return None
        finally:
            if conn:
                conn.close()

    def _process_artist(self, artist_id: int, artist_name: str):
        """Process an artist: search Qobuz, verify, store metadata"""
        existing_id = self._get_existing_id('artist', artist_id)
        if existing_id:
            logger.debug(f"Preserving existing Qobuz ID for artist '{artist_name}': {existing_id}")
            return

        result = self.client.search_artist(artist_name)

        if result:
            result_name = result.get('name', '')
            if self._name_matches(artist_name, result_name):
                qobuz_artist_id = result.get('id')
                if not qobuz_artist_id:
                    self._mark_status('artist', artist_id, 'error')
                    self.stats['errors'] += 1
                    logger.warning(f"Qobuz search result for '{artist_name}' has no ID")
                    return

                # Fetch full artist details
                full_artist = None
                try:
                    full_artist = self.client.get_artist(qobuz_artist_id)
                except Exception as e:
                    logger.warning(f"Failed to fetch full artist details for '{artist_name}': {e}")

                self._update_artist(artist_id, result, full_artist)
                self.stats['matched'] += 1
                logger.info(f"Matched artist '{artist_name}' -> Qobuz ID: {qobuz_artist_id}")
            else:
                self._mark_status('artist', artist_id, 'not_found')
                self.stats['not_found'] += 1
                logger.debug(f"Name mismatch for artist '{artist_name}' (got '{result_name}')")
        else:
            if _qobuz_is_rate_limited():
                logger.warning(f"Rate limited while searching artist '{artist_name}', will retry")
                return
            self._mark_status('artist', artist_id, 'not_found')
            self.stats['not_found'] += 1
            logger.debug(f"No match for artist '{artist_name}'")

    def _process_album(self, album_id: int, album_name: str, artist_name: str, item: Dict[str, Any]):
        """Process an album: search Qobuz, verify, fetch full details, store metadata"""
        existing_id = self._get_existing_id('album', album_id)
        if existing_id:
            logger.debug(f"Preserving existing Qobuz ID for album '{album_name}': {existing_id}")
            return

        result = self.client.search_album(artist_name, album_name)

        if result:
            result_name = result.get('title', '')
            if self._name_matches(album_name, result_name):
                # Verify artist ID
                result_artist = result.get('artist', {})
                result_artist_id = result_artist.get('id') if result_artist else None
                self._verify_artist_id(item, result_artist_id)

                # Fetch full album details
                qobuz_album_id = result.get('id')
                if not qobuz_album_id:
                    self._mark_status('album', album_id, 'error')
                    self.stats['errors'] += 1
                    logger.warning(f"Qobuz search result for album '{album_name}' has no ID")
                    return

                full_album = None
                try:
                    full_album = self.client.get_album(qobuz_album_id)
                except Exception as e:
                    logger.warning(f"Failed to fetch full album details for '{album_name}': {e}")

                if full_album is None:
                    if _qobuz_is_rate_limited():
                        logger.warning(f"Rate limited while fetching album '{album_name}', will retry")
                        return
                    self._mark_status('album', album_id, 'error')
                    self.stats['errors'] += 1
                    logger.warning(f"Album '{album_name}' matched but full details unavailable, will retry")
                    return

                self._update_album(album_id, result, full_album)
                self.stats['matched'] += 1
                logger.info(f"Matched album '{album_name}' -> Qobuz ID: {qobuz_album_id}")
            else:
                self._mark_status('album', album_id, 'not_found')
                self.stats['not_found'] += 1
                logger.debug(f"Name mismatch for album '{album_name}' (got '{result_name}')")
        else:
            if _qobuz_is_rate_limited():
                logger.warning(f"Rate limited while searching album '{album_name}', will retry")
                return
            self._mark_status('album', album_id, 'not_found')
            self.stats['not_found'] += 1
            logger.debug(f"No match for album '{album_name}'")

    def _process_track(self, track_id: int, track_name: str, artist_name: str, item: Dict[str, Any]):
        """Process a track: search Qobuz, verify, fetch full details, store metadata"""
        existing_id = self._get_existing_id('track', track_id)
        if existing_id:
            logger.debug(f"Preserving existing Qobuz ID for track '{track_name}': {existing_id}")
            return

        result = self.client.search_track(artist_name, track_name)

        if result:
            result_name = result.get('title', '')
            if self._name_matches(track_name, result_name):
                # Verify artist ID
                result_artist = result.get('artist', result.get('performer', {}))
                result_artist_id = result_artist.get('id') if result_artist else None
                self._verify_artist_id(item, result_artist_id)

                # Fetch full track details
                qobuz_track_id = result.get('id')
                if not qobuz_track_id:
                    self._mark_status('track', track_id, 'error')
                    self.stats['errors'] += 1
                    logger.warning(f"Qobuz search result for track '{track_name}' has no ID")
                    return

                full_track = None
                try:
                    full_track = self.client.get_track(qobuz_track_id)
                except Exception as e:
                    logger.warning(f"Failed to fetch full track details for '{track_name}': {e}")

                if full_track is None:
                    if _qobuz_is_rate_limited():
                        logger.warning(f"Rate limited while fetching track '{track_name}', will retry")
                        return
                    self._mark_status('track', track_id, 'error')
                    self.stats['errors'] += 1
                    logger.warning(f"Track '{track_name}' matched but full details unavailable, will retry")
                    return

                self._update_track(track_id, result, full_track)
                self.stats['matched'] += 1
                logger.info(f"Matched track '{track_name}' -> Qobuz ID: {qobuz_track_id}")
            else:
                self._mark_status('track', track_id, 'not_found')
                self.stats['not_found'] += 1
                logger.debug(f"Name mismatch for track '{track_name}' (got '{result_name}')")
        else:
            if _qobuz_is_rate_limited():
                logger.warning(f"Rate limited while searching track '{track_name}', will retry")
                return
            self._mark_status('track', track_id, 'not_found')
            self.stats['not_found'] += 1
            logger.debug(f"No match for track '{track_name}'")

    def _update_artist(self, artist_id: int, data: Dict[str, Any], full_data: Optional[Dict[str, Any]] = None):
        """Store Qobuz metadata for an artist"""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                UPDATE artists SET
                    qobuz_id = ?,
                    qobuz_match_status = 'matched',
                    qobuz_last_attempted = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (
                str(data.get('id')),
                artist_id
            ))
            conn.commit()

            # Backfill optional metadata (failures here won't lose the match)
            try:
                src = full_data or data
                thumb_url = None
                image = src.get('image', {})
                if isinstance(image, dict):
                    thumb_url = image.get('large', image.get('medium', image.get('small', image.get('thumbnail', ''))))
                elif isinstance(image, str):
                    thumb_url = image
                # Also check picture field
                if not thumb_url:
                    thumb_url = src.get('picture', '')

                if thumb_url:
                    cursor.execute("""
                        UPDATE artists SET thumb_url = ?
                        WHERE id = ? AND (thumb_url IS NULL OR thumb_url = '')
                    """, (thumb_url, artist_id))

                conn.commit()
            except Exception as e:
                logger.warning(f"Backfill failed for artist #{artist_id} (match preserved): {e}")

        except Exception as e:
            logger.error(f"Error updating artist #{artist_id} with Qobuz data: {e}")
            raise
        finally:
            if conn:
                conn.close()

    def _update_album(self, album_id: int, search_data: Dict[str, Any], full_data: Optional[Dict[str, Any]]):
        """Store Qobuz metadata for an album"""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            data = full_data or search_data

            cursor.execute("""
                UPDATE albums SET
                    qobuz_id = ?,
                    qobuz_match_status = 'matched',
                    qobuz_last_attempted = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (
                str(search_data.get('id')),
                album_id
            ))
            conn.commit()

            # Backfill optional metadata (failures here won't lose the match)
            try:
                label = data.get('label', {})
                label_name = label.get('name', '') if isinstance(label, dict) else str(label) if label else ''
                if label_name:
                    cursor.execute("""
                        UPDATE albums SET label = ?
                        WHERE id = ? AND (label IS NULL OR label = '')
                    """, (label_name, album_id))

                parental = data.get('parental_warning')
                if parental is not None:
                    cursor.execute("""
                        UPDATE albums SET explicit = ?
                        WHERE id = ? AND explicit IS NULL
                    """, (1 if parental else 0, album_id))

                genre = data.get('genre', {})
                genre_name = genre.get('name', '') if isinstance(genre, dict) else str(genre) if genre else ''
                if genre_name:
                    cursor.execute("""
                        UPDATE albums SET genres = ?
                        WHERE id = ? AND (genres IS NULL OR genres = '' OR genres = '[]')
                    """, (json.dumps([genre_name]), album_id))

                upc = data.get('upc')
                if upc:
                    cursor.execute("""
                        UPDATE albums SET upc = ?
                        WHERE id = ? AND (upc IS NULL OR upc = '')
                    """, (str(upc), album_id))

                tracks_count = data.get('tracks_count')
                if tracks_count and isinstance(tracks_count, int) and tracks_count > 0:
                    cursor.execute("""
                        UPDATE albums SET track_count = ?
                        WHERE id = ? AND track_count IS NULL
                    """, (tracks_count, album_id))

                duration = data.get('duration')
                if duration and isinstance(duration, (int, float)) and duration > 0:
                    duration_ms = int(duration * 1000)
                    cursor.execute("""
                        UPDATE albums SET duration = ?
                        WHERE id = ? AND duration IS NULL
                    """, (duration_ms, album_id))

                copyright_text = data.get('copyright')
                if isinstance(copyright_text, dict):
                    copyright_text = copyright_text.get('text', copyright_text.get('name', ''))
                if copyright_text and isinstance(copyright_text, str):
                    cursor.execute("""
                        UPDATE albums SET copyright = ?
                        WHERE id = ? AND (copyright IS NULL OR copyright = '')
                    """, (copyright_text, album_id))

                thumb_url = None
                image = data.get('image', {})
                if isinstance(image, dict):
                    thumb_url = image.get('large', image.get('medium', image.get('small', image.get('thumbnail', ''))))
                elif isinstance(image, str):
                    thumb_url = image
                if thumb_url:
                    cursor.execute("""
                        UPDATE albums SET thumb_url = ?
                        WHERE id = ? AND (thumb_url IS NULL OR thumb_url = '')
                    """, (thumb_url, album_id))

                conn.commit()
            except Exception as e:
                logger.warning(f"Backfill failed for album #{album_id} (match preserved): {e}")

        except Exception as e:
            logger.error(f"Error updating album #{album_id} with Qobuz data: {e}")
            raise
        finally:
            if conn:
                conn.close()

    def _update_track(self, track_id: int, search_data: Dict[str, Any], full_data: Optional[Dict[str, Any]]):
        """Store Qobuz metadata for a track"""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            data = full_data or search_data

            cursor.execute("""
                UPDATE tracks SET
                    qobuz_id = ?,
                    qobuz_match_status = 'matched',
                    qobuz_last_attempted = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (
                str(search_data.get('id')),
                track_id
            ))
            conn.commit()

            # Backfill optional metadata (failures here won't lose the match)
            try:
                parental = data.get('parental_warning')
                if parental is not None:
                    cursor.execute("""
                        UPDATE tracks SET explicit = ?
                        WHERE id = ? AND explicit IS NULL
                    """, (1 if parental else 0, track_id))

                isrc = data.get('isrc')
                if isinstance(isrc, dict):
                    isrc = isrc.get('value', isrc.get('id', ''))
                if isrc and isinstance(isrc, str):
                    cursor.execute("""
                        UPDATE tracks SET isrc = ?
                        WHERE id = ? AND (isrc IS NULL OR isrc = '')
                    """, (isrc, track_id))

                duration = data.get('duration')
                if duration and isinstance(duration, (int, float)) and duration > 0:
                    duration_ms = int(duration * 1000)
                    cursor.execute("""
                        UPDATE tracks SET duration = ?
                        WHERE id = ? AND duration IS NULL
                    """, (duration_ms, track_id))

                copyright_text = data.get('copyright')
                if isinstance(copyright_text, dict):
                    copyright_text = copyright_text.get('text', copyright_text.get('name', ''))
                if copyright_text and isinstance(copyright_text, str):
                    cursor.execute("""
                        UPDATE tracks SET copyright = ?
                        WHERE id = ? AND (copyright IS NULL OR copyright = '')
                    """, (copyright_text, track_id))

                conn.commit()
            except Exception as e:
                logger.warning(f"Backfill failed for track #{track_id} (match preserved): {e}")

        except Exception as e:
            logger.error(f"Error updating track #{track_id} with Qobuz data: {e}")
            raise
        finally:
            if conn:
                conn.close()

    def _mark_status(self, entity_type: str, entity_id: int, status: str):
        """Mark an entity with a match status"""
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
                    qobuz_match_status = ?,
                    qobuz_last_attempted = CURRENT_TIMESTAMP,
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
        """Count how many items still need processing"""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT
                    (SELECT COUNT(*) FROM artists WHERE qobuz_match_status IS NULL AND id IS NOT NULL) +
                    (SELECT COUNT(*) FROM albums WHERE qobuz_match_status IS NULL AND id IS NOT NULL) +
                    (SELECT COUNT(*) FROM tracks WHERE qobuz_match_status IS NULL AND id IS NOT NULL)
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

            cursor.execute("""
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN qobuz_match_status IS NOT NULL THEN 1 ELSE 0 END) AS processed
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

            cursor.execute("""
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN qobuz_match_status IS NOT NULL THEN 1 ELSE 0 END) AS processed
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

            cursor.execute("""
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN qobuz_match_status IS NOT NULL THEN 1 ELSE 0 END) AS processed
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
