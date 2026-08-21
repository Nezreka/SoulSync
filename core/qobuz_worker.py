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
from core.worker_utils import idle_backoff_seconds, interruptible_sleep
from core.library2.worker_support import (
    MATCHED,
    accept_artist_match,
    honor_stored_match,
    provider_id_conflict,
)

logger = get_logger("qobuz_worker")

def _parent_artist_id(conn, entity_type: str, entity_id) -> Optional[int]:
    """The lib2 artist that owns an album or track.

    A track's artist is two joins away in lib2 — track → album → primary artist —
    where legacy carried ``tracks.artist_id`` on the row itself.
    """
    sql = {
        'album': "SELECT primary_artist_id FROM lib2_albums WHERE id=?",
        'track': ("SELECT al.primary_artist_id FROM lib2_tracks t "
                  "JOIN lib2_albums al ON al.id=t.album_id WHERE t.id=?"),
    }.get(entity_type)
    if not sql:
        return None
    row = conn.execute(sql, (entity_id,)).fetchone()
    return row[0] if row else None



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

        # Consecutive empty-queue polls, drives idle_backoff_seconds()
        self._empty_streak = 0

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
        except Exception as e:
            logger.debug("qobuz auth status check: %s", e)

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
                    interruptible_sleep(self._stop_event, idle_backoff_seconds(self._empty_streak))
                    self._empty_streak += 1
                    continue

                self._empty_streak = 0
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

                # Throttle between API calls
                interruptible_sleep(self._stop_event, 2)

            except Exception as e:
                logger.error(f"Error in worker loop: {e}")
                interruptible_sleep(self._stop_event, 5)

        logger.info("Qobuz worker thread finished")

    def _get_next_item(self) -> Optional[Dict[str, Any]]:
        """Get next item to process from the Library-v2 catalogue.

        Priority, retry window and the pinned-group override all live in
        ``core.library2.worker_queue`` — the same rules every enrichment worker uses
        (docs §32.3.1 stage 2). ``include_parent_id`` puts the parent artist's
        Qobuz id on an album or track item, which ``_verify_artist_id`` compares
        the result against.
        """
        conn = None
        try:
            from core.library2.worker_queue import next_pending
            from core.worker_utils import read_enrichment_priority

            conn = self.db._get_connection()
            return next_pending(
                conn, 'qobuz',
                retry_after_days=self.retry_days,
                pinned=read_enrichment_priority('qobuz') or None,
                include_parent_id=True,
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
        """Check if Qobuz result name matches our query with fuzzy matching"""
        norm_query = self._normalize_name(query_name)
        norm_result = self._normalize_name(result_name)
        if not norm_query or not norm_result:
            raw_query = (query_name or '').strip().lower()
            raw_result = (result_name or '').strip().lower()
            return bool(raw_query) and raw_query == raw_result

        similarity = SequenceMatcher(None, norm_query, norm_result).ratio()
        logger.debug(f"Name similarity: '{query_name}' vs '{result_name}' = {similarity:.2f}")
        return similarity >= self.name_similarity_threshold

    def _verify_artist_id(self, item: Dict[str, Any], result_artist_id,
                          result_artist_name: Optional[str] = None) -> bool:
        """Verify/correct parent artist's Qobuz ID based on album/track match.

        Only corrects when the result's artist *name* matches our parent artist —
        otherwise a collaboration/compilation would stamp the wrong Qobuz id onto
        our artist. See the Deezer fix for the full write-up."""
        parent_qobuz_id = item.get('artist_qobuz_id')
        if not parent_qobuz_id or not result_artist_id:
            return True

        if str(result_artist_id) != str(parent_qobuz_id):
            parent_name = item.get('artist') or ''
            if not (result_artist_name and parent_name
                    and self._name_matches(parent_name, result_artist_name)):
                logger.info(
                    f"Skipping artist-ID correction from {item['type']} "
                    f"'{item['name']}': cannot verify result artist "
                    f"'{result_artist_name}' == parent '{parent_name}' "
                    f"(collab/compilation or missing name, not a correction)"
                )
                return True

            logger.info(
                f"Artist ID correction from {item['type']} '{item['name']}': "
                f"updating parent artist Qobuz ID from {parent_qobuz_id} to {result_artist_id}"
            )
            self._correct_artist_qobuz_id(item, str(result_artist_id))

        return True

    def _correct_artist_qobuz_id(self, item: Dict[str, Any], correct_qobuz_id: str):
        """Correct the parent artist's Qobuz id from a more specific album/track
        match. The name guard in ``_verify_artist_id`` has already run."""
        conn = None
        try:
            from core.library2.provider_writes import write_provider_enrichment

            conn = self.db._get_connection()
            artist_id = _parent_artist_id(conn, item['type'], item['id'])
            if artist_id is None:
                return

            row = conn.execute(
                "SELECT name FROM lib2_artists WHERE id=?", (artist_id,)
            ).fetchone()
            this_name = (row[0] if row else '') or (item.get('artist') or '')
            conflict = provider_id_conflict(
                conn, 'qobuz', correct_qobuz_id, artist_id, this_name)
            if conflict:
                logger.warning(
                    "Refusing Qobuz-ID correction: id %s is already held by "
                    "'%s' (≠ '%s') — avoiding a shared/duplicate id (artist #%s)",
                    correct_qobuz_id, conflict, this_name, artist_id,
                )
                return

            write_provider_enrichment(
                conn, entity_type='artist', entity_id=artist_id,
                service='qobuz', provider_id=correct_qobuz_id)
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
        """The Qobuz id already stored for this entity, if any."""
        conn = None
        try:
            from core.library2.worker_support import stored_provider_id

            conn = self.db._get_connection()
            return stored_provider_id(conn, entity_type, entity_id, 'qobuz')
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
            # Mark as matched so this row is not re-selected on every loop.
            self._mark_status('artist', artist_id, 'matched')
            return

        result = self.client.search_artist(artist_name)

        if result:
            result_name = result.get('name', '')
            qobuz_artist_id = result.get('id')
            conn = self.db._get_connection()
            try:
                ok, reason = accept_artist_match(
                    conn, 'qobuz', qobuz_artist_id, artist_id, artist_name, result_name,
                )
            finally:
                conn.close()
            if not ok:
                self._mark_status('artist', artist_id, 'not_found')
                self.stats['not_found'] += 1
                logger.debug(f"Artist '{artist_name}' not matched: {reason}")
            elif not qobuz_artist_id:
                self._mark_status('artist', artist_id, 'error')
                self.stats['errors'] += 1
                logger.warning(f"Qobuz search result for '{artist_name}' has no ID")
            else:
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
            if _qobuz_is_rate_limited():
                logger.warning(f"Rate limited while searching artist '{artist_name}', will retry")
                return
            self._mark_status('artist', artist_id, 'not_found')
            self.stats['not_found'] += 1
            logger.debug(f"No match for artist '{artist_name}'")

    def _refresh_album_via_stored_id(self, album_id, stored_id, full_album_dict):
        """Issue #501 callback. Same shape as Tidal/Deezer — pass the
        full-album dict in both arg slots."""
        self._update_album(album_id, full_album_dict, full_album_dict)

    def _refresh_track_via_stored_id(self, track_id, stored_id, full_track_dict):
        self._update_track(track_id, full_track_dict, full_track_dict)

    def _process_album(self, album_id: int, album_name: str, artist_name: str, item: Dict[str, Any]):
        """Process an album: search Qobuz, verify, fetch full details, store metadata"""
        # Issue #501: honor manual matches. Pre-fix this just marked
        # status='matched' without refreshing metadata.
        _stored = honor_stored_match(
            self.db, entity_type='album', entity_id=album_id,
            service='qobuz',
            fetch=self.client.get_album,
            on_match=self._refresh_album_via_stored_id,
            log_prefix='Qobuz',
        )
        if _stored:
            # L2-005: a stored id the provider could not confirm right now is
            # NOT released to the fuzzy name search below — a transient failure
            # is not evidence that the id is wrong, and searching overwrote
            # deliberately chosen matches with whatever came back.
            if _stored == MATCHED:
                self.stats['matched'] += 1
            return

        result = self.client.search_album(artist_name, album_name)

        if result:
            result_name = result.get('title', '')
            if self._name_matches(album_name, result_name):
                # Verify artist ID
                result_artist = result.get('artist', {})
                result_artist_id = result_artist.get('id') if result_artist else None
                result_artist_name = result_artist.get('name') if result_artist else None
                self._verify_artist_id(item, result_artist_id, result_artist_name)

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
        # Issue #501: honor manual matches.
        _stored = honor_stored_match(
            self.db, entity_type='track', entity_id=track_id,
            service='qobuz',
            fetch=self.client.get_track,
            on_match=self._refresh_track_via_stored_id,
            log_prefix='Qobuz',
        )
        if _stored:
            # L2-005: a stored id the provider could not confirm right now is
            # NOT released to the fuzzy name search below — a transient failure
            # is not evidence that the id is wrong, and searching overwrote
            # deliberately chosen matches with whatever came back.
            if _stored == MATCHED:
                self.stats['matched'] += 1
            return

        result = self.client.search_track(artist_name, track_name)

        if result:
            result_name = result.get('title', '')
            if self._name_matches(track_name, result_name):
                # Verify artist ID
                result_artist = result.get('artist', result.get('performer', {}))
                result_artist_id = result_artist.get('id') if result_artist else None
                result_artist_name = result_artist.get('name') if result_artist else None
                self._verify_artist_id(item, result_artist_id, result_artist_name)

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

    @staticmethod
    def _image(src: Dict[str, Any]) -> Optional[str]:
        """Qobuz returns artwork as a size-keyed dict, a bare string, or under
        ``picture``. Largest first."""
        image = src.get('image', {})
        if isinstance(image, dict):
            return image.get('large') or image.get('medium') or image.get('small') \
                or image.get('thumbnail') or src.get('picture') or None
        if isinstance(image, str) and image:
            return image
        return src.get('picture') or None

    @staticmethod
    def _text(value) -> Optional[str]:
        """Label and copyright arrive either as a string or as a named object."""
        if isinstance(value, dict):
            value = value.get('name') or value.get('text')
        return value if isinstance(value, str) and value else None

    def _update_artist(self, artist_id: int, data: Dict[str, Any],
                       full_data: Optional[Dict[str, Any]] = None):
        """Store Qobuz metadata for an artist"""
        src = full_data or data
        self._write('artist', artist_id, data.get('id'),
                    backfill={'image_url': self._image(src)})

    def _update_album(self, album_id: int, search_data: Dict[str, Any],
                      full_data: Optional[Dict[str, Any]]):
        """Store Qobuz metadata for an album"""
        data = full_data or search_data
        backfill = {'image_url': self._image(data),
                    'label': self._text(data.get('label')),
                    'upc': str(data['upc']) if data.get('upc') else None}
        parental = data.get('parental_warning')
        if parental is not None:
            backfill['explicit'] = 1 if parental else 0
        tracks_count = data.get('tracks_count')
        if isinstance(tracks_count, int) and tracks_count > 0:
            backfill['track_count'] = tracks_count
        genre = data.get('genre', {})
        genre_name = genre.get('name') if isinstance(genre, dict) else (
            genre if isinstance(genre, str) else None)
        if genre_name:
            from core.genre_filter import filter_genres
            from core.settings import config_manager as _cfg
            _filtered = filter_genres([genre_name], _cfg)
            if _filtered:
                backfill['genres'] = json.dumps(_filtered)
        # `duration` and `copyright` have no album-level column in lib2 (tracks
        # carry both; albums never did). Keeping them in the payload loses nothing
        # and invents no column.
        duration = data.get('duration')
        payload = {
            'duration_ms': int(duration * 1000)
            if isinstance(duration, (int, float)) and duration > 0 else None,
            'copyright': self._text(data.get('copyright')),
        }
        self._write('album', album_id, search_data.get('id'),
                    backfill=backfill, payload=payload)

    def _update_track(self, track_id: int, search_data: Dict[str, Any],
                      full_data: Optional[Dict[str, Any]]):
        """Store Qobuz metadata for a track"""
        data = full_data or search_data
        backfill = {'copyright': self._text(data.get('copyright'))}
        parental = data.get('parental_warning')
        if parental is not None:
            backfill['explicit'] = 1 if parental else 0
        isrc = data.get('isrc')
        if isinstance(isrc, dict):
            isrc = isrc.get('value') or isrc.get('id')
        if isinstance(isrc, str) and isrc:
            backfill['isrc'] = isrc
        duration = data.get('duration')
        if isinstance(duration, (int, float)) and duration > 0:
            backfill['duration'] = int(duration * 1000)
        self._write('track', track_id, search_data.get('id'), backfill=backfill)

    def _write(self, entity_type: str, entity_id: int, provider_id,
               backfill: Optional[Dict[str, Any]] = None,
               payload: Optional[Dict[str, Any]] = None):
        """One write path for all three entity types (docs §32.3.1 stage 2).

        Everything outside Qobuz's own id is backfill — artwork, label, genres and
        the rest are shared with better sources and with the user's own choice. The
        match itself is committed even if a backfill value is unusable, which is what
        the old "failures here won't lose the match" second transaction was for;
        write_provider_enrichment simply skips an empty value instead.
        """
        conn = None
        try:
            from core.library2.provider_attempts import record_attempt
            from core.library2.provider_writes import write_provider_enrichment

            conn = self.db._get_connection()
            write_provider_enrichment(
                conn, entity_type=entity_type, entity_id=entity_id,
                service='qobuz',
                payload=payload,
                provider_id=str(provider_id) if provider_id else None,
                backfill={k: v for k, v in (backfill or {}).items() if v is not None}
                or None,
            )
            record_attempt(conn, entity_type=entity_type, entity_id=entity_id,
                           service='qobuz', status='matched')
            conn.commit()
        except Exception as e:
            logger.error(f"Error updating {entity_type} #{entity_id} with Qobuz data: {e}")
            raise
        finally:
            if conn:
                conn.close()

    def _mark_status(self, entity_type: str, entity_id: int, status: str):
        """Record the outcome of an attempt in the provider ledger.

        Replaces the legacy `qobuz_match_status`/`_last_attempted` column pair.
        Both `not_found` and `error` become due again after the retry
        window; a source-wide outage is handled by the worker's own backoff
        before an attempt is ever recorded, so it cannot become a tight loop.
        """
        conn = None
        try:
            from core.library2.provider_attempts import record_attempt

            conn = self.db._get_connection()
            record_attempt(conn, entity_type=entity_type, entity_id=entity_id,
                           service='qobuz', status=status)
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
            return pending_count(conn, 'qobuz', retry_after_days=self.retry_days)
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
            return progress_breakdown(conn, 'qobuz')
        except Exception as e:
            logger.error(f"Error getting progress breakdown: {e}")
            return {}
        finally:
            if conn:
                conn.close()
