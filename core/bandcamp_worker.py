import json
import re
import threading
from difflib import SequenceMatcher
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
from utils.logging_config import get_logger
from database.music_database import MusicDatabase
from core.bandcamp_client import BandcampClient
from core.worker_utils import interruptible_sleep

logger = get_logger("bandcamp_worker")


class BandcampWorker:
    """Background worker for enriching library albums and tracks with
    Bandcamp metadata.

    Album+track (unlike Last.fm/Genius, which also enrich artists) —
    Bandcamp's band/label pages don't carry enough structured data to be
    worth a separate artist enrichment pass, but releases (albums) are
    Bandcamp's primary unit: a release's JSON-LD carries the full tracklist
    plus tags/label/credits in a single fetch, richer than any individual
    track page. Keyless: BandcampClient uses Bandcamp's own public search +
    release-page endpoints, no API token.
    """

    def __init__(self, database: MusicDatabase):
        self.db = database
        self.client = BandcampClient()

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
        self.name_similarity_threshold = 0.75

        logger.info("Bandcamp background worker initialized")

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
        logger.info("Bandcamp background worker started")

    def stop(self):
        """Stop the background worker"""
        if not self.running:
            return

        logger.info("Stopping Bandcamp worker...")
        self.should_stop = True
        self.running = False
        self._stop_event.set()

        if self.thread:
            self.thread.join(timeout=1)

        logger.info("Bandcamp worker stopped")

    def pause(self):
        """Pause the worker"""
        if not self.running:
            logger.warning("Worker not running, cannot pause")
            return
        self.paused = True
        logger.info("Bandcamp worker paused")

    def resume(self):
        """Resume the worker"""
        if not self.running:
            logger.warning("Worker not running, start it first")
            return
        self.paused = False
        logger.info("Bandcamp worker resumed")

    def get_stats(self) -> Dict[str, Any]:
        """Get current statistics"""
        from core.metadata.registry import is_source_enabled

        self.stats['pending'] = self._count_pending_items()
        progress = self._get_progress_breakdown()
        is_actually_running = self.running and (self.thread is not None and self.thread.is_alive())
        is_idle = is_actually_running and not self.paused and self.stats['pending'] == 0 and self.current_item is None

        return {
            'enabled': is_source_enabled('bandcamp'),
            'running': is_actually_running and not self.paused,
            'paused': self.paused,
            'idle': is_idle,
            'authenticated': True,  # keyless — always "authenticated"
            'current_item': self.current_item,
            'stats': self.stats.copy(),
            'progress': progress
        }

    def _run(self):
        """Main worker loop"""
        logger.info("Bandcamp worker thread started")

        while not self.should_stop:
            try:
                if self.paused:
                    interruptible_sleep(self._stop_event, 1)
                    continue

                # Bandcamp is an opt-in experimental source (see
                # core.metadata.registry.EXPERIMENTAL_SOURCES). This worker is
                # started unconditionally at app startup like the other
                # enrichment workers, but stays idle unless the setting is on
                # — checked live so toggling it in Settings takes effect
                # immediately, with no restart required.
                from core.metadata.registry import is_source_enabled
                if not is_source_enabled('bandcamp'):
                    interruptible_sleep(self._stop_event, 30)
                    continue

                self.current_item = None
                item = self._get_next_item()

                if not item:
                    logger.debug("No pending items, sleeping...")
                    interruptible_sleep(self._stop_event, 10)
                    continue

                self.current_item = item
                if item.get('id') is None:
                    logger.warning(f"Skipping {item.get('type', 'item')} with NULL id: {item.get('name', '?')}")
                    continue

                self._process_item(item)

                # Bandcamp rate limiting is conservative (1s/call) + a release-page fetch per match
                interruptible_sleep(self._stop_event, 1)

            except Exception as e:
                logger.error(f"Error in worker loop: {e}")
                interruptible_sleep(self._stop_event, 5)

        logger.info("Bandcamp worker thread finished")

    def _get_next_item(self) -> Optional[Dict[str, Any]]:
        """Get next album or track to process from the priority queue.

        Albums are prioritized ahead of tracks: matching the containing
        album first captures the full tracklist's Bandcamp URLs in one
        fetch, so by the time a track is picked up it can often reuse an
        already-matched sibling instead of triggering its own search."""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            # Priority 1: Unattempted albums
            cursor.execute("""
                SELECT al.id, al.title, ar.name AS artist_name
                FROM albums al
                JOIN artists ar ON al.artist_id = ar.id
                WHERE al.bandcamp_match_status IS NULL AND al.id IS NOT NULL
                ORDER BY al.id ASC
                LIMIT 1
            """)
            row = cursor.fetchone()
            if row:
                return {'type': 'album', 'id': row[0], 'name': row[1], 'artist': row[2]}

            # Priority 2: Unattempted tracks
            cursor.execute("""
                SELECT t.id, t.title, ar.name AS artist_name
                FROM tracks t
                JOIN artists ar ON t.artist_id = ar.id
                WHERE t.bandcamp_match_status IS NULL AND t.id IS NOT NULL
                ORDER BY t.id ASC
                LIMIT 1
            """)
            row = cursor.fetchone()
            if row:
                return {'type': 'track', 'id': row[0], 'name': row[1], 'artist': row[2]}

            # Priority 3: Retry 'not_found' albums
            not_found_cutoff = datetime.now() - timedelta(days=self.retry_days)
            cursor.execute("""
                SELECT al.id, al.title, ar.name AS artist_name
                FROM albums al
                JOIN artists ar ON al.artist_id = ar.id
                WHERE al.bandcamp_match_status = 'not_found' AND al.bandcamp_last_attempted < ?
                ORDER BY al.bandcamp_last_attempted ASC
                LIMIT 1
            """, (not_found_cutoff,))
            row = cursor.fetchone()
            if row:
                return {'type': 'album', 'id': row[0], 'name': row[1], 'artist': row[2]}

            # Priority 4: Retry 'not_found' tracks
            cursor.execute("""
                SELECT t.id, t.title, ar.name AS artist_name
                FROM tracks t
                JOIN artists ar ON t.artist_id = ar.id
                WHERE t.bandcamp_match_status = 'not_found' AND t.bandcamp_last_attempted < ?
                ORDER BY t.bandcamp_last_attempted ASC
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
        name = re.sub(r'\s*\[.*?\]\s*', ' ', name)
        name = re.sub(r'\s*feat\.?\s+.*$', '', name)
        name = re.sub(r'[^\w\s]', '', name)
        name = re.sub(r'\s+', ' ', name).strip()
        return name

    def _name_matches(self, query_name: str, result_name: str) -> bool:
        """Check if result name matches our query with fuzzy matching"""
        norm_query = self._normalize_name(query_name)
        norm_result = self._normalize_name(result_name)
        similarity = SequenceMatcher(None, norm_query, norm_result).ratio()
        return similarity >= self.name_similarity_threshold

    def _get_existing_url(self, entity_type: str, entity_id: int) -> Optional[str]:
        """Check if an album/track already has a bandcamp_url (e.g. from manual match)."""
        table = 'albums' if entity_type == 'album' else 'tracks'
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute(f"SELECT bandcamp_url FROM {table} WHERE id = ?", (entity_id,))
            row = cursor.fetchone()
            return row[0] if row and row[0] else None
        except Exception:
            return None
        finally:
            if conn:
                conn.close()

    def _process_item(self, item: Dict[str, Any]):
        """Process a single item (album or track)"""
        try:
            item_type = item['type']
            item_id = item['id']
            item_name = item['name']

            logger.debug(f"Processing {item_type} #{item_id}: {item_name}")

            if item_type == 'album':
                self._process_album(item_id, item_name, item.get('artist', ''))
            elif item_type == 'track':
                self._process_track(item_id, item_name, item.get('artist', ''))

        except Exception as e:
            logger.error(f"Error processing {item['type']} #{item['id']}: {e}")
            self.stats['errors'] += 1
            try:
                self._mark_status(item['type'], item['id'], 'error')
            except Exception as e2:
                logger.error(f"Error updating item status: {e2}")

    def _process_album(self, album_id: int, album_name: str, artist_name: str):
        """Process an album: search Bandcamp, get release metadata.

        If the album already has a bandcamp_url (e.g. from manual match),
        re-fetches its release metadata directly instead of searching by name."""
        existing_url = self._get_existing_url('album', album_id)
        if existing_url:
            release = self.client.get_release_metadata(existing_url)
            if release:
                self._update_entity('album', album_id, {
                    'id': None, 'url': existing_url, 'title': release.get('title', album_name),
                    'tags': release.get('tags') or [], 'label': release.get('label'),
                })
                self.stats['matched'] += 1
                logger.info(f"Enriched album '{album_name}' from existing Bandcamp URL: {existing_url}")
                return
            logger.debug(f"Preserving manual match for album '{album_name}' (Bandcamp URL: {existing_url})")
            return

        result = self.client.search_album(artist_name, album_name)
        if result and self._name_matches(album_name, result.get('title', '')):
            self._update_entity('album', album_id, result)
            self.stats['matched'] += 1
            logger.info(f"Matched album '{album_name}' -> Bandcamp URL: {result.get('url')}")
        else:
            self._mark_status('album', album_id, 'not_found')
            self.stats['not_found'] += 1
            logger.debug(f"No confident Bandcamp match for album '{album_name}'")

    def _process_track(self, track_id: int, track_name: str, artist_name: str):
        """Process a track: search Bandcamp, get release metadata.

        If the track already has a bandcamp_url (e.g. from manual match),
        re-fetches its release metadata directly instead of searching by name."""
        existing_url = self._get_existing_url('track', track_id)
        if existing_url:
            release = self.client.get_release_metadata(existing_url)
            if release:
                self._update_entity('track', track_id, {
                    'id': None, 'url': existing_url, 'title': release.get('title', track_name),
                    'tags': release.get('tags') or [], 'label': release.get('label'),
                })
                self.stats['matched'] += 1
                logger.info(f"Enriched track '{track_name}' from existing Bandcamp URL: {existing_url}")
                return
            logger.debug(f"Preserving manual match for track '{track_name}' (Bandcamp URL: {existing_url})")
            return

        result = self.client.search_track(artist_name, track_name)
        if result and self._name_matches(track_name, result.get('title', '')):
            self._update_entity('track', track_id, result)
            self.stats['matched'] += 1
            logger.info(f"Matched track '{track_name}' -> Bandcamp URL: {result.get('url')}")
        else:
            self._mark_status('track', track_id, 'not_found')
            self.stats['not_found'] += 1
            logger.debug(f"No confident Bandcamp match for track '{track_name}'")

    def _update_entity(self, entity_type: str, entity_id: int, result: Dict[str, Any]):
        """Store Bandcamp metadata for an album or track"""
        table = 'albums' if entity_type == 'album' else 'tracks'
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            bandcamp_id = str(result.get('id')) if result.get('id') else None
            bandcamp_url = result.get('url')
            tags = result.get('tags') or []
            tags_json = json.dumps(tags) if tags else None
            label = result.get('label')

            cursor.execute(f"""
                UPDATE {table} SET
                    bandcamp_id = ?,
                    bandcamp_match_status = 'matched',
                    bandcamp_last_attempted = CURRENT_TIMESTAMP,
                    bandcamp_url = ?,
                    bandcamp_tags = ?,
                    bandcamp_label = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (bandcamp_id, bandcamp_url, tags_json, label, entity_id))

            conn.commit()

        except Exception as e:
            logger.error(f"Error updating {entity_type} #{entity_id} with Bandcamp data: {e}")
            raise
        finally:
            if conn:
                conn.close()

    def _mark_status(self, entity_type: str, entity_id: int, status: str):
        """Mark an album/track with a match status"""
        table = 'albums' if entity_type == 'album' else 'tracks'
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute(f"""
                UPDATE {table} SET
                    bandcamp_match_status = ?,
                    bandcamp_last_attempted = CURRENT_TIMESTAMP,
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
        """Count how many albums + tracks still need processing"""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    (SELECT COUNT(*) FROM albums WHERE bandcamp_match_status IS NULL AND id IS NOT NULL) +
                    (SELECT COUNT(*) FROM tracks WHERE bandcamp_match_status IS NULL AND id IS NOT NULL)
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

            for entity, table in [('albums', 'albums'), ('tracks', 'tracks')]:
                cursor.execute(f"""
                    SELECT
                        COUNT(*) AS total,
                        SUM(CASE WHEN bandcamp_match_status IS NOT NULL THEN 1 ELSE 0 END) AS processed
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
