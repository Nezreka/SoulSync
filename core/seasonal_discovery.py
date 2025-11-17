#!/usr/bin/env python3

"""
Seasonal Discovery Service - Provides seasonal/holiday music content
"""

from typing import List, Dict, Any, Optional
from datetime import datetime
from dataclasses import dataclass
import random
from utils.logging_config import get_logger

logger = get_logger("seasonal_discovery")

# Seasonal configuration with keywords and active periods
SEASONAL_CONFIG = {
    "halloween": {
        "name": "Halloween Hits",
        "description": "Spooky albums and tracks for Halloween",
        "keywords": ["halloween", "spooky", "horror", "monster", "witch", "zombie", "ghost", "haunted", "scary"],
        "active_months": [10],  # October
        "playlist_size": 50,
        "icon": "🎃"
    },
    "christmas": {
        "name": "Christmas Classics",
        "description": "Holiday music and Christmas favorites",
        "keywords": ["christmas", "xmas", "holiday", "santa", "jingle", "winter wonderland", "sleigh", "noel", "carol"],
        "active_months": [11, 12],  # November-December
        "playlist_size": 50,
        "icon": "🎄"
    },
    "valentines": {
        "name": "Love Songs",
        "description": "Romantic tracks for Valentine's Day",
        "keywords": ["love", "valentine", "romance", "heart", "romantic", "darling"],
        "active_months": [2],  # February
        "playlist_size": 50,
        "icon": "❤️"
    },
    "summer": {
        "name": "Summer Vibes",
        "description": "Hot tracks for summer days",
        "keywords": ["summer", "beach", "sun", "vacation", "tropical", "poolside", "sunshine"],
        "active_months": [6, 7, 8],  # June-August
        "playlist_size": 50,
        "icon": "☀️"
    },
    "spring": {
        "name": "Spring Awakening",
        "description": "Fresh sounds for spring",
        "keywords": ["spring", "bloom", "fresh", "renewal", "garden", "flower"],
        "active_months": [3, 4, 5],  # March-May
        "playlist_size": 50,
        "icon": "🌸"
    },
    "autumn": {
        "name": "Autumn Sounds",
        "description": "Cozy tracks for fall",
        "keywords": ["fall", "autumn", "harvest", "leaves", "cozy", "pumpkin"],
        "active_months": [9, 10, 11],  # September-November (overlaps with Halloween)
        "playlist_size": 50,
        "icon": "🍂"
    }
}

@dataclass
class SeasonalAlbum:
    """Represents a seasonal album"""
    spotify_id: str
    title: str
    artist_name: str
    cover_url: Optional[str]
    release_date: Optional[str]
    popularity: int
    season_key: str

@dataclass
class SeasonalTrack:
    """Represents a seasonal track"""
    spotify_id: str
    title: str
    artist_name: str
    album_name: str
    album_cover_url: Optional[str]
    duration_ms: int
    popularity: int
    season_key: str

class SeasonalDiscoveryService:
    """Service for managing seasonal music discovery"""

    def __init__(self, spotify_client, database):
        self.spotify_client = spotify_client
        self.database = database
        self._ensure_database_schema()

    def _ensure_database_schema(self):
        """Create seasonal content tables if they don't exist"""
        try:
            with self.database._get_connection() as conn:
                cursor = conn.cursor()

                # Seasonal albums cache
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS seasonal_albums (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        season_key TEXT NOT NULL,
                        spotify_album_id TEXT NOT NULL,
                        album_name TEXT,
                        artist_name TEXT,
                        album_cover_url TEXT,
                        release_date TEXT,
                        popularity INTEGER DEFAULT 0,
                        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(season_key, spotify_album_id)
                    )
                """)

                # Seasonal tracks cache
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS seasonal_tracks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        season_key TEXT NOT NULL,
                        spotify_track_id TEXT NOT NULL,
                        track_name TEXT,
                        artist_name TEXT,
                        album_name TEXT,
                        album_cover_url TEXT,
                        duration_ms INTEGER,
                        popularity INTEGER DEFAULT 0,
                        track_data_json TEXT,
                        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(season_key, spotify_track_id)
                    )
                """)

                # Curated seasonal playlists
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS curated_seasonal_playlists (
                        season_key TEXT PRIMARY KEY,
                        track_ids TEXT,
                        curated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        track_count INTEGER DEFAULT 0
                    )
                """)

                # Metadata about last seasonal update
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS seasonal_metadata (
                        season_key TEXT PRIMARY KEY,
                        last_populated_at TIMESTAMP,
                        album_count INTEGER DEFAULT 0,
                        track_count INTEGER DEFAULT 0
                    )
                """)

                conn.commit()
                logger.info("Seasonal discovery database schema initialized")

        except Exception as e:
            logger.error(f"Error creating seasonal database schema: {e}")

    def get_current_season(self) -> Optional[str]:
        """
        Detect current season based on current month.

        Returns:
            Season key (e.g., 'halloween', 'christmas') or None
        """
        current_month = datetime.now().month

        # Check each season to find active ones
        active_seasons = []
        for season_key, config in SEASONAL_CONFIG.items():
            if current_month in config['active_months']:
                active_seasons.append(season_key)

        if not active_seasons:
            return None

        # Prioritize specific holidays over general seasons
        # Halloween > Autumn, Christmas > Winter, etc.
        priority_order = ['halloween', 'christmas', 'valentines', 'summer', 'spring', 'autumn']

        for priority_season in priority_order:
            if priority_season in active_seasons:
                return priority_season

        return active_seasons[0] if active_seasons else None

    def get_all_active_seasons(self) -> List[str]:
        """Get all seasons active in current month (for displaying multiple sections)"""
        current_month = datetime.now().month

        active_seasons = []
        for season_key, config in SEASONAL_CONFIG.items():
            if current_month in config['active_months']:
                active_seasons.append(season_key)

        return active_seasons

    def should_populate_seasonal_content(self, season_key: str, days_threshold: int = 7) -> bool:
        """
        Check if seasonal content should be re-populated.

        Args:
            season_key: Season to check
            days_threshold: Minimum days since last population (default: 7)

        Returns:
            True if should populate, False otherwise
        """
        try:
            with self.database._get_connection() as conn:
                cursor = conn.cursor()

                cursor.execute("""
                    SELECT last_populated_at
                    FROM seasonal_metadata
                    WHERE season_key = ?
                """, (season_key,))

                result = cursor.fetchone()

                if not result or not result['last_populated_at']:
                    return True  # Never populated

                last_populated = datetime.fromisoformat(result['last_populated_at'])
                days_since = (datetime.now() - last_populated).days

                return days_since >= days_threshold

        except Exception as e:
            logger.error(f"Error checking seasonal population status: {e}")
            return True  # Populate if we can't determine

    def populate_seasonal_content(self, season_key: str):
        """
        Populate seasonal content from multiple sources:
        1. Discovery pool keyword search
        2. Spotify search from watchlist/similar artists
        3. General Spotify seasonal search

        Args:
            season_key: Season to populate (e.g., 'halloween', 'christmas')
        """
        try:
            if season_key not in SEASONAL_CONFIG:
                logger.error(f"Unknown season key: {season_key}")
                return

            config = SEASONAL_CONFIG[season_key]
            logger.info(f"Populating seasonal content for: {config['name']}")

            # Clear existing seasonal content for this season
            self._clear_seasonal_content(season_key)

            albums_found = 0
            tracks_found = 0

            # Source 1: Search discovery pool for seasonal tracks
            logger.info(f"Searching discovery pool for {season_key} tracks...")
            pool_tracks = self._search_discovery_pool_seasonal(season_key)
            for track in pool_tracks:
                if self._add_seasonal_track(season_key, track):
                    tracks_found += 1

            logger.info(f"Found {len(pool_tracks)} tracks from discovery pool")

            # Source 2: Search Spotify for seasonal albums from watchlist artists
            logger.info(f"Searching Spotify for {season_key} albums from watchlist artists...")
            watchlist_albums = self._search_watchlist_seasonal_albums(season_key)
            for album in watchlist_albums:
                if self._add_seasonal_album(season_key, album):
                    albums_found += 1

            logger.info(f"Found {len(watchlist_albums)} albums from watchlist artists")

            # Source 3: General Spotify search for seasonal content
            logger.info(f"Searching Spotify for {season_key} albums...")
            spotify_albums = self._search_spotify_seasonal_albums(season_key, limit=20)
            for album in spotify_albums:
                if self._add_seasonal_album(season_key, album):
                    albums_found += 1

            logger.info(f"Found {len(spotify_albums)} albums from general Spotify search")

            # Update metadata
            self._update_seasonal_metadata(season_key, albums_found, tracks_found)

            logger.info(f"Seasonal content populated for {config['name']}: {albums_found} albums, {tracks_found} tracks")

        except Exception as e:
            logger.error(f"Error populating seasonal content for {season_key}: {e}")
            import traceback
            traceback.print_exc()

    def _search_discovery_pool_seasonal(self, season_key: str) -> List[Dict]:
        """Search discovery pool for tracks matching seasonal keywords"""
        try:
            config = SEASONAL_CONFIG[season_key]
            keywords = config['keywords']

            seasonal_tracks = []

            with self.database._get_connection() as conn:
                cursor = conn.cursor()

                # Build keyword search query
                keyword_conditions = " OR ".join([f"LOWER(track_name) LIKE ?" for _ in keywords])
                keyword_conditions += " OR " + " OR ".join([f"LOWER(album_name) LIKE ?" for _ in keywords])

                keyword_params = [f"%{kw}%" for kw in keywords] + [f"%{kw}%" for kw in keywords]

                cursor.execute(f"""
                    SELECT DISTINCT
                        spotify_track_id,
                        track_name,
                        artist_name,
                        album_name,
                        album_cover_url,
                        duration_ms,
                        popularity,
                        track_data_json
                    FROM discovery_pool
                    WHERE {keyword_conditions}
                    LIMIT 100
                """, keyword_params)

                rows = cursor.fetchall()

                for row in rows:
                    seasonal_tracks.append({
                        'spotify_track_id': row['spotify_track_id'],
                        'track_name': row['track_name'],
                        'artist_name': row['artist_name'],
                        'album_name': row['album_name'],
                        'album_cover_url': row['album_cover_url'],
                        'duration_ms': row['duration_ms'],
                        'popularity': row['popularity'],
                        'track_data_json': row['track_data_json']
                    })

                return seasonal_tracks

        except Exception as e:
            logger.error(f"Error searching discovery pool for seasonal tracks: {e}")
            return []

    def _search_watchlist_seasonal_albums(self, season_key: str) -> List[Dict]:
        """Search for seasonal albums from watchlist artists"""
        try:
            if not self.spotify_client or not self.spotify_client.is_authenticated():
                return []

            config = SEASONAL_CONFIG[season_key]
            keywords = config['keywords']

            watchlist_artists = self.database.get_watchlist_artists()
            if not watchlist_artists:
                return []

            seasonal_albums = []

            # Sample 10 random watchlist artists to avoid API abuse
            sampled_artists = random.sample(watchlist_artists, min(10, len(watchlist_artists)))

            for artist in sampled_artists:
                try:
                    # Get artist's albums
                    albums = self.spotify_client.get_artist_albums(
                        artist.spotify_artist_id,
                        album_type='album,single',
                        limit=50
                    )

                    # Filter albums by seasonal keywords in title
                    for album in albums:
                        album_name_lower = album.name.lower()

                        if any(keyword in album_name_lower for keyword in keywords):
                            seasonal_albums.append({
                                'spotify_album_id': album.id,
                                'album_name': album.name,
                                'artist_name': artist.artist_name,
                                'album_cover_url': album.image_url if hasattr(album, 'image_url') else None,
                                'release_date': album.release_date if hasattr(album, 'release_date') else None,
                                'popularity': getattr(album, 'popularity', 50)
                            })

                    import time
                    time.sleep(0.5)  # Rate limiting

                except Exception as e:
                    logger.debug(f"Error searching albums for {artist.artist_name}: {e}")
                    continue

            return seasonal_albums

        except Exception as e:
            logger.error(f"Error searching watchlist seasonal albums: {e}")
            return []

    def _search_spotify_seasonal_albums(self, season_key: str, limit: int = 20) -> List[Dict]:
        """Search Spotify for seasonal albums using keyword search"""
        try:
            if not self.spotify_client or not self.spotify_client.is_authenticated():
                return []

            config = SEASONAL_CONFIG[season_key]
            keywords = config['keywords']

            seasonal_albums = []
            seen_album_ids = set()

            # Search with top 3 keywords
            for keyword in keywords[:3]:
                try:
                    # Search for albums
                    search_results = self.spotify_client.search_albums(keyword, limit=10)

                    for album in search_results:
                        if album.id in seen_album_ids:
                            continue

                        seen_album_ids.add(album.id)

                        seasonal_albums.append({
                            'spotify_album_id': album.id,
                            'album_name': album.name,
                            'artist_name': album.artist if hasattr(album, 'artist') else 'Various Artists',
                            'album_cover_url': album.image_url if hasattr(album, 'image_url') else None,
                            'release_date': album.release_date if hasattr(album, 'release_date') else None,
                            'popularity': getattr(album, 'popularity', 50)
                        })

                    import time
                    time.sleep(0.3)  # Rate limiting

                except Exception as e:
                    logger.debug(f"Error searching Spotify for '{keyword}': {e}")
                    continue

            # Limit total albums
            return seasonal_albums[:limit]

        except Exception as e:
            logger.error(f"Error searching Spotify seasonal albums: {e}")
            return []

    def _add_seasonal_album(self, season_key: str, album_data: Dict) -> bool:
        """Add a seasonal album to the database"""
        try:
            with self.database._get_connection() as conn:
                cursor = conn.cursor()

                cursor.execute("""
                    INSERT OR IGNORE INTO seasonal_albums (
                        season_key, spotify_album_id, album_name, artist_name,
                        album_cover_url, release_date, popularity
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    season_key,
                    album_data['spotify_album_id'],
                    album_data['album_name'],
                    album_data['artist_name'],
                    album_data.get('album_cover_url'),
                    album_data.get('release_date'),
                    album_data.get('popularity', 50)
                ))

                conn.commit()
                return cursor.rowcount > 0

        except Exception as e:
            logger.error(f"Error adding seasonal album: {e}")
            return False

    def _add_seasonal_track(self, season_key: str, track_data: Dict) -> bool:
        """Add a seasonal track to the database"""
        try:
            import json

            with self.database._get_connection() as conn:
                cursor = conn.cursor()

                cursor.execute("""
                    INSERT OR IGNORE INTO seasonal_tracks (
                        season_key, spotify_track_id, track_name, artist_name,
                        album_name, album_cover_url, duration_ms, popularity, track_data_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    season_key,
                    track_data['spotify_track_id'],
                    track_data['track_name'],
                    track_data['artist_name'],
                    track_data['album_name'],
                    track_data.get('album_cover_url'),
                    track_data.get('duration_ms', 0),
                    track_data.get('popularity', 50),
                    json.dumps(track_data.get('track_data_json', {}))
                ))

                conn.commit()
                return cursor.rowcount > 0

        except Exception as e:
            logger.error(f"Error adding seasonal track: {e}")
            return False

    def _clear_seasonal_content(self, season_key: str):
        """Clear existing seasonal content for a season"""
        try:
            with self.database._get_connection() as conn:
                cursor = conn.cursor()

                cursor.execute("DELETE FROM seasonal_albums WHERE season_key = ?", (season_key,))
                cursor.execute("DELETE FROM seasonal_tracks WHERE season_key = ?", (season_key,))

                conn.commit()
                logger.debug(f"Cleared existing seasonal content for {season_key}")

        except Exception as e:
            logger.error(f"Error clearing seasonal content: {e}")

    def _update_seasonal_metadata(self, season_key: str, album_count: int, track_count: int):
        """Update metadata about seasonal content population"""
        try:
            with self.database._get_connection() as conn:
                cursor = conn.cursor()

                cursor.execute("""
                    INSERT OR REPLACE INTO seasonal_metadata (
                        season_key, last_populated_at, album_count, track_count
                    ) VALUES (?, CURRENT_TIMESTAMP, ?, ?)
                """, (season_key, album_count, track_count))

                conn.commit()

        except Exception as e:
            logger.error(f"Error updating seasonal metadata: {e}")

    def get_seasonal_albums(self, season_key: str, limit: int = 20) -> List[Dict]:
        """Get cached seasonal albums for a season"""
        try:
            with self.database._get_connection() as conn:
                cursor = conn.cursor()

                cursor.execute("""
                    SELECT
                        spotify_album_id,
                        album_name,
                        artist_name,
                        album_cover_url,
                        release_date,
                        popularity
                    FROM seasonal_albums
                    WHERE season_key = ?
                    ORDER BY popularity DESC, album_name ASC
                    LIMIT ?
                """, (season_key, limit))

                rows = cursor.fetchall()

                return [dict(row) for row in rows]

        except Exception as e:
            logger.error(f"Error getting seasonal albums: {e}")
            return []

    def curate_seasonal_playlist(self, season_key: str):
        """
        Curate a seasonal playlist using Spotify-quality algorithm.

        Strategy:
        - Pulls tracks from seasonal albums
        - Balances by artist (max 3 per artist)
        - Mixes popular + mid-tier + deep cuts (60/30/10 split)
        - Saves curated playlist to database
        """
        try:
            if season_key not in SEASONAL_CONFIG:
                logger.error(f"Unknown season key: {season_key}")
                return

            config = SEASONAL_CONFIG[season_key]
            playlist_size = config['playlist_size']

            logger.info(f"Curating seasonal playlist for: {config['name']}")

            # Get all seasonal tracks for this season
            all_tracks = []

            # Get tracks from seasonal_tracks table
            with self.database._get_connection() as conn:
                cursor = conn.cursor()

                cursor.execute("""
                    SELECT
                        spotify_track_id,
                        track_name,
                        artist_name,
                        album_name,
                        popularity
                    FROM seasonal_tracks
                    WHERE season_key = ?
                """, (season_key,))

                rows = cursor.fetchall()
                all_tracks.extend([dict(row) for row in rows])

            # Get tracks from seasonal albums
            seasonal_albums = self.get_seasonal_albums(season_key, limit=30)

            for album in seasonal_albums:
                try:
                    if not self.spotify_client or not self.spotify_client.is_authenticated():
                        break

                    # Get album tracks
                    album_data = self.spotify_client.get_album(album['spotify_album_id'])
                    if album_data and 'tracks' in album_data:
                        for track in album_data['tracks'].get('items', []):
                            # Use track's actual artist, not album artist
                            track_artist = track['artists'][0]['name'] if track.get('artists') else album['artist_name']
                            all_tracks.append({
                                'spotify_track_id': track['id'],
                                'track_name': track['name'],
                                'artist_name': track_artist,
                                'album_name': album['album_name'],
                                'popularity': album.get('popularity', 50)
                            })

                    import time
                    time.sleep(0.3)  # Rate limiting

                except Exception as e:
                    logger.debug(f"Error getting tracks from album {album['album_name']}: {e}")
                    continue

            if not all_tracks:
                logger.warning(f"No tracks found for seasonal playlist: {season_key}")
                return

            logger.info(f"Found {len(all_tracks)} total tracks for {season_key} curation")

            # Balance by artist - max 3 tracks per artist
            tracks_by_artist = {}
            for track in all_tracks:
                artist = track['artist_name']
                if artist not in tracks_by_artist:
                    tracks_by_artist[artist] = []
                tracks_by_artist[artist].append(track)

            balanced_tracks = []
            for artist, artist_tracks in tracks_by_artist.items():
                # Sort by popularity and take top 3
                sorted_tracks = sorted(artist_tracks, key=lambda t: t.get('popularity', 50), reverse=True)
                balanced_tracks.extend(sorted_tracks[:3])

            # Separate by popularity tiers
            popular = [t for t in balanced_tracks if t.get('popularity', 50) >= 60]
            mid_tier = [t for t in balanced_tracks if 40 <= t.get('popularity', 50) < 60]
            deep_cuts = [t for t in balanced_tracks if t.get('popularity', 50) < 40]

            # Shuffle each tier
            random.shuffle(popular)
            random.shuffle(mid_tier)
            random.shuffle(deep_cuts)

            # Create balanced mix (60% popular, 30% mid-tier, 10% deep cuts)
            curated_tracks = []
            curated_tracks.extend(popular[:int(playlist_size * 0.6)])
            curated_tracks.extend(mid_tier[:int(playlist_size * 0.3)])
            curated_tracks.extend(deep_cuts[:int(playlist_size * 0.1)])

            # Shuffle final selection
            random.shuffle(curated_tracks)
            curated_tracks = curated_tracks[:playlist_size]

            # Extract track IDs
            track_ids = [track['spotify_track_id'] for track in curated_tracks]

            # Save curated playlist
            self._save_curated_playlist(season_key, track_ids)

            logger.info(f"Curated {len(track_ids)} tracks for {config['name']} playlist")

        except Exception as e:
            logger.error(f"Error curating seasonal playlist for {season_key}: {e}")
            import traceback
            traceback.print_exc()

    def _save_curated_playlist(self, season_key: str, track_ids: List[str]):
        """Save curated playlist to database"""
        try:
            import json

            with self.database._get_connection() as conn:
                cursor = conn.cursor()

                cursor.execute("""
                    INSERT OR REPLACE INTO curated_seasonal_playlists (
                        season_key, track_ids, curated_at, track_count
                    ) VALUES (?, ?, CURRENT_TIMESTAMP, ?)
                """, (season_key, json.dumps(track_ids), len(track_ids)))

                conn.commit()

        except Exception as e:
            logger.error(f"Error saving curated seasonal playlist: {e}")

    def get_curated_seasonal_playlist(self, season_key: str) -> List[str]:
        """Get curated seasonal playlist track IDs"""
        try:
            import json

            with self.database._get_connection() as conn:
                cursor = conn.cursor()

                cursor.execute("""
                    SELECT track_ids
                    FROM curated_seasonal_playlists
                    WHERE season_key = ?
                """, (season_key,))

                result = cursor.fetchone()

                if result and result['track_ids']:
                    return json.loads(result['track_ids'])

                return []

        except Exception as e:
            logger.error(f"Error getting curated seasonal playlist: {e}")
            return []

    def populate_all_seasons(self):
        """Populate content for all seasons (run periodically)"""
        logger.info("Starting population of all seasonal content...")

        for season_key in SEASONAL_CONFIG.keys():
            try:
                # Check if needs update (7 day threshold)
                if self.should_populate_seasonal_content(season_key, days_threshold=7):
                    logger.info(f"Populating {season_key}...")
                    self.populate_seasonal_content(season_key)
                    self.curate_seasonal_playlist(season_key)
                else:
                    logger.info(f"Skipping {season_key} (recently updated)")
            except Exception as e:
                logger.error(f"Error populating season {season_key}: {e}")
                continue

        logger.info("Finished populating all seasonal content")


# Singleton instance
_seasonal_discovery_instance = None

def get_seasonal_discovery_service(spotify_client, database):
    """Get the global seasonal discovery service instance"""
    global _seasonal_discovery_instance
    if _seasonal_discovery_instance is None:
        _seasonal_discovery_instance = SeasonalDiscoveryService(spotify_client, database)
    return _seasonal_discovery_instance
