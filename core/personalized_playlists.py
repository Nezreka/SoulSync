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
        """
        try:
            with self.database._get_connection() as conn:
                cursor = conn.cursor()

                cursor.execute("""
                    SELECT
                        t.id,
                        t.spotify_track_id,
                        t.title as track_name,
                        t.duration_ms,
                        ar.name as artist_name,
                        al.title as album_name,
                        al.cover_url as album_cover_url,
                        t.popularity,
                        t.date_added
                    FROM tracks t
                    LEFT JOIN artists ar ON t.artist_id = ar.id
                    LEFT JOIN albums al ON t.album_id = al.id
                    WHERE t.spotify_track_id IS NOT NULL
                    ORDER BY t.date_added DESC
                    LIMIT ?
                """, (limit,))

                rows = cursor.fetchall()
                return [dict(row) for row in rows]

        except Exception as e:
            logger.error(f"Error getting recently added tracks: {e}")
            return []

    def get_top_tracks(self, limit: int = 50) -> List[Dict]:
        """
        Get user's all-time top tracks based on play count.

        Note: Requires play_count column in tracks table
        """
        try:
            with self.database._get_connection() as conn:
                cursor = conn.cursor()

                # Check if play_count column exists
                cursor.execute("PRAGMA table_info(tracks)")
                columns = [row['name'] for row in cursor.fetchall()]

                if 'play_count' not in columns:
                    logger.warning("play_count column not found - using random selection")
                    # Fallback: return random tracks
                    cursor.execute("""
                        SELECT
                            t.id,
                            t.spotify_track_id,
                            t.title as track_name,
                            t.duration_ms,
                            ar.name as artist_name,
                            al.title as album_name,
                            al.cover_url as album_cover_url,
                            t.popularity,
                            0 as play_count
                        FROM tracks t
                        LEFT JOIN artists ar ON t.artist_id = ar.id
                        LEFT JOIN albums al ON t.album_id = al.id
                        WHERE t.spotify_track_id IS NOT NULL
                        ORDER BY RANDOM()
                        LIMIT ?
                    """, (limit,))
                else:
                    cursor.execute("""
                        SELECT
                            t.id,
                            t.spotify_track_id,
                            t.title as track_name,
                            t.duration_ms,
                            ar.name as artist_name,
                            al.title as album_name,
                            al.cover_url as album_cover_url,
                            t.popularity,
                            t.play_count
                        FROM tracks t
                        LEFT JOIN artists ar ON t.artist_id = ar.id
                        LEFT JOIN albums al ON t.album_id = al.id
                        WHERE t.spotify_track_id IS NOT NULL
                        ORDER BY t.play_count DESC
                        LIMIT ?
                    """, (limit,))

                rows = cursor.fetchall()
                return [dict(row) for row in rows]

        except Exception as e:
            logger.error(f"Error getting top tracks: {e}")
            return []

    def get_forgotten_favorites(self, limit: int = 50) -> List[Dict]:
        """
        Get tracks you loved but haven't played recently.

        Criteria: High play count but not played in 60+ days
        """
        try:
            with self.database._get_connection() as conn:
                cursor = conn.cursor()

                # Check if required columns exist
                cursor.execute("PRAGMA table_info(tracks)")
                columns = [row['name'] for row in cursor.fetchall()]

                has_play_count = 'play_count' in columns
                has_last_played = 'last_played' in columns

                if not has_play_count or not has_last_played:
                    logger.warning("play_count or last_played columns not found - using older tracks")
                    # Fallback: return older tracks by date_added
                    sixty_days_ago = (datetime.now() - timedelta(days=60)).isoformat()
                    cursor.execute("""
                        SELECT
                            t.id,
                            t.spotify_track_id,
                            t.title as track_name,
                            t.duration_ms,
                            ar.name as artist_name,
                            al.title as album_name,
                            al.cover_url as album_cover_url,
                            t.popularity,
                            t.date_added
                        FROM tracks t
                        LEFT JOIN artists ar ON t.artist_id = ar.id
                        LEFT JOIN albums al ON t.album_id = al.id
                        WHERE t.spotify_track_id IS NOT NULL
                          AND t.date_added < ?
                        ORDER BY t.date_added DESC
                        LIMIT ?
                    """, (sixty_days_ago, limit))
                else:
                    sixty_days_ago = (datetime.now() - timedelta(days=60)).isoformat()
                    cursor.execute("""
                        SELECT
                            t.id,
                            t.spotify_track_id,
                            t.title as track_name,
                            t.duration_ms,
                            ar.name as artist_name,
                            al.title as album_name,
                            al.cover_url as album_cover_url,
                            t.popularity,
                            t.play_count,
                            t.last_played
                        FROM tracks t
                        LEFT JOIN artists ar ON t.artist_id = ar.id
                        LEFT JOIN albums al ON t.album_id = al.id
                        WHERE t.spotify_track_id IS NOT NULL
                          AND t.play_count > 5
                          AND (t.last_played IS NULL OR t.last_played < ?)
                        ORDER BY t.play_count DESC
                        LIMIT ?
                    """, (sixty_days_ago, limit))

                rows = cursor.fetchall()
                return [dict(row) for row in rows]

        except Exception as e:
            logger.error(f"Error getting forgotten favorites: {e}")
            return []

    def get_decade_playlist(self, decade: int, limit: int = 100) -> List[Dict]:
        """
        Get tracks from a specific decade.

        Args:
            decade: Decade year (e.g., 2020 for 2020s, 2010 for 2010s)
            limit: Maximum tracks to return
        """
        try:
            start_year = decade
            end_year = decade + 9

            with self.database._get_connection() as conn:
                cursor = conn.cursor()

                # Check if release_year column exists
                cursor.execute("PRAGMA table_info(tracks)")
                columns = [row['name'] for row in cursor.fetchall()]

                if 'release_year' in columns:
                    cursor.execute("""
                        SELECT
                            t.id,
                            t.spotify_track_id,
                            t.title as track_name,
                            t.duration_ms,
                            ar.name as artist_name,
                            al.title as album_name,
                            al.cover_url as album_cover_url,
                            t.popularity,
                            t.release_year
                        FROM tracks t
                        LEFT JOIN artists ar ON t.artist_id = ar.id
                        LEFT JOIN albums al ON t.album_id = al.id
                        WHERE t.spotify_track_id IS NOT NULL
                          AND t.release_year BETWEEN ? AND ?
                        ORDER BY t.popularity DESC
                        LIMIT ?
                    """, (start_year, end_year, limit))
                else:
                    # Try to extract year from album release_date
                    logger.warning("release_year column not found - using album release_date")
                    cursor.execute("""
                        SELECT
                            t.id,
                            t.spotify_track_id,
                            t.title as track_name,
                            t.duration_ms,
                            ar.name as artist_name,
                            al.title as album_name,
                            al.cover_url as album_cover_url,
                            t.popularity,
                            al.release_date
                        FROM tracks t
                        LEFT JOIN artists ar ON t.artist_id = ar.id
                        LEFT JOIN albums al ON t.album_id = al.id
                        WHERE t.spotify_track_id IS NOT NULL
                          AND al.release_date IS NOT NULL
                          AND CAST(SUBSTR(al.release_date, 1, 4) AS INTEGER) BETWEEN ? AND ?
                        ORDER BY t.popularity DESC
                        LIMIT ?
                    """, (start_year, end_year, limit))

                rows = cursor.fetchall()
                return [dict(row) for row in rows]

        except Exception as e:
            logger.error(f"Error getting decade playlist for {decade}s: {e}")
            return []

    # ========================================
    # DISCOVERY POOL PLAYLISTS
    # ========================================

    def get_popular_picks(self, limit: int = 50) -> List[Dict]:
        """Get high popularity tracks from discovery pool"""
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
                    WHERE popularity >= 60
                    ORDER BY popularity DESC, RANDOM()
                    LIMIT ?
                """, (limit,))

                rows = cursor.fetchall()
                return [dict(row) for row in rows]

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

        Not overplayed, not rare - just right!
        """
        try:
            with self.database._get_connection() as conn:
                cursor = conn.cursor()

                # Check if play_count exists
                cursor.execute("PRAGMA table_info(tracks)")
                columns = [row['name'] for row in cursor.fetchall()]

                if 'play_count' not in columns:
                    logger.warning("play_count column not found - using random older tracks")
                    # Fallback: tracks added 30-90 days ago
                    thirty_days_ago = (datetime.now() - timedelta(days=30)).isoformat()
                    ninety_days_ago = (datetime.now() - timedelta(days=90)).isoformat()

                    cursor.execute("""
                        SELECT
                            t.id,
                            t.spotify_track_id,
                            t.title as track_name,
                            t.duration_ms,
                            ar.name as artist_name,
                            al.title as album_name,
                            al.cover_url as album_cover_url,
                            t.popularity
                        FROM tracks t
                        LEFT JOIN artists ar ON t.artist_id = ar.id
                        LEFT JOIN albums al ON t.album_id = al.id
                        WHERE t.spotify_track_id IS NOT NULL
                          AND t.date_added BETWEEN ? AND ?
                        ORDER BY RANDOM()
                        LIMIT ?
                    """, (ninety_days_ago, thirty_days_ago, limit))
                else:
                    cursor.execute("""
                        SELECT
                            t.id,
                            t.spotify_track_id,
                            t.title as track_name,
                            t.duration_ms,
                            ar.name as artist_name,
                            al.title as album_name,
                            al.cover_url as album_cover_url,
                            t.popularity,
                            t.play_count
                        FROM tracks t
                        LEFT JOIN artists ar ON t.artist_id = ar.id
                        LEFT JOIN albums al ON t.album_id = al.id
                        WHERE t.spotify_track_id IS NOT NULL
                          AND t.play_count BETWEEN 3 AND 15
                        ORDER BY t.play_count DESC, RANDOM()
                        LIMIT ?
                    """, (limit,))

                rows = cursor.fetchall()
                return [dict(row) for row in rows]

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
        """Get tracks from library matching genre or artist"""
        try:
            with self.database._get_connection() as conn:
                cursor = conn.cursor()

                # Try genre match first, then artist match
                cursor.execute("""
                    SELECT
                        t.id,
                        t.spotify_track_id,
                        t.title as track_name,
                        t.duration_ms,
                        ar.name as artist_name,
                        al.title as album_name,
                        al.cover_url as album_cover_url,
                        t.popularity
                    FROM tracks t
                    LEFT JOIN artists ar ON t.artist_id = ar.id
                    LEFT JOIN albums al ON t.album_id = al.id
                    WHERE t.spotify_track_id IS NOT NULL
                      AND (ar.name LIKE ? OR t.genres LIKE ?)
                    ORDER BY RANDOM()
                    LIMIT ?
                """, (f'%{category}%', f'%{category}%', limit))

                rows = cursor.fetchall()
                return [dict(row) for row in rows]

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

            # Step 1: Get similar artists for each seed
            all_similar_artists = []
            seen_artist_ids = set(seed_artist_ids)  # Don't include seed artists themselves

            for seed_artist_id in seed_artist_ids:
                try:
                    # Get similar artists from Spotify
                    similar = self.spotify_client.get_similar_artists(seed_artist_id)

                    if similar:
                        for artist in similar[:10]:  # Max 10 per seed
                            if artist.id not in seen_artist_ids:
                                all_similar_artists.append(artist)
                                seen_artist_ids.add(artist.id)

                                if len(all_similar_artists) >= 25:
                                    break

                    if len(all_similar_artists) >= 25:
                        break

                except Exception as e:
                    logger.warning(f"Error getting similar artists for {seed_artist_id}: {e}")
                    continue

            logger.info(f"Found {len(all_similar_artists)} similar artists")

            if not all_similar_artists:
                return {'tracks': [], 'error': 'No similar artists found'}

            # Limit to 25 similar artists
            similar_artists_to_use = all_similar_artists[:25]

            # Step 2: Get albums from similar artists
            all_albums = []
            for artist in similar_artists_to_use:
                try:
                    albums = self.spotify_client.get_artist_albums(
                        artist.id,
                        album_type='album,single',
                        limit=10
                    )

                    if albums:
                        all_albums.extend(albums)

                    import time
                    time.sleep(0.3)  # Rate limiting

                except Exception as e:
                    logger.warning(f"Error getting albums for {artist.name}: {e}")
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
                                all_tracks.append({
                                    'spotify_track_id': track['id'],
                                    'track_name': track['name'],
                                    'artist_name': ', '.join([a['name'] for a in track.get('artists', [])]),
                                    'album_name': album_data.get('name', 'Unknown'),
                                    'album_cover_url': album_data.get('images', [{}])[0].get('url') if album_data.get('images') else None,
                                    'duration_ms': track.get('duration_ms', 0),
                                    'popularity': album_data.get('popularity', 0)
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
                'similar_artists_count': len(similar_artists_to_use),
                'albums_used': len(selected_albums)
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
