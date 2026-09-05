import json
import re
import threading
import time
from difflib import SequenceMatcher
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
from utils.logging_config import get_logger
from database.music_database import MusicDatabase
from core.deezer_client import DeezerClient
from core.worker_utils import (
    artist_name_matches,
    idle_backoff_seconds,
    interruptible_sleep,
    pick_artist_by_catalog,
    release_titles,
)
from core.library2.worker_support import (
    MATCHED,
    accept_artist_match,
    honor_stored_match,
    owned_album_titles,
    provider_id_conflict,
)

logger = get_logger("deezer_worker")

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



class DeezerWorker:
    """Background worker for enriching library artists, albums, and tracks with Deezer metadata"""

    def __init__(self, database: MusicDatabase):
        self.db = database
        self.client = DeezerClient()

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

        logger.info("Deezer background worker initialized")

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
        logger.info("Deezer background worker started")

    def stop(self):
        """Stop the background worker"""
        if not self.running:
            return

        logger.info("Stopping Deezer worker...")
        self.should_stop = True
        self.running = False
        self._stop_event.set()

        if self.thread:
            self.thread.join(timeout=1)

        logger.info("Deezer worker stopped")

    def pause(self):
        """Pause the worker"""
        if not self.running:
            logger.warning("Worker not running, cannot pause")
            return

        self.paused = True
        logger.info("Deezer worker paused")

    def resume(self):
        """Resume the worker"""
        if not self.running:
            logger.warning("Worker not running, start it first")
            return

        self.paused = False
        logger.info("Deezer worker resumed")

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
            'current_item': self.current_item,
            'stats': self.stats.copy(),
            'progress': progress
        }

    def _run(self):
        """Main worker loop"""
        logger.info("Deezer worker thread started")

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

                interruptible_sleep(self._stop_event, 2)

            except Exception as e:
                logger.error(f"Error in worker loop: {e}")
                interruptible_sleep(self._stop_event, 5)

        logger.info("Deezer worker thread finished")

    def _get_next_item(self) -> Optional[Dict[str, Any]]:
        """Get next item to process from the Library-v2 catalogue.

        Priority, retry window and the pinned-group override all live in
        ``core.library2.worker_queue`` — the same rules every enrichment worker uses
        (docs §32.3.1 stage 2). ``include_parent_id`` puts the parent artist's
        Deezer id on an album or track item, which ``_verify_artist_id`` compares
        the result against.
        """
        conn = None
        try:
            from core.library2.worker_queue import next_pending
            from core.worker_utils import read_enrichment_priority

            conn = self.db._get_connection()
            return next_pending(
                conn, 'deezer',
                retry_after_days=self.retry_days,
                pinned=read_enrichment_priority('deezer') or None,
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
        """Check if Deezer result name matches our query with fuzzy matching"""
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
        """Verify that the result's artist ID matches the parent artist's stored Deezer ID.

        If mismatched, the album/track search is more specific (uses artist+title),
        so we trust it and correct the parent artist's deezer_id — BUT only when
        the result's artist *name* actually matches our parent artist. Without
        that guard, a collaboration or compilation track (e.g. a track our
        library credits to Jorja Smith that lives on Kendrick Lamar's curated
        "Black Panther" album) would search up to an album whose Deezer primary
        artist is someone else (Kendrick), and we'd stamp that wrong Deezer ID
        onto our artist — corrupting it (and causing duplicate ids shared across
        unrelated artists)."""
        parent_deezer_id = item.get('artist_deezer_id')
        if not parent_deezer_id:
            return True

        if not result_artist_id:
            return True

        if str(result_artist_id) != str(parent_deezer_id):
            # Guard: only correct when the album/track's primary artist is the
            # SAME artist by name — a POSITIVE match. A missing result name
            # (#988: compilation/collab Deezer payloads often omit it) or a
            # mismatch means we can't confirm it's the same artist, so we must
            # NOT rewrite the parent's id — that's how a wrong Deezer id
            # (The Beatles' id 1) got smeared onto The Outfield.
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
                f"updating parent artist Deezer ID from {parent_deezer_id} to {result_artist_id}"
            )
            self._correct_artist_deezer_id(item, str(result_artist_id))

        return True

    def _correct_artist_deezer_id(self, item: Dict[str, Any], correct_deezer_id: str):
        """Correct the parent artist's Deezer id from a more specific album/track
        match. The name guard in ``_verify_artist_id`` has already run."""
        conn = None
        try:
            from core.library2.provider_writes import write_provider_enrichment

            conn = self.db._get_connection()
            artist_id = _parent_artist_id(conn, item['type'], item['id'])
            if artist_id is None:
                return

            # #988: never overwrite with an id already owned by a DIFFERENTLY-named
            # artist — that is the exact smear (Beatles' id 1 onto The Outfield).
            # Same-named holders legitimately share an id.
            this_name = conn.execute(
                "SELECT name FROM lib2_artists WHERE id = ?", (artist_id,)
            ).fetchone()
            this_name = (this_name[0] if this_name else '') or (item.get('artist') or '')
            conflict = provider_id_conflict(
                conn, 'deezer', correct_deezer_id, artist_id, this_name)
            if conflict:
                logger.warning(
                    f"Refusing Deezer-ID correction: id {correct_deezer_id} is "
                    f"already held by '{conflict}' (≠ '{this_name}') — avoiding a "
                    f"shared/duplicate id (artist #{artist_id})")
                return

            write_provider_enrichment(
                conn, entity_type='artist', entity_id=artist_id,
                service='deezer', provider_id=correct_deezer_id)
            conn.commit()

            logger.info(f"Corrected artist #{artist_id} Deezer ID to {correct_deezer_id}")

        except Exception as e:
            logger.error(f"Error correcting artist Deezer ID: {e}")
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
            logger.error(f"Error processing {item['type']} #{item['id']}: {e}")
            self.stats['errors'] += 1
            try:
                self._mark_status(item['type'], item['id'], 'error')
            except Exception as e2:
                logger.error(f"Error updating item status: {e2}")

    def _get_existing_id(self, entity_type: str, entity_id: int) -> Optional[str]:
        """The Deezer id already stored for this entity, if any."""
        conn = None
        try:
            from core.library2.worker_support import stored_provider_id

            conn = self.db._get_connection()
            return stored_provider_id(conn, entity_type, entity_id, 'deezer')
        except Exception:
            return None
        finally:
            if conn:
                conn.close()

    def _process_artist(self, artist_id: int, artist_name: str):
        """Process an artist: search Deezer, verify, store metadata"""
        existing_id = self._get_existing_id('artist', artist_id)
        if existing_id:
            logger.debug(f"Preserving existing Deezer ID for artist '{artist_name}': {existing_id}")
            self._mark_status('artist', artist_id, 'matched')
            return

        # Multi-candidate search (was single search_artist) so same-name artists
        # can be disambiguated: gate by name, then pick the one whose catalog
        # overlaps the albums this library owns.
        results = self.client.search_artists(artist_name, limit=5)
        gated = [a for a in (results or []) if artist_name_matches(artist_name, getattr(a, 'name', ''))]
        conn = self.db._get_connection()
        try:
            _owned = owned_album_titles(conn, artist_id)
        finally:
            conn.close()
        chosen, _overlap = pick_artist_by_catalog(
            gated,
            _owned,
            lambda a: release_titles(self.client.get_artist_albums_list(a.id)),
        )

        # search_artists returns lean Artist objects; fetch the full dict (same
        # shape the old search_artist returned) for storage.
        result = self.client.get_artist_info(chosen.id) if chosen else None
        if result:
            result_name = result.get('name', '')
            conn = self.db._get_connection()
            try:
                ok, reason = accept_artist_match(
                    conn, 'deezer', result.get('id'), artist_id,
                    artist_name, result_name,
                )
            finally:
                conn.close()
            if ok:
                self._update_artist(artist_id, result)
                self.stats['matched'] += 1
                logger.info(f"Matched artist '{artist_name}' -> Deezer ID: {result.get('id')}")
            else:
                self._mark_status('artist', artist_id, 'not_found')
                self.stats['not_found'] += 1
                logger.debug(f"Artist '{artist_name}' not matched: {reason}")
        else:
            self._mark_status('artist', artist_id, 'not_found')
            self.stats['not_found'] += 1
            logger.debug(f"No match for artist '{artist_name}'")

    def _refresh_album_via_stored_id(self, album_id, stored_id, full_album_dict):
        """Issue #501 callback. Stored ID exists → fetched full Deezer
        album payload. Use it as both args to ``_update_album`` (search-
        result and full-data shapes overlap on the fields we need —
        artist verification skipped since manual match presumably
        already vetted)."""
        self._update_album(album_id, full_album_dict, full_album_dict)

    def _refresh_track_via_stored_id(self, track_id, stored_id, full_track_dict):
        """Issue #501 callback for tracks — same pattern as albums."""
        self._update_track(track_id, full_track_dict, full_track_dict)

    def _process_album(self, album_id: int, album_name: str, artist_name: str, item: Dict[str, Any]):
        """Process an album: search Deezer, verify, fetch full details, store metadata"""
        # Issue #501: honor manual matches. Pre-fix this method just
        # SKIPPED when a stored ID was present (preserved the ID but
        # never refreshed metadata). Now it goes through the full
        # refresh path via the stored ID, picking up label / genres /
        # explicit updates without ever overwriting the manual match.
        _stored = honor_stored_match(
            self.db, entity_type='album', entity_id=album_id,
            service='deezer',
            fetch=self.client.get_album_raw,
            on_match=self._refresh_album_via_stored_id,
            log_prefix='Deezer',
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

                # Fetch full album details for label, genres, explicit
                deezer_album_id = result.get('id')
                full_album = None
                if deezer_album_id:
                    try:
                        full_album = self.client.get_album_raw(deezer_album_id)
                    except Exception as e:
                        logger.warning(f"Failed to fetch full album details for '{album_name}' (Deezer ID: {deezer_album_id}): {e}")

                if full_album is None:
                    # Full details fetch failed — mark as error so it retries later
                    # rather than storing a match without label/genres/explicit
                    self._mark_status('album', album_id, 'error')
                    self.stats['errors'] += 1
                    logger.warning(f"Album '{album_name}' matched but full details unavailable, will retry")
                    return

                self._update_album(album_id, result, full_album)
                self.stats['matched'] += 1
                logger.info(f"Matched album '{album_name}' -> Deezer ID: {deezer_album_id}")
            else:
                self._mark_status('album', album_id, 'not_found')
                self.stats['not_found'] += 1
                logger.debug(f"Name mismatch for album '{album_name}' (got '{result_name}')")
        else:
            self._mark_status('album', album_id, 'not_found')
            self.stats['not_found'] += 1
            logger.debug(f"No match for album '{album_name}'")

    def _process_track(self, track_id: int, track_name: str, artist_name: str, item: Dict[str, Any]):
        """Process a track: search Deezer, verify, fetch full details for BPM, store metadata"""
        # Issue #501: honor manual matches (see _process_album).
        _stored = honor_stored_match(
            self.db, entity_type='track', entity_id=track_id,
            service='deezer',
            fetch=self.client.get_track_raw,
            on_match=self._refresh_track_via_stored_id,
            log_prefix='Deezer',
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

                # Fetch full track details for BPM
                deezer_track_id = result.get('id')
                full_track = None
                if deezer_track_id:
                    try:
                        full_track = self.client.get_track_raw(deezer_track_id)
                    except Exception as e:
                        logger.warning(f"Failed to fetch full track details for '{track_name}' (Deezer ID: {deezer_track_id}): {e}")

                if full_track is None:
                    # Full details fetch failed — mark as error so it retries later
                    # rather than storing a match without BPM/explicit
                    self._mark_status('track', track_id, 'error')
                    self.stats['errors'] += 1
                    logger.warning(f"Track '{track_name}' matched but full details unavailable, will retry")
                    return

                self._update_track(track_id, result, full_track)
                self.stats['matched'] += 1
                logger.info(f"Matched track '{track_name}' -> Deezer ID: {deezer_track_id}")
            else:
                self._mark_status('track', track_id, 'not_found')
                self.stats['not_found'] += 1
                logger.debug(f"Name mismatch for track '{track_name}' (got '{result_name}')")
        else:
            self._mark_status('track', track_id, 'not_found')
            self.stats['not_found'] += 1
            logger.debug(f"No match for track '{track_name}'")

    def _update_artist(self, artist_id: int, data: Dict[str, Any]):
        """Store Deezer metadata for an artist"""
        self._write('artist', artist_id, data.get('id'),
                    backfill={'image_url': data.get('picture_xl')})

    def _update_album(self, album_id: int, search_data: Dict[str, Any],
                      full_data: Optional[Dict[str, Any]]):
        """Store Deezer metadata for an album"""
        data = full_data or search_data
        backfill = {
            'image_url': search_data.get('cover_xl')
            or (data.get('cover_xl') if full_data else None),
            'label': data.get('label') if full_data else None,
            'explicit': 1 if data.get('explicit_lyrics') else 0,
        }
        if full_data:
            genre_names = [g.get('name') for g
                           in (full_data.get('genres', {}) or {}).get('data', [])
                           if g.get('name')]
            if genre_names:
                from core.genre_filter import filter_genres
                from core.settings import config_manager as _cfg
                _filtered = filter_genres(genre_names, _cfg)
                if _filtered:
                    backfill['genres'] = json.dumps(_filtered)
        # `record_type` has no lib2 counterpart to backfill: lib2_albums.album_type
        # always carries a classification the importer and MB reconcile own, so there
        # is no empty state to fill. Deezer's word goes to the payload.
        self._write('album', album_id, search_data.get('id'), backfill=backfill,
                    payload={'record_type': data.get('record_type')},
                    total_tracks=(full_data.get('nb_tracks') if full_data else None)
                    or search_data.get('nb_tracks'))

    def _update_track(self, track_id: int, search_data: Dict[str, Any],
                      full_data: Optional[Dict[str, Any]]):
        """Store Deezer metadata for a track"""
        data = full_data or search_data
        backfill = {'explicit': 1 if data.get('explicit_lyrics') else 0}
        bpm = data.get('bpm') if full_data else None
        if bpm and bpm > 0:
            backfill['bpm'] = float(bpm)
        self._write('track', track_id, search_data.get('id'), backfill=backfill)

    def _write(self, entity_type: str, entity_id: int, provider_id,
               backfill: Optional[Dict[str, Any]] = None,
               payload: Optional[Dict[str, Any]] = None,
               total_tracks: Any = None):
        """One write path for all three entity types (docs §32.3.1 stage 2).

        Everything outside Deezer's own id is backfill — artwork, label, genres and
        the explicit flag are shared with better sources and with the user's choice.
        """
        conn = None
        try:
            from core.library2.provider_attempts import record_attempt
            from core.library2.provider_writes import write_provider_enrichment
            from core.library2.worker_support import set_expected_track_count

            conn = self.db._get_connection()
            write_provider_enrichment(
                conn, entity_type=entity_type, entity_id=entity_id,
                service='deezer',
                payload=payload,
                provider_id=str(provider_id) if provider_id else None,
                backfill={k: v for k, v in (backfill or {}).items() if v is not None}
                or None,
            )
            if entity_type == 'album':
                # The authoritative expected total for the Album Completeness job.
                set_expected_track_count(conn, entity_id, total_tracks)
            record_attempt(conn, entity_type=entity_type, entity_id=entity_id,
                           service='deezer', status='matched')
            conn.commit()
        except Exception as e:
            logger.error(f"Error updating {entity_type} #{entity_id} with Deezer data: {e}")
            raise
        finally:
            if conn:
                conn.close()

    def _mark_status(self, entity_type: str, entity_id: int, status: str):
        """Record the outcome of an attempt in the provider ledger.

        Replaces the legacy `deezer_match_status`/`_last_attempted` column pair.
        Both `not_found` and `error` become due again after the retry
        window; a source-wide outage is handled by the worker's own backoff
        before an attempt is ever recorded, so it cannot become a tight loop.
        """
        conn = None
        try:
            from core.library2.provider_attempts import record_attempt

            conn = self.db._get_connection()
            record_attempt(conn, entity_type=entity_type, entity_id=entity_id,
                           service='deezer', status=status)
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
            return pending_count(conn, 'deezer', retry_after_days=self.retry_days)
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
            return progress_breakdown(conn, 'deezer')
        except Exception as e:
            logger.error(f"Error getting progress breakdown: {e}")
            return {}
        finally:
            if conn:
                conn.close()
