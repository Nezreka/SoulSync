#!/usr/bin/env python3

"""
Personalized Playlists Service - Creates Spotify-quality personalized playlists
from user's library and discovery pool
"""

from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timedelta
from collections import Counter
import random
import json
from utils.logging_config import get_logger

logger = get_logger("personalized_playlists")

class PersonalizedPlaylistsService:
    """Service for generating personalized playlists from library and discovery pool"""

    # Genre consolidation mapping - maps specific Spotify genres to broad parent categories
    GENRE_MAPPING = {
        'Electronic/Dance': [
            'house', 'techno', 'trance', 'edm', 'electro', 'dubstep', 'drum and bass',
            'breakbeat', 'jungle', 'dnb', 'bass', 'garage', 'uk garage', 'future bass',
            'trap', 'hardstyle', 'hardcore', 'rave', 'dance', 'electronic', 'electronica',
            'synth', 'downtempo', 'chillwave', 'vaporwave', 'synthwave', 'idm', 'glitch'
        ],
        'Hip Hop/Rap': [
            'hip hop', 'rap', 'trap', 'drill', 'grime', 'boom bap', 'underground hip hop',
            'conscious hip hop', 'gangsta rap', 'southern hip hop', 'east coast', 'west coast',
            'crunk', 'hyphy', 'cloud rap', 'emo rap', 'mumble rap'
        ],
        'Rock': [
            'rock', 'alternative rock', 'indie rock', 'garage rock', 'post-punk', 'punk',
            'hard rock', 'psychedelic rock', 'progressive rock', 'art rock', 'glam rock',
            'blues rock', 'southern rock', 'surf rock', 'rockabilly', 'grunge', 'shoegaze',
            'noise rock', 'post-rock', 'math rock', 'emo', 'screamo'
        ],
        'Pop': [
            'pop', 'dance pop', 'electropop', 'synth pop', 'indie pop', 'chamber pop',
            'art pop', 'baroque pop', 'dream pop', 'power pop', 'bubblegum pop', 'k-pop',
            'j-pop', 'hyperpop', 'pop rock', 'teen pop'
        ],
        'R&B/Soul': [
            'r&b', 'soul', 'neo soul', 'contemporary r&b', 'alternative r&b', 'funk',
            'disco', 'motown', 'northern soul', 'quiet storm', 'new jack swing'
        ],
        'Jazz': [
            'jazz', 'bebop', 'cool jazz', 'hard bop', 'modal jazz', 'free jazz',
            'fusion', 'jazz fusion', 'smooth jazz', 'contemporary jazz', 'latin jazz',
            'afro-cuban jazz', 'swing', 'big band', 'ragtime', 'dixieland'
        ],
        'Classical': [
            'classical', 'baroque', 'romantic', 'contemporary classical', 'minimalism',
            'opera', 'orchestral', 'chamber music', 'choral', 'renaissance', 'medieval'
        ],
        'Metal': [
            'metal', 'heavy metal', 'thrash metal', 'death metal', 'black metal',
            'doom metal', 'power metal', 'progressive metal', 'metalcore', 'deathcore',
            'djent', 'nu metal', 'industrial metal', 'symphonic metal', 'gothic metal'
        ],
        'Country': [
            'country', 'bluegrass', 'americana', 'outlaw country', 'country rock',
            'alt-country', 'contemporary country', 'traditional country', 'honky tonk',
            'western', 'nashville sound'
        ],
        'Folk/Indie': [
            'folk', 'indie folk', 'folk rock', 'freak folk', 'anti-folk', 'singer-songwriter',
            'acoustic', 'indie', 'lo-fi', 'bedroom pop', 'slowcore', 'sadcore'
        ],
        'Latin': [
            'latin', 'reggaeton', 'salsa', 'bachata', 'merengue', 'cumbia', 'banda',
            'regional mexican', 'mariachi', 'ranchera', 'corrido', 'latin pop',
            'latin trap', 'urbano latino', 'bossa nova', 'samba', 'tango'
        ],
        'Reggae/Dancehall': [
            'reggae', 'dancehall', 'dub', 'roots reggae', 'ska', 'rocksteady',
            'lovers rock', 'reggae fusion'
        ],
        'World': [
            'afrobeat', 'afropop', 'african', 'world', 'worldbeat', 'ethnic',
            'traditional', 'folk music', 'celtic', 'klezmer', 'flamenco', 'fado',
            'indian classical', 'raga', 'qawwali', 'k-indie', 'j-indie'
        ],
        'Alternative': [
            'alternative', 'experimental', 'avant-garde', 'noise', 'ambient',
            'industrial', 'new wave', 'no wave', 'gothic', 'darkwave', 'coldwave',
            'witch house', 'trip hop', 'downtempo'
        ],
        'Blues': [
            'blues', 'delta blues', 'chicago blues', 'electric blues', 'blues rock',
            'rhythm and blues', 'soul blues', 'gospel blues'
        ],
        'Funk/Disco': [
            'funk', 'disco', 'p-funk', 'boogie', 'electro-funk', 'g-funk'
        ]
    }

    def __init__(self, database, spotify_client=None):
        self.database = database
        self.spotify_client = spotify_client

    @staticmethod
    def get_parent_genre(spotify_genre: str) -> str:
        """
        Map a specific Spotify genre to its parent category.
        Returns the parent genre or 'Other' if no match found.
        """
        spotify_genre_lower = spotify_genre.lower()

        for parent_genre, keywords in PersonalizedPlaylistsService.GENRE_MAPPING.items():
            for keyword in keywords:
                if keyword in spotify_genre_lower:
                    return parent_genre

        return 'Other'

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
                        release_date,
                        track_data_json
                    FROM discovery_pool
                    WHERE release_date IS NOT NULL
                      AND CAST(SUBSTR(release_date, 1, 4) AS INTEGER) BETWEEN ? AND ?
                    ORDER BY RANDOM()
                    LIMIT ?
                """, (start_year, end_year, limit * 10))

                rows = cursor.fetchall()
                all_tracks = []
                for row in rows:
                    track_dict = dict(row)
                    # Parse track_data_json if available
                    if track_dict.get('track_data_json'):
                        try:
                            import json
                            track_dict['track_data_json'] = json.loads(track_dict['track_data_json'])
                        except:
                            pass
                    all_tracks.append(track_dict)

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

    def get_available_genres(self) -> List[Dict]:
        """
        Get list of consolidated parent genres with track counts from discovery pool.
        Uses cached artist genres from database (populated during discovery scan).
        Consolidates specific Spotify genres into broader parent categories.
        """
        try:
            with self.database._get_connection() as conn:
                cursor = conn.cursor()

                # Get all tracks with genres from discovery pool
                cursor.execute("""
                    SELECT artist_genres
                    FROM discovery_pool
                    WHERE artist_genres IS NOT NULL
                """)
                rows = cursor.fetchall()

                if not rows:
                    logger.warning("No genres found in discovery pool - genres may not be populated yet")
                    return []

                # Count tracks per PARENT genre (consolidated)
                parent_genre_track_count = {}  # {parent_genre: count}

                for row in rows:
                    try:
                        artist_genres_json = row[0]
                        if artist_genres_json:
                            genres = json.loads(artist_genres_json)
                            # Map each Spotify genre to parent and count tracks
                            mapped_parents = set()  # Use set to avoid double-counting per track
                            for genre in genres:
                                parent_genre = self.get_parent_genre(genre)
                                mapped_parents.add(parent_genre)

                            # Add this track to all parent genres
                            for parent_genre in mapped_parents:
                                parent_genre_track_count[parent_genre] = parent_genre_track_count.get(parent_genre, 0) + 1
                    except Exception as e:
                        logger.debug(f"Error parsing genres JSON: {e}")
                        continue

                # Filter genres with at least 10 tracks and sort by count
                # Exclude 'Other' category
                available_genres = [
                    {'name': genre, 'track_count': count}
                    for genre, count in parent_genre_track_count.items()
                    if count >= 10 and genre != 'Other'
                ]
                available_genres.sort(key=lambda x: x['track_count'], reverse=True)

                logger.info(f"Found {len(available_genres)} consolidated genres with 10+ tracks")
                return available_genres[:20]  # Top 20 parent genres

        except Exception as e:
            logger.error(f"Error getting available genres: {e}")
            return []

    def get_genre_playlist(self, genre: str, limit: int = 50) -> List[Dict]:
        """
        Get tracks from a specific genre with diversity filtering.
        Uses cached artist genres from database (populated during discovery scan).
        Supports both parent genres (e.g., "Electronic/Dance") and specific genres (e.g., "house").
        """
        try:
            with self.database._get_connection() as conn:
                cursor = conn.cursor()

                # Get all tracks with genres from discovery pool
                cursor.execute("""
                    SELECT
                        spotify_track_id,
                        track_name,
                        artist_name,
                        album_name,
                        album_cover_url,
                        duration_ms,
                        popularity,
                        artist_genres,
                        track_data_json
                    FROM discovery_pool
                    WHERE artist_genres IS NOT NULL
                """)
                rows = cursor.fetchall()

                # Determine if this is a parent genre or specific genre
                is_parent_genre = genre in self.GENRE_MAPPING
                search_keywords = []

                if is_parent_genre:
                    # Use all child genre keywords for matching
                    search_keywords = self.GENRE_MAPPING[genre]
                    logger.info(f"Matching parent genre '{genre}' with {len(search_keywords)} child keywords")
                else:
                    # Use the genre name itself for partial matching
                    search_keywords = [genre.lower()]
                    logger.info(f"Matching specific genre '{genre}' with partial matching")

                # Filter tracks that match the genre
                matching_tracks = []

                for row in rows:
                    try:
                        artist_genres_json = row[7]  # artist_genres column
                        if artist_genres_json:
                            genres = json.loads(artist_genres_json)

                            # Check if any artist genre matches any search keyword
                            genre_match = False
                            for artist_genre in genres:
                                artist_genre_lower = artist_genre.lower()
                                for keyword in search_keywords:
                                    if keyword in artist_genre_lower:
                                        genre_match = True
                                        break
                                if genre_match:
                                    break

                            if genre_match:
                                # Convert row to dict (exclude artist_genres from output)
                                track_dict = {
                                    'spotify_track_id': row[0],
                                    'track_name': row[1],
                                    'artist_name': row[2],
                                    'album_name': row[3],
                                    'album_cover_url': row[4],
                                    'duration_ms': row[5],
                                    'popularity': row[6]
                                }
                                # Parse track_data_json if available
                                if row[8]:  # track_data_json column
                                    try:
                                        track_dict['track_data_json'] = json.loads(row[8])
                                    except:
                                        pass
                                matching_tracks.append(track_dict)
                    except Exception as e:
                        logger.debug(f"Error parsing genres for track: {e}")
                        continue

                if not matching_tracks:
                    logger.warning(f"No tracks found for genre: {genre}")
                    return []

                # Shuffle before limiting for better variety
                random.shuffle(matching_tracks)

                # Limit to 10x for diversity filtering
                all_tracks = matching_tracks[:limit * 10] if len(matching_tracks) > limit * 10 else matching_tracks

                if not all_tracks:
                    return []

                # Apply adaptive diversity filtering (relaxed for genres)
                unique_artists = len(set(track['artist_name'] for track in all_tracks))

                if unique_artists >= 20:
                    max_per_album = 3
                    max_per_artist = 5
                elif unique_artists >= 10:
                    max_per_album = 4
                    max_per_artist = 10
                elif unique_artists >= 5:
                    max_per_album = 6
                    max_per_artist = 15
                else:
                    # Very limited artist pool - be more lenient
                    max_per_album = 8
                    max_per_artist = 25

                logger.info(f"Genre '{genre}' has {unique_artists} artists, {len(all_tracks)} total tracks - limits: {max_per_album}/album, {max_per_artist}/artist")

                # Shuffle and apply diversity
                random.shuffle(all_tracks)
                tracks_by_album = {}
                tracks_by_artist = {}
                diverse_tracks = []

                for track in all_tracks:
                    album = track['album_name']
                    artist = track['artist_name']

                    album_count = tracks_by_album.get(album, 0)
                    artist_count = tracks_by_artist.get(artist, 0)

                    if album_count < max_per_album and artist_count < max_per_artist:
                        diverse_tracks.append(track)
                        tracks_by_album[album] = album_count + 1
                        tracks_by_artist[artist] = artist_count + 1

                        if len(diverse_tracks) >= limit:
                            break

                logger.info(f"Found {len(diverse_tracks)} tracks for genre '{genre}'")
                return diverse_tracks[:limit]

        except Exception as e:
            logger.error(f"Error getting genre playlist for {genre}: {e}")
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
                        popularity,
                        track_data_json
                    FROM discovery_pool
                    WHERE popularity >= 60
                    ORDER BY popularity DESC, RANDOM()
                    LIMIT ?
                """, (limit * 3,))  # Get 3x more for diversity filtering

                rows = cursor.fetchall()
                all_tracks = []
                for row in rows:
                    track_dict = dict(row)
                    # Parse track_data_json if available
                    if track_dict.get('track_data_json'):
                        try:
                            import json
                            track_dict['track_data_json'] = json.loads(track_dict['track_data_json'])
                        except:
                            pass
                    all_tracks.append(track_dict)

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
                        popularity,
                        track_data_json
                    FROM discovery_pool
                    WHERE popularity < 40
                    ORDER BY RANDOM()
                    LIMIT ?
                """, (limit,))

                rows = cursor.fetchall()
                tracks = []
                for row in rows:
                    track_dict = dict(row)
                    # Parse track_data_json if available
                    if track_dict.get('track_data_json'):
                        try:
                            import json
                            track_dict['track_data_json'] = json.loads(track_dict['track_data_json'])
                        except:
                            pass
                    tracks.append(track_dict)
                return tracks

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
                        popularity,
                        track_data_json
                    FROM discovery_pool
                    ORDER BY RANDOM()
                    LIMIT ?
                """, (limit,))

                rows = cursor.fetchall()
                tracks = []
                for row in rows:
                    track_dict = dict(row)
                    # Parse track_data_json if available
                    if track_dict.get('track_data_json'):
                        try:
                            import json
                            track_dict['track_data_json'] = json.loads(track_dict['track_data_json'])
                        except:
                            pass
                    tracks.append(track_dict)
                return tracks

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
                        popularity,
                        track_data_json
                    FROM discovery_pool
                    WHERE artist_name LIKE ? OR track_name LIKE ?
                    ORDER BY RANDOM()
                    LIMIT ?
                """, (f'%{category}%', f'%{category}%', limit))

                rows = cursor.fetchall()
                tracks = []
                for row in rows:
                    track_dict = dict(row)
                    # Parse track_data_json if available
                    if track_dict.get('track_data_json'):
                        try:
                            import json
                            track_dict['track_data_json'] = json.loads(track_dict['track_data_json'])
                        except:
                            pass
                    tracks.append(track_dict)
                return tracks

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
