import json
import re
import threading
import time
from difflib import SequenceMatcher
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
from utils.logging_config import get_logger
from database.music_database import MusicDatabase
from core.audiodb_client import AudioDBClient
from core.library2.worker_support import accept_artist_match, provider_id_conflict
from core.worker_utils import interruptible_sleep

logger = get_logger("audiodb_worker")


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


class AudioDBWorker:
    """Background worker for enriching library artists, albums, and tracks with AudioDB metadata"""

    def __init__(self, database: MusicDatabase):
        self.db = database
        self.client = AudioDBClient()

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

        logger.info("AudioDB background worker initialized")

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
        logger.info("AudioDB background worker started")

    def stop(self):
        """Stop the background worker"""
        if not self.running:
            return

        logger.info("Stopping AudioDB worker...")
        self.should_stop = True
        self.running = False
        self._stop_event.set()

        if self.thread:
            self.thread.join(timeout=1)

        logger.info("AudioDB worker stopped")

    def pause(self):
        """Pause the worker"""
        if not self.running:
            logger.warning("Worker not running, cannot pause")
            return

        self.paused = True
        logger.info("AudioDB worker paused")

    def resume(self):
        """Resume the worker"""
        if not self.running:
            logger.warning("Worker not running, start it first")
            return

        self.paused = False
        logger.info("AudioDB worker resumed")

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
        logger.info("AudioDB worker thread started")

        while not self.should_stop:
            try:
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

        logger.info("AudioDB worker thread finished")

    # AudioDB retries 'error' alongside 'not_found'. Issue #553 marks transient
    # AudioDB outages (timeouts, 500s) instead of leaving the row NULL, and without
    # this those rows would stay errored forever.
    _RETRY_STATUSES = ('error', 'not_found')

    def _get_next_item(self) -> Optional[Dict[str, Any]]:
        """Get next item to process from the Library-v2 catalogue.

        Priority, retry window and the pinned-group override all live in
        ``core.library2.worker_queue`` — the same rules every enrichment worker
        uses (docs §32.3.1 stage 2). ``include_parent_id`` puts the parent artist's
        AudioDB id on an album or track item, which ``_verify_artist_id`` compares
        the result against.
        """
        conn = None
        try:
            from core.library2.worker_queue import next_pending
            from core.worker_utils import read_enrichment_priority

            conn = self.db._get_connection()
            return next_pending(
                conn, 'audiodb',
                retry_after_days=self.retry_days,
                pinned=read_enrichment_priority('audiodb') or None,
                retry_statuses=self._RETRY_STATUSES,
                include_parent_id=True,
            )

        except Exception as e:
            logger.error(f"Error getting next item: {e}")
            return None
        finally:
            if conn:
                conn.close()

    def _normalize_name(self, name: str) -> str:
        """Normalize artist name for comparison"""
        name = name.lower().strip()
        name = re.sub(r'\s+[-–—]\s+.*$', '', name)
        name = re.sub(r'\s*\(.*?\)\s*', ' ', name)
        name = re.sub(r'[^\w\s]', '', name)
        name = re.sub(r'\s+', ' ', name).strip()
        return name

    def _verify_artist_id(self, item: Dict[str, Any], result: Dict[str, Any]) -> bool:
        """Verify that the result's artist ID matches the parent artist's stored AudioDB ID.

        If mismatched, the album/track search is more specific (uses artist+title),
        so we trust it and correct the parent artist's audiodb_id — BUT only when
        the result's artist *name* matches our parent artist. Without that guard,
        a collaboration/compilation (a track our library credits to one artist
        that lives on another artist's album) would stamp the wrong AudioDB id
        onto our artist. See the Deezer fix for the full write-up."""
        parent_audiodb_id = item.get('artist_audiodb_id')
        if not parent_audiodb_id:
            return True

        result_artist_id = result.get('idArtist')
        if not result_artist_id:
            return True

        if str(result_artist_id) != str(parent_audiodb_id):
            parent_name = item.get('artist') or ''
            result_artist_name = result.get('strArtist') or ''
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
                f"updating parent artist AudioDB ID from {parent_audiodb_id} to {result_artist_id}"
            )
            self._correct_artist_audiodb_id(item, str(result_artist_id))

        return True

    def _correct_artist_audiodb_id(self, item: Dict[str, Any], correct_audiodb_id: str):
        """Correct the parent artist's AudioDB id from a more specific album/track
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
                conn, 'audiodb', correct_audiodb_id, artist_id, this_name)
            if conflict:
                logger.warning(
                    "Refusing AudioDB-ID correction: id %s is already held by "
                    "'%s' (≠ '%s') — avoiding a shared/duplicate id (artist #%s)",
                    correct_audiodb_id, conflict, this_name, artist_id,
                )
                return

            write_provider_enrichment(
                conn, entity_type='artist', entity_id=artist_id,
                service='audiodb', provider_id=correct_audiodb_id)
            conn.commit()

            logger.info(f"Corrected artist #{artist_id} AudioDB ID to {correct_audiodb_id}")

        except Exception as e:
            logger.error(f"Error correcting artist AudioDB ID: {e}")
        finally:
            if conn:
                conn.close()

    def _name_matches(self, query_name: str, result_name: str) -> bool:
        """Check if AudioDB result name matches our query with fuzzy matching"""
        norm_query = self._normalize_name(query_name)
        norm_result = self._normalize_name(result_name)
        if not norm_query or not norm_result:
            raw_query = (query_name or '').strip().lower()
            raw_result = (result_name or '').strip().lower()
            return bool(raw_query) and raw_query == raw_result

        similarity = SequenceMatcher(None, norm_query, norm_result).ratio()
        logger.debug(f"Name similarity: '{query_name}' vs '{result_name}' = {similarity:.2f}")
        return similarity >= self.name_similarity_threshold

    def _get_existing_id(self, entity_type: str, entity_id: int) -> Optional[str]:
        """The AudioDB id already stored for this entity, if any.

        Set by a manual match or an earlier run; honoring it is what keeps a manual
        match from being searched over (issue #501).
        """
        conn = None
        try:
            from core.library2.worker_support import stored_provider_id

            conn = self.db._get_connection()
            return stored_provider_id(conn, entity_type, entity_id, 'audiodb')
        except Exception:
            return None
        finally:
            if conn:
                conn.close()

    def _process_item(self, item: Dict[str, Any]):
        """Process a single item (artist, album, or track).
        If the entity already has an audiodb_id (e.g. from manual match),
        uses it for direct lookup instead of searching by name."""
        try:
            item_type = item['type']
            item_id = item['id']
            item_name = item['name']

            logger.debug(f"Processing {item_type} #{item_id}: {item_name}")

            # Check for existing ID (manual match) — use direct lookup instead of name search
            existing_id = self._get_existing_id(item_type, item_id)
            if existing_id:
                lookup_methods = {
                    'artist': self.client.lookup_artist_by_id,
                    'album': self.client.lookup_album_by_id,
                    'track': self.client.lookup_track_by_id,
                }
                update_methods = {
                    'artist': lambda r: self._update_artist(item_id, r),
                    'album': lambda r: (self._verify_artist_id(item, r), self._update_album(item_id, r)),
                    'track': lambda r: (self._verify_artist_id(item, r), self._update_track(item_id, r)),
                }
                lookup = lookup_methods.get(item_type)
                update = update_methods.get(item_type)
                if lookup and update:
                    try:
                        result = lookup(existing_id)
                        if result:
                            update(result)
                            self.stats['matched'] += 1
                            logger.info(f"Enriched {item_type} '{item_name}' from existing AudioDB ID: {existing_id}")
                            return
                    except Exception as e:
                        logger.warning(f"Direct lookup failed for existing AudioDB ID {existing_id}: {e}")
                    # Direct lookup returned no metadata (None) or raised — don't
                    # fall through to the name-search path below, which could
                    # overwrite a manually-matched audiodb_id with a wrong guess.
                    # Mark status='error' so the queue's NULL-status filter stops
                    # re-picking this row on every tick (issue #553: AudioDB
                    # `track.php` timeouts caused infinite enrichment loops as
                    # the row was repeatedly picked + re-attempted because it
                    # never left the NULL state). The error-retry priority block
                    # in `_get_next_item` re-attempts after `retry_days` so
                    # transient AudioDB outages still recover automatically.
                    self._mark_status(item_type, item_id, 'error')
                    self.stats['errors'] += 1
                    logger.debug(
                        f"Preserving manual match for {item_type} '{item_name}' "
                        f"(AudioDB ID: {existing_id}); marked error pending retry"
                    )
                    return

            if item_type == 'artist':
                result = self.client.search_artist(item_name)
                if result:
                    result_name = result.get('strArtist', '')
                    conn = self.db._get_connection()
                    try:
                        ok, reason = accept_artist_match(
                            conn, 'audiodb', result.get('idArtist'), item_id,
                            item_name, result_name,
                        )
                    finally:
                        conn.close()
                    if ok:
                        self._update_artist(item_id, result)
                        self.stats['matched'] += 1
                        logger.info(f"Matched artist '{item_name}' -> AudioDB ID: {result.get('idArtist')}")
                    else:
                        self._mark_status('artist', item_id, 'not_found')
                        self.stats['not_found'] += 1
                        logger.debug(f"Artist '{item_name}' not matched: {reason}")
                else:
                    self._mark_status('artist', item_id, 'not_found')
                    self.stats['not_found'] += 1
                    logger.debug(f"No match for artist '{item_name}'")

            elif item_type == 'album':
                artist_name = item.get('artist', '')
                result = self.client.search_album(artist_name, item_name)
                if result:
                    result_name = result.get('strAlbum', '')
                    if self._name_matches(item_name, result_name):
                        self._verify_artist_id(item, result)
                        self._update_album(item_id, result)
                        self.stats['matched'] += 1
                        logger.info(f"Matched album '{item_name}' -> AudioDB ID: {result.get('idAlbum')}")
                    else:
                        self._mark_status('album', item_id, 'not_found')
                        self.stats['not_found'] += 1
                        logger.debug(f"Name mismatch for album '{item_name}' (got '{result_name}')")
                else:
                    self._mark_status('album', item_id, 'not_found')
                    self.stats['not_found'] += 1
                    logger.debug(f"No match for album '{item_name}'")

            elif item_type == 'track':
                artist_name = item.get('artist', '')
                result = self.client.search_track(artist_name, item_name)
                if result:
                    result_name = result.get('strTrack', '')
                    if self._name_matches(item_name, result_name):
                        self._verify_artist_id(item, result)
                        self._update_track(item_id, result)
                        self.stats['matched'] += 1
                        logger.info(f"Matched track '{item_name}' -> AudioDB ID: {result.get('idTrack')}")
                    else:
                        self._mark_status('track', item_id, 'not_found')
                        self.stats['not_found'] += 1
                        logger.debug(f"Name mismatch for track '{item_name}' (got '{result_name}')")
                else:
                    self._mark_status('track', item_id, 'not_found')
                    self.stats['not_found'] += 1
                    logger.debug(f"No match for track '{item_name}'")

        except Exception as e:
            logger.error(f"Error processing {item['type']} #{item['id']}: {e}")
            self.stats['errors'] += 1
            try:
                self._mark_status(item['type'], item['id'], 'error')
            except Exception as e2:
                logger.error(f"Error updating item status: {e2}")

    def _update_artist(self, artist_id: int, data: Dict[str, Any]):
        """Store AudioDB metadata for an artist using generic column names"""
        self._write('artist', artist_id, data.get('idArtist'), data, {
            'style': data.get('strStyle'),
            'mood': data.get('strMood'),
            'label': data.get('strLabel'),
            'banner_url': data.get('strArtistBanner'),
        }, image=data.get('strArtistThumb'))

    def _update_album(self, album_id: int, data: Dict[str, Any]):
        """Store AudioDB metadata for an album using generic column names"""
        self._write('album', album_id, data.get('idAlbum'), data, {
            'style': data.get('strStyle'),
            'mood': data.get('strMood'),
        }, image=data.get('strAlbumThumb'))

    def _update_track(self, track_id: int, data: Dict[str, Any]):
        """Store AudioDB metadata for a track using generic column names"""
        # Tracks carry no artwork or genre columns of their own.
        self._write('track', track_id, data.get('idTrack'), data, {
            'style': data.get('strStyle'),
            'mood': data.get('strMood'),
        })

    def _write(self, entity_type: str, entity_id: int, provider_id,
               data: Dict[str, Any], columns: Dict[str, Any],
               image: Optional[str] = None):
        """One write path for all three entity types (docs §32.3.1 stage 2).

        ``columns`` are written outright: style/mood/label/banner_url carry no
        service in their names, but AudioDB is their only writer, so a fresh fetch
        is the newer truth. Artwork and genres are backfilled instead — they are
        shared with better sources and with the user's own choice.
        """
        conn = None
        try:
            from core.library2.provider_attempts import record_attempt
            from core.library2.provider_writes import write_provider_enrichment

            conn = self.db._get_connection()

            backfill = {}
            if image:
                backfill['image_url'] = image
            genre = data.get('strGenre')
            if genre and entity_type != 'track':
                from core.genre_filter import filter_genres
                from core.settings import config_manager as _cfg
                _filtered = filter_genres([genre], _cfg)
                if _filtered:
                    backfill['genres'] = json.dumps(_filtered)

            write_provider_enrichment(
                conn, entity_type=entity_type, entity_id=entity_id,
                service='audiodb',
                provider_id=provider_id,
                columns=columns,
                backfill=backfill or None,
            )
            record_attempt(conn, entity_type=entity_type, entity_id=entity_id,
                           service='audiodb', status='matched')
            conn.commit()

        except Exception as e:
            logger.error(
                f"Error updating {entity_type} #{entity_id} with AudioDB data: {e}")
            raise
        finally:
            if conn:
                conn.close()

    def _mark_status(self, entity_type: str, entity_id: int, status: str):
        """Record the outcome of an attempt in the provider ledger.

        Replaces the legacy `audiodb_match_status`/`_last_attempted` column pair.
        Both `not_found` and `error` become due again after the retry window here —
        see `_RETRY_STATUSES`.
        """
        conn = None
        try:
            from core.library2.provider_attempts import record_attempt

            conn = self.db._get_connection()
            record_attempt(conn, entity_type=entity_type, entity_id=entity_id,
                           service='audiodb', status=status)
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
            return pending_count(conn, 'audiodb', retry_after_days=self.retry_days,
                                 retry_statuses=self._RETRY_STATUSES)
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
            return progress_breakdown(conn, 'audiodb')
        except Exception as e:
            logger.error(f"Error getting progress breakdown: {e}")
            return {}
        finally:
            if conn:
                conn.close()
