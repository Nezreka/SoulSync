#!/usr/bin/env python3

"""
Personalized Playlists Service - Creates Spotify-quality personalized playlists
from user's library and discovery pool
"""

from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timedelta
from collections import Counter
import random
from utils.logging_config import get_logger

logger = get_logger("personalized_playlists")

class PersonalizedPlaylistsService:
    """Service for generating personalized playlists from library and discovery pool"""

    def __init__(self, database, spotify_client=None):
        self.database = database
        self.spotify_client = spotify_client

    # ========================================
    # LIBRARY-BASED PLAYLISTS
    # ========================================

    def get_recently_added(self, limit: int = 50) -> List[Dict]:
        """
        Get recently added tracks from library.

        Returns tracks ordered by date_added DESC

        NOTE: This requires library tracks to have Spotify metadata which may not be available.
        Returns empty list if schema incompatible.
        """
        try:
            logger.warning("Recently Added requires Spotify-linked library tracks - returning empty")
            return []

        except Exception as e:
            logger.error(f"Error getting recently added tracks: {e}")
            return []

    def get_top_tracks(self, limit: int = 50) -> List[Dict]:
        """
        Get user's all-time top tracks based on play count.

        NOTE: This requires library tracks to have Spotify metadata which may not be available.
        Returns empty list if schema incompatible.
        """
        try:
            logger.warning("Top Tracks requires Spotify-linked library tracks - returning empty")
            return []

        except Exception as e:
            logger.error(f"Error getting top tracks: {e}")
            return []

    def get_forgotten_favorites(self, limit: int = 50) -> List[Dict]:
        """
        Get tracks you loved but haven't played recently.

        NOTE: This requires library tracks to have Spotify metadata which may not be available.
        Returns empty list if schema incompatible.
        """
        try:
            logger.warning("Forgotten Favorites requires Spotify-linked library tracks - returning empty")
            return []

        except Exception as e:
            logger.error(f"Error getting forgotten favorites: {e}")
            return []

    def get_decade_playlist(self, decade: int, limit: int = 100) -> List[Dict]:
        """
        Get tracks from a specific decade from discovery pool with diversity filtering.

        Args:
            decade: Decade year (e.g., 2020 for 2020s, 2010 for 2010s)
            limit: Maximum tracks to return
        """
        try:
            start_year = decade
            end_year = decade + 9

            with self.database._get_connection() as conn:
                cursor = conn.cursor()

                # Query discovery_pool - get 10x more for diversity filtering
                cursor.execute("""
                    SELECT
                        spotify_track_id,
                        track_name,
                        artist_name,
                        album_name,
                        album_cover_url,
                        duration_ms,
                        popularity,
                        release_date
                    FROM discovery_pool
                    WHERE release_date IS NOT NULL
                      AND CAST(SUBSTR(release_date, 1, 4) AS INTEGER) BETWEEN ? AND ?
                    ORDER BY RANDOM()
                    LIMIT ?
                """, (start_year, end_year, limit * 10))

                rows = cursor.fetchall()
                all_tracks = [dict(row) for row in rows]

                if not all_tracks:
                    logger.warning(f"No tracks found for {decade}s")
                    return []

                # Shuffle first for randomness
                import random
                random.shuffle(all_tracks)

                # Count unique artists to determine diversity level
                unique_artists = len(set(track['artist_name'] for track in all_tracks))

                # Adaptive diversity limits based on artist variety
                if unique_artists >= 20:
                    # Good variety - apply diversity constraints
                    max_per_album = 3
                    max_per_artist = 5
                elif unique_artists >= 10:
                    # Moderate variety - more lenient
                    max_per_album = 4
                    max_per_artist = 8
                else:
                    # Low variety - very lenient to hit 50 tracks
                    max_per_album = 5
                    max_per_artist = 12

                logger.info(f"{decade}s has {unique_artists} unique artists - using limits: {max_per_album} per album, {max_per_artist} per artist")

                # Apply diversity constraints
                tracks_by_album = {}
                tracks_by_artist = {}
                diverse_tracks = []

                for track in all_tracks:
                    album = track['album_name']
                    artist = track['artist_name']

                    # Count current tracks for this album/artist
                    album_count = tracks_by_album.get(album, 0)
                    artist_count = tracks_by_artist.get(artist, 0)

                    if album_count < max_per_album and artist_count < max_per_artist:
                        diverse_tracks.append(track)
                        tracks_by_album[album] = album_count + 1
                        tracks_by_artist[artist] = artist_count + 1

                        if len(diverse_tracks) >= limit:
                            break

                logger.info(f"Found {len(diverse_tracks)} tracks from {decade}s in discovery pool (adaptive diversity)")
                return diverse_tracks[:limit]

        except Exception as e:
            logger.error(f"Error getting decade playlist for {decade}s: {e}")
            return []

    # ========================================
    # DISCOVERY POOL PLAYLISTS
    # ========================================

    def get_popular_picks(self, limit: int = 50) -> List[Dict]:
        """Get high popularity tracks from discovery pool with diversity (max 2 tracks per album/artist)"""
        try:
            with self.database._get_connection() as conn:
                cursor = conn.cursor()

                # Get more tracks than needed to allow for filtering
                cursor.execute("""
                    SELECT
                        spotify_track_id,
                        track_name,
                        artist_name,
                        album_name,
                        album_cover_url,
                        duration_ms,
                        popularity
                    FROM discovery_pool
                    WHERE popularity >= 60
                    ORDER BY popularity DESC, RANDOM()
                    LIMIT ?
                """, (limit * 3,))  # Get 3x more for diversity filtering

                rows = cursor.fetchall()
                all_tracks = [dict(row) for row in rows]

                # Apply diversity constraint: max 2 tracks per album, max 3 per artist
                tracks_by_album = {}
                tracks_by_artist = {}
                diverse_tracks = []

                for track in all_tracks:
                    album = track['album_name']
                    artist = track['artist_name']

                    # Count current tracks for this album/artist
                    album_count = tracks_by_album.get(album, 0)
                    artist_count = tracks_by_artist.get(artist, 0)

                    # Apply limits: max 2 per album, max 3 per artist
                    if album_count < 2 and artist_count < 3:
                        diverse_tracks.append(track)
                        tracks_by_album[album] = album_count + 1
                        tracks_by_artist[artist] = artist_count + 1

                        if len(diverse_tracks) >= limit:
                            break

                logger.info(f"Popular Picks: Selected {len(diverse_tracks)} tracks with diversity")
                return diverse_tracks[:limit]

        except Exception as e:
            logger.error(f"Error getting popular picks: {e}")
            return []

    def get_hidden_gems(self, limit: int = 50) -> List[Dict]:
        """Get low popularity (underground/indie) tracks from discovery pool"""
        try:
            with self.database._get_connection() as conn:
                cursor = conn.cursor()

                cursor.execute("""
                    SELECT
                        spotify_track_id,
                        track_name,
                        artist_name,
                        album_name,
                        album_cover_url,
                        duration_ms,
                        popularity
                    FROM discovery_pool
                    WHERE popularity < 40
                    ORDER BY RANDOM()
                    LIMIT ?
                """, (limit,))

                rows = cursor.fetchall()
                return [dict(row) for row in rows]

        except Exception as e:
            logger.error(f"Error getting hidden gems: {e}")
            return []

    def get_discovery_shuffle(self, limit: int = 50) -> List[Dict]:
        """
        Get random tracks from discovery pool - pure exploration.

        Different every time you call it!
        """
        try:
            with self.database._get_connection() as conn:
                cursor = conn.cursor()

                cursor.execute("""
                    SELECT
                        spotify_track_id,
                        track_name,
                        artist_name,
                        album_name,
                        album_cover_url,
                        duration_ms,
                        popularity
                    FROM discovery_pool
                    ORDER BY RANDOM()
                    LIMIT ?
                """, (limit,))

                rows = cursor.fetchall()
                return [dict(row) for row in rows]

        except Exception as e:
            logger.error(f"Error getting discovery shuffle: {e}")
            return []

    def get_familiar_favorites(self, limit: int = 50) -> List[Dict]:
        """
        Get tracks with medium play counts (3-15 plays) - your reliable go-tos.

        NOTE: This requires library tracks to have Spotify metadata which may not be available.
        Returns empty list if schema incompatible.
        """
        try:
            logger.warning("Familiar Favorites requires Spotify-linked library tracks - returning empty")
            return []

        except Exception as e:
            logger.error(f"Error getting familiar favorites: {e}")
            return []

    # ========================================
    # DAILY MIX (HYBRID PLAYLISTS)
    # ========================================

    def get_top_genres_from_library(self, limit: int = 5) -> List[Tuple[str, int]]:
        """
        Get top genres from user's library by track count.

        Returns: List of (genre_name, track_count) tuples
        """
        try:
            # Get all genres from library tracks
            with self.database._get_connection() as conn:
                cursor = conn.cursor()

                # Try to get genres from tracks or albums
                cursor.execute("PRAGMA table_info(tracks)")
                columns = [row['name'] for row in cursor.fetchall()]

                if 'genres' in columns:
                    # Get genres directly from tracks
                    cursor.execute("""
                        SELECT genres FROM tracks WHERE genres IS NOT NULL
                    """)
                    rows = cursor.fetchall()

                    # Parse genres (assuming JSON array or comma-separated)
                    all_genres = []
                    for row in rows:
                        genres_str = row['genres']
                        if genres_str:
                            # Try JSON parse first
                            try:
                                import json
                                genres = json.loads(genres_str)
                                all_genres.extend(genres)
                            except:
                                # Fallback to comma-separated
                                genres = [g.strip() for g in genres_str.split(',')]
                                all_genres.extend(genres)

                    # Count genres
                    genre_counts = Counter(all_genres)
                    return genre_counts.most_common(limit)
                else:
                    # Fallback: use artist names as "genres"
                    logger.warning("No genres column - using top artists as categories")
                    cursor.execute("""
                        SELECT ar.name, COUNT(*) as count
                        FROM tracks t
                        LEFT JOIN artists ar ON t.artist_id = ar.id
                        WHERE ar.name IS NOT NULL
                        GROUP BY ar.name
                        ORDER BY count DESC
                        LIMIT ?
                    """, (limit,))

                    rows = cursor.fetchall()
                    return [(row['name'], row['count']) for row in rows]

        except Exception as e:
            logger.error(f"Error getting top genres: {e}")
            return []

    def create_daily_mix(self, genre_or_artist: str, mix_number: int = 1) -> Dict[str, Any]:
        """
        Create a Daily Mix playlist - hybrid of library + discovery pool.

        Strategy:
        - 50% tracks from user's library matching genre/artist
        - 50% tracks from discovery pool matching genre/artist

        Args:
            genre_or_artist: Genre name or artist name to base mix on
            mix_number: Mix number (1, 2, 3, etc.)

        Returns:
            Dict with playlist metadata and tracks
        """
        try:
            logger.info(f"Creating Daily Mix #{mix_number} for: {genre_or_artist}")

            mix_size = 50
            library_portion = mix_size // 2  # 25 tracks
            discovery_portion = mix_size - library_portion  # 25 tracks

            # Get tracks from library
            library_tracks = self._get_library_tracks_by_category(genre_or_artist, library_portion)

            # Get tracks from discovery pool
            discovery_tracks = self._get_discovery_tracks_by_category(genre_or_artist, discovery_portion)

            # Combine and shuffle
            all_tracks = library_tracks + discovery_tracks
            random.shuffle(all_tracks)

            return {
                'mix_number': mix_number,
                'name': f"Daily Mix {mix_number}",
                'description': f"{genre_or_artist} mix",
                'category': genre_or_artist,
                'track_count': len(all_tracks),
                'tracks': all_tracks
            }

        except Exception as e:
            logger.error(f"Error creating daily mix: {e}")
            return {
                'mix_number': mix_number,
                'name': f"Daily Mix {mix_number}",
                'description': 'Mix',
                'category': genre_or_artist,
                'track_count': 0,
                'tracks': []
            }

    def _get_library_tracks_by_category(self, category: str, limit: int) -> List[Dict]:
        """
        Get tracks from library matching genre or artist

        NOTE: This requires library tracks to have Spotify metadata which may not be available.
        Returns empty list if schema incompatible.
        """
        try:
            logger.warning("Library tracks by category requires Spotify-linked library - returning empty")
            return []

        except Exception as e:
            logger.error(f"Error getting library tracks by category: {e}")
            return []

    def _get_discovery_tracks_by_category(self, category: str, limit: int) -> List[Dict]:
        """Get tracks from discovery pool matching genre or artist"""
        try:
            with self.database._get_connection() as conn:
                cursor = conn.cursor()

                cursor.execute("""
                    SELECT
                        spotify_track_id,
                        track_name,
                        artist_name,
                        album_name,
                        album_cover_url,
                        duration_ms,
                        popularity
                    FROM discovery_pool
                    WHERE artist_name LIKE ? OR track_name LIKE ?
                    ORDER BY RANDOM()
                    LIMIT ?
                """, (f'%{category}%', f'%{category}%', limit))

                rows = cursor.fetchall()
                return [dict(row) for row in rows]

        except Exception as e:
            logger.error(f"Error getting discovery tracks by category: {e}")
            return []

    def get_all_daily_mixes(self, max_mixes: int = 4) -> List[Dict]:
        """
        Generate multiple Daily Mix playlists based on top genres/artists.

        Args:
            max_mixes: Maximum number of mixes to generate (default: 4)

        Returns:
            List of daily mix dictionaries
        """
        try:
            # Get top categories (genres or artists)
            top_categories = self.get_top_genres_from_library(limit=max_mixes)

            if not top_categories:
                logger.warning("No categories found for Daily Mixes")
                return []

            daily_mixes = []
            for i, (category, _count) in enumerate(top_categories, 1):
                mix = self.create_daily_mix(category, mix_number=i)
                if mix['track_count'] > 0:
                    daily_mixes.append(mix)

            logger.info(f"Created {len(daily_mixes)} Daily Mixes")
            return daily_mixes

        except Exception as e:
            logger.error(f"Error getting all daily mixes: {e}")
            return []

    # ========================================
    # BUILD A PLAYLIST (CUSTOM GENERATOR)
    # ========================================

    def build_custom_playlist(self, seed_artist_ids: List[str], playlist_size: int = 50) -> Dict[str, Any]:
        """
        Build a custom playlist from seed artists.

        Process:
        1. Get similar artists for each seed artist (max 25 total)
        2. Get albums from those similar artists
        3. Select 20 random albums
        4. Build playlist from tracks in those albums (max 50 tracks)

        Args:
            seed_artist_ids: List of 1-5 Spotify artist IDs
            playlist_size: Maximum tracks in final playlist (default: 50)

        Returns:
            Dict with playlist metadata and tracks
        """
        try:
            if not seed_artist_ids or len(seed_artist_ids) > 5:
                logger.error(f"Invalid seed artists count: {len(seed_artist_ids)}")
                return {'tracks': [], 'error': 'Must provide 1-5 seed artists'}

            if not self.spotify_client or not self.spotify_client.is_authenticated():
                logger.error("Spotify client not available")
                return {'tracks': [], 'error': 'Spotify not authenticated'}

            logger.info(f"Building custom playlist from {len(seed_artist_ids)} seed artists")

            # Step 1: Get similar artists for each seed from database
            all_similar_artists = []
            seen_artist_ids = set(seed_artist_ids)  # Don't include seed artists themselves

            for seed_artist_id in seed_artist_ids:
                try:
                    # Get similar artists from database (cached from MusicMap)
                    with self.database._get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute("""
                            SELECT similar_artist_spotify_id, similar_artist_name
                            FROM similar_artists
                            WHERE source_artist_id = ?
                            ORDER BY similarity_rank ASC
                            LIMIT 10
                        """, (seed_artist_id,))

                        rows = cursor.fetchall()

                        for row in rows:
                            artist_id = row['similar_artist_spotify_id']
                            artist_name = row['similar_artist_name']

                            if artist_id not in seen_artist_ids:
                                # Create artist-like object
                                all_similar_artists.append({
                                    'id': artist_id,
                                    'name': artist_name
                                })
                                seen_artist_ids.add(artist_id)

                                if len(all_similar_artists) >= 25:
                                    break

                    if len(all_similar_artists) >= 25:
                        break

                except Exception as e:
                    logger.warning(f"Error getting similar artists for {seed_artist_id}: {e}")
                    continue

            logger.info(f"Found {len(all_similar_artists)} similar artists from database")

            if not all_similar_artists:
                return {'tracks': [], 'error': 'No similar artists found'}

            # Limit to 25 similar artists
            similar_artists_to_use = all_similar_artists[:25]

            # Step 2: Get albums from similar artists
            all_albums = []
            for artist in similar_artists_to_use:
                try:
                    albums = self.spotify_client.get_artist_albums(
                        artist['id'],
                        album_type='album,single',
                        limit=10
                    )

                    if albums:
                        all_albums.extend(albums)

                    import time
                    time.sleep(0.3)  # Rate limiting

                except Exception as e:
                    logger.warning(f"Error getting albums for {artist['name']}: {e}")
                    continue

            logger.info(f"Found {len(all_albums)} total albums")

            if not all_albums:
                return {'tracks': [], 'error': 'No albums found'}

            # Step 3: Select 20 random albums
            random.shuffle(all_albums)
            selected_albums = all_albums[:20]

            logger.info(f"Selected {len(selected_albums)} random albums")

            # Step 4: Build playlist from tracks in those albums
            all_tracks = []
            for album in selected_albums:
                try:
                    album_data = self.spotify_client.get_album(album.id)

                    if album_data and 'tracks' in album_data:
                        tracks = album_data['tracks'].get('items', [])

                        for track in tracks:
                            if track['id']:
                                # Format in discovery pool format (for rendering + modal compatibility)
                                all_tracks.append({
                                    'spotify_track_id': track['id'],
                                    'track_name': track['name'],
                                    'artist_name': ', '.join([a['name'] for a in track.get('artists', [])]),
                                    'album_name': album_data.get('name', 'Unknown'),
                                    'album_cover_url': album_data.get('images', [{}])[0].get('url') if album_data.get('images') else None,
                                    'duration_ms': track.get('duration_ms', 0),
                                    'popularity': album_data.get('popularity', 0),
                                    # Also include Spotify format fields for modal
                                    'id': track['id'],
                                    'name': track['name'],
                                    'artists': [a['name'] for a in track.get('artists', [])],
                                    'album': {
                                        'name': album_data.get('name', 'Unknown'),
                                        'images': album_data.get('images', [])
                                    }
                                })

                    import time
                    time.sleep(0.3)  # Rate limiting

                except Exception as e:
                    logger.warning(f"Error getting tracks from album: {e}")
                    continue

            logger.info(f"Collected {len(all_tracks)} total tracks")

            if not all_tracks:
                return {'tracks': [], 'error': 'No tracks found'}

            # Shuffle and limit to playlist_size
            random.shuffle(all_tracks)
            final_tracks = all_tracks[:playlist_size]

            logger.info(f"Built custom playlist with {len(final_tracks)} tracks")

            return {
                'name': 'Custom Playlist',
                'description': f'Built from {len(seed_artist_ids)} seed artists',
                'track_count': len(final_tracks),
                'tracks': final_tracks,
                'metadata': {
                    'total_tracks': len(final_tracks),
                    'similar_artists_count': len(similar_artists_to_use),
                    'albums_count': len(selected_albums)
                }
            }

        except Exception as e:
            logger.error(f"Error building custom playlist: {e}")
            import traceback
            traceback.print_exc()
            return {'tracks': [], 'error': str(e)}


# Singleton instance
_personalized_playlists_instance = None

def get_personalized_playlists_service(database, spotify_client=None):
    """Get the global personalized playlists service instance"""
    global _personalized_playlists_instance
    if _personalized_playlists_instance is None:
        _personalized_playlists_instance = PersonalizedPlaylistsService(database, spotify_client)
    return _personalized_playlists_instance
