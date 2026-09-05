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
from core.settings import config_manager
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
                    except Exception as e:
                        logger.debug("null-id item type lookup: %s", e)
                    continue

                self._process_item(item)

                # Genius rate limiting is conservative (500ms per call) + lyrics scraping
                interruptible_sleep(self._stop_event, 1)

            except Exception as e:
                logger.error(f"Error in worker loop: {e}")
                interruptible_sleep(self._stop_event, 5)

        logger.info("Genius worker thread finished")

    def _get_next_item(self) -> Optional[Dict[str, Any]]:
        """Get next item to process from the Library-v2 catalogue.

        Genius is artist+track focused — there are no album endpoints, so albums
        are excluded rather than attempted and marked. Priority, retry window and
        the pinned-group override come from ``core.library2.worker_queue``
        (docs §32.3.1 stage 2).
        """
        conn = None
        try:
            from core.library2.worker_queue import next_pending
            from core.worker_utils import read_enrichment_priority

            pinned = read_enrichment_priority('genius')
            conn = self.db._get_connection()
            return next_pending(
                conn, 'genius',
                entity_types=('artist', 'track'),
                retry_after_days=self.retry_days,
                pinned=pinned if pinned in ('artist', 'track') else None,
            )
        except Exception as e:
            logger.error(f"Error getting next item: {e}")
            return None
        finally:
            if conn:
                conn.close()

    def _normalize_name(self, name: str) -> str:
        """Normalize provider titles before fuzzy comparison."""
        name = (name or '').lower().strip()
        name = re.sub(r'\s+[-–—]\s+.*$', '', name)
        name = re.sub(r'\s*\(.*?\)\s*', ' ', name)
        name = re.sub(r'\s*\[.*?\]\s*', ' ', name)
        name = re.sub(r'\s*feat\.?\s+.*$', '', name)
        name = re.sub(r'[^\w\s]', '', name)
        return re.sub(r'\s+', ' ', name).strip()

    def _name_matches(self, query_name: str, result_name: str) -> bool:
        """Match names without treating two normalized-empty titles as equal."""
        norm_query = self._normalize_name(query_name)
        norm_result = self._normalize_name(result_name)
        if not norm_query or not norm_result:
            raw_query = (query_name or '').strip().lower()
            raw_result = (result_name or '').strip().lower()
            return bool(raw_query) and raw_query == raw_result
        similarity = SequenceMatcher(None, norm_query, norm_result).ratio()
        logger.debug(
            "Name similarity: '%s' vs '%s' = %.2f",
            query_name,
            result_name,
            similarity,
        )
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
        """The Genius id already stored for this entity, if any."""
        conn = None
        try:
            from core.library2.provider_ids import parse_external_ids

            table = {'artist': 'lib2_artists', 'album': 'lib2_albums',
                     'track': 'lib2_tracks'}.get(entity_type)
            if not table:
                return None
            conn = self.db._get_connection()
            row = conn.execute(
                f"SELECT external_ids FROM {table} WHERE id = ?", (entity_id,)
            ).fetchone()
            if not row:
                return None
            return parse_external_ids(row[0]).get('genius') or None
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
                        except Exception as _e:
                            logger.debug("genius lyrics scrape: %s", _e)
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

            genius_id = str(full_data.get('id', search_data.get('id', '')))
            description = self.client.extract_description(full_data.get('description'))
            image_url = full_data.get('image_url') or search_data.get('image_url')
            genius_url = full_data.get('url') or search_data.get('url')

            # Alternate names
            alt_names = full_data.get('alternate_names', [])
            alt_names_json = json.dumps(alt_names) if alt_names else None

            from core.library2.provider_attempts import record_attempt
            from core.library2.provider_writes import write_provider_enrichment

            write_provider_enrichment(
                conn, entity_type='artist', entity_id=artist_id, service='genius',
                payload={
                    'description': description,
                    'alt_names': alt_names or None,
                    'url': genius_url,
                },
                provider_id=genius_id or None,
                # Genius artwork is a fallback, exactly as before.
                backfill={'image_url': image_url} if image_url else None,
            )
            record_attempt(conn, entity_type='artist', entity_id=artist_id,
                           service='genius', status='matched')
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

            genius_id = str(full_data.get('id', search_data.get('id', '')))
            description = self.client.extract_description(full_data.get('description'))
            genius_url = full_data.get('url') or search_data.get('url')

            from core.library2.provider_attempts import record_attempt
            from core.library2.provider_writes import write_provider_enrichment

            write_provider_enrichment(
                conn, entity_type='track', entity_id=track_id, service='genius',
                payload={'description': description, 'url': genius_url},
                provider_id=genius_id or None,
                # Lyrics are not a fallback: a fresh fetch is the newer truth.
                # A failed fetch passes None and leaves what is stored alone.
                columns={'genius_lyrics': lyrics},
            )
            record_attempt(conn, entity_type='track', entity_id=track_id,
                           service='genius', status='matched')
            conn.commit()

        except Exception as e:
            logger.error(f"Error updating track #{track_id} with Genius data: {e}")
            raise
        finally:
            if conn:
                conn.close()

    def _mark_status(self, entity_type: str, entity_id: int, status: str):
        """Record the outcome of an attempt in the provider ledger."""
        conn = None
        try:
            from core.library2.provider_attempts import record_attempt

            conn = self.db._get_connection()
            record_attempt(conn, entity_type=entity_type, entity_id=entity_id,
                           service='genius', status=status)
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
            from core.library2.worker_queue import pending_count

            conn = self.db._get_connection()
            return pending_count(conn, 'genius', entity_types=('artist', 'track'),
                                 retry_after_days=self.retry_days)
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
            from core.library2.worker_queue import progress_breakdown

            conn = self.db._get_connection()
            return progress_breakdown(conn, 'genius',
                                      entity_types=('artist', 'track'))
        except Exception as e:
            logger.error(f"Error getting progress breakdown: {e}")
            return {}
        finally:
            if conn:
                conn.close()
