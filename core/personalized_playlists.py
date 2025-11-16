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


# Singleton instance
_personalized_playlists_instance = None

def get_personalized_playlists_service(database, spotify_client=None):
    """Get the global personalized playlists service instance"""
    global _personalized_playlists_instance
    if _personalized_playlists_instance is None:
        _personalized_playlists_instance = PersonalizedPlaylistsService(database, spotify_client)
    return _personalized_playlists_instance
