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
from core.settings import config_manager
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
                    except Exception as e:
                        logger.debug("null id table resolve failed: %s", e)
                    continue

                self._process_item(item)

                # Last.fm allows 5 req/sec but we use multiple calls per item
                interruptible_sleep(self._stop_event, 1)

            except Exception as e:
                logger.error(f"Error in worker loop: {e}")
                interruptible_sleep(self._stop_event, 5)

        logger.info("Last.fm worker thread finished")

    def _get_next_item(self) -> Optional[Dict[str, Any]]:
        """Get next item to process from the Library-v2 catalogue.

        Priority, retry window and the pinned-group override all live in
        ``core.library2.worker_queue`` — the same rules every enrichment worker
        uses, so they cannot drift apart per worker (docs §32.3.1 stage 2).
        """
        conn = None
        try:
            from core.library2.worker_queue import next_pending
            from core.worker_utils import read_enrichment_priority

            conn = self.db._get_connection()
            return next_pending(
                conn, 'lastfm',
                retry_after_days=self.retry_days,
                pinned=read_enrichment_priority('lastfm') or None,
            )
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
        if not norm_query or not norm_result:
            raw_query = (query_name or '').strip().lower()
            raw_result = (result_name or '').strip().lower()
            return bool(raw_query) and raw_query == raw_result
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
        """The Last.fm URL already stored for this entity, if any.

        Last.fm identifies by URL rather than a numeric id, so that is what lands
        in ``external_ids['lastfm']`` — set by a manual match or by an earlier run.
        """
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
            return parse_external_ids(row[0]).get('lastfm') or None
        except Exception:
            return None
        finally:
            if conn:
                conn.close()

    def _process_artist(self, artist_id: int, artist_name: str):
        """Process an artist: get full info from Last.fm"""
        existing_id = self._get_existing_id('artist', artist_id)
        if existing_id:
            # Row already has a Last.fm URL but status is NULL (legacy/manual match).
            # Mark as matched so the worker does not re-select it forever.
            logger.debug(f"Preserving existing Last.fm URL for artist '{artist_name}': {existing_id}")
            self._mark_status('artist', artist_id, 'matched')
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
            logger.debug(f"Preserving existing Last.fm URL for album '{album_name}': {existing_id}")
            self._mark_status('album', album_id, 'matched')
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
            logger.debug(f"Preserving existing Last.fm URL for track '{track_name}': {existing_id}")
            self._mark_status('track', track_id, 'matched')
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

            # Library v2 is the catalogue (docs §32.3.1 stage 2). The payload
            # shape is the mirror's own declaration, so a natively written row is
            # indistinguishable from a mirrored one and the divergence report
            # stays quiet about our own output.
            from core.library2.provider_attempts import record_attempt
            from core.library2.provider_writes import write_provider_enrichment

            backfill = {}
            if thumb_url:
                backfill['image_url'] = thumb_url
            if tags:
                # Style is a backfill, never an overwrite — same as before.
                backfill['style'] = ', '.join(tags[:5])
            write_provider_enrichment(
                conn, entity_type='artist', entity_id=artist_id, service='lastfm',
                payload={
                    'listeners': listeners,
                    'playcount': playcount,
                    'tags': tags or None,
                    'similar': json.loads(similar_json) if similar_json else None,
                    'bio': summary or None,
                    'url': lastfm_url,
                },
                provider_id=lastfm_url,
                backfill=backfill or None,
            )
            record_attempt(conn, entity_type='artist', entity_id=artist_id,
                           service='lastfm', status='matched')
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

            listeners = int(data.get('listeners', 0)) if data.get('listeners') else None
            playcount = int(data.get('playcount', 0)) if data.get('playcount') else None

            # Extract tags
            tags = self.client.extract_tags(data.get('tags'))

            # Extract wiki summary
            wiki = data.get('wiki', {})
            summary = wiki.get('summary', '') if wiki else ''
            if summary:
                summary = re.sub(r'<a href=".*?">.*?</a>\.?', '', summary).strip()

            # Get best image
            thumb_url = self.client.get_best_image(data.get('image', []))

            # Last.fm URL
            lastfm_url = data.get('url')

            from core.library2.provider_attempts import record_attempt
            from core.library2.provider_writes import write_provider_enrichment

            backfill = {}
            if thumb_url:
                backfill['image_url'] = thumb_url
            if tags:
                from core.genre_filter import filter_genres
                filtered_tags = filter_genres(tags[:10], config_manager)
                if filtered_tags:
                    backfill['genres'] = json.dumps(filtered_tags)
            write_provider_enrichment(
                conn, entity_type='album', entity_id=album_id, service='lastfm',
                payload={
                    'listeners': listeners,
                    'playcount': playcount,
                    'tags': tags or None,
                    'wiki': summary or None,
                    'url': lastfm_url,
                },
                provider_id=lastfm_url,
                backfill=backfill or None,
            )
            record_attempt(conn, entity_type='album', entity_id=album_id,
                           service='lastfm', status='matched')
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

            listeners = int(data.get('listeners', 0)) if data.get('listeners') else None
            playcount = int(data.get('playcount', 0)) if data.get('playcount') else None

            # Extract tags
            tags_data = data.get('toptags', {})
            tags = self.client.extract_tags(tags_data)

            # Last.fm URL
            lastfm_url = data.get('url')

            from core.library2.provider_attempts import record_attempt
            from core.library2.provider_writes import write_provider_enrichment

            write_provider_enrichment(
                conn, entity_type='track', entity_id=track_id, service='lastfm',
                payload={
                    'listeners': listeners,
                    'playcount': playcount,
                    'tags': tags or None,
                    'url': lastfm_url,
                },
                provider_id=lastfm_url,
            )
            record_attempt(conn, entity_type='track', entity_id=track_id,
                           service='lastfm', status='matched')
            conn.commit()

        except Exception as e:
            logger.error(f"Error updating track #{track_id} with Last.fm data: {e}")
            raise
        finally:
            if conn:
                conn.close()

    def _mark_status(self, entity_type: str, entity_id: int, status: str):
        """Record the outcome of an attempt in the provider ledger.

        Replaces the legacy `<service>_match_status`/`_last_attempted` column
        pair. Both `not_found` and `error` become due again after the retry
        window; a source-wide outage is handled by the worker's own backoff
        before an attempt is ever recorded, so it cannot become a tight loop.
        """
        conn = None
        try:
            from core.library2.provider_attempts import record_attempt

            conn = self.db._get_connection()
            record_attempt(conn, entity_type=entity_type, entity_id=entity_id,
                           service='lastfm', status=status)
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
            from core.library2.worker_queue import pending_count

            conn = self.db._get_connection()
            return pending_count(conn, 'lastfm', retry_after_days=self.retry_days)
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
            return progress_breakdown(conn, 'lastfm')
        except Exception as e:
            logger.error(f"Error getting progress breakdown: {e}")
            return {}
        finally:
            if conn:
                conn.close()
