"""
Listening Stats Worker — polls the active media server for play history
and stores it in the local database for the Stats page.

Runs every 30 minutes (configurable). Detects the active server type
(Plex/Jellyfin/Navidrome) and calls the appropriate client methods.
"""

import threading
import time
from typing import Dict, Any

from utils.logging_config import get_logger
from core.worker_utils import interruptible_sleep


def _name_key(name) -> str:
    """The catalogue's folded artist key (indexed, and not ASCII-only)."""
    from core.library2.importer import normalize_name

    return normalize_name(str(name or ''))

logger = get_logger("listening_stats_worker")


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

        while not self.should_stop:
            try:
                if self.paused:
                    interruptible_sleep(self._stop_event, 5)
                    continue

                # Check if enabled
                if not self.config_manager.get('listening_stats.enabled', True):
                    interruptible_sleep(self._stop_event, 30)
                    continue

                # Update poll interval from config
                self.poll_interval = self.config_manager.get('listening_stats.poll_interval', 30) * 60

                self._poll()
                self.stats['polls_completed'] += 1
                self.stats['last_poll'] = time.strftime('%Y-%m-%d %H:%M:%S')
                self.current_item = None

                # Sleep until next poll
                for _ in range(int(self.poll_interval)):
                    if self.should_stop:
                        break
                    if interruptible_sleep(self._stop_event, 1):
                        break

            except Exception as e:
                logger.error(f"Error in listening stats worker: {e}", exc_info=True)
                self.stats['errors'] += 1
                interruptible_sleep(self._stop_event, 60)

        self.current_item = None
        logger.info("Listening stats worker thread finished")

    def _poll(self):
        """Poll the active media server for play data."""
        active_server = self.config_manager.get_active_media_server()
        logger.info(f"Polling {active_server} for listening data...")
        self.current_item = f"Polling {active_server}..."

        client = self._engine.client(active_server) if self._engine else None
        # SoulSync standalone has no listening data; only the three
        # streaming servers contribute. Mirror the legacy guard here.
        if active_server not in ('plex', 'jellyfin', 'navidrome'):
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

        if history:
            # Convert to DB format
            events = []
            for entry in history:
                if not entry.get('played_at'):
                    continue
                events.append({
                    'track_id': entry.get('track_id', ''),
                    'title': entry.get('track_title', ''),
                    'artist': entry.get('artist', ''),
                    'album': entry.get('album', ''),
                    'played_at': entry.get('played_at'),
                    'duration_ms': entry.get('duration_ms', 0),
                    'server_source': active_server,
                    # db_track_id filled in below by a single batched lookup
                    'db_track_id': None,
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

        # Step 3: Scrobble new events to ListenBrainz and Last.fm
        self.current_item = "Scrobbling to external services..."
        self._scrobble_new_events()

        # Step 4: Pre-compute stats cache for all time ranges
        self.current_item = "Building stats cache..."
        self._build_stats_cache()

    def _build_stats_cache(self):
        """Pre-compute stats for all time ranges, enrich with images/IDs, and store."""
        import json
        try:
            for time_range in ('7d', '30d', '12m', 'all'):
                granularity = 'month' if time_range in ('12m', 'all') else 'day'
                cache = {
                    'overview': self.db.get_listening_stats(time_range),
                    # The same aggregate over the window immediately before this
                    # one, so the page can say "vs last month" instead of
                    # printing a total that stands alone. None for 'all' —
                    # there is no period before everything, and the UI omits the
                    # comparison rather than inventing a zero to beat.
                    'previous': self.db.get_listening_stats_previous(time_range),
                    'top_artists': self.db.get_top_artists(time_range, 25),
                    'top_albums': self.db.get_top_albums(time_range, 25),
                    'top_tracks': self.db.get_top_tracks(time_range, 25),
                    'timeline': self.db.get_listening_timeline(time_range, granularity),
                    'genres': self.db.get_genre_breakdown(time_range),
                    # When you listen, and whether you keep listening — the
                    # first stats that are about a person rather than a total.
                    'clock': self.db.get_listening_clock(time_range),
                    'rhythm': self.db.get_listening_rhythm(time_range),
                    # The one thing only SoulSync can answer: what you own
                    # against what you actually play.
                    'own_vs_play': self.db.get_genre_own_vs_play(time_range),
                    'neglected': self.db.get_neglected_albums(),
                }

                # Enrich with images/IDs so the endpoint doesn't have to
                self._enrich_stats_items(cache)

                conn = self.db._get_connection()
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
                    (f'stats_cache_{time_range}', json.dumps(cache))
                )
                conn.commit()
                conn.close()

            # Cache recent plays and library health separately
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT title, artist, album, played_at, duration_ms
                FROM listening_history ORDER BY played_at DESC LIMIT 20
            """)
            recent = [{'title': r[0], 'artist': r[1], 'album': r[2], 'played_at': r[3], 'duration_ms': r[4]}
                      for r in cursor.fetchall()]
            cursor.execute(
                "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
                ('stats_cache_recent', json.dumps(recent))
            )

            # Year in Listening — cached ONCE, not per range. It is a fixed
            # period rather than a filter, so it has no business inside the
            # per-range loop: four identical copies under four keys would be
            # four chances for them to disagree after a partial rebuild.
            year = self.db.get_year_in_listening()
            # Same enrichment the per-range caches get. Without it the story
            # renders name-only — and this surface is carried by its artwork.
            self._enrich_stats_items(year)
            cursor.execute(
                "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
                ('stats_cache_year', json.dumps(year))
            )

            health = self.db.get_library_health()
            cursor.execute(
                "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
                ('stats_cache_health', json.dumps(health))
            )
            conn.commit()
            conn.close()

            logger.info("Stats cache rebuilt for all time ranges")
        except Exception as e:
            logger.error(f"Failed to build stats cache: {e}")

    def _enrich_stats_items(self, cache):
        """Delegates to the shared enricher — see core/stats/enrich.py.

        Kept as a method because it is part of this class's tested surface;
        the BODY moved so the Year in Listening endpoint can reuse it on its
        live-compute path."""
        from core.stats.enrich import enrich_stats_items
        enrich_stats_items(self.db, cache)

    def _scrobble_new_events(self):
        """Scrobble unscrobbled listening events to ListenBrainz and Last.fm."""
        conn = None
        try:
            # ListenBrainz scrobbling
            if self.config_manager.get('listenbrainz.scrobble_enabled', False):
                lb_token = self.config_manager.get('listenbrainz.token', '')
                if lb_token:
                    conn = self.db._get_connection()
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT id, title, artist, album, played_at
                        FROM listening_history
                        WHERE scrobbled_listenbrainz = 0
                        ORDER BY played_at ASC
                        LIMIT 500
                    """)
                    rows = cursor.fetchall()
                    conn.close()
                    conn = None

                    if rows:
                        try:
                            from core.listenbrainz_client import ListenBrainzClient
                            lb_client = ListenBrainzClient(token=lb_token)
                            if lb_client.is_authenticated():
                                listens = [{
                                    'artist': r[2] or '',
                                    'track': r[1] or '',
                                    'album': r[3] or '',
                                    'timestamp': r[4],
                                } for r in rows]

                                if lb_client.submit_listens(listens):
                                    # Mark as scrobbled
                                    ids = [r[0] for r in rows]
                                    conn = self.db._get_connection()
                                    cursor = conn.cursor()
                                    placeholders = ','.join(['?'] * len(ids))
                                    cursor.execute(f"UPDATE listening_history SET scrobbled_listenbrainz = 1 WHERE id IN ({placeholders})", ids)
                                    conn.commit()
                                    conn.close()
                                    conn = None
                                    logger.info(f"Scrobbled {len(ids)} events to ListenBrainz")
                        except Exception as e:
                            logger.debug(f"ListenBrainz scrobble failed: {e}")

            # Last.fm scrobbling
            if self.config_manager.get('lastfm.scrobble_enabled', False):
                api_key = self.config_manager.get('lastfm.api_key', '')
                api_secret = self.config_manager.get('lastfm.api_secret', '')
                session_key = self.config_manager.get('lastfm.session_key', '')
                if api_key and api_secret and session_key:
                    conn = self.db._get_connection()
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT id, title, artist, album, played_at
                        FROM listening_history
                        WHERE scrobbled_lastfm = 0
                        ORDER BY played_at ASC
                        LIMIT 200
                    """)
                    rows = cursor.fetchall()
                    conn.close()
                    conn = None

                    if rows:
                        try:
                            from core.lastfm_client import LastFMClient
                            lfm_client = LastFMClient(api_key=api_key, api_secret=api_secret, session_key=session_key)

                            # Process in batches of 50 (Last.fm limit)
                            all_scrobbled_ids = []
                            for i in range(0, len(rows), 50):
                                batch = rows[i:i + 50]
                                tracks = [{
                                    'artist': r[2] or '',
                                    'track': r[1] or '',
                                    'album': r[3] or '',
                                    'timestamp': r[4],
                                } for r in batch]

                                if lfm_client.scrobble_tracks(tracks):
                                    all_scrobbled_ids.extend(r[0] for r in batch)

                            if all_scrobbled_ids:
                                conn = self.db._get_connection()
                                cursor = conn.cursor()
                                placeholders = ','.join(['?'] * len(all_scrobbled_ids))
                                cursor.execute(f"UPDATE listening_history SET scrobbled_lastfm = 1 WHERE id IN ({placeholders})", all_scrobbled_ids)
                                conn.commit()
                                conn.close()
                                conn = None
                                logger.info(f"Scrobbled {len(all_scrobbled_ids)} events to Last.fm")
                        except Exception as e:
                            logger.debug(f"Last.fm scrobble failed: {e}")

        except Exception as e:
            logger.error(f"Scrobble error: {e}")
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:  # noqa: S110 — finally-block cleanup, logger may be torn down
                    pass

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
