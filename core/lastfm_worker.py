import json
import re
import threading
import time
from difflib import SequenceMatcher
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
from utils.logging_config import get_logger
from database.music_database import MusicDatabase
from core.lastfm_client import LastFMClient
from config.settings import config_manager
from core.worker_utils import interruptible_sleep

logger = get_logger("lastfm_worker")


class LastFMWorker:
    """Background worker for enriching library artists, albums, and tracks with Last.fm metadata.

    Enriches:
      - Artists: listeners, playcount, bio/summary, tags, similar artists, images
      - Albums: listeners, playcount, tags, wiki/summary, images
      - Tracks: listeners, playcount, tags, duration
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
        self.error_retry_days = 7

        # Name matching threshold
        self.name_similarity_threshold = 0.80

        logger.info("Last.fm background worker initialized")

    def _init_client(self):
        """Initialize or reinitialize the Last.fm client from config"""
        api_key = config_manager.get('lastfm.api_key', '')
        self.client = LastFMClient(api_key=api_key)

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
        logger.info("Last.fm background worker started")

    def stop(self):
        """Stop the background worker"""
        if not self.running:
            return

        logger.info("Stopping Last.fm worker...")
        self.should_stop = True
        self.running = False
        self._stop_event.set()

        if self.thread:
            self.thread.join(timeout=1)

        logger.info("Last.fm worker stopped")

    def pause(self):
        """Pause the worker"""
        if not self.running:
            logger.warning("Worker not running, cannot pause")
            return
        self.paused = True
        logger.info("Last.fm worker paused")

    def resume(self):
        """Resume the worker"""
        if not self.running:
            logger.warning("Worker not running, start it first")
            return
        self.paused = False
        logger.info("Last.fm worker resumed")

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
            'authenticated': bool(self.client and self.client.api_key),
            'current_item': self.current_item,
            'stats': self.stats.copy(),
            'progress': progress
        }

    def _run(self):
        """Main worker loop"""
        logger.info("Last.fm worker thread started")

        while not self.should_stop:
            try:
                if self.paused:
                    interruptible_sleep(self._stop_event, 1)
                    continue

                # Check if API key is configured
                if not self.client.api_key:
                    self._init_client()
                    if not self.client.api_key:
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

                # Last.fm allows 5 req/sec but we use multiple calls per item
                interruptible_sleep(self._stop_event, 1)

            except Exception as e:
                logger.error(f"Error in worker loop: {e}")
                interruptible_sleep(self._stop_event, 5)

        logger.info("Last.fm worker thread finished")

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
                WHERE lastfm_match_status IS NULL AND id IS NOT NULL
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
                WHERE a.lastfm_match_status IS NULL AND a.id IS NOT NULL
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
                WHERE t.lastfm_match_status IS NULL AND t.id IS NOT NULL
                ORDER BY t.id ASC
                LIMIT 1
            """)
            row = cursor.fetchone()
            if row:
                return {'type': 'track', 'id': row[0], 'name': row[1], 'artist': row[2]}

            # Priority 4: Retry 'not_found' artists only (errors don't auto-retry —
            # they require a user-triggered full refresh to prevent infinite retry loops)
            not_found_cutoff = datetime.now() - timedelta(days=self.retry_days)
            cursor.execute("""
                SELECT id, name
                FROM artists
                WHERE lastfm_match_status = 'not_found' AND lastfm_last_attempted < ?
                ORDER BY lastfm_last_attempted ASC
                LIMIT 1
            """, (not_found_cutoff,))
            row = cursor.fetchone()
            if row:
                logger.info(f"Retrying artist '{row[1]}' (last attempted before cutoff)")
                return {'type': 'artist', 'id': row[0], 'name': row[1]}

            # Priority 5: Retry not_found albums
            cursor.execute("""
                SELECT a.id, a.title, ar.name AS artist_name
                FROM albums a
                JOIN artists ar ON a.artist_id = ar.id
                WHERE a.lastfm_match_status = 'not_found' AND a.lastfm_last_attempted < ?
                ORDER BY a.lastfm_last_attempted ASC
                LIMIT 1
            """, (not_found_cutoff,))
            row = cursor.fetchone()
            if row:
                return {'type': 'album', 'id': row[0], 'name': row[1], 'artist': row[2]}

            # Priority 6: Retry not_found tracks
            cursor.execute("""
                SELECT t.id, t.title, ar.name AS artist_name
                FROM tracks t
                JOIN artists ar ON t.artist_id = ar.id
                WHERE t.lastfm_match_status = 'not_found' AND t.lastfm_last_attempted < ?
                ORDER BY t.lastfm_last_attempted ASC
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
        """Process a single item (artist, album, or track)"""
        try:
            item_type = item['type']
            item_id = item['id']
            item_name = item['name']

            logger.debug(f"Processing {item_type} #{item_id}: {item_name}")

            if item_type == 'artist':
                self._process_artist(item_id, item_name)
            elif item_type == 'album':
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

    def _get_existing_id(self, entity_type: str, entity_id: int) -> Optional[str]:
        """Check if an entity already has a lastfm_id (e.g. from manual match)."""
        table_map = {'artist': 'artists', 'album': 'albums', 'track': 'tracks'}
        table = table_map.get(entity_type)
        if not table:
            return None
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute(f"SELECT lastfm_id FROM {table} WHERE id = ?", (entity_id,))
            row = cursor.fetchone()
            return row[0] if row and row[0] else None
        except Exception:
            return None
        finally:
            if conn:
                conn.close()

    def _process_artist(self, artist_id: int, artist_name: str):
        """Process an artist: get full info from Last.fm"""
        existing_id = self._get_existing_id('artist', artist_id)
        if existing_id:
            logger.debug(f"Preserving existing Last.fm ID for artist '{artist_name}': {existing_id}")
            return

        # Use get_artist_info for detailed data (includes stats, bio, tags, similar)
        result = self.client.get_artist_info(artist_name)
        if result:
            result_name = result.get('name', '')
            if self._name_matches(artist_name, result_name):
                self._update_artist(artist_id, result)
                self.stats['matched'] += 1
                logger.info(f"Matched artist '{artist_name}' on Last.fm")
            else:
                self._mark_status('artist', artist_id, 'not_found')
                self.stats['not_found'] += 1
                logger.debug(f"Name mismatch for artist '{artist_name}' (got '{result_name}')")
        else:
            self._mark_status('artist', artist_id, 'not_found')
            self.stats['not_found'] += 1
            logger.debug(f"No match for artist '{artist_name}'")

    def _process_album(self, album_id: int, album_name: str, artist_name: str):
        """Process an album: get full info from Last.fm"""
        existing_id = self._get_existing_id('album', album_id)
        if existing_id:
            logger.debug(f"Preserving existing Last.fm ID for album '{album_name}': {existing_id}")
            return

        result = self.client.get_album_info(artist_name, album_name)
        if result:
            result_name = result.get('name', '')
            if self._name_matches(album_name, result_name):
                self._update_album(album_id, result)
                self.stats['matched'] += 1
                logger.info(f"Matched album '{album_name}' on Last.fm")
            else:
                self._mark_status('album', album_id, 'not_found')
                self.stats['not_found'] += 1
                logger.debug(f"Name mismatch for album '{album_name}' (got '{result_name}')")
        else:
            self._mark_status('album', album_id, 'not_found')
            self.stats['not_found'] += 1
            logger.debug(f"No match for album '{album_name}'")

    def _process_track(self, track_id: int, track_name: str, artist_name: str):
        """Process a track: get full info from Last.fm"""
        existing_id = self._get_existing_id('track', track_id)
        if existing_id:
            logger.debug(f"Preserving existing Last.fm ID for track '{track_name}': {existing_id}")
            return

        result = self.client.get_track_info(artist_name, track_name)
        if result:
            result_name = result.get('name', '')
            if self._name_matches(track_name, result_name):
                self._update_track(track_id, result)
                self.stats['matched'] += 1
                logger.info(f"Matched track '{track_name}' on Last.fm")
            else:
                self._mark_status('track', track_id, 'not_found')
                self.stats['not_found'] += 1
                logger.debug(f"Name mismatch for track '{track_name}' (got '{result_name}')")
        else:
            self._mark_status('track', track_id, 'not_found')
            self.stats['not_found'] += 1
            logger.debug(f"No match for track '{track_name}'")

    def _update_artist(self, artist_id: int, data: Dict[str, Any]):
        """Store Last.fm metadata for an artist"""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            # Extract stats
            stats = data.get('stats', {})
            listeners = int(stats.get('listeners', 0)) if stats.get('listeners') else None
            playcount = int(stats.get('playcount', 0)) if stats.get('playcount') else None

            # Extract bio summary
            bio = data.get('bio', {})
            summary = bio.get('summary', '') if bio else ''
            # Clean Last.fm's HTML link from summary
            if summary:
                summary = re.sub(r'<a href=".*?">.*?</a>\.?', '', summary).strip()

            # Extract tags
            tags = self.client.extract_tags(data.get('tags'))
            tags_json = json.dumps(tags) if tags else None

            # Extract similar artists (Last.fm returns a single dict instead of list when only 1 result)
            similar_data = data.get('similar')
            similar_raw = similar_data.get('artist', []) if isinstance(similar_data, dict) else []
            if similar_raw and not isinstance(similar_raw, list):
                similar_raw = [similar_raw]
            if similar_raw:
                similar = [{'name': s.get('name', ''), 'match': s.get('match', '')} for s in similar_raw[:10] if isinstance(s, dict)]
                similar_json = json.dumps(similar) if similar else None
            else:
                similar_json = None

            # Get best image
            thumb_url = self.client.get_best_image(data.get('image', []))

            # Last.fm URL (serves as unique identifier)
            lastfm_url = data.get('url')

            # Update core lastfm fields
            cursor.execute("""
                UPDATE artists SET
                    lastfm_match_status = 'matched',
                    lastfm_last_attempted = CURRENT_TIMESTAMP,
                    lastfm_listeners = ?,
                    lastfm_playcount = ?,
                    lastfm_tags = ?,
                    lastfm_similar = ?,
                    lastfm_bio = ?,
                    lastfm_url = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (listeners, playcount, tags_json, similar_json, summary or None, lastfm_url, artist_id))

            # Backfill thumb_url if missing
            if thumb_url:
                cursor.execute("""
                    UPDATE artists SET thumb_url = ?
                    WHERE id = ? AND (thumb_url IS NULL OR thumb_url = '')
                """, (thumb_url, artist_id))

            # Backfill style from tags if missing
            if tags:
                cursor.execute("""
                    UPDATE artists SET style = ?
                    WHERE id = ? AND (style IS NULL OR style = '')
                """, (', '.join(tags[:5]), artist_id))

            conn.commit()

        except Exception as e:
            logger.error(f"Error updating artist #{artist_id} with Last.fm data: {e}")
            raise
        finally:
            if conn:
                conn.close()

    def _update_album(self, album_id: int, data: Dict[str, Any]):
        """Store Last.fm metadata for an album"""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            listeners = int(data.get('listeners', 0)) if data.get('listeners') else None
            playcount = int(data.get('playcount', 0)) if data.get('playcount') else None

            # Extract tags
            tags = self.client.extract_tags(data.get('tags'))
            tags_json = json.dumps(tags) if tags else None

            # Extract wiki summary
            wiki = data.get('wiki', {})
            summary = wiki.get('summary', '') if wiki else ''
            if summary:
                summary = re.sub(r'<a href=".*?">.*?</a>\.?', '', summary).strip()

            # Get best image
            thumb_url = self.client.get_best_image(data.get('image', []))

            # Last.fm URL
            lastfm_url = data.get('url')

            cursor.execute("""
                UPDATE albums SET
                    lastfm_match_status = 'matched',
                    lastfm_last_attempted = CURRENT_TIMESTAMP,
                    lastfm_listeners = ?,
                    lastfm_playcount = ?,
                    lastfm_tags = ?,
                    lastfm_wiki = ?,
                    lastfm_url = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (listeners, playcount, tags_json, summary or None, lastfm_url, album_id))

            # Backfill thumb_url
            if thumb_url:
                cursor.execute("""
                    UPDATE albums SET thumb_url = ?
                    WHERE id = ? AND (thumb_url IS NULL OR thumb_url = '')
                """, (thumb_url, album_id))

            # Backfill genres from tags
            if tags:
                cursor.execute("""
                    UPDATE albums SET genres = ?
                    WHERE id = ? AND (genres IS NULL OR genres = '' OR genres = '[]')
                """, (json.dumps(tags[:10]), album_id))

            conn.commit()

        except Exception as e:
            logger.error(f"Error updating album #{album_id} with Last.fm data: {e}")
            raise
        finally:
            if conn:
                conn.close()

    def _update_track(self, track_id: int, data: Dict[str, Any]):
        """Store Last.fm metadata for a track"""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            listeners = int(data.get('listeners', 0)) if data.get('listeners') else None
            playcount = int(data.get('playcount', 0)) if data.get('playcount') else None

            # Extract tags
            tags_data = data.get('toptags', {})
            tags = self.client.extract_tags(tags_data)
            tags_json = json.dumps(tags) if tags else None

            # Last.fm URL
            lastfm_url = data.get('url')

            cursor.execute("""
                UPDATE tracks SET
                    lastfm_match_status = 'matched',
                    lastfm_last_attempted = CURRENT_TIMESTAMP,
                    lastfm_listeners = ?,
                    lastfm_playcount = ?,
                    lastfm_tags = ?,
                    lastfm_url = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (listeners, playcount, tags_json, lastfm_url, track_id))

            conn.commit()

        except Exception as e:
            logger.error(f"Error updating track #{track_id} with Last.fm data: {e}")
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
                    lastfm_match_status = ?,
                    lastfm_last_attempted = CURRENT_TIMESTAMP,
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
                    (SELECT COUNT(*) FROM artists WHERE lastfm_match_status IS NULL AND id IS NOT NULL) +
                    (SELECT COUNT(*) FROM albums WHERE lastfm_match_status IS NULL AND id IS NOT NULL) +
                    (SELECT COUNT(*) FROM tracks WHERE lastfm_match_status IS NULL AND id IS NOT NULL)
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

            for entity, table in [('artists', 'artists'), ('albums', 'albums'), ('tracks', 'tracks')]:
                cursor.execute(f"""
                    SELECT
                        COUNT(*) AS total,
                        SUM(CASE WHEN lastfm_match_status IS NOT NULL THEN 1 ELSE 0 END) AS processed
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
