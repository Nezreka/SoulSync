import re
import threading
import time
from difflib import SequenceMatcher
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
from utils.logging_config import get_logger
from database.music_database import MusicDatabase
from core.amazon_client import AmazonClient
from core.worker_utils import idle_backoff_seconds, interruptible_sleep
from core.library2.worker_support import MATCHED, honor_stored_match
from core.amazon_outage import is_source_outage, next_poll_delay_seconds

logger = get_logger("amazon_worker")


class AmazonWorker:
    """Background worker for enriching library artists, albums, and tracks with Amazon Music metadata."""

    def __init__(self, database: MusicDatabase):
        self.db = database
        self.client = AmazonClient()

        self.running = False
        self.paused = False
        self.should_stop = False
        self.thread = None
        self._stop_event = threading.Event()

        self.current_item = None

        # Consecutive empty-queue polls, drives idle_backoff_seconds()
        self._empty_streak = 0

        self.stats = {
            'matched': 0,
            'not_found': 0,
            'pending': 0,
            'errors': 0,
        }

        self.retry_days = 30
        self.name_similarity_threshold = 0.80

        # Source-outage circuit breaker: counts consecutive whole-source
        # failures (proxy down / "not initialized" / unreachable) so the loop
        # backs off instead of grinding the whole library item-by-item.
        self._outage_streak = 0

        logger.info("Amazon background worker initialized")

    def start(self):
        if self.running:
            logger.warning("Worker already running")
            return

        self.running = True
        self.should_stop = False
        self._stop_event.clear()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        logger.info("Amazon background worker started")

    def stop(self):
        if not self.running:
            return

        logger.info("Stopping Amazon worker...")
        self.should_stop = True
        self.running = False
        self._stop_event.set()

        if self.thread:
            self.thread.join(timeout=1)

        logger.info("Amazon worker stopped")

    def pause(self):
        if not self.running:
            logger.warning("Worker not running, cannot pause")
            return
        self.paused = True
        logger.info("Amazon worker paused")

    def resume(self):
        if not self.running:
            logger.warning("Worker not running, start it first")
            return
        self.paused = False
        logger.info("Amazon worker resumed")

    def get_stats(self) -> Dict[str, Any]:
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
            'progress': progress,
        }

    def _run(self):
        logger.info("Amazon worker thread started")
        while not self.should_stop:
            try:
                if self.paused:
                    interruptible_sleep(self._stop_event, 1)
                    continue

                self.current_item = None
                item = self._get_next_item()

                if not item:
                    logger.debug("No pending items, sleeping...")
                    interruptible_sleep(self._stop_event, idle_backoff_seconds(self._empty_streak))
                    self._empty_streak += 1
                    continue

                self._empty_streak = 0
                self.current_item = item
                item_id = item.get('id') or item.get('artist_id') or item.get('album_id')
                if item_id is None:
                    logger.warning(f"Skipping {item.get('type', 'unknown')} with NULL id: {item.get('name', '?')}")
                    continue

                self._process_item(item)
                # Normal 2s cadence when healthy; escalating back-off (up to
                # 30 min) while the source is in an outage streak.
                interruptible_sleep(self._stop_event, next_poll_delay_seconds(self._outage_streak))

            except Exception as e:
                logger.error(f"Error in worker loop: {e}")
                interruptible_sleep(self._stop_event, 5)

        logger.info("Amazon worker thread finished")

    def _get_next_item(self) -> Optional[Dict[str, Any]]:
        """Get next item to process from the Library-v2 catalogue.

        Priority, retry window and the pinned-group override all live in
        ``core.library2.worker_queue`` — the same rules every enrichment worker uses
        (docs §32.3.1 stage 2).
        """
        conn = None
        try:
            from core.library2.worker_queue import next_pending
            from core.worker_utils import read_enrichment_priority

            conn = self.db._get_connection()
            return next_pending(
                conn, 'amazon',
                retry_after_days=self.retry_days,
                pinned=read_enrichment_priority('amazon') or None,
            )

        except Exception as e:
            logger.error(f"Error getting next item: {e}")
            return None
        finally:
            if conn:
                conn.close()

    def _normalize_name(self, name: str) -> str:
        name = name.lower().strip()
        name = re.sub(r'\s+[-–—]\s+.*$', '', name)
        name = re.sub(r'\s*\(.*?\)\s*', ' ', name)
        name = re.sub(r'[^\w\s]', '', name)
        name = re.sub(r'\s+', ' ', name).strip()
        return name

    def _name_matches(self, query_name: str, result_name: str) -> bool:
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

            # The source answered (match or not_found) — clear any outage streak.
            if self._outage_streak:
                logger.info("Amazon source recovered after %d outage(s), resuming",
                            self._outage_streak)
                self._outage_streak = 0

        except Exception as e:
            if is_source_outage(e):
                # The whole source is down (proxy 5xx / "not initialized" /
                # unreachable). Do NOT mark the item 'error' — that would burn
                # the entire library to a state the retry tiers never re-attempt
                # for a transient outage. Leave it untouched so it's retried once
                # the instance recovers, and let the loop back off. Log once per
                # streak to avoid flooding.
                self._outage_streak += 1
                if self._outage_streak == 1:
                    logger.warning("Amazon source unavailable — pausing enrichment "
                                   "until it recovers: %s", e)
                else:
                    logger.debug("Amazon source still unavailable (streak=%d): %s",
                                 self._outage_streak, e)
                return
            # A non-outage error means the source actually answered (e.g. a
            # 404/parse error on a real response), so the outage is over —
            # clear the streak and handle this as a normal per-item error.
            self._outage_streak = 0
            logger.error(f"Error processing {item['type']} #{item['id']}: {e}")
            self.stats['errors'] += 1
            try:
                self._mark_status(item['type'], item['id'], 'error')
            except Exception as e2:
                logger.error(f"Error updating item status: {e2}")

    def _get_existing_id(self, entity_type: str, entity_id: int) -> Optional[str]:
        """The Amazon ASIN already stored for this entity, if any."""
        conn = None
        try:
            from core.library2.worker_support import stored_provider_id

            conn = self.db._get_connection()
            return stored_provider_id(conn, entity_type, entity_id, 'amazon')
        except Exception:
            return None
        finally:
            if conn:
                conn.close()

    def _process_artist(self, artist_id: int, artist_name: str):
        existing_id = self._get_existing_id('artist', artist_id)
        if existing_id:
            logger.debug(f"Preserving existing Amazon ID for artist '{artist_name}': {existing_id}")
            self._mark_status('artist', artist_id, 'matched')
            return

        results = self.client.search_artists(artist_name, limit=5)
        if results:
            result = results[0]
            if self._name_matches(artist_name, result.name):
                self._update_artist(artist_id, result)
                self.stats['matched'] += 1
                logger.info(f"Matched artist '{artist_name}' -> Amazon ID: {result.id}")
            else:
                self._mark_status('artist', artist_id, 'not_found')
                self.stats['not_found'] += 1
                logger.debug(f"Name mismatch for artist '{artist_name}' (got '{result.name}')")
        else:
            self._mark_status('artist', artist_id, 'not_found')
            self.stats['not_found'] += 1
            logger.debug(f"No match for artist '{artist_name}'")

    def _refresh_album_via_stored_id(self, album_id, stored_id, api_data):
        self._update_album(album_id, api_data, stored_id)

    def _refresh_track_via_stored_id(self, track_id, stored_id, api_data):
        self._update_track(track_id, api_data, stored_id)

    def _process_album(self, album_id: int, album_name: str, artist_name: str, item: Dict[str, Any]):
        _stored = honor_stored_match(
            self.db, entity_type='album', entity_id=album_id, service='amazon',
            fetch=lambda asin: self.client.get_album(asin, include_tracks=False),
            on_match=self._refresh_album_via_stored_id,
            log_prefix='Amazon',
        )
        if _stored:
            # L2-005: a stored id the provider could not confirm right now is
            # NOT released to the fuzzy name search below — a transient failure
            # is not evidence that the id is wrong, and searching overwrote
            # deliberately chosen matches with whatever came back.
            if _stored == MATCHED:
                self.stats['matched'] += 1
            return
        # A stored/manual id whose provider refresh temporarily failed must not
        # fall through to fuzzy search and be replaced by a different result.
        if self._get_existing_id('album', album_id):
            logger.debug(
                "Preserving Amazon match for album '%s' despite a refresh miss",
                album_name,
            )
            return

        query = f"{artist_name} {album_name}"
        results = self.client.search_albums(query, limit=10)
        if results:
            result = results[0]
            if self._name_matches(album_name, result.name):
                full_album = None
                if result.id:
                    try:
                        full_album = self.client.get_album(result.id, include_tracks=False)
                    except Exception as e:
                        logger.warning(f"Failed to fetch full album '{album_name}' (ASIN: {result.id}): {e}")

                if full_album is None:
                    self._mark_status('album', album_id, 'error')
                    self.stats['errors'] += 1
                    logger.warning(f"Album '{album_name}' matched but full details unavailable, will retry")
                    return

                self._update_album(album_id, full_album, result.id)
                self.stats['matched'] += 1
                logger.info(f"Matched album '{album_name}' -> Amazon ASIN: {result.id}")
            else:
                self._mark_status('album', album_id, 'not_found')
                self.stats['not_found'] += 1
                logger.debug(f"Name mismatch for album '{album_name}' (got '{result.name}')")
        else:
            self._mark_status('album', album_id, 'not_found')
            self.stats['not_found'] += 1
            logger.debug(f"No match for album '{album_name}'")

    def _process_track(self, track_id: int, track_name: str, artist_name: str, item: Dict[str, Any]):
        _stored = honor_stored_match(
            self.db, entity_type='track', entity_id=track_id, service='amazon',
            fetch=self.client.get_track_details,
            on_match=self._refresh_track_via_stored_id,
            log_prefix='Amazon',
        )
        if _stored:
            # L2-005: a stored id the provider could not confirm right now is
            # NOT released to the fuzzy name search below — a transient failure
            # is not evidence that the id is wrong, and searching overwrote
            # deliberately chosen matches with whatever came back.
            if _stored == MATCHED:
                self.stats['matched'] += 1
            return
        if self._get_existing_id('track', track_id):
            logger.debug(
                "Preserving Amazon match for track '%s' despite a refresh miss",
                track_name,
            )
            return

        query = f"{artist_name} {track_name}"
        results = self.client.search_tracks(query, limit=10)
        if results:
            result = results[0]
            if self._name_matches(track_name, result.name):
                full_track = None
                if result.id:
                    try:
                        full_track = self.client.get_track_details(result.id)
                    except Exception as e:
                        logger.warning(f"Failed to fetch full track '{track_name}' (ASIN: {result.id}): {e}")

                if full_track is None:
                    self._mark_status('track', track_id, 'error')
                    self.stats['errors'] += 1
                    logger.warning(f"Track '{track_name}' matched but full details unavailable, will retry")
                    return

                self._update_track(track_id, full_track, result.id)
                self.stats['matched'] += 1
                logger.info(f"Matched track '{track_name}' -> Amazon ASIN: {result.id}")
            else:
                self._mark_status('track', track_id, 'not_found')
                self.stats['not_found'] += 1
                logger.debug(f"Name mismatch for track '{track_name}' (got '{result.name}')")
        else:
            self._mark_status('track', track_id, 'not_found')
            self.stats['not_found'] += 1
            logger.debug(f"No match for track '{track_name}'")

    def _update_artist(self, artist_id: int, result):
        """Store Amazon metadata for an artist. ``result`` is an Artist dataclass."""
        image_url = result.image_url
        if not image_url:
            # Amazon has no artist image endpoint; an album cover stands in.
            try:
                image_url = self.client._get_artist_image_from_albums(result.id)
            except Exception as exc:
                logger.debug("Artist image via album cover failed for %s: %s", result.id, exc)
        self._write('artist', artist_id, str(result.id), image=image_url)

    def _update_album(self, album_id: int, full_data: Dict[str, Any], asin: str):
        """Store Amazon metadata for an album. ``full_data`` is a get_album() dict."""
        images = full_data.get('images') or []
        total_tracks = full_data.get('total_tracks') or (
            full_data.get('tracks', {}).get('total')
            if isinstance(full_data.get('tracks'), dict) else None
        )
        self._write(
            'album', album_id, asin,
            image=images[0].get('url') if images else None,
            label=full_data.get('label'),
            total_tracks=total_tracks,
        )

    def _update_track(self, track_id: int, full_data: Dict[str, Any], asin: str):
        """Store Amazon metadata for a track. ``full_data`` is a get_track_details() dict."""
        self._write('track', track_id, asin)

    def _write(self, entity_type: str, entity_id: int, provider_id,
               image: Optional[str] = None, label: Optional[str] = None,
               total_tracks: Any = None):
        """One write path for all three entity types (docs §32.3.1 stage 2).

        Everything outside Amazon's own namespace is backfill: its artwork and label
        are stand-ins, not authorities, and must not overwrite what a better source
        or the user chose.
        """
        conn = None
        try:
            from core.library2.provider_attempts import record_attempt
            from core.library2.provider_writes import write_provider_enrichment
            from core.library2.worker_support import set_expected_track_count

            conn = self.db._get_connection()

            backfill = {}
            if image:
                backfill['image_url'] = image
            if label:
                backfill['label'] = label

            write_provider_enrichment(
                conn, entity_type=entity_type, entity_id=entity_id,
                service='amazon',
                provider_id=provider_id,
                backfill=backfill or None,
            )
            if entity_type == 'album':
                set_expected_track_count(conn, entity_id, total_tracks)
            record_attempt(conn, entity_type=entity_type, entity_id=entity_id,
                           service='amazon', status='matched')
            conn.commit()

        except Exception as e:
            logger.error(f"Error updating {entity_type} #{entity_id} with Amazon data: {e}")
            raise
        finally:
            if conn:
                conn.close()

    def _mark_status(self, entity_type: str, entity_id: int, status: str):
        """Record the outcome of an attempt in the provider ledger.

        Replaces the legacy `amazon_match_status`/`_last_attempted` column pair.
        `not_found` and per-item `error` outcomes become due again after the retry
        window. Source-wide outages remain untouched and use the worker backoff,
        so an outage cannot turn into a tight retry loop.
        """
        conn = None
        try:
            from core.library2.provider_attempts import record_attempt

            conn = self.db._get_connection()
            record_attempt(conn, entity_type=entity_type, entity_id=entity_id,
                           service='amazon', status=status)
            conn.commit()
        except Exception as e:
            logger.error(f"Error marking {entity_type} #{entity_id} status: {e}")
        finally:
            if conn:
                conn.close()

    def _count_pending_items(self) -> int:
        conn = None
        try:
            from core.library2.worker_queue import pending_count

            conn = self.db._get_connection()
            return pending_count(conn, 'amazon', retry_after_days=self.retry_days)
        except Exception as e:
            logger.error(f"Error counting pending items: {e}")
            return 0
        finally:
            if conn:
                conn.close()

    def _get_progress_breakdown(self) -> Dict[str, Dict[str, int]]:
        conn = None
        try:
            from core.library2.worker_queue import progress_breakdown

            conn = self.db._get_connection()
            return progress_breakdown(conn, 'amazon')
        except Exception as e:
            logger.error(f"Error getting progress breakdown: {e}")
            return {}
        finally:
            if conn:
                conn.close()
