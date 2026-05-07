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
                artist_l = (ev.get('artist') or '').strip().lower()
                if title_l:
                    ev['db_track_id'] = id_map.get((title_l, artist_l))

            inserted = self.db.insert_listening_events(events)
            self.stats['events_added'] += inserted
            logger.info(f"Inserted {inserted} new listening events (of {len(events)} total)")

        # Step 2: Fetch play counts and update tracks table
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
                    'top_artists': self.db.get_top_artists(time_range, 25),
                    'top_albums': self.db.get_top_albums(time_range, 25),
                    'top_tracks': self.db.get_top_tracks(time_range, 25),
                    'timeline': self.db.get_listening_timeline(time_range, granularity),
                    'genres': self.db.get_genre_breakdown(time_range),
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
        """Add image URLs, IDs, and Last.fm data to cached stats items.

        Previously ran one SELECT per artist / album / track entry. Now each
        of the three lists is resolved with a single batched IN query so
        cache rebuilds scale with the number of result sets, not with the
        number of items in them.
        """
        top_artists = cache.get('top_artists') or []
        top_albums = cache.get('top_albums') or []
        top_tracks = cache.get('top_tracks') or []

        if not (top_artists or top_albums or top_tracks):
            return

        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            # ---- top_artists: match by LOWER(name) ----
            if top_artists:
                names = [a.get('name') or '' for a in top_artists]
                unique_names = {n.lower() for n in names if n}
                artist_rows = {}
                if unique_names:
                    name_list = list(unique_names)
                    chunk = 500
                    for i in range(0, len(name_list), chunk):
                        sub = name_list[i:i + chunk]
                        placeholders = ','.join(['?'] * len(sub))
                        cursor.execute(
                            f"""
                            SELECT LOWER(name), thumb_url, id, lastfm_listeners,
                                   lastfm_playcount, soul_id
                            FROM artists
                            WHERE LOWER(name) IN ({placeholders})
                            """,
                            sub,
                        )
                        for row in cursor.fetchall():
                            # Keep first match per lowered name (LIMIT 1 equiv).
                            artist_rows.setdefault(row[0], row)

                for artist in top_artists:
                    key = (artist.get('name') or '').lower()
                    r = artist_rows.get(key)
                    if r:
                        artist['image_url'] = r[1] or None
                        artist['id'] = r[2]
                        artist['global_listeners'] = r[3]
                        artist['global_playcount'] = r[4]
                        artist['soul_id'] = r[5]

            # ---- top_albums: match by LOWER(title) ----
            if top_albums:
                titles = [a.get('name') or '' for a in top_albums]
                unique_titles = {t.lower() for t in titles if t}
                album_rows = {}
                if unique_titles:
                    title_list = list(unique_titles)
                    chunk = 500
                    for i in range(0, len(title_list), chunk):
                        sub = title_list[i:i + chunk]
                        placeholders = ','.join(['?'] * len(sub))
                        cursor.execute(
                            f"""
                            SELECT LOWER(title), thumb_url, id, artist_id
                            FROM albums
                            WHERE LOWER(title) IN ({placeholders})
                            """,
                            sub,
                        )
                        for row in cursor.fetchall():
                            album_rows.setdefault(row[0], row)

                for album in top_albums:
                    key = (album.get('name') or '').lower()
                    r = album_rows.get(key)
                    if r:
                        album['image_url'] = r[1] or None
                        album['id'] = r[2]
                        album['artist_id'] = r[3]

            # ---- top_tracks: match by (LOWER(title), LOWER(artist name)) ----
            if top_tracks:
                pairs = set()
                for t in top_tracks:
                    name = (t.get('name') or '').lower()
                    artist = (t.get('artist') or '').lower()
                    if name:
                        pairs.add((name, artist))
                track_rows = {}
                if pairs:
                    pair_list = list(pairs)
                    chunk = 500
                    for i in range(0, len(pair_list), chunk):
                        sub = pair_list[i:i + chunk]
                        placeholders = ','.join(['(?,?)'] * len(sub))
                        flat = [v for pair in sub for v in pair]
                        cursor.execute(
                            f"""
                            SELECT LOWER(t.title), LOWER(ar.name),
                                   al.thumb_url, t.id, t.artist_id
                            FROM tracks t
                            JOIN albums al ON al.id = t.album_id
                            JOIN artists ar ON ar.id = t.artist_id
                            WHERE (LOWER(t.title), LOWER(ar.name)) IN ({placeholders})
                            """,
                            flat,
                        )
                        for row in cursor.fetchall():
                            track_rows.setdefault((row[0], row[1]), row)

                for track in top_tracks:
                    key = ((track.get('name') or '').lower(),
                           (track.get('artist') or '').lower())
                    r = track_rows.get(key)
                    if r:
                        track['image_url'] = r[2] or None
                        track['id'] = r[3]
                        track['artist_id'] = r[4]
        except Exception as e:
            logger.error(f"Error enriching stats items: {e}")
        finally:
            if conn:
                conn.close()

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
                SELECT t.id FROM tracks t
                JOIN artists ar ON ar.id = t.artist_id
                WHERE LOWER(t.title) = LOWER(?) AND LOWER(ar.name) = LOWER(?)
                LIMIT 1
            """, (title.strip(), (artist or '').strip()))
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
                pairs.add((title.lower(), artist.lower()))

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
                flat_args = [v for pair in chunk for v in pair]
                cursor.execute(
                    f"""
                    SELECT LOWER(t.title), LOWER(ar.name), t.id
                    FROM tracks t
                    JOIN artists ar ON ar.id = t.artist_id
                    WHERE (LOWER(t.title), LOWER(ar.name)) IN ({placeholders})
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
        """Map server track IDs to DB track IDs for play count updates.

        Looks up which server IDs exist in the tracks table. Replaces a
        previous N+1 pattern of one SELECT per server ID with a single
        batched IN query (chunked for safety).
        """
        if not server_counts:
            return []

        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            ids = list(server_counts.keys())
            existing = set()
            chunk_size = 500
            for i in range(0, len(ids), chunk_size):
                chunk = ids[i:i + chunk_size]
                placeholders = ','.join(['?'] * len(chunk))
                cursor.execute(
                    f"SELECT id FROM tracks WHERE id IN ({placeholders})",
                    chunk,
                )
                existing.update(r[0] for r in cursor.fetchall())

            return [
                {
                    'db_track_id': server_id,
                    'play_count': play_count,
                    'last_played': None,  # Could be fetched separately
                }
                for server_id, play_count in server_counts.items()
                if server_id in existing
            ]
        except Exception as e:
            logger.error(f"Error mapping play counts: {e}")
            return []
        finally:
            if conn:
                conn.close()
