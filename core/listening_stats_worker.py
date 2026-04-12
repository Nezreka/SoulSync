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

    def __init__(self, database, config_manager, plex_client=None,
                 jellyfin_client=None, navidrome_client=None):
        self.db = database
        self.config_manager = config_manager
        self.plex_client = plex_client
        self.jellyfin_client = jellyfin_client
        self.navidrome_client = navidrome_client

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

        client = None
        if active_server == 'plex' and self.plex_client:
            client = self.plex_client
        elif active_server == 'jellyfin' and self.jellyfin_client:
            client = self.jellyfin_client
        elif active_server == 'navidrome' and self.navidrome_client:
            client = self.navidrome_client

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
                    'db_track_id': self._resolve_db_track_id(
                        entry.get('track_title', ''),
                        entry.get('artist', '')
                    ),
                })

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
        """Add image URLs, IDs, and Last.fm data to cached stats items."""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            for artist in (cache.get('top_artists') or []):
                try:
                    cursor.execute("""
                        SELECT thumb_url, id, lastfm_listeners, lastfm_playcount, soul_id
                        FROM artists WHERE LOWER(name) = LOWER(?) LIMIT 1
                    """, (artist['name'],))
                    r = cursor.fetchone()
                    if r:
                        artist['image_url'] = r[0] or None
                        artist['id'] = r[1]
                        artist['global_listeners'] = r[2]
                        artist['global_playcount'] = r[3]
                        artist['soul_id'] = r[4]
                except Exception:
                    pass

            for album in (cache.get('top_albums') or []):
                try:
                    cursor.execute("""
                        SELECT al.thumb_url, al.id, al.artist_id FROM albums al
                        WHERE LOWER(al.title) = LOWER(?) LIMIT 1
                    """, (album['name'],))
                    r = cursor.fetchone()
                    if r:
                        album['image_url'] = r[0] or None
                        album['id'] = r[1]
                        album['artist_id'] = r[2]
                except Exception:
                    pass

            for track in (cache.get('top_tracks') or []):
                try:
                    cursor.execute("""
                        SELECT al.thumb_url, t.id, t.artist_id FROM tracks t
                        JOIN albums al ON al.id = t.album_id
                        JOIN artists ar ON ar.id = t.artist_id
                        WHERE LOWER(t.title) = LOWER(?) AND LOWER(ar.name) = LOWER(?) LIMIT 1
                    """, (track['name'], track.get('artist', '')))
                    r = cursor.fetchone()
                    if r:
                        track['image_url'] = r[0] or None
                        track['id'] = r[1]
                        track['artist_id'] = r[2]
                except Exception:
                    pass
        except Exception:
            pass
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
                except Exception:
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

    def _map_play_counts_to_db(self, server_counts, server_source):
        """Map server track IDs to DB track IDs for play count updates.

        Looks up tracks by matching the server's track ID stored in
        the tracks table (from library sync).
        """
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            # Build a lookup of server_id → db_track_id
            # The tracks table stores server IDs as the primary 'id' column
            updates = []
            for server_id, play_count in server_counts.items():
                cursor.execute("SELECT id FROM tracks WHERE id = ?", (server_id,))
                row = cursor.fetchone()
                if row:
                    updates.append({
                        'db_track_id': row[0],
                        'play_count': play_count,
                        'last_played': None,  # Could be fetched separately
                    })
            return updates
        except Exception as e:
            logger.error(f"Error mapping play counts: {e}")
            return []
        finally:
            if conn:
                conn.close()
