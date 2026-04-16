import json
import re
import threading
import time
from difflib import SequenceMatcher
from typing import Optional, Dict, Any, List
from datetime import datetime, date, timedelta
from utils.logging_config import get_logger
from database.music_database import MusicDatabase
from core.spotify_client import SpotifyClient, SpotifyRateLimitError
from core.worker_utils import interruptible_sleep

logger = get_logger("spotify_worker")


class SpotifyWorker:
    """Background worker for enriching library artists, albums, and tracks with Spotify metadata.

    Uses a smart cascading batch approach:
      1. Search artist by name (1 API call)
      2. get_artist_albums once per matched artist -> match all DB albums locally
      3. get_album_tracks once per matched album -> match all DB tracks locally
      4. Fallback individual search for items whose parent wasn't matched
    """

    def __init__(self, database: MusicDatabase):
        self.db = database
        self.client = SpotifyClient()

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

        # Rate limiting (SpotifyClient already rate-limits at 350ms between API calls)
        self.inter_item_sleep = 1.5       # Between top-level items (each can trigger 5+ paginated calls)
        self.batch_inter_item_sleep = 0.1  # Between local matches within a batch (no API calls)

        # Daily budget — caps how many items this worker processes per calendar day
        self.daily_budget = 3000
        self._daily_items_processed = 0
        self._daily_date = date.today()

        logger.info("Spotify background worker initialized")

    def start(self):
        if self.running:
            logger.warning("Worker already running")
            return
        self.running = True
        self.should_stop = False
        self._stop_event.clear()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        logger.info("Spotify background worker started")

    def stop(self):
        if not self.running:
            return
        logger.info("Stopping Spotify worker...")
        self.should_stop = True
        self.running = False
        self._stop_event.set()
        if self.thread:
            self.thread.join(timeout=1)
        logger.info("Spotify worker stopped")

    def pause(self):
        if not self.running:
            logger.warning("Worker not running, cannot pause")
            return
        self.paused = True
        logger.info("Spotify worker paused")

    def resume(self):
        if not self.running:
            logger.warning("Worker not running, start it first")
            return
        self.paused = False
        logger.info("Spotify worker resumed")

    def get_stats(self) -> Dict[str, Any]:
        self.stats['pending'] = self._count_pending_items()
        progress = self._get_progress_breakdown()
        is_actually_running = self.running and (self.thread is not None and self.thread.is_alive())
        is_idle = is_actually_running and not self.paused and self.stats['pending'] == 0 and self.current_item is None

        try:
            # NEVER call is_spotify_authenticated() here — get_stats() is called
            # every 2 seconds by the WebSocket status loop. The auth probe has a
            # 60-second cache, so it would fire a real Spotify API call (current_user)
            # every 60 seconds indefinitely, wasting API quota and risking rate limits.
            # Instead, use sp presence as a lightweight proxy for "configured".
            rate_limited = self.client.is_rate_limited()
            rate_limit_info = self.client.get_rate_limit_info() if rate_limited else None
            in_cooldown = self.client.get_post_ban_cooldown_remaining() > 0
            authenticated = self.client.sp is not None
        except Exception:
            authenticated = False
            rate_limited = False
            rate_limit_info = None
            in_cooldown = False

        return {
            'enabled': True,
            'running': is_actually_running and not self.paused,
            'paused': self.paused,
            'idle': is_idle,
            'authenticated': authenticated,
            'rate_limited': rate_limited,
            'rate_limit': rate_limit_info,
            'daily_budget': self._get_daily_budget_info(),
            'current_item': self.current_item,
            'stats': self.stats.copy(),
            'progress': progress
        }

    # ── Daily budget ──────────────────────────────────────────────────

    def _increment_daily_budget(self):
        """Increment the daily processed counter, resetting on a new calendar day."""
        today = date.today()
        if self._daily_date != today:
            self._daily_items_processed = 0
            self._daily_date = today
        self._daily_items_processed += 1

    def _is_daily_budget_exhausted(self) -> bool:
        today = date.today()
        if self._daily_date != today:
            return False
        return self._daily_items_processed >= self.daily_budget

    def _get_daily_budget_info(self) -> dict:
        today = date.today()
        used = self._daily_items_processed if self._daily_date == today else 0
        now = datetime.now()
        midnight = datetime.combine(today + timedelta(days=1), datetime.min.time())
        resets_in = int((midnight - now).total_seconds())
        return {
            'used': used,
            'limit': self.daily_budget,
            'remaining': max(0, self.daily_budget - used),
            'exhausted': used >= self.daily_budget,
            'resets_in_seconds': resets_in
        }

    # ── Main loop ──────────────────────────────────────────────────────

    def _run(self):
        logger.info("Spotify worker thread started")
        while not self.should_stop:
            try:
                if self.paused:
                    interruptible_sleep(self._stop_event, 1)
                    continue

                # Rate limit guard — if globally rate limited, sleep until ban expires
                if self.client.is_rate_limited():
                    info = self.client.get_rate_limit_info()
                    remaining = info['remaining_seconds'] if info else 60
                    logger.debug(f"Spotify globally rate limited, sleeping {remaining}s...")
                    interruptible_sleep(self._stop_event, min(remaining, 60))  # Check again every 60s max
                    continue

                # Daily budget guard — worker-only cap to avoid saturating Spotify rate limits
                if self._is_daily_budget_exhausted():
                    budget = self._get_daily_budget_info()
                    resets_in = budget['resets_in_seconds']
                    logger.info(f"Daily enrichment budget exhausted ({budget['used']}/{budget['limit']}), "
                                f"resets in {resets_in // 3600}h {(resets_in % 3600) // 60}m")
                    interruptible_sleep(self._stop_event, min(resets_in, 300))  # Check every 5 min max
                    continue

                # Post-ban cooldown guard — after ban expires, wait before resuming
                # to avoid immediately re-triggering the rate limit
                cooldown = self.client.get_post_ban_cooldown_remaining()
                if cooldown > 0:
                    logger.debug(f"Post-ban cooldown active ({cooldown}s left), sleeping...")
                    interruptible_sleep(self._stop_event, min(cooldown, 60))
                    continue

                # Auth guard — check if Spotify client is configured (no API call).
                # We intentionally avoid calling is_spotify_authenticated() here
                # because it makes an API probe that can re-trigger rate limits
                # and lock users in an infinite rate-limit loop.
                if not self.client.is_spotify_authenticated():
                    self.client.reload_config()
                    if not self.client.is_spotify_authenticated():
                        logger.debug("Spotify not authenticated, sleeping 30s...")
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
                self._increment_daily_budget()
                interruptible_sleep(self._stop_event, self.inter_item_sleep)

            except SpotifyRateLimitError:
                logger.debug("Spotify rate limit hit in worker loop, will retry after ban expires")
                interruptible_sleep(self._stop_event, 10)
            except Exception as e:
                logger.error(f"Error in worker loop: {e}")
                interruptible_sleep(self._stop_event, 5)

        self.current_item = None
        logger.info("Spotify worker thread finished")

    # ── Priority queue ─────────────────────────────────────────────────

    def _get_next_item(self) -> Optional[Dict[str, Any]]:
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            # Priority 1: Unattempted artists
            cursor.execute("""
                SELECT id, name
                FROM artists
                WHERE spotify_match_status IS NULL AND id IS NOT NULL
                ORDER BY id ASC
                LIMIT 1
            """)
            row = cursor.fetchone()
            if row:
                return {'type': 'artist', 'id': row[0], 'name': row[1]}

            # Priority 2: Album batch — matched artist with unattempted albums
            cursor.execute("""
                SELECT ar.id, ar.name, ar.spotify_artist_id
                FROM artists ar
                WHERE ar.spotify_match_status = 'matched'
                  AND ar.spotify_artist_id IS NOT NULL
                  AND EXISTS (
                      SELECT 1 FROM albums al
                      WHERE al.artist_id = ar.id AND al.spotify_match_status IS NULL AND al.id IS NOT NULL
                  )
                ORDER BY ar.id ASC
                LIMIT 1
            """)
            row = cursor.fetchone()
            if row:
                return {
                    'type': 'album_batch',
                    'artist_id': row[0],
                    'artist_name': row[1],
                    'spotify_artist_id': row[2],
                    'name': f"Albums for {row[1]}"
                }

            # Priority 3: Track batch — matched album with unattempted tracks
            cursor.execute("""
                SELECT al.id, al.title, al.spotify_album_id, ar.name AS artist_name
                FROM albums al
                JOIN artists ar ON al.artist_id = ar.id
                WHERE al.spotify_match_status = 'matched'
                  AND al.spotify_album_id IS NOT NULL
                  AND EXISTS (
                      SELECT 1 FROM tracks t
                      WHERE t.album_id = al.id AND t.spotify_match_status IS NULL AND t.id IS NOT NULL
                  )
                ORDER BY al.id ASC
                LIMIT 1
            """)
            row = cursor.fetchone()
            if row:
                return {
                    'type': 'track_batch',
                    'album_id': row[0],
                    'album_name': row[1],
                    'spotify_album_id': row[2],
                    'artist_name': row[3],
                    'name': f"Tracks on {row[1]}"
                }

            # Priority 4: Fallback individual albums (parent artist unmatched)
            cursor.execute("""
                SELECT a.id, a.title, ar.name AS artist_name
                FROM albums a
                JOIN artists ar ON a.artist_id = ar.id
                WHERE a.spotify_match_status IS NULL AND a.id IS NOT NULL
                ORDER BY a.id ASC
                LIMIT 1
            """)
            row = cursor.fetchone()
            if row:
                return {'type': 'album_individual', 'id': row[0], 'name': row[1], 'artist': row[2]}

            # Priority 5: Fallback individual tracks (parent album unmatched)
            cursor.execute("""
                SELECT t.id, t.title, ar.name AS artist_name
                FROM tracks t
                JOIN artists ar ON t.artist_id = ar.id
                WHERE t.spotify_match_status IS NULL AND t.id IS NOT NULL
                ORDER BY t.id ASC
                LIMIT 1
            """)
            row = cursor.fetchone()
            if row:
                return {'type': 'track_individual', 'id': row[0], 'name': row[1], 'artist': row[2]}

            # Priority 6: Retry stale 'not_found' failures
            not_found_cutoff = datetime.now() - timedelta(days=self.retry_days)

            cursor.execute("""
                SELECT id, name
                FROM artists
                WHERE spotify_match_status = 'not_found' AND spotify_last_attempted < ?
                ORDER BY spotify_last_attempted ASC
                LIMIT 1
            """, (not_found_cutoff,))
            row = cursor.fetchone()
            if row:
                return {'type': 'artist', 'id': row[0], 'name': row[1]}

            cursor.execute("""
                SELECT a.id, a.title, ar.name AS artist_name
                FROM albums a
                JOIN artists ar ON a.artist_id = ar.id
                WHERE a.spotify_match_status = 'not_found' AND a.spotify_last_attempted < ?
                ORDER BY a.spotify_last_attempted ASC
                LIMIT 1
            """, (not_found_cutoff,))
            row = cursor.fetchone()
            if row:
                return {'type': 'album_individual', 'id': row[0], 'name': row[1], 'artist': row[2]}

            cursor.execute("""
                SELECT t.id, t.title, ar.name AS artist_name
                FROM tracks t
                JOIN artists ar ON t.artist_id = ar.id
                WHERE t.spotify_match_status = 'not_found' AND t.spotify_last_attempted < ?
                ORDER BY t.spotify_last_attempted ASC
                LIMIT 1
            """, (not_found_cutoff,))
            row = cursor.fetchone()
            if row:
                return {'type': 'track_individual', 'id': row[0], 'name': row[1], 'artist': row[2]}

            return None

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

        except SpotifyRateLimitError:
            raise  # Propagate to main loop so it activates the sleep/ban guard
        except Exception as e:
            logger.error(f"Error processing {item.get('type')} '{item.get('name', '')}': {e}")
            self.stats['errors'] += 1
            # Mark the item so we don't retry immediately
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
        """Check if an entity already has a spotify_artist_id/spotify_album_id/spotify_track_id."""
        col_map = {'artist': 'spotify_artist_id', 'album': 'spotify_album_id', 'track': 'spotify_track_id'}
        table_map = {'artist': 'artists', 'album': 'albums', 'track': 'tracks'}
        col = col_map.get(entity_type)
        table = table_map.get(entity_type)
        if not col or not table:
            return None
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute(f"SELECT {col} FROM {table} WHERE id = ?", (entity_id,))
            row = cursor.fetchone()
            return row[0] if row and row[0] else None
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
            logger.debug(f"Preserving existing Spotify ID for artist '{artist_name}': {existing_id}")
            self._mark_status('artist', artist_id, 'matched')
            return

        results = self.client.search_artists(artist_name, limit=5)
        if not results:
            self._mark_status('artist', artist_id, 'not_found')
            self.stats['not_found'] += 1
            logger.debug(f"No Spotify results for artist '{artist_name}'")
            return

        # Find best fuzzy match — score all candidates, pick highest above threshold
        best_obj = None
        best_score = 0
        for artist_obj in results:
            score = self._name_similarity(artist_name, artist_obj.name)
            if score >= self.name_similarity_threshold and score > best_score:
                best_obj = artist_obj
                best_score = score

        if best_obj:
            if not self._is_spotify_id(best_obj.id):
                logger.warning(f"Rejecting non-Spotify ID '{best_obj.id}' for artist '{artist_name}' (iTunes fallback leak)")
                self._mark_status('artist', artist_id, 'error')
                self.stats['errors'] += 1
                return
            self._update_artist(artist_id, best_obj)
            self.stats['matched'] += 1
            logger.info(f"Matched artist '{artist_name}' -> Spotify ID: {best_obj.id} (score: {best_score:.2f})")
            return

        self._mark_status('artist', artist_id, 'not_found')
        self.stats['not_found'] += 1
        logger.debug(f"Name mismatch for artist '{artist_name}' (best: '{results[0].name}')")

    # ── Album batch processing ─────────────────────────────────────────

    def _process_album_batch(self, item: Dict[str, Any]):
        artist_id = item['artist_id']
        spotify_artist_id = item['spotify_artist_id']
        artist_name = item['artist_name']

        # Fetch albums with pagination cap — Spotify returns 10/page, so max_pages=5
        # gives 50 albums (newest first). Avoids 20+ paginated calls for prolific artists
        # (e.g., 217 albums = 22 API calls without cap, vs 5 with cap)
        try:
            spotify_albums = self.client.get_artist_albums(
                spotify_artist_id, album_type='album,single,compilation', limit=50,
                max_pages=5
            )
        except Exception as e:
            logger.error(f"Failed to get Spotify albums for artist '{artist_name}': {e}")
            self._mark_artist_albums_error(artist_id)
            self.stats['errors'] += 1
            return

        if not spotify_albums:
            logger.debug(f"No Spotify albums for artist '{artist_name}'")
            self._mark_artist_albums_not_found(artist_id)
            return

        # Load all unmatched DB albums for this artist
        db_albums = self._get_unmatched_albums_for_artist(artist_id)
        if not db_albums:
            return

        # Validate that we got Spotify albums, not iTunes fallback
        if spotify_albums and not self._is_spotify_id(spotify_albums[0].id):
            logger.warning(f"Rejecting album batch for '{artist_name}': got iTunes IDs instead of Spotify")
            self._mark_artist_albums_error(artist_id)
            self.stats['errors'] += 1
            return

        matched_count = 0
        for db_album in db_albums:
            db_id, db_title = db_album['id'], db_album['title']
            best_match = None

            for sp_album in spotify_albums:
                if self._name_matches(db_title, sp_album.name):
                    best_match = sp_album
                    break

            if best_match:
                self._update_album(db_id, best_match)
                self.stats['matched'] += 1
                matched_count += 1
                logger.info(f"Batch matched album '{db_title}' -> Spotify ID: {best_match.id}")
            else:
                self._mark_status('album', db_id, 'not_found')
                self.stats['not_found'] += 1

            interruptible_sleep(self._stop_event, self.batch_inter_item_sleep)

        logger.info(f"Album batch for '{artist_name}': {matched_count}/{len(db_albums)} matched")

    # ── Track batch processing ─────────────────────────────────────────

    def _process_track_batch(self, item: Dict[str, Any]):
        album_id = item['album_id']
        spotify_album_id = item['spotify_album_id']
        album_name = item['album_name']

        # 1 API call: get all tracks for this album from Spotify
        try:
            result = self.client.get_album_tracks(spotify_album_id)
        except Exception as e:
            logger.error(f"Failed to get Spotify tracks for album '{album_name}': {e}")
            self._mark_album_tracks_error(album_id)
            self.stats['errors'] += 1
            return

        if not result or not result.get('items'):
            logger.debug(f"No Spotify tracks for album '{album_name}'")
            self._mark_album_tracks_not_found(album_id)
            return

        spotify_tracks = result['items']

        # Validate that we got Spotify tracks, not iTunes fallback
        if spotify_tracks and not self._is_spotify_id(str(spotify_tracks[0].get('id', ''))):
            logger.warning(f"Rejecting track batch for '{album_name}': got iTunes IDs instead of Spotify")
            self._mark_album_tracks_error(album_id)
            self.stats['errors'] += 1
            return

        # Load all unmatched DB tracks for this album
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
                for sp_track in spotify_tracks:
                    sp_num = sp_track.get('track_number')
                    if sp_num and sp_num == db_track_number:
                        sp_name = sp_track.get('name', '')
                        if self._name_matches(db_title, sp_name):
                            best_match = sp_track
                            break

            # Strategy B: pure name match fallback
            if not best_match:
                for sp_track in spotify_tracks:
                    sp_name = sp_track.get('name', '')
                    if self._name_matches(db_title, sp_name):
                        best_match = sp_track
                        break

            if best_match:
                self._update_track(db_id, best_match)
                self.stats['matched'] += 1
                matched_count += 1
                logger.info(f"Batch matched track '{db_title}' -> Spotify ID: {best_match.get('id')}")
            else:
                self._mark_status('track', db_id, 'not_found')
                self.stats['not_found'] += 1

            interruptible_sleep(self._stop_event, self.batch_inter_item_sleep)

        logger.info(f"Track batch for '{album_name}': {matched_count}/{len(db_tracks)} matched")

    # ── Individual fallback processing ─────────────────────────────────

    def _process_album_individual(self, item: Dict[str, Any]):
        album_id = item['id']
        album_name = item['name']
        artist_name = item.get('artist', '')

        query = f"{artist_name} {album_name}" if artist_name else album_name
        results = self.client.search_albums(query, limit=5)

        if not results:
            self._mark_status('album', album_id, 'not_found')
            self.stats['not_found'] += 1
            logger.debug(f"No Spotify results for album '{album_name}'")
            return

        best_obj = None
        best_score = 0
        for album_obj in results:
            score = self._name_similarity(album_name, album_obj.name)
            if score >= self.name_similarity_threshold and score > best_score:
                best_obj = album_obj
                best_score = score

        if best_obj:
            if not self._is_spotify_id(best_obj.id):
                logger.warning(f"Rejecting non-Spotify ID '{best_obj.id}' for album '{album_name}'")
                self._mark_status('album', album_id, 'error')
                self.stats['errors'] += 1
                return
            self._update_album(album_id, best_obj)
            self.stats['matched'] += 1
            logger.info(f"Matched album '{album_name}' -> Spotify ID: {best_obj.id} (score: {best_score:.2f})")
            return

        self._mark_status('album', album_id, 'not_found')
        self.stats['not_found'] += 1
        logger.debug(f"Name mismatch for album '{album_name}'")

    def _process_track_individual(self, item: Dict[str, Any]):
        track_id = item['id']
        track_name = item['name']
        artist_name = item.get('artist', '')

        query = f"{artist_name} {track_name}" if artist_name else track_name
        results = self.client.search_tracks(query, limit=5)

        if not results:
            self._mark_status('track', track_id, 'not_found')
            self.stats['not_found'] += 1
            logger.debug(f"No Spotify results for track '{track_name}'")
            return

        best_obj = None
        best_score = 0
        for track_obj in results:
            score = self._name_similarity(track_name, track_obj.name)
            if score >= self.name_similarity_threshold and score > best_score:
                best_obj = track_obj
                best_score = score

        if best_obj:
            if not self._is_spotify_id(best_obj.id):
                logger.warning(f"Rejecting non-Spotify ID '{best_obj.id}' for track '{track_name}'")
                self._mark_status('track', track_id, 'error')
                self.stats['errors'] += 1
                return
            self._update_track_from_search(track_id, best_obj)
            self.stats['matched'] += 1
            logger.info(f"Matched track '{track_name}' -> Spotify ID: {best_obj.id} (score: {best_score:.2f})")
            return

        self._mark_status('track', track_id, 'not_found')
        self.stats['not_found'] += 1
        logger.debug(f"Name mismatch for track '{track_name}'")

    # ── DB update methods ──────────────────────────────────────────────

    def _update_artist(self, artist_id: int, artist_obj):
        """Store Spotify metadata for an artist (from Artist dataclass)"""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                UPDATE artists SET
                    spotify_artist_id = ?,
                    spotify_match_status = 'matched',
                    spotify_last_attempted = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (str(artist_obj.id), artist_id))

            # Backfill thumb_url if empty
            if artist_obj.image_url:
                cursor.execute("""
                    UPDATE artists SET thumb_url = ?
                    WHERE id = ? AND (thumb_url IS NULL OR thumb_url = '')
                """, (artist_obj.image_url, artist_id))

            # Backfill genres if empty
            if artist_obj.genres:
                cursor.execute("""
                    UPDATE artists SET genres = ?
                    WHERE id = ? AND (genres IS NULL OR genres = '' OR genres = '[]')
                """, (json.dumps(artist_obj.genres), artist_id))

            conn.commit()
        except Exception as e:
            logger.error(f"Error updating artist #{artist_id} with Spotify data: {e}")
            raise
        finally:
            if conn:
                conn.close()

    def _update_album(self, album_id: int, album_obj):
        """Store Spotify metadata for an album (from Album dataclass)"""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                UPDATE albums SET
                    spotify_album_id = ?,
                    spotify_match_status = 'matched',
                    spotify_last_attempted = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (str(album_obj.id), album_id))

            # Backfill thumb_url if empty
            if album_obj.image_url:
                cursor.execute("""
                    UPDATE albums SET thumb_url = ?
                    WHERE id = ? AND (thumb_url IS NULL OR thumb_url = '')
                """, (album_obj.image_url, album_id))

            # Backfill record_type if empty
            if album_obj.album_type:
                cursor.execute("""
                    UPDATE albums SET record_type = ?
                    WHERE id = ? AND (record_type IS NULL OR record_type = '')
                """, (album_obj.album_type, album_id))

            # Backfill year from release_date if empty
            if album_obj.release_date:
                year = album_obj.release_date[:4] if len(album_obj.release_date) >= 4 else None
                if year and year.isdigit():
                    cursor.execute("""
                        UPDATE albums SET year = ?
                        WHERE id = ? AND (year IS NULL OR year = '' OR year = '0')
                    """, (year, album_id))

            conn.commit()
        except Exception as e:
            logger.error(f"Error updating album #{album_id} with Spotify data: {e}")
            raise
        finally:
            if conn:
                conn.close()

    def _update_track(self, track_id: int, track_data: Dict[str, Any]):
        """Store Spotify metadata for a track (from get_album_tracks dict)"""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            spotify_id = str(track_data.get('id', ''))

            cursor.execute("""
                UPDATE tracks SET
                    spotify_track_id = ?,
                    spotify_match_status = 'matched',
                    spotify_last_attempted = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (spotify_id, track_id))

            # Backfill explicit flag
            if 'explicit' in track_data:
                explicit_val = 1 if track_data['explicit'] else 0
                cursor.execute("""
                    UPDATE tracks SET explicit = ?
                    WHERE id = ? AND explicit IS NULL
                """, (explicit_val, track_id))

            conn.commit()
        except Exception as e:
            logger.error(f"Error updating track #{track_id} with Spotify data: {e}")
            raise
        finally:
            if conn:
                conn.close()

    def _update_track_from_search(self, track_id: int, track_obj):
        """Store Spotify metadata for a track (from Track dataclass, individual search)"""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                UPDATE tracks SET
                    spotify_track_id = ?,
                    spotify_match_status = 'matched',
                    spotify_last_attempted = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (str(track_obj.id), track_id))

            conn.commit()
        except Exception as e:
            logger.error(f"Error updating track #{track_id} with Spotify data: {e}")
            raise
        finally:
            if conn:
                conn.close()

    # ── Batch helpers ──────────────────────────────────────────────────

    def _get_unmatched_albums_for_artist(self, artist_id: int) -> List[Dict[str, Any]]:
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, title FROM albums
                WHERE artist_id = ? AND spotify_match_status IS NULL
                ORDER BY id ASC
            """, (artist_id,))
            return [{'id': row[0], 'title': row[1]} for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Error getting unmatched albums for artist #{artist_id}: {e}")
            return []
        finally:
            if conn:
                conn.close()

    def _get_unmatched_tracks_for_album(self, album_id: int) -> List[Dict[str, Any]]:
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, title, track_number FROM tracks
                WHERE album_id = ? AND spotify_match_status IS NULL AND id IS NOT NULL
                ORDER BY id ASC
            """, (album_id,))
            return [{'id': row[0], 'title': row[1], 'track_number': row[2]} for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Error getting unmatched tracks for album #{album_id}: {e}")
            return []
        finally:
            if conn:
                conn.close()

    def _mark_artist_albums_error(self, artist_id: int):
        """Bulk mark unattempted albums for an artist as 'error'"""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE albums SET
                    spotify_match_status = 'error',
                    spotify_last_attempted = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE artist_id = ? AND spotify_match_status IS NULL
            """, (artist_id,))
            conn.commit()
        except Exception as e:
            logger.error(f"Error bulk-marking albums for artist #{artist_id}: {e}")
        finally:
            if conn:
                conn.close()

    def _mark_artist_albums_not_found(self, artist_id: int):
        """Bulk mark unattempted albums for an artist as 'not_found'"""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE albums SET
                    spotify_match_status = 'not_found',
                    spotify_last_attempted = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE artist_id = ? AND spotify_match_status IS NULL
            """, (artist_id,))
            conn.commit()
        except Exception as e:
            logger.error(f"Error bulk-marking albums not_found for artist #{artist_id}: {e}")
        finally:
            if conn:
                conn.close()

    def _mark_album_tracks_error(self, album_id: int):
        """Bulk mark unattempted tracks for an album as 'error'"""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE tracks SET
                    spotify_match_status = 'error',
                    spotify_last_attempted = CURRENT_TIMESTAMP
                WHERE album_id = ? AND spotify_match_status IS NULL
            """, (album_id,))
            conn.commit()
        except Exception as e:
            logger.error(f"Error bulk-marking tracks for album #{album_id}: {e}")
        finally:
            if conn:
                conn.close()

    def _mark_album_tracks_not_found(self, album_id: int):
        """Bulk mark unattempted tracks for an album as 'not_found'"""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE tracks SET
                    spotify_match_status = 'not_found',
                    spotify_last_attempted = CURRENT_TIMESTAMP
                WHERE album_id = ? AND spotify_match_status IS NULL
            """, (album_id,))
            conn.commit()
        except Exception as e:
            logger.error(f"Error bulk-marking tracks not_found for album #{album_id}: {e}")
        finally:
            if conn:
                conn.close()

    # ── Status / counting ──────────────────────────────────────────────

    def _mark_status(self, entity_type: str, entity_id: int, status: str):
        table_map = {'artist': 'artists', 'album': 'albums', 'track': 'tracks'}
        table = table_map.get(entity_type)
        if not table:
            return

        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute(f"""
                UPDATE {table} SET
                    spotify_match_status = ?,
                    spotify_last_attempted = CURRENT_TIMESTAMP,
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
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    (SELECT COUNT(*) FROM artists WHERE spotify_match_status IS NULL AND id IS NOT NULL) +
                    (SELECT COUNT(*) FROM albums WHERE spotify_match_status IS NULL AND id IS NOT NULL) +
                    (SELECT COUNT(*) FROM tracks WHERE spotify_match_status IS NULL AND id IS NOT NULL)
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
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            progress = {}

            for entity, table in [('artists', 'artists'), ('albums', 'albums'), ('tracks', 'tracks')]:
                cursor.execute(f"""
                    SELECT
                        COUNT(*) AS total,
                        SUM(CASE WHEN spotify_match_status IS NOT NULL THEN 1 ELSE 0 END) AS processed
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

    # ── ID validation ────────────────────────────────────────────────

    def _is_spotify_id(self, id_str: str) -> bool:
        """Spotify IDs are alphanumeric (contain letters). iTunes IDs are purely numeric.
        Reject numeric-only IDs to prevent iTunes contamination of spotify_* columns."""
        if not id_str:
            return False
        return not str(id_str).isdigit()

    # ── Name matching ──────────────────────────────────────────────────

    def _normalize_name(self, name: str) -> str:
        name = name.lower().strip()
        name = re.sub(r'\s+[-–—]\s+.*$', '', name)  # Strip " - Remix/Edit/etc" suffixes (Spotify format)
        name = re.sub(r'\s*\(.*?\)\s*', ' ', name)   # Strip "(Remix/Edit/etc)" parentheticals
        name = re.sub(r'[^\w\s]', '', name)
        name = re.sub(r'\s+', ' ', name).strip()
        return name

    def _name_similarity(self, query_name: str, result_name: str) -> float:
        norm_query = self._normalize_name(query_name)
        norm_result = self._normalize_name(result_name)
        return SequenceMatcher(None, norm_query, norm_result).ratio()

    def _name_matches(self, query_name: str, result_name: str) -> bool:
        similarity = self._name_similarity(query_name, result_name)
        logger.debug(f"Name similarity: '{query_name}' vs '{result_name}' = {similarity:.2f}")
        return similarity >= self.name_similarity_threshold
