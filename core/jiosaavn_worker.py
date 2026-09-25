import re
import threading
from difflib import SequenceMatcher
from typing import Optional, Dict, Any
from datetime import datetime, timedelta

from utils.logging_config import get_logger
from database.music_database import MusicDatabase
from core.metadata.registry import get_jiosaavn_client, is_jiosaavn_enabled
from core.worker_utils import artist_name_matches, interruptible_sleep
from core.library2.worker_support import (
    MATCHED, accept_artist_match, honor_stored_match,
)

logger = get_logger("jiosaavn_worker")


class JioSaavnWorker:
    """Background worker for enriching library artists, albums, and tracks with JioSaavn metadata."""

    def __init__(self, database: MusicDatabase):
        self.db = database
        self._client = None

        self.running = False
        self.paused = False
        self.should_stop = False
        self.thread = None
        self._stop_event = threading.Event()

        self.current_item = None

        self.stats = {
            'matched': 0,
            'not_found': 0,
            'pending': 0,
            'errors': 0,
        }

        self.retry_days = 30
        self.name_similarity_threshold = 0.80

        logger.info("JioSaavn background worker initialized")

    @property
    def client(self):
        if self._client is None:
            self._client = get_jiosaavn_client()
        return self._client

    def start(self):
        if self.running:
            logger.warning("Worker already running")
            return

        self.running = True
        self.should_stop = False
        self._stop_event.clear()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        logger.info("JioSaavn background worker started")

    def stop(self):
        if not self.running:
            return

        logger.info("Stopping JioSaavn worker...")
        self.should_stop = True
        self.running = False
        self._stop_event.set()

        if self.thread:
            self.thread.join(timeout=1)

        logger.info("JioSaavn worker stopped")

    def pause(self):
        if not self.running:
            logger.warning("Worker not running, cannot pause")
            return

        self.paused = True
        logger.info("JioSaavn worker paused")

    def resume(self):
        if not self.running:
            logger.warning("Worker not running, start it first")
            return

        self.paused = False
        logger.info("JioSaavn worker resumed")

    def get_stats(self) -> Dict[str, Any]:
        self.stats['pending'] = self._count_pending_items()
        progress = self._get_progress_breakdown()

        is_actually_running = self.running and (self.thread is not None and self.thread.is_alive())
        is_idle = (
            is_actually_running
            and not self.paused
            and self.stats['pending'] == 0
            and self.current_item is None
        )

        return {
            'enabled': is_jiosaavn_enabled(),
            'running': is_actually_running and not self.paused and is_jiosaavn_enabled(),
            'paused': self.paused,
            'idle': is_idle,
            'current_item': self.current_item,
            'stats': self.stats.copy(),
            'progress': progress,
        }

    def _run(self):
        logger.info("JioSaavn worker thread started")

        while not self.should_stop:
            try:
                if not is_jiosaavn_enabled():
                    interruptible_sleep(self._stop_event, 10)
                    continue

                if self.paused:
                    interruptible_sleep(self._stop_event, 1)
                    continue

                self.current_item = None
                item = self._get_next_item()

                if not item:
                    logger.debug("No pending items, sleeping...")
                    interruptible_sleep(self._stop_event, 10)
                    continue

                self.current_item = item
                item_id = item.get('id') or item.get('artist_id') or item.get('album_id')
                if item_id is None:
                    logger.warning(
                        "Skipping %s with NULL id: %s",
                        item.get('type', 'unknown'),
                        item.get('name', '?'),
                    )
                    continue

                self._process_item(item)
                interruptible_sleep(self._stop_event, 1)

            except Exception as e:
                logger.error("Error in worker loop: %s", e)
                interruptible_sleep(self._stop_event, 5)

        logger.info("JioSaavn worker thread finished")

    # JioSaavn retries 'error' alongside 'not_found'. Issue #964: a detail fetch
    # that fails after a search match is marked rather than left unattempted, so the
    # queue stops re-picking it every tick — but it has to come back eventually.
    _RETRY_STATUSES = ('error', 'not_found')

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
                conn, 'jiosaavn',
                retry_after_days=self.retry_days,
                pinned=read_enrichment_priority('jiosaavn') or None,
                retry_statuses=self._RETRY_STATUSES,
            )

        except Exception as e:
            logger.error("Error getting next item: %s", e)
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
            # Titles that normalize to NOTHING ("(Intro)", "[Skit]", "!!!",
            # "...") would compare at SequenceMatcher ratio 1.0 against any
            # other such title — fall back to exact raw comparison instead.
            raw_q = (query_name or '').strip().lower()
            raw_r = (result_name or '').strip().lower()
            return bool(raw_q) and raw_q == raw_r
        similarity = SequenceMatcher(None, norm_query, norm_result).ratio()
        logger.debug("Name similarity: '%s' vs '%s' = %.2f", query_name, result_name, similarity)
        return similarity >= self.name_similarity_threshold

    def _artist_matches_result(self, artist_name: str, result_artists: list) -> bool:
        if not result_artists:
            return False
        return any(artist_name_matches(artist_name, a) for a in result_artists)

    def _process_item(self, item: Dict[str, Any]):
        try:
            item_type = item['type']
            item_id = item['id']
            item_name = item['name']

            logger.debug("Processing %s #%s: %s", item_type, item_id, item_name)

            if item_type == 'artist':
                self._process_artist(item_id, item_name)
            elif item_type == 'album':
                self._process_album(item_id, item_name, item.get('artist', ''))
            elif item_type == 'track':
                self._process_track(item_id, item_name, item.get('artist', ''))

        except Exception as e:
            logger.error("Error processing %s #%s: %s", item['type'], item['id'], e)
            self.stats['errors'] += 1
            try:
                self._mark_status(item['type'], item['id'], 'error')
            except Exception as e2:
                logger.error("Error updating item status: %s", e2)

    def _get_existing_id(self, entity_type: str, entity_id: int) -> Optional[str]:
        """The JioSaavn id already stored for this entity, if any."""
        conn = None
        try:
            from core.library2.worker_support import stored_provider_id

            conn = self.db._get_connection()
            return stored_provider_id(conn, entity_type, entity_id, 'jiosaavn')
        except Exception:
            return None
        finally:
            if conn:
                conn.close()

    def _process_artist(self, artist_id: int, artist_name: str):
        existing_id = self._get_existing_id('artist', artist_id)
        if existing_id:
            # Has an id but status may still be NULL (e.g. an id-only manual match),
            # and _get_next_item selects NULL rows every loop — stamp 'matched' so it
            # stops re-selecting this artist and blocking the queue (#964).
            self._mark_status('artist', artist_id, 'matched')
            logger.debug("Preserving existing JioSaavn ID for artist '%s': %s", artist_name, existing_id)
            return

        results = self.client.search_artists(artist_name, limit=5)
        gated = [a for a in (results or []) if artist_name_matches(artist_name, getattr(a, 'name', ''))]
        chosen = gated[0] if gated else None

        if chosen:
            conn = self.db._get_connection()
            try:
                ok, reason = accept_artist_match(
                    conn, 'jiosaavn', chosen.id, artist_id,
                    artist_name, chosen.name,
                )
            finally:
                conn.close()
            if ok:
                self._update_artist(artist_id, chosen)
                self.stats['matched'] += 1
                logger.info("Matched artist '%s' -> JioSaavn ID: %s", artist_name, chosen.id)
            else:
                self._mark_status('artist', artist_id, 'not_found')
                self.stats['not_found'] += 1
                logger.debug("Artist '%s' not matched: %s", artist_name, reason)
        else:
            self._mark_status('artist', artist_id, 'not_found')
            self.stats['not_found'] += 1
            logger.debug("No match for artist '%s'", artist_name)

    def _refresh_album_via_stored_id(self, album_id, stored_id, full_album_dict):
        self._update_album(album_id, full_album_dict)

    def _refresh_track_via_stored_id(self, track_id, stored_id, full_track_dict):
        self._update_track(track_id, full_track_dict)

    def _process_album(self, album_id: int, album_name: str, artist_name: str):
        _stored = honor_stored_match(
            self.db, entity_type='album', entity_id=album_id, service='jiosaavn',
            fetch=self.client.get_album,
            on_match=self._refresh_album_via_stored_id,
            log_prefix='JioSaavn',
        )
        if _stored:
            # L2-005: a stored id the provider could not confirm right now is
            # NOT released to the fuzzy name search below — a transient failure
            # is not evidence that the id is wrong, and searching overwrote
            # deliberately chosen matches with whatever came back.
            if _stored == MATCHED:
                self.stats['matched'] += 1
            return
        # honor_stored_match also returns False when the stored id failed to
        # re-fetch (transient error / rate limit). Don't fall through to a
        # name search — it could clobber a manual match. Only search when
        # there's genuinely no stored id (the Bandcamp guard, applied here).
        if self._get_existing_id('album', album_id):
            logger.debug("Preserving JioSaavn match for album '%s' despite a refresh miss", album_name)
            return

        query = f"{artist_name} {album_name}".strip()
        results = self.client.search_albums(query, limit=5)
        chosen = None
        for candidate in results or []:
            if self._name_matches(album_name, candidate.name) and self._artist_matches_result(
                artist_name, candidate.artists
            ):
                chosen = candidate
                break

        if chosen:
            full_album = self.client.get_album(chosen.id)
            if full_album is None:
                # Detail fetch failed after a search match. Mark 'error' (NOT left
                # NULL): _get_next_item selects NULL rows by id ASC every loop, so a
                # NULL row here would be re-picked forever, wedging the whole album
                # pass on one bad id and hammering the API. 'error' moves it to the
                # deferred (retry_days) queue instead (#964).
                self._mark_status('album', album_id, 'error')
                self.stats['errors'] += 1
                logger.warning(
                    "Album '%s' matched but full details unavailable, deferring retry",
                    album_name,
                )
                return

            self._update_album(album_id, full_album)
            self.stats['matched'] += 1
            logger.info("Matched album '%s' -> JioSaavn ID: %s", album_name, chosen.id)
        else:
            self._mark_status('album', album_id, 'not_found')
            self.stats['not_found'] += 1
            logger.debug("No match for album '%s'", album_name)

    def _process_track(self, track_id: int, track_name: str, artist_name: str):
        _stored = honor_stored_match(
            self.db, entity_type='track', entity_id=track_id, service='jiosaavn',
            fetch=self.client.get_track_details,
            on_match=self._refresh_track_via_stored_id,
            log_prefix='JioSaavn',
        )
        if _stored:
            # L2-005: a stored id the provider could not confirm right now is
            # NOT released to the fuzzy name search below — a transient failure
            # is not evidence that the id is wrong, and searching overwrote
            # deliberately chosen matches with whatever came back.
            if _stored == MATCHED:
                self.stats['matched'] += 1
            return
        # honor_stored_match also returns False when the stored id failed to
        # re-fetch (transient error / rate limit). Don't fall through to a
        # name search — it could clobber a manual match. Only search when
        # there's genuinely no stored id (the Bandcamp guard, applied here).
        if self._get_existing_id('track', track_id):
            logger.debug("Preserving JioSaavn match for track '%s' despite a refresh miss", track_name)
            return

        query = f"{artist_name} {track_name}".strip()
        results = self.client.search_tracks(query, limit=5)
        chosen = None
        for candidate in results or []:
            if self._name_matches(track_name, candidate.name) and self._artist_matches_result(
                artist_name, candidate.artists
            ):
                chosen = candidate
                break

        if chosen:
            full_track = self.client.get_track_details(chosen.id)
            if full_track is None:
                # See _process_album: a NULL row is re-picked every loop, so mark
                # 'error' to defer it to the retry_days queue instead (#964).
                self._mark_status('track', track_id, 'error')
                self.stats['errors'] += 1
                logger.warning(
                    "Track '%s' matched but full details unavailable, deferring retry",
                    track_name,
                )
                return

            self._update_track(track_id, full_track)
            self.stats['matched'] += 1
            logger.info("Matched track '%s' -> JioSaavn ID: %s", track_name, chosen.id)
        else:
            self._mark_status('track', track_id, 'not_found')
            self.stats['not_found'] += 1
            logger.debug("No match for track '%s'", track_name)

    def _update_artist(self, artist_id: int, data) -> None:
        artist_js_id = str(getattr(data, 'id', None) or data.get('id'))
        thumb_url = getattr(data, 'image_url', None) or (
            data.get('image_url') if isinstance(data, dict) else None)
        self._write('artist', artist_id, artist_js_id, image=thumb_url)

    def _update_album(self, album_id: int, data: Dict[str, Any]) -> None:
        # `album_type` has no lib2 equivalent to backfill: legacy `record_type` could
        # be empty, while `lib2_albums.album_type` always carries a classification
        # (the importer and MB reconcile own it, defaulting to 'album'). Keeping
        # JioSaavn's word in the enrichment payload loses nothing and overwrites
        # nobody.
        self._write(
            'album', album_id, str(data.get('id')),
            payload={'album_type': data.get('album_type')},
            image=data.get('image_url'),
            label=data.get('label'),
            total_tracks=data.get('total_tracks'),
        )

    def _update_track(self, track_id: int, data: Dict[str, Any]) -> None:
        self._write('track', track_id, str(data.get('id')))

    def _write(self, entity_type: str, entity_id: int, provider_id,
               payload: Optional[Dict[str, Any]] = None,
               image: Optional[str] = None, label: Optional[str] = None,
               total_tracks: Any = None) -> None:
        """One write path for all three entity types (docs §32.3.1 stage 2).

        Everything outside JioSaavn's own namespace is backfill: its artwork and
        label are stand-ins, not authorities, and must not overwrite what a better
        source or the user chose.
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
                service='jiosaavn',
                payload=payload,
                provider_id=provider_id,
                backfill=backfill or None,
            )
            if entity_type == 'album':
                set_expected_track_count(conn, entity_id, total_tracks)
            record_attempt(conn, entity_type=entity_type, entity_id=entity_id,
                           service='jiosaavn', status='matched')
            conn.commit()

        except Exception as e:
            logger.error("Error updating %s #%s with JioSaavn data: %s",
                         entity_type, entity_id, e)
            raise
        finally:
            if conn:
                conn.close()

    def _mark_status(self, entity_type: str, entity_id: int, status: str) -> None:
        """Record the outcome of an attempt in the provider ledger.

        Replaces the legacy `jiosaavn_match_status`/`_last_attempted` column pair.
        Both `not_found` and `error` become due again after the retry window here —
        see `_RETRY_STATUSES`.
        """
        conn = None
        try:
            from core.library2.provider_attempts import record_attempt

            conn = self.db._get_connection()
            record_attempt(conn, entity_type=entity_type, entity_id=entity_id,
                           service='jiosaavn', status=status)
            conn.commit()
        except Exception as e:
            logger.error("Error marking %s #%s status: %s", entity_type, entity_id, e)
        finally:
            if conn:
                conn.close()

    def _count_pending_items(self) -> int:
        conn = None
        try:
            from core.library2.worker_queue import pending_count

            conn = self.db._get_connection()
            return pending_count(conn, 'jiosaavn', retry_after_days=self.retry_days,
                                 retry_statuses=self._RETRY_STATUSES)
        except Exception as e:
            logger.error("Error counting pending items: %s", e)
            return 0
        finally:
            if conn:
                conn.close()

    def _get_progress_breakdown(self) -> Dict[str, Dict[str, int]]:
        conn = None
        try:
            from core.library2.worker_queue import progress_breakdown

            conn = self.db._get_connection()
            return progress_breakdown(conn, 'jiosaavn')
        except Exception as e:
            logger.error("Error getting progress breakdown: %s", e)
            return {}
        finally:
            if conn:
                conn.close()
