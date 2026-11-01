"""
Listening Stats Worker — polls the active media server for play history
and stores it in the local database for the Stats page.

Runs every 30 minutes (configurable). Detects the active server type
(Plex/Jellyfin/Navidrome) and calls the appropriate client methods.
"""

import json
import threading
import time
from typing import Dict, Any

from utils.logging_config import get_logger
from core.listening_scope import SHARED_OWNER, owner_clause
from core.worker_utils import interruptible_sleep


def _name_key(name) -> str:
    """The catalogue's folded artist key (indexed, and not ASCII-only)."""
    from core.library2.importer import normalize_name

    return normalize_name(str(name or ''))

logger = get_logger("listening_stats_worker")


def _play_epoch(played_at):
    """a stored play time (utc) as unix seconds, None when unreadable"""
    from core.listening_import.dedup import canonical_played_at, _timestamp
    canonical = canonical_played_at(played_at)
    return _timestamp(canonical) if canonical else None

SHARED_SCOPE = owner_clause(SHARED_OWNER)


class ListeningStatsWorker:
    """Background worker that polls media servers for play data."""

    def __init__(self, database, config_manager, media_server_engine=None):
        """Initialize the worker.

        ``media_server_engine`` owns the per-server clients (Plex /
        Jellyfin / Navidrome). The worker resolves the active server's
        client through ``self._engine.client(name)`` instead of holding
        per-server kwargs.
        """
        self.db = database
        self.config_manager = config_manager
        self._engine = media_server_engine

        # Worker state
        self.running = False
        self.paused = False
        self.should_stop = False
        self.thread = None
        self.current_item = None
        self._stop_event = threading.Event()

        # Stats
        self.stats = {
            'polls_completed': 0,
            'events_added': 0,
            'tracks_updated': 0,
            'errors': 0,
            'last_poll': None,
        }

        # Config
        self.poll_interval = 30 * 60  # 30 minutes default
        # Outbound scrobbles flush on their own cadence, independent of the
        # stats poll interval (and of the stats toggle entirely).
        self.scrobble_interval = 5 * 60  # 5 minutes

        logger.info("Listening stats worker initialized")

    def start(self):
        if self.running:
            return
        self.running = True
        self.should_stop = False
        self._stop_event.clear()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        logger.info("Listening stats worker started")

    def stop(self):
        if not self.running:
            return
        self.should_stop = True
        self.running = False
        self._stop_event.set()
        if self.thread:
            self.thread.join(timeout=1)
        logger.info("Listening stats worker stopped")

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

    def get_stats(self) -> Dict[str, Any]:
        is_running = self.running and self.thread is not None and self.thread.is_alive()
        return {
            'enabled': True,
            'running': is_running and not self.paused,
            'paused': self.paused,
            'idle': is_running and not self.paused and self.current_item is None,
            'current_item': self.current_item,
            'stats': self.stats.copy(),
        }

    def _run(self):
        logger.info("Listening stats worker thread started")

        # Build cache from existing data immediately (before first poll)
        if interruptible_sleep(self._stop_event, 5):
            return
        try:
            self._build_stats_cache()
            logger.info("Initial stats cache built from existing data")
        except Exception as e:
            logger.debug(f"Initial cache build skipped: {e}")

        if self.should_stop:
            return

        # Wait before first poll
        if interruptible_sleep(self._stop_event, 10):
            return

        # Stats polling and scrobble flushing keep independent due-times: the
        # poll honors the user's configured interval, the flush runs every
        # scrobble_interval, and the loop wakes for whichever comes first.
        # Both start due immediately so a fresh start polls + flushes once.
        next_poll_at = time.monotonic()
        next_scrobble_at = time.monotonic()

        while not self.should_stop:
            try:
                if self.paused:
                    interruptible_sleep(self._stop_event, 5)
                    continue

                now = time.monotonic()

                # Media-server history polling. This is the ONLY thing the
                # stats toggle gates.
                stats_enabled = self.config_manager.get('listening_stats.enabled', True)
                if stats_enabled:
                    # Update poll interval from config
                    self.poll_interval = self.config_manager.get('listening_stats.poll_interval', 30) * 60
                    if now >= next_poll_at:
                        self._poll()
                        self.stats['polls_completed'] += 1
                        self.stats['last_poll'] = time.strftime('%Y-%m-%d %H:%M:%S')
                        self.current_item = None
                        next_poll_at = now + self.poll_interval

                # Outbound scrobbling runs regardless of the stats toggle.
                # _scrobble_new_events self-gates on each service's own
                # scrobble_enabled + credentials, so with nothing configured
                # this is a cheap no-op.
                if now >= next_scrobble_at:
                    self.current_item = "Scrobbling to external services..."
                    self._scrobble_new_events()
                    self.current_item = None
                    next_scrobble_at = now + self.scrobble_interval

                # Sleep until the next due time. interruptible_sleep wakes
                # promptly on stop; the loop re-evaluates pause/due-times.
                wake_in = next_scrobble_at - now
                if stats_enabled:
                    wake_in = min(wake_in, next_poll_at - now)
                interruptible_sleep(self._stop_event, max(0, wake_in))

            except Exception as e:
                logger.error(f"Error in listening stats worker: {e}", exc_info=True)
                self.stats['errors'] += 1
                interruptible_sleep(self._stop_event, 60)

        self.current_item = None
        logger.info("Listening stats worker thread finished")

    _TAGGED_KEY = 'listening_plays_account_tagged_{server}'

    def _tag_old_plays_once(self, client, server):
        """rows recorded before plays kept their account don't say who played
        them, so they all sat in the shared pile. read the server's full
        history once and tag each row with its account; re-filing then moves
        them. plex only (the only server whose history names the account).
        retried next poll if it fails; never twice once done"""
        if server != 'plex':
            return
        key = self._TAGGED_KEY.format(server=server)
        try:
            if self.db.get_metadata(key):
                return
            history = client.get_play_history(limit=None)
            if not history:
                return
            tags = [(e['account_id'], e['track_id'], e['played_at']) for e in history
                    if e.get('account_id') and e.get('track_id') and e.get('played_at')]
            conn = self.db._get_connection()
            try:
                tagged = 0
                for account_id, track_id, played_at in tags:
                    cur = conn.execute(
                        "UPDATE listening_history SET server_account_id = ? "
                        "WHERE server_source = ? AND track_id = ? AND played_at = ? "
                        "AND server_account_id IS NULL",
                        (account_id, server, track_id, played_at))
                    tagged += cur.rowcount
                conn.commit()
            finally:
                conn.close()
            self.db.set_metadata(key, '1')
            logger.info(f"Tagged {tagged} earlier {server} plays with the account that played them")
        except Exception as e:
            logger.warning(f"Tagging earlier {server} plays by account failed, will retry: {e}")

    _JELLYFIN_SEEDED_KEY = 'listening_jellyfin_seeded_{user}'
    _JELLYFIN_SEED_LIMIT = 5000

    def _jellyfin_history_by_user(self, client, history):
        """jellyfin answers history per user, and the app reads as one user.
        tag those plays with that user and add each linked profile's own
        user's plays, so a profile's plays land in its pile (Phonk_Ben_67:
        per-profile stats from jellyfin). a user read for the first time is
        read wider, so their pile doesn't start empty."""
        from core.listening_scope import JELLYFIN_OWNER_KEY
        app_user = getattr(client, 'user_id', None)
        if not app_user:
            return history
        app_user = str(app_user)
        try:
            self.db.set_metadata(JELLYFIN_OWNER_KEY, app_user)
        except Exception as e:  # noqa: BLE001 - the next poll records it
            logger.debug("could not record the jellyfin owner user: %s", e)
        out = [dict(e, account_id=app_user) for e in history or []]
        for user in self._linked_jellyfin_users(app_user):
            seeded_key = self._JELLYFIN_SEEDED_KEY.format(user=user)
            try:
                seeded = bool(self.db.get_metadata(seeded_key))
                plays = client.as_user(user).get_play_history(
                    limit=500 if seeded else self._JELLYFIN_SEED_LIMIT)
            except Exception as e:  # noqa: BLE001 - one user failing leaves the rest
                logger.warning(f"Reading jellyfin plays for user {user} failed: {e}")
                continue
            out.extend(dict(e, account_id=user) for e in plays or [])
            if not seeded:
                self.db.set_metadata(seeded_key, '1')
        return out

    def _linked_jellyfin_users(self, app_user):
        """the jellyfin users profiles are linked to, other than the app's own."""
        conn = None
        try:
            conn = self.db._get_connection()
            cols = {r[1] for r in conn.execute("PRAGMA table_info(profiles)").fetchall()}
            if 'jellyfin_user_id' not in cols:
                return []
            rows = conn.execute(
                "SELECT DISTINCT jellyfin_user_id FROM profiles WHERE jellyfin_user_id IS NOT NULL "
                "AND jellyfin_user_id != '' AND jellyfin_user_id != ?", (app_user,)).fetchall()
            return [str(r[0]) for r in rows]
        except Exception as e:  # noqa: BLE001
            logger.debug("linked jellyfin users lookup failed: %s", e)
            return []
        finally:
            if conn is not None:
                conn.close()

    def _forget_jellyfin_owner(self):
        """not reading jellyfin: a jellyfin link owns no pile (its pile would
        get nothing)"""
        try:
            from core.listening_scope import JELLYFIN_OWNER_KEY
            if self.db.get_metadata(JELLYFIN_OWNER_KEY):
                self.db.set_metadata(JELLYFIN_OWNER_KEY, '')
        except Exception as e:  # noqa: BLE001
            logger.debug("could not clear the jellyfin owner user: %s", e)

    def _repair_history_once(self):
        try:
            from core.listening_import.history_repair import repair_once
            repair_once(self.db)
        except Exception as e:
            logger.warning(f"Listening history repair failed, will retry next poll: {e}")

    def _poll(self):
        """Poll the active media server for play data."""
        active_server = self.config_manager.get_active_media_server()
        logger.info(f"Polling {active_server} for listening data...")
        self.current_item = f"Polling {active_server}..."

        # once: put every stored play on one clock and fold the last.fm /
        # listenbrainz copies of soulsync's own scrobbles back into their
        # plays. before this poll inserts, so new plays meet repaired rows
        self._repair_history_once()

        client = self._engine.client(active_server) if self._engine else None
        # Only a server that declares play history contributes (the
        # standalone library and, for now, Music Assistant do not).
        from core.media_server.contract import Capability
        supports = getattr(self._engine, 'supports', None)
        has_history = (supports(active_server, Capability.PLAY_HISTORY) if callable(supports)
                       else active_server in ('plex', 'jellyfin', 'navidrome'))  # an engine stub without the query
        if not has_history:
            client = None

        if not client:
            logger.warning(f"No client available for active server: {active_server}")
            return

        # Step 1: Fetch play history
        self.current_item = f"Fetching play history from {active_server}..."
        try:
            history = client.get_play_history(limit=500)
        except Exception as e:
            logger.error(f"Failed to fetch play history from {active_server}: {e}")
            self.stats['errors'] += 1
            return
        if active_server == 'jellyfin':
            history = self._jellyfin_history_by_user(client, history)
        else:
            self._forget_jellyfin_owner()

        if history:
            # each play goes in the pile of whoever played it: the owner's
            # plays are the shared pile, a linked profile's are its own, an
            # account linked to nobody is kept but claimed by no one
            from core.listening_scope import media_account_owner, media_account_owners
            account_owners = media_account_owners(self.db, active_server)
            # Convert to DB format
            events = []
            for entry in history:
                if not entry.get('played_at'):
                    continue
                account_id = entry.get('account_id')
                events.append({
                    'track_id': entry.get('track_id', ''),
                    'title': entry.get('track_title', ''),
                    'artist': entry.get('artist', ''),
                    'album': entry.get('album', ''),
                    'played_at': entry.get('played_at'),
                    'duration_ms': entry.get('duration_ms', 0),
                    'server_source': active_server,
                    # no account (jellyfin, navidrome: the app's own account
                    # only) is the shared pile, as before (#1293)
                    'profile_id': media_account_owner(account_owners, account_id),
                    'server_account_id': account_id,
                    # db_track_id filled in below by a single batched lookup
                    'db_track_id': None,
                    # the server attests its own plays: scrobble-eligible
                    'scrobble_eligible': 1,
                })

            # Batch-resolve track IDs for all events at once (was N+1 before).
            id_map = self._resolve_db_track_ids_batch(events)
            for ev in events:
                title_l = (ev.get('title') or '').strip().lower()
                artist_l = _name_key((ev.get('artist') or '').strip())
                if title_l:
                    ev['lib2_track_id'] = id_map.get((title_l, artist_l))

            inserted = self.db.insert_listening_events(events)
            self.stats['events_added'] += inserted
            logger.info(f"Inserted {inserted} new listening events (of {len(events)} total)")

        # plays recorded before accounts were kept: tag them once from the
        # server's full history, then re-file anything whose account's pile
        # changed (a profile linked or unlinked since)
        self._tag_old_plays_once(client, active_server)
        try:
            from core.listening_scope import reattribute_media_plays
            reattribute_media_plays(self.db, active_server)
        except Exception as e:
            logger.warning(f"Re-filing plays by account failed: {e}")

        # Step 2: Fetch play counts and record them per track
        self.current_item = f"Updating play counts from {active_server}..."
        try:
            server_counts = client.get_track_play_counts()
        except Exception as e:
            logger.error(f"Failed to fetch play counts from {active_server}: {e}")
            self.stats['errors'] += 1
            return

        if server_counts:
            # Map server track IDs to DB track IDs and update
            updates = self._map_play_counts_to_db(server_counts, active_server)
            if updates:
                self.db.update_track_play_counts(updates)
                self.stats['tracks_updated'] += len(updates)
                logger.info(f"Updated play counts for {len(updates)} tracks")

        # Step 2b: Per-user curation signals (favourites / ratings / playlist
        # membership) for the Expired Download Cleaner.
        #
        # GATED, and that matters: this poll runs on virtually every install
        # (listening_stats.enabled defaults on), while the cleaner is an opt-in
        # repair job that ships disabled. Without the gate every user would pay
        # per-user server scans every 30 minutes to feed a feature they never
        # turned on. curation_sweep_due() also throttles to a few hours —
        # retention windows are weeks, so half-hourly freshness buys nothing.
        #
        # Wrapped so a curation failure can never take down play counts or
        # scrobbling, and a failed sweep deliberately leaves the previous
        # signals and stamp alone: that ages out and makes the cleaner keep
        # everything rather than delete it.
        try:
            from core.library.curation_sync import (
                curation_sweep_due,
                navidrome_user_credentials,
                sync_curation_signals,
            )
            if curation_sweep_due(self.config_manager, self.db):
                self.current_item = f"Reading curation signals from {active_server}..."
                per_user = {}
                if active_server == 'navidrome':
                    # Subsonic can only report one user's stars per credential.
                    per_user['navidrome'] = navidrome_user_credentials(self.db)
                sync_curation_signals(self.db, {active_server: client},
                                      user_credentials=per_user)
        except Exception as e:
            logger.warning(f"Curation signal sync failed for {active_server}: {e}")

        # Step 3: Pre-compute stats cache for all time ranges
        # (scrobbling moved out: it runs in _run() on its own cadence,
        # independent of the stats toggle.)
        self.current_item = "Building stats cache..."
        self._build_stats_cache()

    def _build_stats_cache(self, owners=None):
        """Pre-compute stats for all time ranges, enrich with images/IDs, and store.

        one cache per pile (#1293). owners narrows it, a profile's own import
        only needs its own pile rebuilt."""
        from core.listening_scope import listening_owners
        try:
            for owner in (owners if owners is not None else listening_owners(self.db)):
                # one pile failing mustn't leave everyone else's stale
                try:
                    self._build_owner_stats_cache(owner)
                except Exception as e:
                    logger.error(f"Failed to build stats cache for pile {owner}: {e}")

            health = self.db.get_library_health()
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
                ('stats_cache_health', json.dumps(health))
            )
            conn.commit()
            conn.close()

            logger.info("Stats cache rebuilt for all time ranges")
        except Exception as e:
            logger.error(f"Failed to build stats cache: {e}")

    def _build_owner_stats_cache(self, owner):
        """every cached stat for one pile. the shared pile keeps the old keys."""
        from core.listening_scope import STATS_CACHE_RANGES, owner_clause, owner_key
        for time_range in STATS_CACHE_RANGES:
            granularity = 'month' if time_range in ('12m', 'all') else 'day'
            cache = {
                'overview': self.db.get_listening_stats(time_range, profile_id=owner),
                # The same aggregate over the window immediately before this
                # one, so the page can say "vs last month" instead of
                # printing a total that stands alone. None for 'all' —
                # there is no period before everything, and the UI omits the
                # comparison rather than inventing a zero to beat.
                'previous': self.db.get_listening_stats_previous(time_range, profile_id=owner),
                'top_artists': self.db.get_top_artists(time_range, 25, profile_id=owner),
                'top_albums': self.db.get_top_albums(time_range, 25, profile_id=owner),
                'top_tracks': self.db.get_top_tracks(time_range, 25, profile_id=owner),
                'timeline': self.db.get_listening_timeline(time_range, granularity, profile_id=owner),
                'genres': self.db.get_genre_breakdown(time_range, profile_id=owner),
                # When you listen, and whether you keep listening — the
                # first stats that are about a person rather than a total.
                'clock': self.db.get_listening_clock(time_range, profile_id=owner),
                'rhythm': self.db.get_listening_rhythm(time_range, profile_id=owner),
                # The one thing only SoulSync can answer: what you own
                # against what you actually play.
                'own_vs_play': self.db.get_genre_own_vs_play(time_range, profile_id=owner),
                'neglected': self.db.get_neglected_albums(),
            }

            # Enrich with images/IDs so the endpoint doesn't have to
            self._enrich_stats_items(cache)

            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
                (owner_key(f'stats_cache_{time_range}', owner), json.dumps(cache))
            )
            conn.commit()
            conn.close()

        # Cache recent plays separately
        conn = self.db._get_connection()
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT title, artist, album, played_at, duration_ms
            FROM listening_history WHERE {owner_clause(owner)}
            ORDER BY played_at DESC LIMIT 20
        """)
        recent = [{'title': r[0], 'artist': r[1], 'album': r[2], 'played_at': r[3], 'duration_ms': r[4]}
                  for r in cursor.fetchall()]
        cursor.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
            (owner_key('stats_cache_recent', owner), json.dumps(recent))
        )
        conn.commit()
        conn.close()

        # Year in Listening — cached ONCE, not per range. It is a fixed
        # period rather than a filter, so it has no business inside the
        # per-range loop: four identical copies under four keys would be
        # four chances for them to disagree after a partial rebuild.
        year = self.db.get_year_in_listening(profile_id=owner)
        # Same enrichment the per-range caches get. Without it the story
        # renders name-only — and this surface is carried by its artwork.
        self._enrich_stats_items(year)
        conn = self.db._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
            (owner_key('stats_cache_year', owner), json.dumps(year))
        )
        conn.commit()
        conn.close()

    def _enrich_stats_items(self, cache):
        """Delegates to the shared enricher — see core/stats/enrich.py.

        Kept as a method because it is part of this class's tested surface;
        the BODY moved so the Year in Listening endpoint can reuse it on its
        live-compute path."""
        from core.stats.enrich import enrich_stats_items
        enrich_stats_items(self.db, cache)

    def _scrobble_new_events(self):
        """Scrobble unscrobbled listening events to ListenBrainz and Last.fm.

        each pile goes to its own accounts. the shared pile goes to the
        admin's (Settings); a profile's own pile goes to the accounts that
        profile connected for scrobbling, from when it turned that on
        (Phonk_Ben_67). a profile's pile never reaches the admin's accounts:
        it is someone else's listening (#1293).

        only scrobble_eligible rows go out: media-server and import rows are
        eligible on insert (the source attests the play), while web-player
        rows become eligible only once the player reports the Last.fm
        threshold was reached. a 10s web-player log is a "play" for stats,
        not a scrobble."""
        try:
            lb_token = (self.config_manager.get('listenbrainz.token', '')
                        if self.config_manager.get('listenbrainz.scrobble_enabled', False) else '')
            lastfm = None
            if self.config_manager.get('lastfm.scrobble_enabled', False):
                session_key = self.config_manager.get('lastfm.session_key', '')
                if session_key:
                    lastfm = session_key
            self._scrobble_pile(SHARED_SCOPE, lb_token=lb_token or None, lb_base_url=None,
                                lastfm_session=lastfm, label='shared')
        except Exception as e:
            logger.error(f"Scrobble error: {e}")
        try:
            profiles = self.db.profiles_scrobbling()
        except Exception as e:  # noqa: BLE001 - the shared pile already went out
            logger.debug(f"Listing scrobbling profiles failed: {e}")
            profiles = []
        from core.listening_scope import listening_owner
        for prof in profiles:
            pid = prof['profile_id']
            # only a profile reading its own pile has plays that are its own
            if listening_owner(self.db, pid) != pid:
                continue
            try:
                self._scrobble_pile(
                    owner_clause(pid), lb_token=prof.get('listenbrainz_token'),
                    lb_base_url=prof.get('listenbrainz_base_url'),
                    lb_since=prof.get('listenbrainz_since'),
                    lastfm_session=prof.get('lastfm_session'), lastfm_since=prof.get('lastfm_since'),
                    label=f'profile {pid}')
            except Exception as e:  # noqa: BLE001 - one profile failing leaves the rest
                logger.error(f"Scrobble error for profile {pid}: {e}")

    def _unscrobbled(self, flag, scope, since, limit):
        conn = self.db._get_connection()
        try:
            sql = (f"SELECT id, title, artist, album, played_at FROM listening_history "
                   f"WHERE {flag} = 0 AND scrobble_eligible = 1 AND {scope}")
            params = []
            if since:
                sql += " AND played_at >= ?"
                params.append(since)
            sql += " ORDER BY played_at ASC LIMIT ?"
            params.append(limit)
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def _mark_scrobbled(self, flag, ids):
        if not ids:
            return
        conn = self.db._get_connection()
        try:
            placeholders = ','.join(['?'] * len(ids))
            conn.execute(f"UPDATE listening_history SET {flag} = 1 WHERE id IN ({placeholders})", list(ids))
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _as_listens(rows):
        return [{
            'artist': r[2] or '',
            'track': r[1] or '',
            'album': r[3] or '',
            # stored utc; the clients read a bare time as local
            'timestamp': _play_epoch(r[4]),
        } for r in rows if _play_epoch(r[4])]

    def _scrobble_pile(self, scope, *, lb_token=None, lb_base_url=None, lb_since=None,
                       lastfm_session=None, lastfm_since=None, label=''):
        """one pile's unscrobbled plays to the given accounts (either may be None)."""
        if lb_token:
            rows = self._unscrobbled('scrobbled_listenbrainz', scope, lb_since, 500)
            if rows:
                try:
                    from core.listenbrainz_client import ListenBrainzClient
                    # a profile on its own listenbrainz server names it; the admin's comes from config
                    lb_client = (ListenBrainzClient(token=lb_token, base_url=lb_base_url) if lb_base_url
                                 else ListenBrainzClient(token=lb_token))
                    if lb_client.is_authenticated() and lb_client.submit_listens(self._as_listens(rows)):
                        self._mark_scrobbled('scrobbled_listenbrainz', [r[0] for r in rows])
                        logger.info(f"Scrobbled {len(rows)} events to ListenBrainz ({label})")
                except Exception as e:
                    logger.debug(f"ListenBrainz scrobble failed ({label}): {e}")

        if lastfm_session:
            api_key = self.config_manager.get('lastfm.api_key', '')
            api_secret = self.config_manager.get('lastfm.api_secret', '')
            if not (api_key and api_secret):
                return
            rows = self._unscrobbled('scrobbled_lastfm', scope, lastfm_since, 200)
            if rows:
                try:
                    from core.lastfm_client import LastFMClient
                    lfm_client = LastFMClient(api_key=api_key, api_secret=api_secret,
                                              session_key=lastfm_session)
                    done = []
                    # Process in batches of 50 (Last.fm limit)
                    for i in range(0, len(rows), 50):
                        batch = rows[i:i + 50]
                        if lfm_client.scrobble_tracks(self._as_listens(batch)):
                            done.extend(r[0] for r in batch)
                    self._mark_scrobbled('scrobbled_lastfm', done)
                    if done:
                        logger.info(f"Scrobbled {len(done)} events to Last.fm ({label})")
                except Exception as e:
                    logger.debug(f"Last.fm scrobble failed ({label}): {e}")

    def _resolve_db_track_id(self, title, artist):
        """Try to match a server track to a local DB track by title+artist."""
        if not title:
            return None
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT t.id FROM lib2_tracks t
                JOIN lib2_albums al ON al.id = t.album_id
                JOIN lib2_artists ar ON ar.id = al.primary_artist_id
                WHERE LOWER(t.title) = LOWER(?) AND ar.name_key = ?
                LIMIT 1
            """, (title.strip(), _name_key(artist)))
            row = cursor.fetchone()
            return row[0] if row else None
        except Exception:
            return None
        finally:
            if conn:
                conn.close()

    def _resolve_db_track_ids_batch(self, events):
        """Batch-resolve DB track IDs for a list of history events.

        Returns a dict ``{(title_lower, artist_lower): track_id}`` so callers
        can look up without another DB round-trip. Replaces the former N+1
        pattern of one SELECT per event (500 events = 500 queries).

        Uses row-value IN with chunking (500 pairs = 1000 variables, well
        under SQLite's default limit). Case-insensitive matching is preserved.
        """
        pairs = set()
        for ev in events:
            title = (ev.get('title') or '').strip()
            artist = (ev.get('artist') or '').strip()
            if title:
                pairs.add((title.lower(), _name_key(artist)))

        result = {}
        if not pairs:
            return result

        pair_list = list(pairs)
        chunk_size = 500

        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            for i in range(0, len(pair_list), chunk_size):
                chunk = pair_list[i:i + chunk_size]
                placeholders = ','.join(['(?,?)'] * len(chunk))
                # The artist half is matched on the indexed, accent-preserving
                # fold `name_key`; SQLite's LOWER() is ASCII-only (iss29-D13).
                flat_args = [v for pair in chunk for v in pair]
                cursor.execute(
                    f"""
                    SELECT LOWER(t.title), ar.name_key, t.id
                    FROM lib2_tracks t
                    JOIN lib2_albums al ON al.id = t.album_id
                    JOIN lib2_artists ar ON ar.id = al.primary_artist_id
                    WHERE (LOWER(t.title), ar.name_key) IN ({placeholders})
                    """,
                    flat_args,
                )
                for title_l, artist_l, tid in cursor.fetchall():
                    # Keep first match per pair to match the LIMIT 1 semantics
                    # of the original per-event query.
                    result.setdefault((title_l, artist_l), tid)
        except Exception as e:
            logger.error(f"Error batch-resolving track IDs: {e}")
        finally:
            if conn:
                conn.close()

        return result

    def _map_play_counts_to_db(self, server_counts, server_source):
        """Map server track ids onto catalogue rows for play-count updates.

        The counts arrive keyed by the media server's own id. Library v2 keeps
        that identity in the server-scoped mapping table; the singular track
        columns are only an upgrade fallback.
        Rows the catalogue has not been told about yet are skipped, exactly as
        before.
        """
        if not server_counts:
            return []

        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            ids = [str(i) for i in server_counts.keys()]
            by_server_id = {}
            chunk_size = 500
            for i in range(0, len(ids), chunk_size):
                chunk = ids[i:i + chunk_size]
                placeholders = ','.join(['?'] * len(chunk))
                # Mapping first, snapshot only for what it does not answer. A
                # UNION has no defined row order — SQLite sorts it, so the lower
                # entity id won, not the authoritative row — and after a
                # re-match the stale snapshot is usually the older, lower id.
                # Play counts landed on the wrong track.
                cursor.execute(
                    f"SELECT m.server_id, m.entity_id FROM lib2_media_server_mappings m "
                    f"WHERE m.entity_type='track' AND m.server_source=? "
                    f"AND m.server_id IN ({placeholders})",
                    [server_source, *chunk],
                )
                for server_id, track_id in cursor.fetchall():
                    by_server_id[str(server_id)] = track_id
                cursor.execute(
                    f"SELECT t.server_id, t.id FROM lib2_tracks t "
                    f"WHERE t.server_source=? AND t.server_id IN ({placeholders})",
                    [server_source, *chunk],
                )
                for server_id, track_id in cursor.fetchall():
                    by_server_id.setdefault(str(server_id), track_id)

            return [
                {
                    'db_track_id': server_id,
                    'lib2_track_id': by_server_id[str(server_id)],
                    'play_count': play_count,
                    'last_played': None,  # Could be fetched separately
                }
                for server_id, play_count in server_counts.items()
                if str(server_id) in by_server_id
            ]
        except Exception as e:
            logger.error(f"Error mapping play counts: {e}")
            return []
        finally:
            if conn:
                conn.close()
