"""
Discogs background enrichment worker.

Enriches library artists and albums with Discogs metadata:
- Artists: discogs_id, bio, members, genres, styles, URLs, images
- Albums: discogs_id, genres, styles, label, catalog number, country, community rating

Follows the exact same pattern as AudioDBWorker.
"""

import json
import re
import threading
import time
from difflib import SequenceMatcher
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
from utils.logging_config import get_logger
from database.music_database import MusicDatabase
from core.discogs_client import DiscogsClient
from core.worker_utils import interruptible_sleep

logger = get_logger("discogs_worker")


class DiscogsWorker:
    """Background worker for enriching library artists and albums with Discogs metadata."""

    def __init__(self, database: MusicDatabase):
        self.db = database
        self.client = DiscogsClient()

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

        logger.info(f"Discogs background worker initialized (authenticated: {self.client.is_authenticated()})")

    def start(self):
        """Start the background worker."""
        if self.running:
            logger.warning("Discogs worker already running")
            return

        self.running = True
        self.should_stop = False
        self._stop_event.clear()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        logger.info("Discogs background worker started")

    def stop(self):
        """Stop the background worker."""
        if not self.running:
            return
        logger.info("Stopping Discogs worker...")
        self.should_stop = True
        self.running = False
        self._stop_event.set()
        if self.thread:
            self.thread.join(timeout=1)
        logger.info("Discogs worker stopped")

    def pause(self):
        """Pause the worker."""
        if not self.running:
            return
        self.paused = True
        logger.info("Discogs worker paused")

    def resume(self):
        """Resume the worker."""
        if not self.running:
            return
        self.paused = False
        logger.info("Discogs worker resumed")

    def get_stats(self) -> Dict[str, Any]:
        """Get current statistics."""
        self.stats['pending'] = self._count_pending_items()
        is_actually_running = self.running and (self.thread is not None and self.thread.is_alive())
        is_idle = is_actually_running and not self.paused and self.stats['pending'] == 0 and self.current_item is None

        return {
            'enabled': True,
            'running': is_actually_running and not self.paused,
            'paused': self.paused,
            'idle': is_idle,
            'current_item': self.current_item,
            'stats': self.stats.copy(),
        }

    def _run(self):
        """Main worker loop."""
        logger.info("Discogs worker thread started")

        while not self.should_stop:
            try:
                if self.paused:
                    interruptible_sleep(self._stop_event, 1)
                    continue

                self.current_item = None
                item = self._get_next_item()

                if not item:
                    interruptible_sleep(self._stop_event, 10)
                    continue

                self.current_item = item.get('name', '')

                # Guard: skip items with None/NULL IDs
                item_id = item.get('id')
                if item_id is None:
                    logger.warning(f"Skipping {item.get('type', 'unknown')} with NULL id")
                    continue

                self._process_item(item)
                interruptible_sleep(self._stop_event, 2)

            except Exception as e:
                logger.error(f"Error in Discogs worker loop: {e}")
                interruptible_sleep(self._stop_event, 5)

        logger.info("Discogs worker thread finished")

    def _get_next_item(self) -> Optional[Dict[str, Any]]:
        """Get next item to process (artists → albums → retries)."""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            # Priority 1: Unattempted artists
            cursor.execute("""
                SELECT id, name FROM artists
                WHERE discogs_match_status IS NULL AND id IS NOT NULL
                ORDER BY id ASC LIMIT 1
            """)
            row = cursor.fetchone()
            if row:
                return {'type': 'artist', 'id': row[0], 'name': row[1]}

            # Priority 2: Unattempted albums
            cursor.execute("""
                SELECT a.id, a.title, ar.name AS artist_name, ar.discogs_id AS artist_discogs_id
                FROM albums a
                JOIN artists ar ON a.artist_id = ar.id
                WHERE a.discogs_match_status IS NULL AND a.id IS NOT NULL
                ORDER BY a.id ASC LIMIT 1
            """)
            row = cursor.fetchone()
            if row:
                return {'type': 'album', 'id': row[0], 'name': row[1], 'artist': row[2], 'artist_discogs_id': row[3]}

            # Priority 3: Retry 'not_found' artists after retry_days
            not_found_cutoff = datetime.now() - timedelta(days=self.retry_days)
            cursor.execute("""
                SELECT id, name FROM artists
                WHERE discogs_match_status = 'not_found' AND discogs_last_attempted < ?
                ORDER BY discogs_last_attempted ASC LIMIT 1
            """, (not_found_cutoff,))
            row = cursor.fetchone()
            if row:
                return {'type': 'artist', 'id': row[0], 'name': row[1]}

            # Priority 4: Retry 'not_found' albums
            cursor.execute("""
                SELECT a.id, a.title, ar.name AS artist_name, ar.discogs_id AS artist_discogs_id
                FROM albums a
                JOIN artists ar ON a.artist_id = ar.id
                WHERE a.discogs_match_status = 'not_found' AND a.discogs_last_attempted < ?
                ORDER BY a.discogs_last_attempted ASC LIMIT 1
            """, (not_found_cutoff,))
            row = cursor.fetchone()
            if row:
                return {'type': 'album', 'id': row[0], 'name': row[1], 'artist': row[2], 'artist_discogs_id': row[3]}

            return None
        except Exception as e:
            logger.error(f"Error getting next Discogs item: {e}")
            return None
        finally:
            if conn:
                conn.close()

    def _count_pending_items(self) -> int:
        """Count items still needing Discogs enrichment."""
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM artists WHERE discogs_match_status IS NULL AND id IS NOT NULL")
            artists = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM albums WHERE discogs_match_status IS NULL AND id IS NOT NULL")
            albums = cursor.fetchone()[0]
            conn.close()
            return artists + albums
        except Exception:
            return 0

    def _normalize_name(self, name: str) -> str:
        """Normalize name for comparison."""
        name = name.lower().strip()
        name = re.sub(r'\s+[-–—]\s+.*$', '', name)
        name = re.sub(r'\s*\(.*?\)\s*', ' ', name)
        name = re.sub(r'[^\w\s]', '', name)
        name = re.sub(r'\s+', ' ', name).strip()
        return name

    def _name_matches(self, query_name: str, result_name: str) -> bool:
        """Check if Discogs result name matches our query with fuzzy matching."""
        norm_query = self._normalize_name(query_name)
        norm_result = self._normalize_name(result_name)
        similarity = SequenceMatcher(None, norm_query, norm_result).ratio()
        return similarity >= self.name_similarity_threshold

    def _process_item(self, item: Dict[str, Any]):
        """Process a single artist or album."""
        try:
            item_type = item['type']
            item_id = item['id']
            item_name = item['name']

            logger.debug(f"Processing {item_type} #{item_id}: {item_name}")

            # Check for existing discogs_id (manual match) — use direct lookup
            existing_id = self._get_existing_id(item_type, item_id)
            if existing_id:
                try:
                    if item_type == 'artist':
                        data = self.client._fetch_and_cache_artist(existing_id)
                        if data:
                            self._update_artist(item_id, data)
                            self.stats['matched'] += 1
                            logger.info(f"Enriched artist '{item_name}' from existing Discogs ID: {existing_id}")
                            return
                    elif item_type == 'album':
                        data = self.client._fetch_and_cache_album(existing_id)
                        if data:
                            self._update_album(item_id, data)
                            self.stats['matched'] += 1
                            logger.info(f"Enriched album '{item_name}' from existing Discogs ID: {existing_id}")
                            return
                except Exception as e:
                    logger.warning(f"Direct Discogs lookup failed for ID {existing_id}: {e}")
                return  # Preserve manual match, don't search

            if item_type == 'artist':
                self._search_and_match_artist(item_id, item_name)
            elif item_type == 'album':
                self._search_and_match_album(item_id, item_name, item.get('artist', ''), item.get('artist_discogs_id'))

        except Exception as e:
            logger.error(f"Error processing {item.get('type')} #{item.get('id')}: {e}")
            self.stats['errors'] += 1
            try:
                self._mark_status(item['type'], item['id'], 'error')
            except Exception:
                pass

    def _get_existing_id(self, entity_type: str, entity_id) -> Optional[str]:
        """Check if entity already has a discogs_id."""
        table = 'artists' if entity_type == 'artist' else 'albums'
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute(f"SELECT discogs_id FROM {table} WHERE id = ?", (entity_id,))
            row = cursor.fetchone()
            conn.close()
            return row[0] if row and row[0] else None
        except Exception:
            return None

    def _search_and_match_artist(self, artist_id, artist_name: str):
        """Search Discogs for an artist and store metadata if matched."""
        results = self.client.search_artists(artist_name, limit=5)
        if not results:
            self._mark_status('artist', artist_id, 'not_found')
            self.stats['not_found'] += 1
            return

        # Find best match by name similarity
        for result in results:
            if self._name_matches(artist_name, result.name):
                # Fetch full artist detail (uses cache)
                data = self.client._fetch_and_cache_artist(result.id)
                if data:
                    self._update_artist(artist_id, data)
                    self.stats['matched'] += 1
                    logger.info(f"Matched artist '{artist_name}' -> Discogs ID: {result.id}")
                    return

        self._mark_status('artist', artist_id, 'not_found')
        self.stats['not_found'] += 1
        logger.debug(f"No confident match for artist '{artist_name}'")

    def _search_and_match_album(self, album_id, album_name: str, artist_name: str, artist_discogs_id: str = None):
        """Search Discogs for an album and store metadata if matched."""
        # Search with artist + album for better precision
        query = f"{artist_name} {album_name}" if artist_name else album_name
        results = self.client.search_albums(query, limit=5)
        if not results:
            self._mark_status('album', album_id, 'not_found')
            self.stats['not_found'] += 1
            return

        for result in results:
            if self._name_matches(album_name, result.name):
                # Fetch full release detail (uses cache)
                data = self.client._fetch_and_cache_album(result.id)
                if data:
                    self._update_album(album_id, data)
                    self.stats['matched'] += 1
                    logger.info(f"Matched album '{album_name}' -> Discogs ID: {result.id}")
                    return

        self._mark_status('album', album_id, 'not_found')
        self.stats['not_found'] += 1
        logger.debug(f"No confident match for album '{album_name}'")

    def _update_artist(self, artist_id, data: Dict[str, Any]):
        """Store Discogs metadata for an artist."""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            discogs_id = str(data.get('id', ''))
            bio = data.get('profile', '')
            members = json.dumps([m.get('name', '') for m in data.get('members', [])]) if data.get('members') else None
            urls = json.dumps(data.get('urls', [])) if data.get('urls') else None

            # Get image
            image_url = None
            images = data.get('images', [])
            if images:
                primary = next((img for img in images if img.get('type') == 'primary'), None)
                image_url = (primary or images[0]).get('uri')

            cursor.execute("""
                UPDATE artists SET
                    discogs_id = ?,
                    discogs_match_status = 'matched',
                    discogs_last_attempted = CURRENT_TIMESTAMP,
                    discogs_bio = ?,
                    discogs_members = ?,
                    discogs_urls = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (discogs_id, bio, members, urls, artist_id))

            # Backfill summary/bio if empty (AudioDB backfill)
            if bio:
                cursor.execute("""
                    UPDATE artists SET summary = ?
                    WHERE id = ? AND (summary IS NULL OR summary = '')
                """, (bio, artist_id))

            # Backfill thumb_url if empty
            if image_url:
                cursor.execute("""
                    UPDATE artists SET thumb_url = ?
                    WHERE id = ? AND (thumb_url IS NULL OR thumb_url = '')
                """, (image_url, artist_id))

            conn.commit()

        except Exception as e:
            logger.error(f"Error updating artist #{artist_id} with Discogs data: {e}")
            raise
        finally:
            if conn:
                conn.close()

    def _update_album(self, album_id, data: Dict[str, Any]):
        """Store Discogs metadata for an album."""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            discogs_id = str(data.get('id', ''))
            genres = json.dumps(data.get('genres', []))
            styles = json.dumps(data.get('styles', []))
            labels = data.get('labels', [])
            label = labels[0].get('name', '') if labels else ''
            catno = labels[0].get('catno', '') if labels else ''
            country = data.get('country', '')

            # Community rating
            community = data.get('community', {})
            rating = community.get('rating', {})
            rating_avg = rating.get('average', 0)
            rating_count = rating.get('count', 0)

            # Image
            image_url = None
            images = data.get('images', [])
            if images:
                primary = next((img for img in images if img.get('type') == 'primary'), None)
                image_url = (primary or images[0]).get('uri')

            cursor.execute("""
                UPDATE albums SET
                    discogs_id = ?,
                    discogs_match_status = 'matched',
                    discogs_last_attempted = CURRENT_TIMESTAMP,
                    discogs_genres = ?,
                    discogs_styles = ?,
                    discogs_label = ?,
                    discogs_catno = ?,
                    discogs_country = ?,
                    discogs_rating = ?,
                    discogs_rating_count = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (discogs_id, genres, styles, label, catno, country, rating_avg, rating_count, album_id))

            # Backfill genres if empty
            if data.get('genres'):
                cursor.execute("""
                    UPDATE albums SET genres = ?
                    WHERE id = ? AND (genres IS NULL OR genres = '' OR genres = '[]')
                """, (genres, album_id))

            # Backfill thumb_url if empty
            if image_url:
                cursor.execute("""
                    UPDATE albums SET thumb_url = ?
                    WHERE id = ? AND (thumb_url IS NULL OR thumb_url = '')
                """, (image_url, album_id))

            conn.commit()

        except Exception as e:
            logger.error(f"Error updating album #{album_id} with Discogs data: {e}")
            raise
        finally:
            if conn:
                conn.close()

    def _mark_status(self, entity_type: str, entity_id, status: str):
        """Mark entity's Discogs match status."""
        table = {'artist': 'artists', 'album': 'albums'}.get(entity_type)
        if not table:
            return
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute(f"""
                UPDATE {table} SET
                    discogs_match_status = ?,
                    discogs_last_attempted = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (status, entity_id))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Error marking {entity_type} #{entity_id} status: {e}")
