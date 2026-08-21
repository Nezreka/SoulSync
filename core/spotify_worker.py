import json
import re
import threading
import time
from difflib import SequenceMatcher
from types import SimpleNamespace
from typing import Optional, Dict, Any, List
from datetime import datetime, date, timedelta
from utils.logging_config import get_logger
from database.music_database import MusicDatabase
from core.spotify_client import SpotifyClient, SpotifyRateLimitError
from core.worker_utils import (
    ARTIST_NAME_MATCH_THRESHOLD,
    interruptible_sleep,
    pick_artist_by_catalog,
    release_titles,
)
from core.library2.worker_support import (
    MATCHED,
    honor_stored_match,
    owned_album_titles,
    provider_id_conflict,
)

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

        # Whether the worker is serving via the no-creds Spotify Free source as of
        # its last loop iteration. Cached from the loop's _free_active() probe so
        # get_stats() can report it without an auth API call (#887: a no-auth user
        # whose enrichment runs on Free was shown "Not Authenticated").
        self._serving_via_free = False

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

        # Daily budget — caps how many items this worker processes per calendar day.
        # Lowered from 3000 to 500 after Spotify's February 2026 API tightening
        # (/v1/search max limit cut from 50 to 10) increased the per-track API call
        # cost. Sustained 3000-item runs were tripping Spotify's automated abuse
        # detection and earning multi-hour 429 bans. 500/day keeps the worker
        # productive without crossing the threshold.
        self.daily_budget = 500
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
            # Is the worker serving via the no-creds Spotify Free source right
            # now? Two cases: bridging a rate-limit ban (only checked WHEN
            # rate-limited — there is_spotify_authenticated() returns False
            # without an API probe, so this is quota-free), OR bridging the spent
            # real-API daily budget (the worker set _budget_exhausted_use_free —
            # a cheap attribute read). Lets the UI show "Running (Spotify Free)"
            # instead of a misleading "rate limited" / "daily limit reached".
            # #887: the loop's cached _free_active() result is the comprehensive
            # signal — it's True for a no-auth user enriching via Spotify Free by
            # default (prefer-free is on unless disabled), not just the rate-limit
            # / budget bridges. The extra terms stay as a fallback for the brief
            # window before the loop's first iteration sets the cache.
            using_free = bool(
                getattr(self, '_serving_via_free', False)
                or (rate_limited and self.client.is_spotify_metadata_available())
                or getattr(self.client, '_budget_exhausted_use_free', False)
            )
        except Exception:
            authenticated = False
            rate_limited = False
            rate_limit_info = None
            in_cooldown = False
            using_free = False

        return {
            'enabled': True,
            'running': is_actually_running and not self.paused,
            'paused': self.paused,
            'idle': is_idle,
            'authenticated': authenticated,
            'rate_limited': rate_limited,
            'using_free': using_free,
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

                # Rate limit guard — if globally rate limited, sleep until ban
                # expires. EXCEPT: when Spotify Free is available it bridges the
                # ban (is_spotify_metadata_available() is True via the no-creds
                # source, and the client routes there), so we keep enriching
                # instead of stalling. Purely additive: with Spotify Free off,
                # is_spotify_metadata_available() is False during a ban and this
                # sleeps exactly as before.
                if self.client.is_rate_limited() and not self.client.is_spotify_metadata_available():
                    info = self.client.get_rate_limit_info()
                    remaining = info['remaining_seconds'] if info else 60
                    logger.debug(f"Spotify globally rate limited, sleeping {remaining}s...")
                    interruptible_sleep(self._stop_event, min(remaining, 60))  # Check again every 60s max
                    continue

                # Enrichment runs on the no-auth Spotify source by DEFAULT
                # (metadata.spotify_free_enrichment, ON unless turned off): bulk
                # enrichment is the workload that bans the real API, so we keep it
                # off your connected account's quota and reserve official Spotify
                # for interactive search + playlist sync. The flag overrides auth
                # for the worker (authed users still enrich via the no-auth path)
                # and also lets the worker run with no auth at all. _free_active()
                # and is_spotify_metadata_available() both honor it; set only on
                # the worker's OWN client, so interactive paths stay official-first.
                # Harmless when the no-auth package isn't installed (the methods
                # fall back to official, then iTunes/Deezer).
                try:
                    from core.settings import config_manager as _cfg
                    self.client._prefer_free = bool(
                        _cfg.get('metadata.spotify_free_enrichment', True))
                except Exception:  # noqa: S110 — prefer-free toggle is best-effort
                    self.client._prefer_free = True

                # Is the worker serving via the no-auth Spotify source this
                # iteration? The daily budget and post-ban cooldown both exist to
                # protect the REAL authenticated API from bans — they don't apply
                # to the no-auth path. Computed once and reused below; the loop
                # already probes auth, so no extra quota cost.
                budget_exhausted = self._is_daily_budget_exhausted()

                # Daily budget is a REAL-API ban protection. When it's spent, if
                # the no-creds free source is available, BRIDGE to it (uncapped)
                # for the rest of the day instead of pausing — so a Spotify-Free
                # user is never stopped by the budget. The flag makes the client
                # route subsequent calls to free; it clears on the daily reset.
                try:
                    if budget_exhausted and self.client._free_available():
                        self.client._budget_exhausted_use_free = True
                    elif not budget_exhausted and getattr(self.client, '_budget_exhausted_use_free', False):
                        self.client._budget_exhausted_use_free = False
                except Exception:  # noqa: S110 — budget→free toggle is best-effort
                    pass

                # Is the worker serving via the no-creds Spotify Free source this
                # iteration? The daily budget and post-ban cooldown both exist to
                # protect the REAL authenticated API from bans — they don't apply
                # to free (a different, anonymous path). _free_active() now also
                # returns True when the budget-bridge flag above is set. Computed
                # once and reused below; the loop already probes auth, no extra cost.
                try:
                    free_serving = self.client._free_active()
                except Exception:
                    free_serving = False
                # Cache for get_stats() so the dashboard status reflects that the
                # worker IS enriching via Free even with no official auth (#887).
                self._serving_via_free = free_serving

                # Daily budget guard — pause ONLY when the budget is spent AND we
                # can't serve via free (no free available). Otherwise free took over.
                if not free_serving and budget_exhausted:
                    budget = self._get_daily_budget_info()
                    resets_in = budget['resets_in_seconds']
                    logger.info(f"Daily enrichment budget exhausted ({budget['used']}/{budget['limit']}), "
                                f"resets in {resets_in // 3600}h {(resets_in % 3600) // 60}m")
                    interruptible_sleep(self._stop_event, min(resets_in, 300))  # Check every 5 min max
                    continue

                # Post-ban cooldown guard — after ban expires, wait before resuming
                # to avoid immediately re-triggering the rate limit. Only matters
                # for the real API, so skip it while serving via free.
                cooldown = 0 if free_serving else self.client.get_post_ban_cooldown_remaining()
                if cooldown > 0:
                    logger.debug(f"Post-ban cooldown active ({cooldown}s left), sleeping...")
                    interruptible_sleep(self._stop_event, min(cooldown, 60))
                    continue

                # Auth guard — check if Spotify client is configured (no API call).
                # We intentionally avoid calling is_spotify_authenticated() here
                # because it makes an API probe that can re-trigger rate limits
                # and lock users in an infinite rate-limit loop.
                # Available = real auth OR the no-creds SpotipyFree fallback
                # (enrichment is metadata-only, so the free source can serve it).
                if not self.client.is_spotify_metadata_available():
                    self.client.reload_config()
                    if not self.client.is_spotify_metadata_available():
                        logger.debug("Spotify metadata unavailable, sleeping 30s...")
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
                # Only real-API work counts toward the daily cap — free-served
                # items don't touch the authenticated API's quota.
                if not free_serving:
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
        """Get next item to process from the Library-v2 catalogue.

        The whole batch-first order — pinned group, unattempted artists, an album
        batch, a track batch, then the individual fallbacks for children whose own
        parent never matched — lives in ``core.library2.worker_queue`` and is shared
        with the iTunes worker, which had the identical queue (docs §32.3.1 stage 2).
        The item dicts it returns are the ones the process methods below already
        consume, including the ``spotify_artist_id`` key.
        """
        conn = None
        try:
            from core.library2.worker_queue import next_batch_pending
            from core.worker_utils import read_enrichment_priority

            conn = self.db._get_connection()
            return next_batch_pending(
                conn, 'spotify',
                retry_after_days=self.retry_days,
                pinned=read_enrichment_priority('spotify') or None,
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
        """The Spotify id already stored for this entity, if any."""
        conn = None
        try:
            from core.library2.worker_support import stored_provider_id

            conn = self.db._get_connection()
            return stored_provider_id(conn, entity_type, entity_id, 'spotify')
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

        # Candidates clearing the (stricter, artist-specific) name gate, best
        # name-score first so [0] is the legacy "first/highest" pick.
        scored = [
            (self._name_similarity(artist_name, a.name), a)
            for a in results
        ]
        gated = [a for score, a in sorted(scored, key=lambda s: s[0], reverse=True)
                 if score >= ARTIST_NAME_MATCH_THRESHOLD]

        # Same-name disambiguation: when more than one "Rone" clears the gate,
        # pick the one whose catalog overlaps the albums this library owns.
        conn = self.db._get_connection()
        try:
            _owned = owned_album_titles(conn, artist_id)
        finally:
            conn.close()
        best_obj, _overlap = pick_artist_by_catalog(
            gated,
            _owned,
            lambda a: release_titles(self.client.get_artist_albums(a.id)),
        )
        best_score = self._name_similarity(artist_name, best_obj.name) if best_obj else 0

        if best_obj:
            if not self._is_spotify_id(best_obj.id):
                logger.warning(f"Rejecting non-Spotify ID '{best_obj.id}' for artist '{artist_name}' (iTunes fallback leak)")
                self._mark_status('artist', artist_id, 'error')
                self.stats['errors'] += 1
                return
            # Don't assign a Spotify id another (differently-named) artist
            # already holds — prevents one id smeared across artists.
            conn = self.db._get_connection()
            try:
                conflict = provider_id_conflict(
                    conn, 'spotify', best_obj.id, artist_id, artist_name)
            finally:
                conn.close()
            if conflict:
                self._mark_status('artist', artist_id, 'not_found')
                self.stats['not_found'] += 1
                logger.debug(
                    f"Artist '{artist_name}' -> Spotify {best_obj.id} skipped: "
                    f"already claimed by '{conflict}'"
                )
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

    def _refresh_album_via_stored_id(self, album_id, stored_id, api_album_dict):
        """``honor_stored_match`` callback. Wraps the dict from
        ``client.get_album(stored_id)`` in a SimpleNamespace adapter
        with the attributes ``_update_album`` reads, then calls it.
        Preserves the manual match — never reaches search-by-name."""
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
        """``honor_stored_match`` callback for tracks. The track-level
        update only writes the ID + match status — no metadata
        backfill, so the dict shape is irrelevant beyond carrying the
        stored ID through."""
        adapter = SimpleNamespace(id=api_track_dict.get('id') or stored_id)
        self._update_track_from_search(track_id, adapter)

    def _process_album_individual(self, item: Dict[str, Any]):
        album_id = item['id']
        album_name = item['name']
        artist_name = item.get('artist', '')

        # Issue #501: honor manual matches. If the user has already
        # set spotify_album_id on this album row (via match-chip UI),
        # refresh metadata via that ID and skip search-by-name — which
        # would otherwise overwrite the manual match with whatever
        # name-search returned.
        _stored = honor_stored_match(
            self.db, entity_type='album', entity_id=album_id, service='spotify',
            fetch=self.client.get_album,
            on_match=self._refresh_album_via_stored_id,
            log_prefix='Spotify',
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
        # Pass artist + album names separately too, so the no-creds Spotify Free
        # path can resolve the album via the artist's discography (SpotipyFree has
        # no album-name search) when bridging a budget/rate-limit ban.
        results = self.client.search_albums(query, limit=5, artist=artist_name, album=album_name)

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

        # Issue #501: honor manual matches (see _process_album_individual).
        _stored = honor_stored_match(
            self.db, entity_type='track', entity_id=track_id, service='spotify',
            fetch=self.client.get_track_details,
            on_match=self._refresh_track_via_stored_id,
            log_prefix='Spotify',
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
        """Store Spotify metadata for an album (from Album dataclass)"""
        backfill = {}
        if album_obj.image_url:
            backfill['image_url'] = album_obj.image_url
        if album_obj.release_date:
            year = album_obj.release_date[:4] if len(album_obj.release_date) >= 4 else None
            if year and year.isdigit():
                backfill['year'] = year
            # #824: also store the FULL release date when Spotify has one (YYYY-MM or
            # YYYY-MM-DD, not just a bare year). Backfill only — never clobber a
            # manually-set release_date.
            if len(album_obj.release_date) > 4:
                backfill['release_date'] = album_obj.release_date
        # `record_type` has no lib2 counterpart to backfill: lib2_albums.album_type
        # always carries a classification (the importer and MB reconcile own it,
        # defaulting to 'album'), so there is no empty state to fill. Spotify's word
        # goes in the payload, which loses nothing and overwrites nobody.
        self._write('album', album_id, album_obj.id, backfill=backfill,
                    payload={'album_type': album_obj.album_type},
                    total_tracks=getattr(album_obj, 'total_tracks', 0))

    def _update_track(self, track_id: int, track_data: Dict[str, Any]):
        """Store Spotify metadata for a track (from get_album_tracks dict)"""
        backfill = {}
        if 'explicit' in track_data:
            backfill['explicit'] = 1 if track_data['explicit'] else 0
        self._write('track', track_id, track_data.get('id', ''), backfill=backfill)

    def _update_track_from_search(self, track_id: int, track_obj):
        """Store Spotify metadata for a track (from Track dataclass, individual search)"""
        self._write('track', track_id, track_obj.id)

    def _write(self, entity_type: str, entity_id: int, provider_id,
               backfill: Optional[Dict[str, Any]] = None,
               payload: Optional[Dict[str, Any]] = None,
               total_tracks: Any = None):
        """One write path for all three entity types (docs §32.3.1 stage 2).

        Spotify's id is promoted to a real ``spotify_id`` column as well as
        ``external_ids`` — write_provider_enrichment keeps both in step, and the read
        paths join on the column. Everything else is backfill: artwork, genres, year
        and the explicit flag are all shared with better sources and with the user.
        """
        conn = None
        try:
            from core.library2.provider_attempts import record_attempt
            from core.library2.provider_writes import write_provider_enrichment
            from core.library2.worker_support import set_expected_track_count

            conn = self.db._get_connection()
            write_provider_enrichment(
                conn, entity_type=entity_type, entity_id=entity_id,
                service='spotify',
                payload=payload,
                provider_id=str(provider_id) if provider_id else None,
                backfill=backfill or None,
            )
            if entity_type == 'album':
                # The authoritative expected total for the Album Completeness job.
                set_expected_track_count(conn, entity_id, total_tracks)
            record_attempt(conn, entity_type=entity_type, entity_id=entity_id,
                           service='spotify', status='matched')
            conn.commit()
        except Exception as e:
            logger.error(f"Error updating {entity_type} #{entity_id} with Spotify data: {e}")
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
            return pending_children(conn, 'spotify', 'artist', artist_id,
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
            return pending_children(conn, 'spotify', 'album', album_id,
                                    child='track')
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
            record_children(conn, 'spotify', parent_type, parent_id, status,
                            child=child)
            conn.commit()
        except Exception as e:
            logger.error(f"Error bulk-marking {child}s for {parent_type} "
                         f"#{parent_id}: {e}")
        finally:
            if conn:
                conn.close()

    def _mark_artist_albums_error(self, artist_id: int):
        """Bulk mark unattempted albums for an artist as 'error'"""
        self._record_batch('artist', artist_id, 'error', 'album')

    def _mark_artist_albums_not_found(self, artist_id: int):
        """Bulk mark unattempted albums for an artist as 'not_found'"""
        self._record_batch('artist', artist_id, 'not_found', 'album')

    def _mark_album_tracks_error(self, album_id: int):
        """Bulk mark unattempted tracks for an album as 'error'"""
        self._record_batch('album', album_id, 'error', 'track')

    def _mark_album_tracks_not_found(self, album_id: int):
        """Bulk mark unattempted tracks for an album as 'not_found'"""
        self._record_batch('album', album_id, 'not_found', 'track')

    # ── Status / counting ──────────────────────────────────────────────

    def _mark_status(self, entity_type: str, entity_id: int, status: str):
        """Record the outcome of an attempt in the provider ledger.

        Replaces the legacy `spotify_match_status`/`_last_attempted` column pair.
        Both `not_found` and `error` become due again after the retry
        window; a source-wide outage is handled by the worker's own backoff
        before an attempt is ever recorded, so it cannot become a tight loop.
        """
        conn = None
        try:
            from core.library2.provider_attempts import record_attempt

            conn = self.db._get_connection()
            record_attempt(conn, entity_type=entity_type, entity_id=entity_id,
                           service='spotify', status=status)
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
            return pending_count(conn, 'spotify', retry_after_days=self.retry_days)
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
            return progress_breakdown(conn, 'spotify')
        except Exception as e:
            logger.error(f"Error getting progress breakdown: {e}")
            return {}
        finally:
            if conn:
                conn.close()

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
        if not norm_query or not norm_result:
            raw_query = (query_name or '').strip().lower()
            raw_result = (result_name or '').strip().lower()
            return 1.0 if raw_query and raw_query == raw_result else 0.0
        return SequenceMatcher(None, norm_query, norm_result).ratio()

    def _name_matches(self, query_name: str, result_name: str) -> bool:
        similarity = self._name_similarity(query_name, result_name)
        logger.debug(f"Name similarity: '{query_name}' vs '{result_name}' = {similarity:.2f}")
        return similarity >= self.name_similarity_threshold
