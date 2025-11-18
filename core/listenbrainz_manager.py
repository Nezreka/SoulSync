"""
ListenBrainz Cache Manager
Handles caching of ListenBrainz playlists and tracks in local database
"""

import sqlite3
import json
from typing import List, Dict, Optional
from datetime import datetime
from pathlib import Path
from utils.logging_config import get_logger
from core.listenbrainz_client import ListenBrainzClient
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = get_logger("listenbrainz_manager")

class ListenBrainzManager:
    """Manages caching of ListenBrainz data in local database"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.client = ListenBrainzClient()

    def _get_db_connection(self):
        """Get database connection"""
        return sqlite3.connect(self.db_path)

    def update_all_playlists(self) -> Dict:
        """
        Update all ListenBrainz playlists (created_for, user, collaborative)
        Returns summary of updates
        """
        if not self.client.is_authenticated():
            logger.warning("ListenBrainz not authenticated, skipping update")
            return {
                "success": False,
                "error": "Not authenticated"
            }

        logger.info("🔄 Starting ListenBrainz playlists update...")

        summary = {
            "created_for": {"updated": 0, "skipped": 0, "new": 0},
            "user": {"updated": 0, "skipped": 0, "new": 0},
            "collaborative": {"updated": 0, "skipped": 0, "new": 0}
        }

        # Fetch all playlist types
        playlist_types = [
            ("created_for", self.client.get_playlists_created_for_user),
            ("user", self.client.get_user_playlists),
            ("collaborative", self.client.get_collaborative_playlists)
        ]

        for playlist_type, fetch_func in playlist_types:
            try:
                playlists = fetch_func()
                logger.info(f"📋 Fetched {len(playlists)} {playlist_type} playlists")

                for playlist in playlists:
                    result = self._update_playlist(playlist, playlist_type)
                    if result == "updated":
                        summary[playlist_type]["updated"] += 1
                    elif result == "skipped":
                        summary[playlist_type]["skipped"] += 1
                    elif result == "new":
                        summary[playlist_type]["new"] += 1

            except Exception as e:
                logger.error(f"Error updating {playlist_type} playlists: {e}")

        logger.info(f"✅ ListenBrainz update complete: {summary}")
        return {
            "success": True,
            "summary": summary
        }

    def _update_playlist(self, playlist_data: Dict, playlist_type: str) -> str:
        """
        Update a single playlist. Returns 'updated', 'skipped', or 'new'
        Implements smart comparison to avoid unnecessary updates
        """
        # Extract playlist metadata
        playlist = playlist_data.get('playlist', playlist_data)
        playlist_mbid = playlist.get('identifier', '').split('/')[-1]

        if not playlist_mbid:
            logger.warning("Playlist missing MBID, skipping")
            return "skipped"

        title = playlist.get('title', 'Untitled')
        creator = playlist.get('creator', 'ListenBrainz')

        # Check if playlist has tracks - if not, fetch full details
        tracks = playlist.get('track', [])
        if not tracks:
            logger.debug(f"Fetching full details for playlist '{title}'...")
            full_playlist = self.client.get_playlist_details(playlist_mbid)
            if full_playlist:
                playlist = full_playlist.get('playlist', full_playlist)
                tracks = playlist.get('track', [])

        track_count = len(tracks)

        # Check if playlist exists in database
        conn = self._get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, track_count, last_updated
            FROM listenbrainz_playlists
            WHERE playlist_mbid = ?
        """, (playlist_mbid,))

        existing = cursor.fetchone()

        # Smart comparison: check if update is needed
        if existing:
            db_id, db_track_count, last_updated = existing

            # Skip if track count hasn't changed (playlist content likely the same)
            if db_track_count == track_count:
                logger.debug(f"✓ Playlist '{title}' unchanged, skipping")
                conn.close()
                return "skipped"

            logger.info(f"🔄 Playlist '{title}' changed ({db_track_count} → {track_count} tracks), updating...")

            # Delete old tracks
            cursor.execute("DELETE FROM listenbrainz_tracks WHERE playlist_id = ?", (db_id,))

            # Update playlist metadata
            cursor.execute("""
                UPDATE listenbrainz_playlists
                SET title = ?, creator = ?, track_count = ?, last_updated = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (title, creator, track_count, db_id))

            playlist_id = db_id
            result_type = "updated"

        else:
            logger.info(f"➕ New playlist '{title}', adding to database...")

            # Insert new playlist
            cursor.execute("""
                INSERT INTO listenbrainz_playlists
                (playlist_mbid, title, creator, playlist_type, track_count, annotation_data)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                playlist_mbid,
                title,
                creator,
                playlist_type,
                track_count,
                json.dumps(playlist.get('annotation', {}))
            ))

            playlist_id = cursor.lastrowid
            result_type = "new"

        # Fetch and cache tracks with cover art
        if tracks:
            self._cache_tracks(playlist_id, playlist_mbid, tracks, cursor)

        conn.commit()
        conn.close()

        return result_type

    def _cache_tracks(self, playlist_id: int, playlist_mbid: str, tracks: List[Dict], cursor):
        """
        Cache tracks for a playlist, including fetching cover art URLs in parallel
        """
        logger.info(f"🎵 Caching {len(tracks)} tracks with cover art...")

        # First pass: extract track data
        track_data_list = []
        for idx, track in enumerate(tracks):
            # Get recording MBID
            recording_mbid = None
            identifiers = track.get('identifier', [])
            for identifier in identifiers:
                if 'musicbrainz.org/recording/' in identifier:
                    recording_mbid = identifier.split('/')[-1]
                    break

            # Get extension data
            extension = track.get('extension', {})
            mb_data = extension.get('https://musicbrainz.org/doc/jspf#track', {})

            # Extract release MBID for cover art
            release_mbid = None
            additional_metadata = mb_data.get('additional_metadata', {})
            if 'caa_release_mbid' in additional_metadata:
                release_mbid = additional_metadata['caa_release_mbid']

            track_data = {
                'position': idx,
                'track_name': track.get('title', 'Unknown Track'),
                'artist_name': track.get('creator', 'Unknown Artist'),
                'album_name': track.get('album', 'Unknown Album'),
                'duration_ms': track.get('duration', 0),
                'recording_mbid': recording_mbid,
                'release_mbid': release_mbid,
                'album_cover_url': None,  # Will be fetched
                'additional_metadata': json.dumps(mb_data)
            }

            track_data_list.append(track_data)

        # Second pass: fetch cover art in parallel
        self._fetch_cover_art_parallel(track_data_list)

        # Third pass: insert into database
        for track_data in track_data_list:
            cursor.execute("""
                INSERT INTO listenbrainz_tracks
                (playlist_id, position, track_name, artist_name, album_name,
                 duration_ms, recording_mbid, release_mbid, album_cover_url, additional_metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                playlist_id,
                track_data['position'],
                track_data['track_name'],
                track_data['artist_name'],
                track_data['album_name'],
                track_data['duration_ms'],
                track_data['recording_mbid'],
                track_data['release_mbid'],
                track_data['album_cover_url'],
                track_data['additional_metadata']
            ))

    def _fetch_cover_art_parallel(self, track_data_list: List[Dict]):
        """Fetch cover art URLs in parallel using threading"""
        def fetch_single_cover(track_data):
            """Fetch cover art for a single track"""
            release_mbid = track_data.get('release_mbid')
            if not release_mbid:
                return None

            try:
                url = f"https://coverartarchive.org/release/{release_mbid}"
                response = requests.get(url, timeout=3)

                if response.status_code == 200:
                    data = response.json()
                    images = data.get('images', [])

                    # Get front cover
                    for img in images:
                        if img.get('front'):
                            return img.get('thumbnails', {}).get('small') or img.get('image')

                    # Fallback to first image
                    if images:
                        return images[0].get('thumbnails', {}).get('small') or images[0].get('image')
            except:
                pass

            return None

        # Fetch up to 10 covers at a time
        with ThreadPoolExecutor(max_workers=10) as executor:
            future_to_idx = {
                executor.submit(fetch_single_cover, track): idx
                for idx, track in enumerate(track_data_list)
            }

            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    cover_url = future.result()
                    if cover_url:
                        track_data_list[idx]['album_cover_url'] = cover_url
                except Exception as e:
                    logger.debug(f"Error fetching cover for track {idx}: {e}")

        covers_found = sum(1 for t in track_data_list if t.get('album_cover_url'))
        logger.info(f"✅ Fetched {covers_found}/{len(track_data_list)} cover art URLs")

    def has_cached_playlists(self) -> bool:
        """Check if there are any cached playlists in the database"""
        conn = self._get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM listenbrainz_playlists")
        count = cursor.fetchone()[0]
        conn.close()
        return count > 0

    def get_cached_playlists(self, playlist_type: str) -> List[Dict]:
        """Get cached playlists of a specific type from database"""
        conn = self._get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, playlist_mbid, title, creator, track_count, annotation_data, last_updated
            FROM listenbrainz_playlists
            WHERE playlist_type = ?
            ORDER BY last_updated DESC
        """, (playlist_type,))

        playlists = []
        for row in cursor.fetchall():
            playlists.append({
                "id": row[0],
                "playlist_mbid": row[1],
                "title": row[2],
                "creator": row[3],
                "track_count": row[4],
                "annotation": json.loads(row[5]) if row[5] else {},
                "last_updated": row[6]
            })

        conn.close()
        return playlists

    def get_cached_tracks(self, playlist_mbid: str) -> List[Dict]:
        """Get cached tracks for a playlist from database"""
        conn = self._get_db_connection()
        cursor = conn.cursor()

        # Get playlist ID
        cursor.execute("""
            SELECT id FROM listenbrainz_playlists WHERE playlist_mbid = ?
        """, (playlist_mbid,))

        playlist_row = cursor.fetchone()
        if not playlist_row:
            conn.close()
            return []

        playlist_id = playlist_row[0]

        # Get tracks
        cursor.execute("""
            SELECT track_name, artist_name, album_name, duration_ms,
                   recording_mbid, release_mbid, album_cover_url, additional_metadata
            FROM listenbrainz_tracks
            WHERE playlist_id = ?
            ORDER BY position ASC
        """, (playlist_id,))

        tracks = []
        for row in cursor.fetchall():
            tracks.append({
                "track_name": row[0],
                "artist_name": row[1],
                "album_name": row[2],
                "duration_ms": row[3],
                "mbid": row[4],  # recording_mbid
                "release_mbid": row[5],
                "album_cover_url": row[6],
                "additional_metadata": json.loads(row[7]) if row[7] else {}
            })

        conn.close()
        return tracks
