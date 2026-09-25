import re
import threading
import time
from difflib import SequenceMatcher
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
from utils.logging_config import get_logger
from database.music_database import MusicDatabase
from core.tidal_client import TidalClient
from core.worker_utils import idle_backoff_seconds, interruptible_sleep
from core.library2.worker_support import (
    MATCHED,
    accept_artist_match,
    honor_stored_match,
    provider_id_conflict,
)

logger = get_logger("tidal_worker")

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
        except Exception as e:
            logger.debug("tidal auth status check: %s", e)

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

                interruptible_sleep(self._stop_event, 2)

            except Exception as e:
                logger.error(f"Error in worker loop: {e}")
                interruptible_sleep(self._stop_event, 5)

        logger.info("Tidal worker thread finished")

    def _get_next_item(self) -> Optional[Dict[str, Any]]:
        """Get next item to process from the Library-v2 catalogue.

        Priority, retry window and the pinned-group override all live in
        ``core.library2.worker_queue`` — the same rules every enrichment worker uses
        (docs §32.3.1 stage 2). ``include_parent_id`` puts the parent artist's
        Tidal id on an album or track item, which ``_verify_artist_id`` compares
        the result against.
        """
        conn = None
        try:
            from core.library2.worker_queue import next_pending
            from core.worker_utils import read_enrichment_priority

            conn = self.db._get_connection()
            return next_pending(
                conn, 'tidal',
                retry_after_days=self.retry_days,
                pinned=read_enrichment_priority('tidal') or None,
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
        """Check if Tidal result name matches our query with fuzzy matching"""
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
        """Verify/correct parent artist's Tidal ID based on album/track match.

        Only corrects when the result's artist *name* matches our parent artist —
        otherwise a collaboration/compilation would stamp the wrong Tidal id onto
        our artist. See the Deezer fix for the full write-up."""
        parent_tidal_id = item.get('artist_tidal_id')
        if not parent_tidal_id or not result_artist_id:
            return True

        if str(result_artist_id) != str(parent_tidal_id):
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
                f"updating parent artist Tidal ID from {parent_tidal_id} to {result_artist_id}"
            )
            self._correct_artist_tidal_id(item, str(result_artist_id))

        return True

    def _correct_artist_tidal_id(self, item: Dict[str, Any], correct_tidal_id: str):
        """Correct the parent artist's Tidal id from a more specific album/track
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
                conn, 'tidal', correct_tidal_id, artist_id, this_name)
            if conflict:
                logger.warning(
                    "Refusing Tidal-ID correction: id %s is already held by "
                    "'%s' (≠ '%s') — avoiding a shared/duplicate id (artist #%s)",
                    correct_tidal_id, conflict, this_name, artist_id,
                )
                return

            write_provider_enrichment(
                conn, entity_type='artist', entity_id=artist_id,
                service='tidal', provider_id=correct_tidal_id)
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
        """The Tidal id already stored for this entity, if any."""
        conn = None
        try:
            from core.library2.worker_support import stored_provider_id

            conn = self.db._get_connection()
            return stored_provider_id(conn, entity_type, entity_id, 'tidal')
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
            # Mark as matched so this row is not re-selected on every loop.
            self._mark_status('artist', artist_id, 'matched')
            return

        result = self.client.search_artist(artist_name)
        if result:
            result_name = result.get('name', '')
            tidal_artist_id = result.get('id')
            conn = self.db._get_connection()
            try:
                ok, reason = accept_artist_match(
                    conn, 'tidal', tidal_artist_id, artist_id, artist_name, result_name,
                )
            finally:
                conn.close()
            if not ok:
                self._mark_status('artist', artist_id, 'not_found')
                self.stats['not_found'] += 1
                logger.debug(f"Artist '{artist_name}' not matched: {reason}")
            elif not tidal_artist_id:
                self._mark_status('artist', artist_id, 'error')
                self.stats['errors'] += 1
                logger.warning(f"Tidal search result for '{artist_name}' has no ID")
            else:
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
            logger.debug(f"No match for artist '{artist_name}'")

    def _refresh_album_via_stored_id(self, album_id, stored_id, full_album_dict):
        """Issue #501 callback. Stored ID exists → fetched full Tidal
        album → call ``_update_album`` with the dict in both arg slots
        (search-result and full-data shapes overlap on the fields we
        need)."""
        self._update_album(album_id, full_album_dict, full_album_dict)

    def _refresh_track_via_stored_id(self, track_id, stored_id, full_track_dict):
        """Issue #501 callback for tracks — same pattern as albums."""
        self._update_track(track_id, full_track_dict, full_track_dict)

    def _process_album(self, album_id: int, album_name: str, artist_name: str, item: Dict[str, Any]):
        """Process an album: search Tidal, verify, fetch full details, store metadata"""
        # Issue #501: honor manual matches. Pre-fix this just marked
        # status='matched' without refreshing metadata. Now goes
        # through the full refresh path via the stored ID.
        _stored = honor_stored_match(
            self.db, entity_type='album', entity_id=album_id,
            service='tidal',
            fetch=self.client.get_album,
            on_match=self._refresh_album_via_stored_id,
            log_prefix='Tidal',
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
        # Issue #501: honor manual matches.
        _stored = honor_stored_match(
            self.db, entity_type='track', entity_id=track_id,
            service='tidal',
            fetch=self.client.get_track,
            on_match=self._refresh_track_via_stored_id,
            log_prefix='Tidal',
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
                result_artist = result.get('artist', {})
                result_artist_id = result_artist.get('id') if result_artist else None
                result_artist_name = result_artist.get('name') if result_artist else None
                self._verify_artist_id(item, result_artist_id, result_artist_name)

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

    @staticmethod
    def _text(value) -> Optional[str]:
        """Label and copyright arrive as a string or as a JSON:API named object."""
        if isinstance(value, dict):
            value = value.get('name') or value.get('text')
        return str(value) if value not in (None, '') else None

    @staticmethod
    def _artist_image(data: Dict[str, Any],
                      full_data: Optional[Dict[str, Any]]) -> Optional[str]:
        """Tidal exposes artwork four different ways depending on the endpoint:
        a sized ``picture`` array, a bare ``picture`` string, JSON:API
        ``imageLinks``, or ``image``. Largest first where there is a choice."""
        if full_data:
            pictures = full_data.get('picture', [])
            if isinstance(pictures, list) and pictures:
                for size in ('1080x1080', '750x750', '480x480', '320x320'):
                    for pic in pictures:
                        if isinstance(pic, dict) and size in pic.get('url', ''):
                            return pic['url']
                first = pictures[0]
                return first.get('url') if isinstance(first, dict) else str(first)
            if isinstance(pictures, str) and pictures:
                return pictures
            for link in full_data.get('imageLinks', []) or []:
                if isinstance(link, dict) and link.get('href'):
                    return link['href']
        candidate = data.get('picture') or data.get('image') or ''
        if isinstance(candidate, list) and candidate:
            first = candidate[0]
            return first.get('url') if isinstance(first, dict) else str(first)
        return candidate or None

    @staticmethod
    def _album_image(data: Dict[str, Any]) -> Optional[str]:
        cover = data.get('cover') or data.get('image') or ''
        if isinstance(cover, list) and cover:
            first = cover[0]
            return first.get('url') if isinstance(first, dict) else str(first)
        if isinstance(cover, str) and cover:
            return cover
        for link in data.get('imageLinks', []) or []:
            if isinstance(link, dict) and link.get('href'):
                return link['href']
        return None

    def _update_artist(self, artist_id: int, data: Dict[str, Any],
                       full_data: Optional[Dict[str, Any]] = None):
        """Store Tidal metadata for an artist"""
        self._write('artist', artist_id, data.get('id'),
                    backfill={'image_url': self._artist_image(data, full_data)})

    def _update_album(self, album_id: int, search_data: Dict[str, Any],
                      full_data: Optional[Dict[str, Any]]):
        """Store Tidal metadata for an album"""
        data = full_data or search_data
        backfill = {'image_url': self._album_image(data),
                    'label': self._text(data.get('label')),
                    'upc': str(data.get('upc') or data.get('barcodeId') or '') or None}
        explicit = data.get('explicit')
        if explicit is not None:
            backfill['explicit'] = 1 if explicit else 0
        num_tracks = data.get('numberOfTracks', data.get('numberOfItems'))
        if isinstance(num_tracks, int) and num_tracks > 0:
            backfill['track_count'] = num_tracks
        # `duration` and `copyright` have no album-level column in lib2 (tracks
        # carry both; albums never did). The payload keeps them without inventing a
        # column.
        payload = {'duration_ms': _parse_duration_to_ms(data.get('duration')),
                   'copyright': self._text(data.get('copyright'))}
        self._write('album', album_id, search_data.get('id'),
                    backfill=backfill, payload=payload)

    def _update_track(self, track_id: int, search_data: Dict[str, Any],
                      full_data: Optional[Dict[str, Any]]):
        """Store Tidal metadata for a track"""
        data = full_data or search_data
        backfill = {'copyright': self._text(data.get('copyright')),
                    'duration': _parse_duration_to_ms(data.get('duration'))}
        explicit = data.get('explicit')
        if explicit is not None:
            backfill['explicit'] = 1 if explicit else 0
        isrc = data.get('isrc')
        if isinstance(isrc, dict):
            isrc = isrc.get('value') or isrc.get('id')
        if isinstance(isrc, str) and isrc:
            backfill['isrc'] = isrc
        self._write('track', track_id, search_data.get('id'), backfill=backfill)

    def _write(self, entity_type: str, entity_id: int, provider_id,
               backfill: Optional[Dict[str, Any]] = None,
               payload: Optional[Dict[str, Any]] = None):
        """One write path for all three entity types (docs §32.3.1 stage 2).

        Everything outside Tidal's own id is backfill — artwork, label, genres and
        the rest are shared with better sources and with the user's own choice. The
        old code committed the id first and then backfilled in a second transaction
        so a bad value could not lose the match; write_provider_enrichment simply
        skips an empty value, which needs no second commit.
        """
        conn = None
        try:
            from core.library2.provider_attempts import record_attempt
            from core.library2.provider_writes import write_provider_enrichment

            conn = self.db._get_connection()
            write_provider_enrichment(
                conn, entity_type=entity_type, entity_id=entity_id,
                service='tidal',
                payload=payload,
                provider_id=str(provider_id) if provider_id else None,
                backfill={k: v for k, v in (backfill or {}).items() if v is not None}
                or None,
            )
            record_attempt(conn, entity_type=entity_type, entity_id=entity_id,
                           service='tidal', status='matched')
            conn.commit()
        except Exception as e:
            logger.error(f"Error updating {entity_type} #{entity_id} with Tidal data: {e}")
            raise
        finally:
            if conn:
                conn.close()

    def _mark_status(self, entity_type: str, entity_id: int, status: str):
        """Record the outcome of an attempt in the provider ledger.

        Replaces the legacy `tidal_match_status`/`_last_attempted` column pair.
        Both `not_found` and `error` become due again after the retry
        window; a source-wide outage is handled by the worker's own backoff
        before an attempt is ever recorded, so it cannot become a tight loop.
        """
        conn = None
        try:
            from core.library2.provider_attempts import record_attempt

            conn = self.db._get_connection()
            record_attempt(conn, entity_type=entity_type, entity_id=entity_id,
                           service='tidal', status=status)
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
            return pending_count(conn, 'tidal', retry_after_days=self.retry_days)
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
            return progress_breakdown(conn, 'tidal')
        except Exception as e:
            logger.error(f"Error getting progress breakdown: {e}")
            return {}
        finally:
            if conn:
                conn.close()
