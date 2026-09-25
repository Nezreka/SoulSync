import json
import re
import threading
import time
from difflib import SequenceMatcher
from types import SimpleNamespace
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
from utils.logging_config import get_logger
from database.music_database import MusicDatabase
from core.itunes_client import iTunesClient
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
)

logger = get_logger("itunes_worker")


class iTunesWorker:
    """Background worker for enriching library artists, albums, and tracks with iTunes metadata.

    Uses the same smart cascading batch approach as SpotifyWorker:
      1. Search artist by name (1 API call)
      2. get_artist_albums once per matched artist -> match all DB albums locally
      3. get_album_tracks once per matched album -> match all DB tracks locally
      4. Fallback individual search for items whose parent wasn't matched

    iTunes _lookup() calls are NOT rate-limited, so batch operations are fast.
    Only _search() calls are rate-limited (~20/min, 3s between calls).
    """

    def __init__(self, database: MusicDatabase):
        self.db = database
        self.client = iTunesClient()

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
        self.error_retry_days = 7

        # Name matching threshold
        self.name_similarity_threshold = 0.80

        # Rate limiting — iTunes search is ~20 calls/min (3s enforced by client),
        # but we add extra sleep between top-level items. Lookup is NOT rate-limited.
        self.inter_item_sleep = 3.5       # Between search items (artist/individual)
        self.batch_inter_item_sleep = 0.1  # Between local matches within a batch (lookup, not rate-limited)

        logger.info("iTunes background worker initialized")

    def start(self):
        if self.running:
            logger.warning("Worker already running")
            return
        self.running = True
        self.should_stop = False
        self._stop_event.clear()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        logger.info("iTunes background worker started")

    def stop(self):
        if not self.running:
            return
        logger.info("Stopping iTunes worker...")
        self.should_stop = True
        self.running = False
        self._stop_event.set()
        if self.thread:
            self.thread.join(timeout=1)
        logger.info("iTunes worker stopped")

    def pause(self):
        if not self.running:
            logger.warning("Worker not running, cannot pause")
            return
        self.paused = True
        logger.info("iTunes worker paused")

    def resume(self):
        if not self.running:
            logger.warning("Worker not running, start it first")
            return
        self.paused = False
        logger.info("iTunes worker resumed")

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
            'progress': progress
        }

    # ── Main loop ──────────────────────────────────────────────────────

    def _run(self):
        logger.info("iTunes worker thread started")
        while not self.should_stop:
            try:
                if self.paused:
                    interruptible_sleep(self._stop_event, 1)
                    continue

                # No auth check needed — iTunes API requires no authentication

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

                # Sleep depends on item type — search items need more delay
                item_type = item.get('type', '')
                if item_type in ('album_batch', 'track_batch'):
                    interruptible_sleep(self._stop_event, self.batch_inter_item_sleep)
                else:
                    interruptible_sleep(self._stop_event, self.inter_item_sleep)

            except Exception as e:
                logger.error(f"Error in worker loop: {e}")
                interruptible_sleep(self._stop_event, 5)

        self.current_item = None
        logger.info("iTunes worker thread finished")

    # ── Priority queue ─────────────────────────────────────────────────

    def _get_next_item(self) -> Optional[Dict[str, Any]]:
        """Get next item to process from the Library-v2 catalogue.

        The batch-first order — pinned group, unattempted artists, an album batch, a
        track batch, then the individual fallbacks for children whose own parent
        never matched — lives in ``core.library2.worker_queue`` and is shared with
        the Spotify worker, which had the identical queue (docs §32.3.1 stage 2).
        """
        conn = None
        try:
            from core.library2.worker_queue import next_batch_pending
            from core.worker_utils import read_enrichment_priority

            conn = self.db._get_connection()
            return next_batch_pending(
                conn, 'itunes',
                retry_after_days=self.retry_days,
                pinned=read_enrichment_priority('itunes') or None,
            )

        except Exception as e:
            logger.error(f"Error getting next item: {e}")
            return None
        finally:
            if conn:
                conn.close()

    # ── Dispatcher ─────────────────────────────────────────────────────

    def _process_item(self, item: Dict[str, Any]):
        try:
            item_type = item['type']
            logger.debug(f"Processing {item_type}: {item.get('name', '')}")

            if item_type == 'artist':
                self._process_artist(item)
            elif item_type == 'album_batch':
                self._process_album_batch(item)
            elif item_type == 'track_batch':
                self._process_track_batch(item)
            elif item_type == 'album_individual':
                self._process_album_individual(item)
            elif item_type == 'track_individual':
                self._process_track_individual(item)

        except Exception as e:
            logger.error(f"Error processing {item.get('type')} '{item.get('name', '')}': {e}")
            self.stats['errors'] += 1
            try:
                itype = item.get('type', '')
                if itype == 'artist':
                    self._mark_status('artist', item['id'], 'error')
                elif itype == 'album_individual':
                    self._mark_status('album', item['id'], 'error')
                elif itype == 'track_individual':
                    self._mark_status('track', item['id'], 'error')
                elif itype == 'album_batch':
                    self._mark_artist_albums_error(item['artist_id'])
                elif itype == 'track_batch':
                    self._mark_album_tracks_error(item['album_id'])
            except Exception as e2:
                logger.error(f"Error updating item status: {e2}")

    # ── Artist processing ──────────────────────────────────────────────

    def _get_existing_id(self, entity_type: str, entity_id: int) -> Optional[str]:
        """The iTunes id already stored for this entity, if any.

        Legacy needed a per-entity column map (itunes_artist_id / itunes_album_id /
        itunes_track_id); lib2 keeps it under one service key on every entity.
        """
        conn = None
        try:
            from core.library2.worker_support import stored_provider_id

            conn = self.db._get_connection()
            return stored_provider_id(conn, entity_type, entity_id, 'itunes')
        except Exception:
            return None
        finally:
            if conn:
                conn.close()

    def _process_artist(self, item: Dict[str, Any]):
        artist_id = item['id']
        artist_name = item['name']

        existing_id = self._get_existing_id('artist', artist_id)
        if existing_id:
            logger.debug(f"Preserving existing iTunes ID for artist '{artist_name}': {existing_id}")
            self._mark_status('artist', artist_id, 'matched')
            return

        results = self.client.search_artists(artist_name, limit=5)
        if not results:
            self._mark_status('artist', artist_id, 'not_found')
            self.stats['not_found'] += 1
            logger.debug(f"No iTunes results for artist '{artist_name}'")
            return

        # Candidates clearing the name gate (results are source-ranked, so [0] is
        # the legacy "first passing" pick), then disambiguate same-name artists by
        # which one's catalog overlaps the albums this library owns.
        gated = [a for a in results if artist_name_matches(artist_name, a.name)]
        conn = self.db._get_connection()
        try:
            _owned = owned_album_titles(conn, artist_id)
        finally:
            conn.close()
        chosen, _overlap = pick_artist_by_catalog(
            gated,
            _owned,
            lambda a: release_titles(self.client.get_artist_albums(a.id)),
        )

        if chosen:
            conn = self.db._get_connection()
            try:
                ok, reason = accept_artist_match(
                    conn, 'itunes', chosen.id, artist_id,
                    artist_name, chosen.name,
                )
            finally:
                conn.close()
            if ok:
                if not self._is_itunes_id(chosen.id):
                    logger.warning(f"Rejecting non-iTunes ID '{chosen.id}' for artist '{artist_name}'")
                    self._mark_status('artist', artist_id, 'error')
                    self.stats['errors'] += 1
                    return
                self._update_artist(artist_id, chosen)
                self.stats['matched'] += 1
                logger.info(f"Matched artist '{artist_name}' -> iTunes ID: {chosen.id}")
                return

        self._mark_status('artist', artist_id, 'not_found')
        self.stats['not_found'] += 1
        logger.debug(f"Name mismatch for artist '{artist_name}' (best: '{results[0].name}')")

    # ── Album batch processing ─────────────────────────────────────────

    def _process_album_batch(self, item: Dict[str, Any]):
        artist_id = item['artist_id']
        itunes_artist_id = item['itunes_artist_id']
        artist_name = item['artist_name']

        # 1 lookup call (NOT rate-limited): get all albums for this artist
        try:
            itunes_albums = self.client.get_artist_albums(
                itunes_artist_id, album_type='album,single', limit=50
            )
        except Exception as e:
            logger.error(f"Failed to get iTunes albums for artist '{artist_name}': {e}")
            self._mark_artist_albums_error(artist_id)
            self.stats['errors'] += 1
            return

        if not itunes_albums:
            logger.debug(f"No iTunes albums for artist '{artist_name}'")
            self._mark_artist_albums_not_found(artist_id)
            return

        # Validate that we got iTunes albums, not some other format
        if itunes_albums and not self._is_itunes_id(itunes_albums[0].id):
            logger.warning(f"Rejecting album batch for '{artist_name}': got non-iTunes IDs")
            self._mark_artist_albums_error(artist_id)
            self.stats['errors'] += 1
            return

        db_albums = self._get_unmatched_albums_for_artist(artist_id)
        if not db_albums:
            return

        matched_count = 0
        for db_album in db_albums:
            db_id, db_title = db_album['id'], db_album['title']
            best_match = None

            for it_album in itunes_albums:
                if self._name_matches(db_title, it_album.name):
                    best_match = it_album
                    break

            if best_match:
                self._update_album(db_id, best_match)
                self.stats['matched'] += 1
                matched_count += 1
                logger.info(f"Batch matched album '{db_title}' -> iTunes ID: {best_match.id}")
            else:
                self._mark_status('album', db_id, 'not_found')
                self.stats['not_found'] += 1

            interruptible_sleep(self._stop_event, self.batch_inter_item_sleep)

        logger.info(f"Album batch for '{artist_name}': {matched_count}/{len(db_albums)} matched")

    # ── Track batch processing ─────────────────────────────────────────

    def _process_track_batch(self, item: Dict[str, Any]):
        album_id = item['album_id']
        itunes_album_id = item['itunes_album_id']
        album_name = item['album_name']

        # 1 lookup call (NOT rate-limited): get all tracks for this album
        try:
            result = self.client.get_album_tracks(itunes_album_id)
        except Exception as e:
            logger.error(f"Failed to get iTunes tracks for album '{album_name}': {e}")
            self._mark_album_tracks_error(album_id)
            self.stats['errors'] += 1
            return

        if not result or not result.get('items'):
            logger.debug(f"No iTunes tracks for album '{album_name}'")
            self._mark_album_tracks_not_found(album_id)
            return

        itunes_tracks = result['items']

        # Validate that we got iTunes tracks
        if itunes_tracks and not self._is_itunes_id(str(itunes_tracks[0].get('id', ''))):
            logger.warning(f"Rejecting track batch for '{album_name}': got non-iTunes IDs")
            self._mark_album_tracks_error(album_id)
            self.stats['errors'] += 1
            return

        db_tracks = self._get_unmatched_tracks_for_album(album_id)
        if not db_tracks:
            return

        matched_count = 0
        for db_track in db_tracks:
            db_id = db_track['id']
            db_title = db_track['title']
            db_track_number = db_track.get('track_number')
            best_match = None

            # Strategy A: track_number match + name verification
            if db_track_number:
                for it_track in itunes_tracks:
                    it_num = it_track.get('track_number')
                    if it_num and it_num == db_track_number:
                        it_name = it_track.get('name', '')
                        if self._name_matches(db_title, it_name):
                            best_match = it_track
                            break

            # Strategy B: pure name match fallback
            if not best_match:
                for it_track in itunes_tracks:
                    it_name = it_track.get('name', '')
                    if self._name_matches(db_title, it_name):
                        best_match = it_track
                        break

            if best_match:
                self._update_track(db_id, best_match)
                self.stats['matched'] += 1
                matched_count += 1
                logger.info(f"Batch matched track '{db_title}' -> iTunes ID: {best_match.get('id')}")
            else:
                self._mark_status('track', db_id, 'not_found')
                self.stats['not_found'] += 1

            interruptible_sleep(self._stop_event, self.batch_inter_item_sleep)

        logger.info(f"Track batch for '{album_name}': {matched_count}/{len(db_tracks)} matched")

    # ── Individual fallback processing ─────────────────────────────────

    def _refresh_album_via_stored_id(self, album_id, stored_id, api_album_dict):
        """Issue #501 callback. Convert ``client.get_album()`` dict into
        the Album-shaped object ``_update_album`` expects, then call it.
        Preserves the manual match — never overwrites the stored ID
        with a different name-search result."""
        images = api_album_dict.get('images') or []
        image_url = ''
        if images and isinstance(images[0], dict):
            image_url = images[0].get('url', '') or ''
        adapter = SimpleNamespace(
            id=api_album_dict.get('id') or stored_id,
            name=api_album_dict.get('name', ''),
            image_url=image_url,
            album_type=api_album_dict.get('album_type', 'album'),
            release_date=api_album_dict.get('release_date', ''),
            total_tracks=api_album_dict.get('total_tracks', 0),
        )
        self._update_album(album_id, adapter)

    def _refresh_track_via_stored_id(self, track_id, stored_id, api_track_dict):
        """Track-level callback — track update only writes ID + status,
        no metadata backfill, so the dict shape is irrelevant beyond
        carrying the stored ID through."""
        adapter = SimpleNamespace(id=api_track_dict.get('id') or stored_id)
        self._update_track_from_search(track_id, adapter)

    def _process_album_individual(self, item: Dict[str, Any]):
        album_id = item['id']
        album_name = item['name']
        artist_name = item.get('artist', '')

        # Issue #501: honor manual matches (see SpotifyWorker for full
        # explanation — same pattern across every per-source worker).
        _stored = honor_stored_match(
            self.db, entity_type='album', entity_id=album_id, service='itunes',
            fetch=self.client.get_album,
            on_match=self._refresh_album_via_stored_id,
            log_prefix='iTunes',
        )
        if _stored:
            # L2-005: a stored id the provider could not confirm right now is
            # NOT released to the fuzzy name search below — a transient failure
            # is not evidence that the id is wrong, and searching overwrote
            # deliberately chosen matches with whatever came back.
            if _stored == MATCHED:
                self.stats['matched'] += 1
            return

        query = f"{artist_name} {album_name}" if artist_name else album_name
        results = self.client.search_albums(query, limit=5)

        if not results:
            self._mark_status('album', album_id, 'not_found')
            self.stats['not_found'] += 1
            logger.debug(f"No iTunes results for album '{album_name}'")
            return

        for album_obj in results:
            if self._name_matches(album_name, album_obj.name):
                if not self._is_itunes_id(album_obj.id):
                    logger.warning(f"Rejecting non-iTunes ID '{album_obj.id}' for album '{album_name}'")
                    self._mark_status('album', album_id, 'error')
                    self.stats['errors'] += 1
                    return
                self._update_album(album_id, album_obj)
                self.stats['matched'] += 1
                logger.info(f"Matched album '{album_name}' -> iTunes ID: {album_obj.id}")
                return

        self._mark_status('album', album_id, 'not_found')
        self.stats['not_found'] += 1
        logger.debug(f"Name mismatch for album '{album_name}'")

    def _process_track_individual(self, item: Dict[str, Any]):
        track_id = item['id']
        track_name = item['name']
        artist_name = item.get('artist', '')

        # Issue #501: honor manual matches.
        _stored = honor_stored_match(
            self.db, entity_type='track', entity_id=track_id, service='itunes',
            fetch=self.client.get_track_details,
            on_match=self._refresh_track_via_stored_id,
            log_prefix='iTunes',
        )
        if _stored:
            # L2-005: a stored id the provider could not confirm right now is
            # NOT released to the fuzzy name search below — a transient failure
            # is not evidence that the id is wrong, and searching overwrote
            # deliberately chosen matches with whatever came back.
            if _stored == MATCHED:
                self.stats['matched'] += 1
            return

        query = f"{artist_name} {track_name}" if artist_name else track_name
        results = self.client.search_tracks(query, limit=5)

        if not results:
            self._mark_status('track', track_id, 'not_found')
            self.stats['not_found'] += 1
            logger.debug(f"No iTunes results for track '{track_name}'")
            return

        for track_obj in results:
            if self._name_matches(track_name, track_obj.name):
                if not self._is_itunes_id(track_obj.id):
                    logger.warning(f"Rejecting non-iTunes ID '{track_obj.id}' for track '{track_name}'")
                    self._mark_status('track', track_id, 'error')
                    self.stats['errors'] += 1
                    return
                self._update_track_from_search(track_id, track_obj)
                self.stats['matched'] += 1
                logger.info(f"Matched track '{track_name}' -> iTunes ID: {track_obj.id}")
                return

        self._mark_status('track', track_id, 'not_found')
        self.stats['not_found'] += 1
        logger.debug(f"Name mismatch for track '{track_name}'")

    # ── DB update methods ──────────────────────────────────────────────

    def _update_artist(self, artist_id: int, artist_obj):
        """Store iTunes metadata for an artist (from Artist dataclass)"""
        backfill = {}
        if artist_obj.image_url:
            backfill['image_url'] = artist_obj.image_url
        if artist_obj.genres:
            from core.genre_filter import filter_genres
            from core.settings import config_manager as _cfg
            _filtered = filter_genres(list(artist_obj.genres), _cfg)
            if _filtered:
                backfill['genres'] = json.dumps(_filtered)
        self._write('artist', artist_id, artist_obj.id, backfill=backfill)

    def _update_album(self, album_id: int, album_obj):
        """Store iTunes metadata for an album (from Album dataclass)"""
        backfill = {}
        if album_obj.image_url:
            backfill['image_url'] = album_obj.image_url
        if album_obj.release_date:
            year = album_obj.release_date[:4] if len(album_obj.release_date) >= 4 else None
            if year and year.isdigit():
                backfill['year'] = year
            # #824: also store the FULL release date when iTunes has one (YYYY-MM or
            # YYYY-MM-DD). Backfill only — never clobber a manually-set date.
            if len(album_obj.release_date) > 4:
                backfill['release_date'] = album_obj.release_date
        # `record_type` has no lib2 counterpart to backfill: lib2_albums.album_type
        # always carries a classification (the importer and MB reconcile own it), so
        # there is no empty state to fill. iTunes' word goes in the payload.
        self._write('album', album_id, album_obj.id, backfill=backfill,
                    payload={'album_type': album_obj.album_type},
                    total_tracks=getattr(album_obj, 'total_tracks', 0))

    def _update_track(self, track_id: int, track_data: Dict[str, Any]):
        """Store iTunes metadata for a track (from get_album_tracks dict)"""
        backfill = {}
        if 'explicit' in track_data:
            backfill['explicit'] = 1 if track_data['explicit'] else 0
        self._write('track', track_id, track_data.get('id', ''), backfill=backfill)

    def _update_track_from_search(self, track_id: int, track_obj):
        """Store iTunes metadata for a track (from Track dataclass, individual search)"""
        self._write('track', track_id, track_obj.id)

    def _write(self, entity_type: str, entity_id: int, provider_id,
               backfill: Optional[Dict[str, Any]] = None,
               payload: Optional[Dict[str, Any]] = None,
               total_tracks: Any = None):
        """One write path for all three entity types (docs §32.3.1 stage 2).

        Everything outside iTunes' own id is backfill: artwork, genres, year and the
        explicit flag are shared with better sources and with the user's own choice.
        """
        conn = None
        try:
            from core.library2.provider_attempts import record_attempt
            from core.library2.provider_writes import write_provider_enrichment
            from core.library2.worker_support import set_expected_track_count

            conn = self.db._get_connection()
            write_provider_enrichment(
                conn, entity_type=entity_type, entity_id=entity_id,
                service='itunes',
                payload=payload,
                provider_id=str(provider_id) if provider_id else None,
                backfill=backfill or None,
            )
            if entity_type == 'album':
                # The authoritative expected total for the Album Completeness job.
                set_expected_track_count(conn, entity_id, total_tracks)
            record_attempt(conn, entity_type=entity_type, entity_id=entity_id,
                           service='itunes', status='matched')
            conn.commit()
        except Exception as e:
            logger.error(f"Error updating {entity_type} #{entity_id} with iTunes data: {e}")
            raise
        finally:
            if conn:
                conn.close()

    # ── Batch helpers ──────────────────────────────────────────────────

    def _get_unmatched_albums_for_artist(self, artist_id: int) -> List[Dict[str, Any]]:
        conn = None
        try:
            from core.library2.worker_queue import pending_children

            conn = self.db._get_connection()
            return pending_children(conn, 'itunes', 'artist', artist_id,
                                    child='album')
        except Exception as e:
            logger.error(f"Error getting unmatched albums for artist #{artist_id}: {e}")
            return []
        finally:
            if conn:
                conn.close()

    def _get_unmatched_tracks_for_album(self, album_id: int) -> List[Dict[str, Any]]:
        conn = None
        try:
            from core.library2.worker_queue import pending_children

            conn = self.db._get_connection()
            return pending_children(conn, 'itunes', 'album', album_id, child='track')
        except Exception as e:
            logger.error(f"Error getting unmatched tracks for album #{album_id}: {e}")
            return []
        finally:
            if conn:
                conn.close()

    def _record_batch(self, parent_type: str, parent_id: int, status: str,
                      child: str):
        """One outcome for every still-unattempted child of a failed bulk call.

        Children the provider already settled are left alone — the batch was never
        about them.
        """
        conn = None
        try:
            from core.library2.worker_queue import record_children

            conn = self.db._get_connection()
            record_children(conn, 'itunes', parent_type, parent_id, status,
                            child=child)
            conn.commit()
        except Exception as e:
            logger.error(f"Error bulk-marking {child}s for {parent_type} "
                         f"#{parent_id}: {e}")
        finally:
            if conn:
                conn.close()

    def _mark_artist_albums_error(self, artist_id: int):
        self._record_batch('artist', artist_id, 'error', 'album')

    def _mark_artist_albums_not_found(self, artist_id: int):
        self._record_batch('artist', artist_id, 'not_found', 'album')

    def _mark_album_tracks_error(self, album_id: int):
        self._record_batch('album', album_id, 'error', 'track')

    def _mark_album_tracks_not_found(self, album_id: int):
        self._record_batch('album', album_id, 'not_found', 'track')

    # ── Status / counting ──────────────────────────────────────────────

    def _mark_status(self, entity_type: str, entity_id: int, status: str):
        """Record the outcome of an attempt in the provider ledger.

        Replaces the legacy `itunes_match_status`/`_last_attempted` column pair.
        Both `not_found` and `error` become due again after the retry
        window; a source-wide outage is handled by the worker's own backoff
        before an attempt is ever recorded, so it cannot become a tight loop.
        """
        conn = None
        try:
            from core.library2.provider_attempts import record_attempt

            conn = self.db._get_connection()
            record_attempt(conn, entity_type=entity_type, entity_id=entity_id,
                           service='itunes', status=status)
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
            return pending_count(conn, 'itunes', retry_after_days=self.retry_days)
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
            return progress_breakdown(conn, 'itunes')
        except Exception as e:
            logger.error(f"Error getting progress breakdown: {e}")
            return {}
        finally:
            if conn:
                conn.close()

    # ── ID validation ────────────────────────────────────────────────

    def _is_itunes_id(self, id_str: str) -> bool:
        """iTunes IDs are purely numeric. Spotify IDs are alphanumeric (contain letters).
        Reject alphanumeric IDs to prevent Spotify contamination of itunes_* columns."""
        if not id_str:
            return False
        return str(id_str).isdigit()

    # ── Name matching ──────────────────────────────────────────────────

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
