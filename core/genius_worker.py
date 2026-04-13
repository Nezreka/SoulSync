import json
import re
import threading
import time
from difflib import SequenceMatcher
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
from utils.logging_config import get_logger
from database.music_database import MusicDatabase
from core.genius_client import GeniusClient
from config.settings import config_manager
from core.worker_utils import interruptible_sleep

logger = get_logger("genius_worker")


class GeniusWorker:
    """Background worker for enriching library artists and tracks with Genius metadata.

    Enriches:
      - Artists: Genius ID, description, alternate names, image
      - Tracks: Genius ID, lyrics, description, song art URL
    Note: Genius is song/artist-focused — album enrichment is minimal (ID only from song data).
    """

    def __init__(self, database: MusicDatabase):
        self.db = database
        self._init_client()

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
        self.name_similarity_threshold = 0.75  # Slightly lower — Genius titles often include featured artists

        logger.info("Genius background worker initialized")

    def _init_client(self):
        """Initialize or reinitialize the Genius client from config"""
        access_token = config_manager.get('genius.access_token', '')
        self.client = GeniusClient(access_token=access_token)

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
        logger.info("Genius background worker started")

    def stop(self):
        """Stop the background worker"""
        if not self.running:
            return

        logger.info("Stopping Genius worker...")
        self.should_stop = True
        self.running = False
        self._stop_event.set()

        if self.thread:
            self.thread.join(timeout=1)

        logger.info("Genius worker stopped")

    def pause(self):
        """Pause the worker"""
        if not self.running:
            logger.warning("Worker not running, cannot pause")
            return
        self.paused = True
        logger.info("Genius worker paused")

    def resume(self):
        """Resume the worker"""
        if not self.running:
            logger.warning("Worker not running, start it first")
            return
        self.paused = False
        logger.info("Genius worker resumed")

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
            'authenticated': bool(self.client and self.client.access_token),
            'current_item': self.current_item,
            'stats': self.stats.copy(),
            'progress': progress
        }

    def _run(self):
        """Main worker loop"""
        logger.info("Genius worker thread started")

        while not self.should_stop:
            try:
                if self.paused:
                    interruptible_sleep(self._stop_event, 1)
                    continue

                # Check if access token is configured
                if not self.client.access_token:
                    self._init_client()
                    if not self.client.access_token:
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

                # Genius rate limiting is conservative (500ms per call) + lyrics scraping
                interruptible_sleep(self._stop_event, 1)

            except Exception as e:
                logger.error(f"Error in worker loop: {e}")
                interruptible_sleep(self._stop_event, 5)

        logger.info("Genius worker thread finished")

    def _get_next_item(self) -> Optional[Dict[str, Any]]:
        """Get next item to process from priority queue.
        Genius is artist+track focused — we skip album-level processing
        since Genius doesn't have direct album endpoints."""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            # Priority 1: Unattempted artists
            cursor.execute("""
                SELECT id, name
                FROM artists
                WHERE genius_match_status IS NULL AND id IS NOT NULL
                ORDER BY id ASC
                LIMIT 1
            """)
            row = cursor.fetchone()
            if row:
                return {'type': 'artist', 'id': row[0], 'name': row[1]}

            # Priority 2: Unattempted tracks (skip albums — Genius is song-centric)
            cursor.execute("""
                SELECT t.id, t.title, ar.name AS artist_name
                FROM tracks t
                JOIN artists ar ON t.artist_id = ar.id
                WHERE t.genius_match_status IS NULL AND t.id IS NOT NULL
                ORDER BY t.id ASC
                LIMIT 1
            """)
            row = cursor.fetchone()
            if row:
                return {'type': 'track', 'id': row[0], 'name': row[1], 'artist': row[2]}

            # Priority 3: Retry 'not_found' artists
            not_found_cutoff = datetime.now() - timedelta(days=self.retry_days)
            cursor.execute("""
                SELECT id, name
                FROM artists
                WHERE genius_match_status = 'not_found' AND genius_last_attempted < ?
                ORDER BY genius_last_attempted ASC
                LIMIT 1
            """, (not_found_cutoff,))
            row = cursor.fetchone()
            if row:
                logger.info(f"Retrying artist '{row[1]}' (last attempted before cutoff)")
                return {'type': 'artist', 'id': row[0], 'name': row[1]}

            # Priority 4: Retry 'not_found' tracks
            cursor.execute("""
                SELECT t.id, t.title, ar.name AS artist_name
                FROM tracks t
                JOIN artists ar ON t.artist_id = ar.id
                WHERE t.genius_match_status = 'not_found' AND t.genius_last_attempted < ?
                ORDER BY t.genius_last_attempted ASC
                LIMIT 1
            """, (not_found_cutoff,))
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

    def _normalize_name(self, name: str) -> str:
        """Normalize name for comparison"""
        name = name.lower().strip()
        name = re.sub(r'\s+[-–—]\s+.*$', '', name)
        name = re.sub(r'\s*\(.*?\)\s*', ' ', name)
        name = re.sub(r'\s*\[.*?\]\s*', ' ', name)  # Also strip brackets (Genius uses these)
        name = re.sub(r'\s*feat\.?\s+.*$', '', name)  # Strip featuring
        name = re.sub(r'[^\w\s]', '', name)
        name = re.sub(r'\s+', ' ', name).strip()
        return name

    def _name_matches(self, query_name: str, result_name: str) -> bool:
        """Check if result name matches our query with fuzzy matching"""
        norm_query = self._normalize_name(query_name)
        norm_result = self._normalize_name(result_name)
        similarity = SequenceMatcher(None, norm_query, norm_result).ratio()
        logger.debug(f"Name similarity: '{query_name}' vs '{result_name}' = {similarity:.2f}")
        return similarity >= self.name_similarity_threshold

    def _process_item(self, item: Dict[str, Any]):
        """Process a single item (artist or track)"""
        try:
            item_type = item['type']
            item_id = item['id']
            item_name = item['name']

            logger.debug(f"Processing {item_type} #{item_id}: {item_name}")

            if item_type == 'artist':
                self._process_artist(item_id, item_name)
            elif item_type == 'track':
                self._process_track(item_id, item_name, item.get('artist', ''))

        except Exception as e:
            logger.error(f"Error processing {item['type']} #{item['id']}: {e}")
            self.stats['errors'] += 1
            try:
                self._mark_status(item['type'], item['id'], 'error')
            except Exception as e2:
                logger.error(f"Error updating item status: {e2}")

    def _get_existing_id(self, entity_type: str, entity_id: int) -> Optional[str]:
        """Check if an entity already has a genius_id (e.g. from manual match)."""
        table_map = {'artist': 'artists', 'album': 'albums', 'track': 'tracks'}
        table = table_map.get(entity_type)
        if not table:
            return None
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute(f"SELECT genius_id FROM {table} WHERE id = ?", (entity_id,))
            row = cursor.fetchone()
            return row[0] if row and row[0] else None
        except Exception:
            return None
        finally:
            if conn:
                conn.close()

    def _process_artist(self, artist_id: int, artist_name: str):
        """Process an artist: search Genius, get full artist details.
        If the artist already has a genius_id (e.g. from manual match),
        uses it for direct lookup instead of searching by name."""

        # Check for existing ID (manual match) — use direct lookup instead of name search
        existing_id = self._get_existing_id('artist', artist_id)
        if existing_id:
            try:
                full_artist = self.client.get_artist(int(existing_id))
                if full_artist:
                    self._update_artist(artist_id, full_artist, full_artist)
                    self.stats['matched'] += 1
                    logger.info(f"Enriched artist '{artist_name}' from existing Genius ID: {existing_id}")
                    return
            except Exception as e:
                logger.warning(f"Direct lookup failed for existing Genius ID {existing_id}: {e}")
            # Direct lookup failed — don't overwrite manual match, just return
            logger.debug(f"Preserving manual match for artist '{artist_name}' (Genius ID: {existing_id})")
            return

        result = self.client.search_artist(artist_name)
        if result:
            result_name = result.get('name', '')
            if self._name_matches(artist_name, result_name):
                genius_id = result.get('id')
                # Fetch full artist details
                full_artist = None
                if genius_id:
                    try:
                        full_artist = self.client.get_artist(genius_id)
                    except Exception as e:
                        logger.warning(f"Failed to fetch full artist details for '{artist_name}': {e}")

                if full_artist is None:
                    self._mark_status('artist', artist_id, 'error')
                    self.stats['errors'] += 1
                    logger.warning(f"Artist '{artist_name}' matched but full details unavailable, will retry")
                    return

                self._update_artist(artist_id, result, full_artist)
                self.stats['matched'] += 1
                logger.info(f"Matched artist '{artist_name}' -> Genius ID: {genius_id}")
            else:
                self._mark_status('artist', artist_id, 'not_found')
                self.stats['not_found'] += 1
                logger.debug(f"Name mismatch for artist '{artist_name}' (got '{result_name}')")
        else:
            self._mark_status('artist', artist_id, 'not_found')
            self.stats['not_found'] += 1
            logger.debug(f"No match for artist '{artist_name}'")

    def _process_track(self, track_id: int, track_name: str, artist_name: str):
        """Process a track: search Genius, get full song details + lyrics.
        If the track already has a genius_id (e.g. from manual match),
        uses it for direct lookup instead of searching by name."""

        # Check for existing ID (manual match) — use direct lookup instead of name search
        existing_id = self._get_existing_id('track', track_id)
        if existing_id:
            try:
                full_song = self.client.get_song(int(existing_id))
                if full_song:
                    lyrics = None
                    song_url = full_song.get('url')
                    if song_url:
                        try:
                            lyrics = self.client.get_lyrics(song_url)
                        except Exception:
                            pass
                    self._update_track(track_id, full_song, full_song, lyrics)
                    self.stats['matched'] += 1
                    logger.info(f"Enriched track '{track_name}' from existing Genius ID: {existing_id}")
                    return
            except Exception as e:
                logger.warning(f"Direct lookup failed for existing Genius ID {existing_id}: {e}")
            logger.debug(f"Preserving manual match for track '{track_name}' (Genius ID: {existing_id})")
            return

        result = self.client.search_song(artist_name, track_name)
        if result:
            result_title = result.get('title', '')
            if self._name_matches(track_name, result_title):
                genius_id = result.get('id')
                # Fetch full song details
                full_song = None
                if genius_id:
                    try:
                        full_song = self.client.get_song(genius_id)
                    except Exception as e:
                        logger.warning(f"Failed to fetch full song details for '{track_name}': {e}")

                if full_song is None:
                    self._mark_status('track', track_id, 'error')
                    self.stats['errors'] += 1
                    logger.warning(f"Track '{track_name}' matched but full details unavailable, will retry")
                    return

                # Scrape lyrics
                lyrics = None
                song_url = result.get('url') or full_song.get('url')
                if song_url:
                    try:
                        lyrics = self.client.get_lyrics(song_url)
                    except Exception as e:
                        logger.debug(f"Lyrics scraping failed for '{track_name}': {e}")

                self._update_track(track_id, result, full_song, lyrics)
                self.stats['matched'] += 1
                logger.info(f"Matched track '{track_name}' -> Genius ID: {genius_id}")
            else:
                self._mark_status('track', track_id, 'not_found')
                self.stats['not_found'] += 1
                logger.debug(f"Name mismatch for track '{track_name}' (got '{result_title}')")
        else:
            self._mark_status('track', track_id, 'not_found')
            self.stats['not_found'] += 1
            logger.debug(f"No match for track '{track_name}'")

    def _update_artist(self, artist_id: int, search_data: Dict[str, Any], full_data: Dict[str, Any]):
        """Store Genius metadata for an artist"""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            genius_id = str(full_data.get('id', search_data.get('id', '')))
            description = self.client.extract_description(full_data.get('description'))
            image_url = full_data.get('image_url') or search_data.get('image_url')
            genius_url = full_data.get('url') or search_data.get('url')

            # Alternate names
            alt_names = full_data.get('alternate_names', [])
            alt_names_json = json.dumps(alt_names) if alt_names else None

            cursor.execute("""
                UPDATE artists SET
                    genius_id = ?,
                    genius_match_status = 'matched',
                    genius_last_attempted = CURRENT_TIMESTAMP,
                    genius_description = ?,
                    genius_alt_names = ?,
                    genius_url = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (genius_id, description, alt_names_json, genius_url, artist_id))

            # Backfill thumb_url
            if image_url:
                cursor.execute("""
                    UPDATE artists SET thumb_url = ?
                    WHERE id = ? AND (thumb_url IS NULL OR thumb_url = '')
                """, (image_url, artist_id))

            conn.commit()

        except Exception as e:
            logger.error(f"Error updating artist #{artist_id} with Genius data: {e}")
            raise
        finally:
            if conn:
                conn.close()

    def _update_track(self, track_id: int, search_data: Dict[str, Any], full_data: Dict[str, Any], lyrics: Optional[str]):
        """Store Genius metadata for a track"""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            genius_id = str(full_data.get('id', search_data.get('id', '')))
            description = self.client.extract_description(full_data.get('description'))
            genius_url = full_data.get('url') or search_data.get('url')

            cursor.execute("""
                UPDATE tracks SET
                    genius_id = ?,
                    genius_match_status = 'matched',
                    genius_last_attempted = CURRENT_TIMESTAMP,
                    genius_lyrics = ?,
                    genius_description = ?,
                    genius_url = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (genius_id, lyrics, description, genius_url, track_id))

            conn.commit()

        except Exception as e:
            logger.error(f"Error updating track #{track_id} with Genius data: {e}")
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
                    genius_match_status = ?,
                    genius_last_attempted = CURRENT_TIMESTAMP,
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
        """Count how many items still need processing (artists + tracks only)"""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    (SELECT COUNT(*) FROM artists WHERE genius_match_status IS NULL AND id IS NOT NULL) +
                    (SELECT COUNT(*) FROM tracks WHERE genius_match_status IS NULL AND id IS NOT NULL)
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

            for entity, table in [('artists', 'artists'), ('tracks', 'tracks')]:
                cursor.execute(f"""
                    SELECT
                        COUNT(*) AS total,
                        SUM(CASE WHEN genius_match_status IS NOT NULL THEN 1 ELSE 0 END) AS processed
                    FROM {table}
                """)
                row = cursor.fetchone()
                if row:
                    total, processed = row[0], row[1] or 0
                    progress[entity] = {
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
