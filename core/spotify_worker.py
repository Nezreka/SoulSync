import json
import re
import threading
import time
from difflib import SequenceMatcher
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
from utils.logging_config import get_logger
from database.music_database import MusicDatabase
from core.spotify_client import SpotifyClient

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
        self.error_retry_days = 7

        # Name matching threshold
        self.name_similarity_threshold = 0.80

        # Rate limiting (SpotifyClient already rate-limits at 200ms between API calls)
        self.inter_item_sleep = 0.5       # Between top-level items
        self.batch_inter_item_sleep = 0.1  # Between local matches within a batch (no API calls)

        logger.info("Spotify background worker initialized")

    def start(self):
        if self.running:
            logger.warning("Worker already running")
            return
        self.running = True
        self.should_stop = False
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        logger.info("Spotify background worker started")

    def stop(self):
        if not self.running:
            return
        logger.info("Stopping Spotify worker...")
        self.should_stop = True
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
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
            authenticated = self.client.is_spotify_authenticated()
        except Exception:
            authenticated = False

        return {
            'enabled': True,
            'running': is_actually_running and not self.paused,
            'paused': self.paused,
            'idle': is_idle,
            'authenticated': authenticated,
            'current_item': self.current_item,
            'stats': self.stats.copy(),
            'progress': progress
        }

    # ── Main loop ──────────────────────────────────────────────────────

    def _run(self):
        logger.info("Spotify worker thread started")
        while not self.should_stop:
            try:
                if self.paused:
                    time.sleep(1)
                    continue

                # Auth guard — don't process anything without Spotify auth
                if not self.client.is_spotify_authenticated():
                    logger.debug("Spotify not authenticated, sleeping 30s...")
                    time.sleep(30)
                    continue

                self.current_item = None
                item = self._get_next_item()

                if not item:
                    logger.debug("No pending items, sleeping...")
                    time.sleep(10)
                    continue

                self.current_item = item
                self._process_item(item)
                time.sleep(self.inter_item_sleep)

            except Exception as e:
                logger.error(f"Error in worker loop: {e}")
                time.sleep(5)

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
                WHERE spotify_match_status IS NULL
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
                      WHERE al.artist_id = ar.id AND al.spotify_match_status IS NULL
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
                      WHERE t.album_id = al.id AND t.spotify_match_status IS NULL
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
                WHERE a.spotify_match_status IS NULL
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
                WHERE t.spotify_match_status IS NULL
                ORDER BY t.id ASC
                LIMIT 1
            """)
            row = cursor.fetchone()
            if row:
                return {'type': 'track_individual', 'id': row[0], 'name': row[1], 'artist': row[2]}

            # Priority 6: Retry stale failures
            not_found_cutoff = datetime.now() - timedelta(days=self.retry_days)
            error_cutoff = datetime.now() - timedelta(days=self.error_retry_days)

            cursor.execute("""
                SELECT id, name
                FROM artists
                WHERE (spotify_match_status = 'not_found' AND spotify_last_attempted < ?)
                   OR (spotify_match_status = 'error' AND spotify_last_attempted < ?)
                ORDER BY spotify_last_attempted ASC
                LIMIT 1
            """, (not_found_cutoff, error_cutoff))
            row = cursor.fetchone()
            if row:
                return {'type': 'artist', 'id': row[0], 'name': row[1]}

            cursor.execute("""
                SELECT a.id, a.title, ar.name AS artist_name
                FROM albums a
                JOIN artists ar ON a.artist_id = ar.id
                WHERE (a.spotify_match_status = 'not_found' AND a.spotify_last_attempted < ?)
                   OR (a.spotify_match_status = 'error' AND a.spotify_last_attempted < ?)
                ORDER BY a.spotify_last_attempted ASC
                LIMIT 1
            """, (not_found_cutoff, error_cutoff))
            row = cursor.fetchone()
            if row:
                return {'type': 'album_individual', 'id': row[0], 'name': row[1], 'artist': row[2]}

            cursor.execute("""
                SELECT t.id, t.title, ar.name AS artist_name
                FROM tracks t
                JOIN artists ar ON t.artist_id = ar.id
                WHERE (t.spotify_match_status = 'not_found' AND t.spotify_last_attempted < ?)
                   OR (t.spotify_match_status = 'error' AND t.spotify_last_attempted < ?)
                ORDER BY t.spotify_last_attempted ASC
                LIMIT 1
            """, (not_found_cutoff, error_cutoff))
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

    def _process_artist(self, item: Dict[str, Any]):
        artist_id = item['id']
        artist_name = item['name']

        results = self.client.search_artists(artist_name, limit=5)
        if not results:
            self._mark_status('artist', artist_id, 'not_found')
            self.stats['not_found'] += 1
            logger.debug(f"No Spotify results for artist '{artist_name}'")
            return

        # Find best fuzzy match
        for artist_obj in results:
            if self._name_matches(artist_name, artist_obj.name):
                if not self._is_spotify_id(artist_obj.id):
                    logger.warning(f"Rejecting non-Spotify ID '{artist_obj.id}' for artist '{artist_name}' (iTunes fallback leak)")
                    self._mark_status('artist', artist_id, 'error')
                    self.stats['errors'] += 1
                    return
                self._update_artist(artist_id, artist_obj)
                self.stats['matched'] += 1
                logger.info(f"Matched artist '{artist_name}' -> Spotify ID: {artist_obj.id}")
                return

        self._mark_status('artist', artist_id, 'not_found')
        self.stats['not_found'] += 1
        logger.debug(f"Name mismatch for artist '{artist_name}' (best: '{results[0].name}')")

    # ── Album batch processing ─────────────────────────────────────────

    def _process_album_batch(self, item: Dict[str, Any]):
        artist_id = item['artist_id']
        spotify_artist_id = item['spotify_artist_id']
        artist_name = item['artist_name']

        # 1 API call: get all albums for this artist from Spotify
        try:
            spotify_albums = self.client.get_artist_albums(
                spotify_artist_id, album_type='album,single,compilation', limit=50
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

            time.sleep(self.batch_inter_item_sleep)

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

            time.sleep(self.batch_inter_item_sleep)

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

        for album_obj in results:
            if self._name_matches(album_name, album_obj.name):
                if not self._is_spotify_id(album_obj.id):
                    logger.warning(f"Rejecting non-Spotify ID '{album_obj.id}' for album '{album_name}'")
                    self._mark_status('album', album_id, 'error')
                    self.stats['errors'] += 1
                    return
                self._update_album(album_id, album_obj)
                self.stats['matched'] += 1
                logger.info(f"Matched album '{album_name}' -> Spotify ID: {album_obj.id}")
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

        for track_obj in results:
            if self._name_matches(track_name, track_obj.name):
                if not self._is_spotify_id(track_obj.id):
                    logger.warning(f"Rejecting non-Spotify ID '{track_obj.id}' for track '{track_name}'")
                    self._mark_status('track', track_id, 'error')
                    self.stats['errors'] += 1
                    return
                self._update_track_from_search(track_id, track_obj)
                self.stats['matched'] += 1
                logger.info(f"Matched track '{track_name}' -> Spotify ID: {track_obj.id}")
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
                WHERE album_id = ? AND spotify_match_status IS NULL
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
                    (SELECT COUNT(*) FROM artists WHERE spotify_match_status IS NULL) +
                    (SELECT COUNT(*) FROM albums WHERE spotify_match_status IS NULL) +
                    (SELECT COUNT(*) FROM tracks WHERE spotify_match_status IS NULL)
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
        name = re.sub(r'\s*\(.*?\)\s*', ' ', name)
        name = re.sub(r'[^\w\s]', '', name)
        name = re.sub(r'\s+', ' ', name).strip()
        return name

    def _name_matches(self, query_name: str, result_name: str) -> bool:
        norm_query = self._normalize_name(query_name)
        norm_result = self._normalize_name(result_name)
        similarity = SequenceMatcher(None, norm_query, norm_result).ratio()
        logger.debug(f"Name similarity: '{query_name}' vs '{result_name}' = {similarity:.2f}")
        return similarity >= self.name_similarity_threshold
