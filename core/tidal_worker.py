import re
import threading
import time
from difflib import SequenceMatcher
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
from utils.logging_config import get_logger
from database.music_database import MusicDatabase
from core.tidal_client import TidalClient
from core.worker_utils import interruptible_sleep

logger = get_logger("tidal_worker")


def _parse_duration_to_ms(duration) -> Optional[int]:
    """Convert duration to milliseconds. Handles integer seconds and ISO-8601 strings (PT3M36S)."""
    if not duration:
        return None
    if isinstance(duration, (int, float)) and duration > 0:
        return int(duration * 1000)
    if isinstance(duration, str) and duration.startswith('PT'):
        total_seconds = 0
        hours_match = re.search(r'(\d+)H', duration)
        minutes_match = re.search(r'(\d+)M', duration)
        seconds_match = re.search(r'(\d+)S', duration)
        if hours_match:
            total_seconds += int(hours_match.group(1)) * 3600
        if minutes_match:
            total_seconds += int(minutes_match.group(1)) * 60
        if seconds_match:
            total_seconds += int(seconds_match.group(1))
        if total_seconds > 0:
            return total_seconds * 1000
    return None


class TidalWorker:
    """Background worker for enriching library artists, albums, and tracks with Tidal metadata"""

    def __init__(self, database: MusicDatabase, client: TidalClient = None):
        self.db = database
        self.client = client or TidalClient()

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

        logger.info("Tidal background worker initialized")

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
        logger.info("Tidal background worker started")

    def stop(self):
        """Stop the background worker"""
        if not self.running:
            return

        logger.info("Stopping Tidal worker...")
        self.should_stop = True
        self.running = False
        self._stop_event.set()

        if self.thread:
            self.thread.join(timeout=1)

        logger.info("Tidal worker stopped")

    def pause(self):
        """Pause the worker"""
        if not self.running:
            logger.warning("Worker not running, cannot pause")
            return
        self.paused = True
        logger.info("Tidal worker paused")

    def resume(self):
        """Resume the worker"""
        if not self.running:
            logger.warning("Worker not running, start it first")
            return
        self.paused = False
        logger.info("Tidal worker resumed")

    def get_stats(self) -> Dict[str, Any]:
        """Get current statistics"""
        self.stats['pending'] = self._count_pending_items()

        progress = self._get_progress_breakdown()

        is_actually_running = self.running and (self.thread is not None and self.thread.is_alive())
        is_idle = is_actually_running and not self.paused and self.stats['pending'] == 0 and self.current_item is None

        authenticated = False
        try:
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
        logger.info("Tidal worker thread started")

        while not self.should_stop:
            try:
                if self.paused:
                    interruptible_sleep(self._stop_event, 1)
                    continue

                # Auth guard: sleep if not authenticated
                try:
                    if not self.client.is_authenticated():
                        self.current_item = None
                        interruptible_sleep(self._stop_event, 30)
                        continue
                except Exception:
                    interruptible_sleep(self._stop_event, 30)
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

                interruptible_sleep(self._stop_event, 2)

            except Exception as e:
                logger.error(f"Error in worker loop: {e}")
                interruptible_sleep(self._stop_event, 5)

        logger.info("Tidal worker thread finished")

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
                WHERE tidal_match_status IS NULL AND id IS NOT NULL
                ORDER BY id ASC
                LIMIT 1
            """)
            row = cursor.fetchone()
            if row:
                return {'type': 'artist', 'id': row[0], 'name': row[1]}

            # Priority 2: Unattempted albums
            cursor.execute("""
                SELECT a.id, a.title, ar.name AS artist_name, ar.tidal_id AS artist_tidal_id
                FROM albums a
                JOIN artists ar ON a.artist_id = ar.id
                WHERE a.tidal_match_status IS NULL AND a.id IS NOT NULL
                ORDER BY a.id ASC
                LIMIT 1
            """)
            row = cursor.fetchone()
            if row:
                return {'type': 'album', 'id': row[0], 'name': row[1], 'artist': row[2], 'artist_tidal_id': row[3]}

            # Priority 3: Unattempted tracks
            cursor.execute("""
                SELECT t.id, t.title, ar.name AS artist_name, ar.tidal_id AS artist_tidal_id
                FROM tracks t
                JOIN artists ar ON t.artist_id = ar.id
                WHERE t.tidal_match_status IS NULL AND t.id IS NOT NULL
                ORDER BY t.id ASC
                LIMIT 1
            """)
            row = cursor.fetchone()
            if row:
                return {'type': 'track', 'id': row[0], 'name': row[1], 'artist': row[2], 'artist_tidal_id': row[3]}

            # Priority 4: Retry 'not_found' artists
            not_found_cutoff = datetime.now() - timedelta(days=self.retry_days)
            cursor.execute("""
                SELECT id, name
                FROM artists
                WHERE tidal_match_status = 'not_found' AND tidal_last_attempted < ?
                ORDER BY tidal_last_attempted ASC
                LIMIT 1
            """, (not_found_cutoff,))
            row = cursor.fetchone()
            if row:
                logger.info(f"Retrying artist '{row[1]}' (last attempted before cutoff)")
                return {'type': 'artist', 'id': row[0], 'name': row[1]}

            # Priority 5: Retry 'not_found' albums
            cursor.execute("""
                SELECT a.id, a.title, ar.name AS artist_name, ar.tidal_id AS artist_tidal_id
                FROM albums a
                JOIN artists ar ON a.artist_id = ar.id
                WHERE a.tidal_match_status = 'not_found' AND a.tidal_last_attempted < ?
                ORDER BY a.tidal_last_attempted ASC
                LIMIT 1
            """, (not_found_cutoff,))
            row = cursor.fetchone()
            if row:
                return {'type': 'album', 'id': row[0], 'name': row[1], 'artist': row[2], 'artist_tidal_id': row[3]}

            # Priority 6: Retry 'not_found' tracks
            cursor.execute("""
                SELECT t.id, t.title, ar.name AS artist_name, ar.tidal_id AS artist_tidal_id
                FROM tracks t
                JOIN artists ar ON t.artist_id = ar.id
                WHERE t.tidal_match_status = 'not_found' AND t.tidal_last_attempted < ?
                ORDER BY t.tidal_last_attempted ASC
                LIMIT 1
            """, (not_found_cutoff,))
            row = cursor.fetchone()
            if row:
                return {'type': 'track', 'id': row[0], 'name': row[1], 'artist': row[2], 'artist_tidal_id': row[3]}

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
        """Check if Tidal result name matches our query with fuzzy matching"""
        norm_query = self._normalize_name(query_name)
        norm_result = self._normalize_name(result_name)

        similarity = SequenceMatcher(None, norm_query, norm_result).ratio()
        logger.debug(f"Name similarity: '{query_name}' vs '{result_name}' = {similarity:.2f}")
        return similarity >= self.name_similarity_threshold

    def _verify_artist_id(self, item: Dict[str, Any], result_artist_id) -> bool:
        """Verify/correct parent artist's Tidal ID based on album/track match"""
        parent_tidal_id = item.get('artist_tidal_id')
        if not parent_tidal_id or not result_artist_id:
            return True

        if str(result_artist_id) != str(parent_tidal_id):
            logger.info(
                f"Artist ID correction from {item['type']} '{item['name']}': "
                f"updating parent artist Tidal ID from {parent_tidal_id} to {result_artist_id}"
            )
            self._correct_artist_tidal_id(item, str(result_artist_id))

        return True

    def _correct_artist_tidal_id(self, item: Dict[str, Any], correct_tidal_id: str):
        """Correct the parent artist's tidal_id based on a more specific album/track match"""
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
                    tidal_id = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (correct_tidal_id, artist_id))
            conn.commit()

            logger.info(f"Corrected artist #{artist_id} Tidal ID to {correct_tidal_id}")

        except Exception as e:
            logger.error(f"Error correcting artist Tidal ID: {e}")
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
                # Rate limit — don't mark as error, back off then retry
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
        """Check if an entity already has a tidal_id (e.g. from manual match)."""
        table_map = {'artist': 'artists', 'album': 'albums', 'track': 'tracks'}
        table = table_map.get(entity_type)
        if not table:
            return None
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute(f"SELECT tidal_id FROM {table} WHERE id = ?", (entity_id,))
            row = cursor.fetchone()
            return row[0] if row and row[0] else None
        except Exception:
            return None
        finally:
            if conn:
                conn.close()

    def _process_artist(self, artist_id: int, artist_name: str):
        """Process an artist: search Tidal, verify, store metadata"""
        existing_id = self._get_existing_id('artist', artist_id)
        if existing_id:
            logger.debug(f"Preserving existing Tidal ID for artist '{artist_name}': {existing_id}")
            return

        result = self.client.search_artist(artist_name)
        if result:
            result_name = result.get('name', '')
            if self._name_matches(artist_name, result_name):
                tidal_artist_id = result.get('id')
                if not tidal_artist_id:
                    self._mark_status('artist', artist_id, 'error')
                    self.stats['errors'] += 1
                    logger.warning(f"Tidal search result for '{artist_name}' has no ID")
                    return

                # Fetch full artist details for image
                full_artist = None
                try:
                    full_artist = self.client.get_artist(tidal_artist_id)
                except Exception as e:
                    logger.warning(f"Failed to fetch full artist details for '{artist_name}': {e}")

                self._update_artist(artist_id, result, full_artist)
                self.stats['matched'] += 1
                logger.info(f"Matched artist '{artist_name}' -> Tidal ID: {tidal_artist_id}")
            else:
                self._mark_status('artist', artist_id, 'not_found')
                self.stats['not_found'] += 1
                logger.debug(f"Name mismatch for artist '{artist_name}' (got '{result_name}')")
        else:
            self._mark_status('artist', artist_id, 'not_found')
            self.stats['not_found'] += 1
            logger.debug(f"No match for artist '{artist_name}'")

    def _process_album(self, album_id: int, album_name: str, artist_name: str, item: Dict[str, Any]):
        """Process an album: search Tidal, verify, fetch full details, store metadata"""
        existing_id = self._get_existing_id('album', album_id)
        if existing_id:
            logger.debug(f"Preserving existing Tidal ID for album '{album_name}': {existing_id}")
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
                tidal_album_id = result.get('id')
                if not tidal_album_id:
                    self._mark_status('album', album_id, 'error')
                    self.stats['errors'] += 1
                    logger.warning(f"Tidal search result for album '{album_name}' has no ID")
                    return

                full_album = None
                try:
                    full_album = self.client.get_album(tidal_album_id)
                except Exception as e:
                    logger.warning(f"Failed to fetch full album details for '{album_name}': {e}")

                if full_album is None:
                    self._mark_status('album', album_id, 'error')
                    self.stats['errors'] += 1
                    logger.warning(f"Album '{album_name}' matched but full details unavailable, will retry")
                    return

                self._update_album(album_id, result, full_album)
                self.stats['matched'] += 1
                logger.info(f"Matched album '{album_name}' -> Tidal ID: {tidal_album_id}")
            else:
                self._mark_status('album', album_id, 'not_found')
                self.stats['not_found'] += 1
                logger.debug(f"Name mismatch for album '{album_name}' (got '{result_name}')")
        else:
            self._mark_status('album', album_id, 'not_found')
            self.stats['not_found'] += 1
            logger.debug(f"No match for album '{album_name}'")

    def _process_track(self, track_id: int, track_name: str, artist_name: str, item: Dict[str, Any]):
        """Process a track: search Tidal, verify, fetch full details, store metadata"""
        existing_id = self._get_existing_id('track', track_id)
        if existing_id:
            logger.debug(f"Preserving existing Tidal ID for track '{track_name}': {existing_id}")
            return

        result = self.client.search_track(artist_name, track_name)
        if result:
            result_name = result.get('title', '')
            if self._name_matches(track_name, result_name):
                # Verify artist ID
                result_artist = result.get('artist', {})
                result_artist_id = result_artist.get('id') if result_artist else None
                self._verify_artist_id(item, result_artist_id)

                # Fetch full track details
                tidal_track_id = result.get('id')
                if not tidal_track_id:
                    self._mark_status('track', track_id, 'error')
                    self.stats['errors'] += 1
                    logger.warning(f"Tidal search result for track '{track_name}' has no ID")
                    return

                full_track = None
                try:
                    full_track = self.client.get_track(tidal_track_id)
                except Exception as e:
                    logger.warning(f"Failed to fetch full track details for '{track_name}': {e}")

                if full_track is None:
                    self._mark_status('track', track_id, 'error')
                    self.stats['errors'] += 1
                    logger.warning(f"Track '{track_name}' matched but full details unavailable, will retry")
                    return

                self._update_track(track_id, result, full_track)
                self.stats['matched'] += 1
                logger.info(f"Matched track '{track_name}' -> Tidal ID: {tidal_track_id}")
            else:
                self._mark_status('track', track_id, 'not_found')
                self.stats['not_found'] += 1
                logger.debug(f"Name mismatch for track '{track_name}' (got '{result_name}')")
        else:
            self._mark_status('track', track_id, 'not_found')
            self.stats['not_found'] += 1
            logger.debug(f"No match for track '{track_name}'")

    def _update_artist(self, artist_id: int, data: Dict[str, Any], full_data: Optional[Dict[str, Any]] = None):
        """Store Tidal metadata for an artist"""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                UPDATE artists SET
                    tidal_id = ?,
                    tidal_match_status = 'matched',
                    tidal_last_attempted = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (
                str(data.get('id')),
                artist_id
            ))
            conn.commit()

            # Backfill optional metadata (failures here won't lose the match)
            try:
                thumb_url = None
                if full_data:
                    # V2 detail may have picture array or picture URL
                    pictures = full_data.get('picture', [])
                    if isinstance(pictures, list) and pictures:
                        # Pick largest available
                        for size in ['1080x1080', '750x750', '480x480', '320x320']:
                            for pic in pictures:
                                if isinstance(pic, dict) and size in pic.get('url', ''):
                                    thumb_url = pic['url']
                                    break
                            if thumb_url:
                                break
                        if not thumb_url and isinstance(pictures[0], dict):
                            thumb_url = pictures[0].get('url')
                        elif not thumb_url and isinstance(pictures[0], str):
                            thumb_url = pictures[0]
                    elif isinstance(pictures, str):
                        thumb_url = pictures
                    # Also check imageLinks (JSON:API attributes are flattened to top level)
                    if not thumb_url:
                        pic_links = full_data.get('imageLinks', [])
                        if pic_links:
                            for pl in pic_links:
                                if isinstance(pl, dict):
                                    thumb_url = pl.get('href', '')
                                    break

                if not thumb_url:
                    thumb_url = data.get('picture', data.get('image', ''))
                    if isinstance(thumb_url, list) and thumb_url:
                        thumb_url = thumb_url[0].get('url', '') if isinstance(thumb_url[0], dict) else str(thumb_url[0])

                if thumb_url:
                    cursor.execute("""
                        UPDATE artists SET thumb_url = ?
                        WHERE id = ? AND (thumb_url IS NULL OR thumb_url = '')
                    """, (thumb_url, artist_id))

                conn.commit()
            except Exception as e:
                logger.warning(f"Backfill failed for artist #{artist_id} (match preserved): {e}")

        except Exception as e:
            logger.error(f"Error updating artist #{artist_id} with Tidal data: {e}")
            raise
        finally:
            if conn:
                conn.close()

    def _update_album(self, album_id: int, search_data: Dict[str, Any], full_data: Optional[Dict[str, Any]]):
        """Store Tidal metadata for an album"""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            data = full_data or search_data

            cursor.execute("""
                UPDATE albums SET
                    tidal_id = ?,
                    tidal_match_status = 'matched',
                    tidal_last_attempted = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (
                str(search_data.get('id')),
                album_id
            ))
            conn.commit()

            # Backfill optional metadata (failures here won't lose the match)
            try:
                # Backfill label (can be string or dict with 'name' key in JSON:API)
                label = data.get('label')
                if isinstance(label, dict):
                    label = label.get('name', '')
                if label:
                    cursor.execute("""
                        UPDATE albums SET label = ?
                        WHERE id = ? AND (label IS NULL OR label = '')
                    """, (str(label), album_id))

                # Backfill explicit flag
                explicit = data.get('explicit')
                if explicit is not None:
                    cursor.execute("""
                        UPDATE albums SET explicit = ?
                        WHERE id = ? AND explicit IS NULL
                    """, (1 if explicit else 0, album_id))

                # Backfill UPC
                upc = data.get('upc', data.get('barcodeId', ''))
                if upc:
                    cursor.execute("""
                        UPDATE albums SET upc = ?
                        WHERE id = ? AND (upc IS NULL OR upc = '')
                    """, (str(upc), album_id))

                # Backfill track_count
                num_tracks = data.get('numberOfTracks', data.get('numberOfItems'))
                if num_tracks and isinstance(num_tracks, int) and num_tracks > 0:
                    cursor.execute("""
                        UPDATE albums SET track_count = ?
                        WHERE id = ? AND track_count IS NULL
                    """, (num_tracks, album_id))

                # Backfill duration (Tidal returns seconds or ISO-8601, DB stores milliseconds)
                duration_ms = _parse_duration_to_ms(data.get('duration'))
                if duration_ms:
                    cursor.execute("""
                        UPDATE albums SET duration = ?
                        WHERE id = ? AND duration IS NULL
                    """, (duration_ms, album_id))

                # Backfill copyright (can be string or dict with 'text' key in JSON:API)
                copyright_text = data.get('copyright')
                if isinstance(copyright_text, dict):
                    copyright_text = copyright_text.get('text', copyright_text.get('name', ''))
                if copyright_text and isinstance(copyright_text, str):
                    cursor.execute("""
                        UPDATE albums SET copyright = ?
                        WHERE id = ? AND (copyright IS NULL OR copyright = '')
                    """, (copyright_text, album_id))

                # Backfill thumb_url
                thumb_url = None
                cover = data.get('cover', data.get('image', ''))
                if isinstance(cover, list) and cover:
                    thumb_url = cover[0].get('url', '') if isinstance(cover[0], dict) else str(cover[0])
                elif isinstance(cover, str) and cover:
                    thumb_url = cover
                # JSON:API imageLinks (attributes are flattened to top level)
                if not thumb_url:
                    img_links = data.get('imageLinks', [])
                    if img_links:
                        for il in img_links:
                            if isinstance(il, dict):
                                thumb_url = il.get('href', '')
                                break
                if thumb_url:
                    cursor.execute("""
                        UPDATE albums SET thumb_url = ?
                        WHERE id = ? AND (thumb_url IS NULL OR thumb_url = '')
                    """, (thumb_url, album_id))

                conn.commit()
            except Exception as e:
                logger.warning(f"Backfill failed for album #{album_id} (match preserved): {e}")

        except Exception as e:
            logger.error(f"Error updating album #{album_id} with Tidal data: {e}")
            raise
        finally:
            if conn:
                conn.close()

    def _update_track(self, track_id: int, search_data: Dict[str, Any], full_data: Optional[Dict[str, Any]]):
        """Store Tidal metadata for a track"""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            data = full_data or search_data

            cursor.execute("""
                UPDATE tracks SET
                    tidal_id = ?,
                    tidal_match_status = 'matched',
                    tidal_last_attempted = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (
                str(search_data.get('id')),
                track_id
            ))
            conn.commit()

            # Backfill optional metadata (failures here won't lose the match)
            try:
                explicit = data.get('explicit')
                if explicit is not None:
                    cursor.execute("""
                        UPDATE tracks SET explicit = ?
                        WHERE id = ? AND explicit IS NULL
                    """, (1 if explicit else 0, track_id))

                isrc = data.get('isrc')
                if isinstance(isrc, dict):
                    isrc = isrc.get('value', isrc.get('id', ''))
                if isrc and isinstance(isrc, str):
                    cursor.execute("""
                        UPDATE tracks SET isrc = ?
                        WHERE id = ? AND (isrc IS NULL OR isrc = '')
                    """, (isrc, track_id))

                duration_ms = _parse_duration_to_ms(data.get('duration'))
                if duration_ms:
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
            logger.error(f"Error updating track #{track_id} with Tidal data: {e}")
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
                    tidal_match_status = ?,
                    tidal_last_attempted = CURRENT_TIMESTAMP,
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
                    (SELECT COUNT(*) FROM artists WHERE tidal_match_status IS NULL AND id IS NOT NULL) +
                    (SELECT COUNT(*) FROM albums WHERE tidal_match_status IS NULL AND id IS NOT NULL) +
                    (SELECT COUNT(*) FROM tracks WHERE tidal_match_status IS NULL AND id IS NOT NULL)
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
                    SUM(CASE WHEN tidal_match_status IS NOT NULL THEN 1 ELSE 0 END) AS processed
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
                    SUM(CASE WHEN tidal_match_status IS NOT NULL THEN 1 ELSE 0 END) AS processed
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
                    SUM(CASE WHEN tidal_match_status IS NOT NULL THEN 1 ELSE 0 END) AS processed
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
