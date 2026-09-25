import threading
import time
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
from utils.logging_config import get_logger
from database.music_database import MusicDatabase
from core.musicbrainz_service import MusicBrainzService
from core.worker_utils import interruptible_sleep
from core.library2.worker_support import owned_album_titles, provider_id_conflict

logger = get_logger("musicbrainz_worker")

class MusicBrainzWorker:
    """Background worker for enriching library with MusicBrainz IDs"""

    def __init__(self, database: MusicDatabase, app_name: str = "SoulSync", app_version: str = "1.0", contact_email: str = ""):
        self.db = database
        self.mb_service = MusicBrainzService(database, app_name, app_version, contact_email)

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
        self.retry_days = 30  # Retry 'not_found' items after 30 days

        logger.info("MusicBrainz background worker initialized")

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
        logger.info("MusicBrainz background worker started")

    def stop(self):
        """Stop the background worker"""
        if not self.running:
            return

        logger.info("Stopping MusicBrainz worker...")
        self.should_stop = True
        self.running = False
        self._stop_event.set()

        if self.thread:
            self.thread.join(timeout=1)

        logger.info("Music Brainz worker stopped")

    def pause(self):
        """Pause the worker"""
        if not self.running:
            logger.warning("Worker not running, cannot pause")
            return

        self.paused = True
        logger.info("MusicBrainz worker paused")

    def resume(self):
        """Resume the worker"""
        if not self.running:
            logger.warning("Worker not running, start it first")
            return

        self.paused = False
        logger.info("MusicBrainz worker resumed")

    def get_stats(self) -> Dict[str, Any]:
        """Get current statistics"""
        # Update pending count
        self.stats['pending'] = self._count_pending_items()

        # Get progress breakdown by entity type
        progress = self._get_progress_breakdown()

        # Check if thread is actually alive (in case it crashed)
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
        logger.info("MusicBrainz worker thread started")

        while not self.should_stop:
            try:
                # Check if paused
                if self.paused:
                    interruptible_sleep(self._stop_event, 1)
                    continue

                # Clear previous item before getting next
                self.current_item = None

                # Get next item to process
                item = self._get_next_item()

                if not item:
                    # No more items - sleep for a bit
                    logger.debug("No pending items, sleeping...")
                    interruptible_sleep(self._stop_event, 10)
                    continue

                # Set current item for UI tracking
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


                # Process the item
                self._process_item(item)

                # MusicBrainzClient paces HTTP requests centrally; no extra
                # per-item delay for mirrors or cache hits is needed.

            except Exception as e:
                logger.error(f"Error in worker loop: {e}")
                interruptible_sleep(self._stop_event, 5)  # Back off on errors

        logger.info("MusicBrainz worker thread finished")

    def _get_next_item(self) -> Optional[Dict[str, Any]]:
        """Get next item to process from priority queue"""
        conn = None
        try:
            from core.library2.worker_queue import next_pending
            from core.worker_utils import read_enrichment_priority

            conn = self.db._get_connection()
            return next_pending(
                conn, 'musicbrainz',
                retry_after_days=self.retry_days,
                pinned=read_enrichment_priority('musicbrainz') or None,
            )

        except Exception as e:
            logger.error(f"Error getting next item: {e}")
            return None
        finally:
            if conn:
                conn.close()

    def _get_existing_id(self, entity_type: str, entity_id: int) -> Optional[str]:
        """The MBID already stored for this entity, if any.

        Legacy needed a per-entity column map (musicbrainz_id /
        musicbrainz_release_id / musicbrainz_recording_id) and an early bug queried
        all three as `musicbrainz_id`, so the check silently failed for albums and
        tracks. lib2 keeps the mbid under one name on every entity, which removes the
        map and the class of bug with it.
        """
        conn = None
        try:
            from core.library2.worker_support import stored_provider_id

            conn = self.db._get_connection()
            return stored_provider_id(conn, entity_type, entity_id, 'musicbrainz')
        except Exception:
            return None
        finally:
            if conn:
                conn.close()

    def _artist_aliases_empty(self, artist_id: Any) -> bool:
        """Whether ``lib2_artists.aliases`` for this row is empty.

        Used by the existing-MBID backfill path to skip the MB call when aliases are
        already populated, so re-scan cycles after the backfill are no-ops.
        Defensive: returns True on any error so the backfill still happens — a
        redundant MB call is cheaper than missing the backfill entirely.
        """
        conn = None
        try:
            conn = self.db._get_connection()
            row = conn.execute(
                "SELECT aliases FROM lib2_artists WHERE id = ? LIMIT 1",
                (artist_id,)).fetchone()
            if not row:
                return False  # Row doesn't exist — nothing to backfill
            value = row[0]
            return value is None or value == '' or value == '[]'
        except Exception:
            return True
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

            # Preserve existing manual matches
            existing_id = self._get_existing_id(item_type, item_id)
            if existing_id:
                logger.debug(f"Preserving existing MusicBrainz ID for {item_type} '{item_name}': {existing_id}")
                # Mark as matched so this row is not re-selected forever when
                # match_status is NULL but the MBID is already populated.
                try:
                    if item_type == 'artist':
                        self.mb_service.update_artist_mbid(item_id, existing_id, 'matched')
                        # Issue #442 — one-time backfill for artists
                        # enriched before alias support landed. Users with
                        # pre-existing libraries on day-one of this PR have
                        # MBIDs but NULL aliases. Fetch ONLY when the
                        # column is empty so re-scan cycles after backfill
                        # don't re-query MB. Best-effort: failures are
                        # logged at debug, don't regress the match outcome.
                        try:
                            if self._artist_aliases_empty(item_id):
                                aliases = self.mb_service.fetch_artist_aliases(existing_id)
                                if aliases:
                                    self.mb_service.update_artist_aliases(item_id, aliases)
                                    logger.debug(
                                        "Backfilled %d aliases for artist '%s'",
                                        len(aliases), item_name,
                                    )
                        except Exception as backfill_err:
                            logger.debug(
                                "Alias backfill failed for artist '%s': %s",
                                item_name, backfill_err,
                            )
                    elif item_type == 'album':
                        self.mb_service.update_album_mbid(item_id, existing_id, 'matched')
                    elif item_type == 'track':
                        self.mb_service.update_track_mbid(item_id, existing_id, 'matched')
                except Exception as mark_err:
                    logger.error(f"Error marking {item_type} #{item_id} matched: {mark_err}")
                return

            if item_type == 'artist':
                conn = self.db._get_connection()
                try:
                    owned = owned_album_titles(conn, item_id)
                finally:
                    conn.close()
                result = self.mb_service.match_artist(item_name, owned_titles=owned)
                mbid = result.get('mbid') if result else None
                # MB's combined score can match a weak name ("Grant" -> "Amy
                # Grant") when its own relevance rank is high. Guard against
                # assigning an mbid a differently-named artist already holds, so
                # one mbid can't be smeared across unrelated artists.
                conflict = None
                if mbid:
                    conn = self.db._get_connection()
                    try:
                        conflict = provider_id_conflict(
                            conn, 'musicbrainz', mbid, item_id, item_name)
                    finally:
                        conn.close()
                if mbid and not conflict:
                    self.mb_service.update_artist_mbid(item_id, mbid, 'matched')
                    # Issue #442 — pull alternate-spelling aliases (Japanese
                    # kanji, Cyrillic, etc.) so the verifier can recognise
                    # cross-script artist names without re-querying MB on
                    # every quarantine candidate. Best-effort: failures are
                    # swallowed inside `fetch_artist_aliases` (returns
                    # empty list) so a transient MB outage never regresses
                    # the enrichment outcome.
                    try:
                        aliases = self.mb_service.fetch_artist_aliases(mbid)
                        if aliases:
                            self.mb_service.update_artist_aliases(item_id, aliases)
                            logger.debug(
                                "Stored %d aliases for artist '%s'", len(aliases), item_name,
                            )
                    except Exception as alias_err:
                        logger.debug(
                            "Alias enrichment failed for artist '%s': %s", item_name, alias_err,
                        )
                    self.stats['matched'] += 1
                    logger.info(f"Matched artist '{item_name}' → MBID: {mbid}")
                else:
                    self.mb_service.update_artist_mbid(item_id, None, 'not_found')
                    self.stats['not_found'] += 1
                    if conflict:
                        logger.debug(
                            f"Artist '{item_name}' → MBID {mbid} skipped: "
                            f"already claimed by '{conflict}'"
                        )
                    else:
                        logger.debug(f"No match for artist '{item_name}'")

            elif item_type == 'album':
                artist_name = item.get('artist')
                result = self.mb_service.match_release(item_name, artist_name)
                if result and result.get('mbid'):
                    self.mb_service.update_album_mbid(item_id, result['mbid'], 'matched')
                    self.stats['matched'] += 1
                    logger.info(f"Matched album '{item_name}' → MBID: {result['mbid']}")
                else:
                    self.mb_service.update_album_mbid(item_id, None, 'not_found')
                    self.stats['not_found'] += 1
                    logger.debug(f"No match for album '{item_name}'")

            elif item_type == 'track':
                artist_name = item.get('artist')
                result = self.mb_service.match_recording(item_name, artist_name)
                if result and result.get('mbid'):
                    self.mb_service.update_track_mbid(item_id, result['mbid'], 'matched')
                    self.stats['matched'] += 1
                    logger.info(f"Matched track '{item_name}' → MBID: {result['mbid']}")
                else:
                    self.mb_service.update_track_mbid(item_id, None, 'not_found')
                    self.stats['not_found'] += 1
                    logger.debug(f"No match for track '{item_name}'")

        except Exception as e:
            logger.error(f"Error processing {item['type']} #{item['id']}: {e}")
            self.stats['errors'] += 1

            # Mark as error in database
            try:
                if item['type'] == 'artist':
                    self.mb_service.update_artist_mbid(item['id'], None, 'error')
                elif item['type'] == 'album':
                    self.mb_service.update_album_mbid(item['id'], None, 'error')
                elif item['type'] == 'track':
                    self.mb_service.update_track_mbid(item['id'], None, 'error')
            except Exception as e2:
                logger.error(f"Error updating item status: {e2}")

    def _count_pending_items(self) -> int:
        """Count how many items still need processing"""
        conn = None
        try:
            from core.library2.worker_queue import pending_count

            conn = self.db._get_connection()
            return pending_count(conn, 'musicbrainz',
                                 retry_after_days=self.retry_days)
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
            return progress_breakdown(conn, 'musicbrainz')
        except Exception as e:
            logger.error(f"Error getting progress breakdown: {e}")
            return {}
        finally:
            if conn:
                conn.close()
