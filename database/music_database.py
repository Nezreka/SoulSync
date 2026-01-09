#!/usr/bin/env python3

import sqlite3
import json
import logging
import os
import re
import threading
import time
from datetime import datetime
from typing import List, Optional, Dict, Any, Tuple
from dataclasses import dataclass
from pathlib import Path
from utils.logging_config import get_logger

logger = get_logger("music_database")

# Import matching engine for enhanced similarity logic
try:
    from core.matching_engine import MusicMatchingEngine
    _matching_engine = MusicMatchingEngine()
except ImportError:
    logger.warning("Could not import MusicMatchingEngine, falling back to basic similarity")
    _matching_engine = None
# Temporarily enable debug logging for edition matching
logger.setLevel(logging.DEBUG)

@dataclass
class DatabaseArtist:
    id: int
    name: str
    thumb_url: Optional[str] = None
    genres: Optional[List[str]] = None
    summary: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

@dataclass
class DatabaseAlbum:
    id: int
    artist_id: int
    title: str
    year: Optional[int] = None
    thumb_url: Optional[str] = None
    genres: Optional[List[str]] = None
    track_count: Optional[int] = None
    duration: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

@dataclass
class DatabaseTrack:
    id: int
    album_id: int
    artist_id: int
    title: str
    track_number: Optional[int] = None
    duration: Optional[int] = None
    file_path: Optional[str] = None
    bitrate: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

@dataclass
class DatabaseTrackWithMetadata:
    """Track with joined artist and album names for metadata comparison"""
    id: int
    album_id: int
    artist_id: int
    title: str
    artist_name: str
    album_title: str
    track_number: Optional[int] = None
    duration: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

@dataclass
class WatchlistArtist:
    """Artist being monitored for new releases"""
    id: int
    spotify_artist_id: str
    artist_name: str
    date_added: datetime
    last_scan_timestamp: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    image_url: Optional[str] = None
    include_albums: bool = True
    include_eps: bool = True
    include_singles: bool = True

@dataclass
class SimilarArtist:
    """Similar artist recommendation from Spotify"""
    id: int
    source_artist_id: str  # Watchlist artist's database ID
    similar_artist_spotify_id: str
    similar_artist_name: str
    similarity_rank: int  # 1-10, where 1 is most similar
    occurrence_count: int  # How many watchlist artists share this similar artist
    last_updated: datetime

@dataclass
class DiscoveryTrack:
    """Track in the discovery pool for recommendations"""
    id: int
    spotify_track_id: str
    spotify_album_id: str
    spotify_artist_id: str
    track_name: str
    artist_name: str
    album_name: str
    album_cover_url: Optional[str]
    duration_ms: int
    popularity: int
    release_date: str
    is_new_release: bool  # Released within last 30 days
    track_data_json: str  # Full Spotify track object for modal
    added_date: datetime

@dataclass
class RecentRelease:
    """Recent album release from watchlist artist"""
    id: int
    watchlist_artist_id: int
    album_spotify_id: str
    album_name: str
    release_date: str
    album_cover_url: Optional[str]
    track_count: int
    added_date: datetime

class MusicDatabase:
    """SQLite database manager for SoulSync music library data"""
    
    def __init__(self, database_path: str = None):
        import os
        # Use env var if path is None OR if it's the default path
        # This ensures Docker containers use the correct mounted volume location
        if database_path is None or database_path == "database/music_library.db":
            database_path = os.environ.get('DATABASE_PATH', 'database/music_library.db')
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Initialize database
        self._initialize_database()
    
    def _get_connection(self) -> sqlite3.Connection:
        """Get a NEW database connection for each operation (thread-safe)"""
        connection = sqlite3.connect(str(self.database_path), timeout=30.0)
        connection.row_factory = sqlite3.Row
        # Enable foreign key constraints and WAL mode for better concurrency
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 30000")  # 30 second timeout
        return connection
    
    def _initialize_database(self):
        """Create database tables if they don't exist"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            # Artists table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS artists (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    thumb_url TEXT,
                    genres TEXT,  -- JSON array
                    summary TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Albums table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS albums (
                    id INTEGER PRIMARY KEY,
                    artist_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    year INTEGER,
                    thumb_url TEXT,
                    genres TEXT,  -- JSON array
                    track_count INTEGER,
                    duration INTEGER,  -- milliseconds
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (artist_id) REFERENCES artists (id) ON DELETE CASCADE
                )
            """)
            
            # Tracks table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS tracks (
                    id INTEGER PRIMARY KEY,
                    album_id INTEGER NOT NULL,
                    artist_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    track_number INTEGER,
                    duration INTEGER,  -- milliseconds
                    file_path TEXT,
                    bitrate INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (album_id) REFERENCES albums (id) ON DELETE CASCADE,
                    FOREIGN KEY (artist_id) REFERENCES artists (id) ON DELETE CASCADE
                )
            """)
            
            # Metadata table for storing system information like last refresh dates
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Wishlist table for storing failed download tracks for retry
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS wishlist_tracks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    spotify_track_id TEXT UNIQUE NOT NULL,
                    spotify_data TEXT NOT NULL,  -- JSON of full Spotify track data
                    failure_reason TEXT,
                    retry_count INTEGER DEFAULT 0,
                    last_attempted TIMESTAMP,
                    date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    source_type TEXT DEFAULT 'unknown',  -- 'playlist', 'album', 'manual'
                    source_info TEXT  -- JSON of source context (playlist name, album info, etc.)
                )
            """)
            
            # Watchlist table for storing artists to monitor for new releases
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS watchlist_artists (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    spotify_artist_id TEXT UNIQUE NOT NULL,
                    artist_name TEXT NOT NULL,
                    date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_scan_timestamp TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Create indexes for performance
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_albums_artist_id ON albums (artist_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tracks_album_id ON tracks (album_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tracks_artist_id ON tracks (artist_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_wishlist_spotify_id ON wishlist_tracks (spotify_track_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_watchlist_spotify_id ON watchlist_artists (spotify_artist_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_wishlist_date_added ON wishlist_tracks (date_added)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_artists_name ON artists (name)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_albums_title ON albums (title)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tracks_title ON tracks (title)")
            
            # Add server_source columns for multi-server support (migration)
            self._add_server_source_columns(cursor)

            # Migrate ID columns to support both integer (Plex) and string (Jellyfin) IDs
            self._migrate_id_columns_to_text(cursor)

            # Add discovery feature tables (migration)
            self._add_discovery_tables(cursor)

            # Add image_url column to watchlist_artists (migration)
            self._add_watchlist_artist_image_column(cursor)

            # Add album type filter columns to watchlist_artists (migration)
            self._add_watchlist_album_type_filters(cursor)

            conn.commit()
            logger.info("Database initialized successfully")
            
        except Exception as e:
            logger.error(f"Error initializing database: {e}")
            raise
    
    def _add_server_source_columns(self, cursor):
        """Add server_source columns to existing tables for multi-server support"""
        try:
            # Check if server_source column exists in artists table
            cursor.execute("PRAGMA table_info(artists)")
            artists_columns = [column[1] for column in cursor.fetchall()]
            
            if 'server_source' not in artists_columns:
                cursor.execute("ALTER TABLE artists ADD COLUMN server_source TEXT DEFAULT 'plex'")
                logger.info("Added server_source column to artists table")
            
            # Check if server_source column exists in albums table
            cursor.execute("PRAGMA table_info(albums)")
            albums_columns = [column[1] for column in cursor.fetchall()]
            
            if 'server_source' not in albums_columns:
                cursor.execute("ALTER TABLE albums ADD COLUMN server_source TEXT DEFAULT 'plex'")
                logger.info("Added server_source column to albums table")
            
            # Check if server_source column exists in tracks table
            cursor.execute("PRAGMA table_info(tracks)")
            tracks_columns = [column[1] for column in cursor.fetchall()]
            
            if 'server_source' not in tracks_columns:
                cursor.execute("ALTER TABLE tracks ADD COLUMN server_source TEXT DEFAULT 'plex'")
                logger.info("Added server_source column to tracks table")
                
            # Create indexes for server_source columns for performance
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_artists_server_source ON artists (server_source)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_albums_server_source ON albums (server_source)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tracks_server_source ON tracks (server_source)")
            
        except Exception as e:
            logger.error(f"Error adding server_source columns: {e}")
            # Don't raise - this is a migration, database can still function without it
    
    def _migrate_id_columns_to_text(self, cursor):
        """Migrate ID columns from INTEGER to TEXT to support both Plex (int) and Jellyfin (GUID) IDs"""
        try:
            # Check if migration has already been applied by looking for a specific marker
            cursor.execute("SELECT value FROM metadata WHERE key = 'id_columns_migrated' LIMIT 1")
            migration_done = cursor.fetchone()
            
            if migration_done:
                logger.debug("ID columns migration already applied")
                return
            
            logger.info("Migrating ID columns to support both integer and string IDs...")
            
            # SQLite doesn't support changing column types directly, so we need to recreate tables
            # This is a complex migration - let's do it safely
            
            # Step 1: Create new tables with TEXT IDs
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS artists_new (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    thumb_url TEXT,
                    genres TEXT,
                    summary TEXT,
                    server_source TEXT DEFAULT 'plex',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS albums_new (
                    id TEXT PRIMARY KEY,
                    artist_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    year INTEGER,
                    thumb_url TEXT,
                    genres TEXT,
                    track_count INTEGER,
                    duration INTEGER,
                    server_source TEXT DEFAULT 'plex',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (artist_id) REFERENCES artists_new (id) ON DELETE CASCADE
                )
            """)
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS tracks_new (
                    id TEXT PRIMARY KEY,
                    album_id TEXT NOT NULL,
                    artist_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    track_number INTEGER,
                    duration INTEGER,
                    file_path TEXT,
                    bitrate INTEGER,
                    server_source TEXT DEFAULT 'plex',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (album_id) REFERENCES albums_new (id) ON DELETE CASCADE,
                    FOREIGN KEY (artist_id) REFERENCES artists_new (id) ON DELETE CASCADE
                )
            """)
            
            # Step 2: Copy existing data (converting INTEGER IDs to TEXT)
            cursor.execute("""
                INSERT INTO artists_new (id, name, thumb_url, genres, summary, server_source, created_at, updated_at)
                SELECT CAST(id AS TEXT), name, thumb_url, genres, summary, 
                       COALESCE(server_source, 'plex'), created_at, updated_at 
                FROM artists
            """)
            
            cursor.execute("""
                INSERT INTO albums_new (id, artist_id, title, year, thumb_url, genres, track_count, duration, server_source, created_at, updated_at)
                SELECT CAST(id AS TEXT), CAST(artist_id AS TEXT), title, year, thumb_url, genres, track_count, duration,
                       COALESCE(server_source, 'plex'), created_at, updated_at
                FROM albums
            """)
            
            cursor.execute("""
                INSERT INTO tracks_new (id, album_id, artist_id, title, track_number, duration, file_path, bitrate, server_source, created_at, updated_at)
                SELECT CAST(id AS TEXT), CAST(album_id AS TEXT), CAST(artist_id AS TEXT), title, track_number, duration, file_path, bitrate,
                       COALESCE(server_source, 'plex'), created_at, updated_at
                FROM tracks
            """)
            
            # Step 3: Drop old tables and rename new ones
            cursor.execute("DROP TABLE IF EXISTS tracks")
            cursor.execute("DROP TABLE IF EXISTS albums") 
            cursor.execute("DROP TABLE IF EXISTS artists")
            
            cursor.execute("ALTER TABLE artists_new RENAME TO artists")
            cursor.execute("ALTER TABLE albums_new RENAME TO albums")
            cursor.execute("ALTER TABLE tracks_new RENAME TO tracks")
            
            # Step 4: Recreate indexes
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_albums_artist_id ON albums (artist_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tracks_album_id ON tracks (album_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tracks_artist_id ON tracks (artist_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_artists_server_source ON artists (server_source)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_albums_server_source ON albums (server_source)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tracks_server_source ON tracks (server_source)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_artists_name ON artists (name)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_albums_title ON albums (title)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tracks_title ON tracks (title)")
            
            # Step 5: Mark migration as complete
            cursor.execute("""
                INSERT OR REPLACE INTO metadata (key, value, updated_at) 
                VALUES ('id_columns_migrated', 'true', CURRENT_TIMESTAMP)
            """)
            
            logger.info("ID columns migration completed successfully")
            
        except Exception as e:
            logger.error(f"Error migrating ID columns: {e}")
            # Don't raise - this is a migration, database can still function

    def _add_discovery_tables(self, cursor):
        """Add tables for discovery feature: similar artists, discovery pool, and recent releases"""
        try:
            # Similar Artists table - stores similar artists for each watchlist artist
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS similar_artists (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_artist_id TEXT NOT NULL,
                    similar_artist_spotify_id TEXT NOT NULL,
                    similar_artist_name TEXT NOT NULL,
                    similarity_rank INTEGER DEFAULT 1,
                    occurrence_count INTEGER DEFAULT 1,
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(source_artist_id, similar_artist_spotify_id)
                )
            """)

            # Discovery Pool table - rotating pool of 1000-2000 tracks for recommendations
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS discovery_pool (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    spotify_track_id TEXT UNIQUE NOT NULL,
                    spotify_album_id TEXT NOT NULL,
                    spotify_artist_id TEXT NOT NULL,
                    track_name TEXT NOT NULL,
                    artist_name TEXT NOT NULL,
                    album_name TEXT NOT NULL,
                    album_cover_url TEXT,
                    duration_ms INTEGER,
                    popularity INTEGER DEFAULT 0,
                    release_date TEXT,
                    is_new_release BOOLEAN DEFAULT 0,
                    track_data_json TEXT NOT NULL,
                    added_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Recent Releases table - tracks new releases from watchlist artists
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS recent_releases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    watchlist_artist_id INTEGER NOT NULL,
                    album_spotify_id TEXT NOT NULL,
                    album_name TEXT NOT NULL,
                    release_date TEXT NOT NULL,
                    album_cover_url TEXT,
                    track_count INTEGER DEFAULT 0,
                    added_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(watchlist_artist_id, album_spotify_id),
                    FOREIGN KEY (watchlist_artist_id) REFERENCES watchlist_artists (id) ON DELETE CASCADE
                )
            """)

            # Discovery Recent Albums cache - for discover page recent releases section
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS discovery_recent_albums (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    album_spotify_id TEXT NOT NULL UNIQUE,
                    album_name TEXT NOT NULL,
                    artist_name TEXT NOT NULL,
                    artist_spotify_id TEXT NOT NULL,
                    album_cover_url TEXT,
                    release_date TEXT NOT NULL,
                    album_type TEXT DEFAULT 'album',
                    cached_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Discovery Curated Playlists - store curated track selections for consistency
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS discovery_curated_playlists (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    playlist_type TEXT NOT NULL UNIQUE,
                    track_ids_json TEXT NOT NULL,
                    curated_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Discovery Pool Metadata - track when pool was last populated to prevent over-polling
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS discovery_pool_metadata (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    last_populated_timestamp TIMESTAMP NOT NULL,
                    track_count INTEGER DEFAULT 0,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # ListenBrainz Playlists - cache playlists from ListenBrainz
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS listenbrainz_playlists (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    playlist_mbid TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    creator TEXT,
                    playlist_type TEXT NOT NULL,
                    track_count INTEGER DEFAULT 0,
                    annotation_data TEXT,
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    cached_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # ListenBrainz Tracks - cache tracks for each playlist
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS listenbrainz_tracks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    playlist_id INTEGER NOT NULL,
                    position INTEGER NOT NULL,
                    track_name TEXT NOT NULL,
                    artist_name TEXT NOT NULL,
                    album_name TEXT NOT NULL,
                    duration_ms INTEGER DEFAULT 0,
                    recording_mbid TEXT,
                    release_mbid TEXT,
                    album_cover_url TEXT,
                    additional_metadata TEXT,
                    FOREIGN KEY (playlist_id) REFERENCES listenbrainz_playlists (id) ON DELETE CASCADE,
                    UNIQUE(playlist_id, position)
                )
            """)

            # Create indexes for performance
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_similar_artists_source ON similar_artists (source_artist_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_similar_artists_spotify ON similar_artists (similar_artist_spotify_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_similar_artists_occurrence ON similar_artists (occurrence_count)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_discovery_pool_spotify_track ON discovery_pool (spotify_track_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_discovery_pool_artist ON discovery_pool (spotify_artist_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_discovery_pool_added_date ON discovery_pool (added_date)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_discovery_pool_is_new ON discovery_pool (is_new_release)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_recent_releases_watchlist ON recent_releases (watchlist_artist_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_recent_releases_date ON recent_releases (release_date)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_discovery_recent_albums_date ON discovery_recent_albums (release_date)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_listenbrainz_playlists_type ON listenbrainz_playlists (playlist_type)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_listenbrainz_playlists_mbid ON listenbrainz_playlists (playlist_mbid)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_listenbrainz_tracks_playlist ON listenbrainz_tracks (playlist_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_listenbrainz_tracks_position ON listenbrainz_tracks (playlist_id, position)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_discovery_recent_albums_artist ON discovery_recent_albums (artist_spotify_id)")

            # Add genres column to discovery_pool if it doesn't exist (migration)
            cursor.execute("PRAGMA table_info(discovery_pool)")
            discovery_pool_columns = [column[1] for column in cursor.fetchall()]

            if 'artist_genres' not in discovery_pool_columns:
                cursor.execute("ALTER TABLE discovery_pool ADD COLUMN artist_genres TEXT")
                logger.info("Added artist_genres column to discovery_pool table")

            logger.info("Discovery tables created successfully")

        except Exception as e:
            logger.error(f"Error creating discovery tables: {e}")
            # Don't raise - this is a migration, database can still function

    def _add_watchlist_artist_image_column(self, cursor):
        """Add image_url column to watchlist_artists table"""
        try:
            cursor.execute("PRAGMA table_info(watchlist_artists)")
            columns = [column[1] for column in cursor.fetchall()]

            if 'image_url' not in columns:
                cursor.execute("ALTER TABLE watchlist_artists ADD COLUMN image_url TEXT")
                logger.info("Added image_url column to watchlist_artists table")

        except Exception as e:
            logger.error(f"Error adding image_url column to watchlist_artists: {e}")
            # Don't raise - this is a migration, database can still function

    def _add_watchlist_album_type_filters(self, cursor):
        """Add album type filter columns to watchlist_artists table"""
        try:
            cursor.execute("PRAGMA table_info(watchlist_artists)")
            columns = [column[1] for column in cursor.fetchall()]

            columns_to_add = {
                'include_albums': ('INTEGER', '1'),     # 1 = True (include albums)
                'include_eps': ('INTEGER', '1'),        # 1 = True (include EPs)
                'include_singles': ('INTEGER', '1')     # 1 = True (include singles)
            }

            for column_name, (column_type, default_value) in columns_to_add.items():
                if column_name not in columns:
                    cursor.execute(f"ALTER TABLE watchlist_artists ADD COLUMN {column_name} {column_type} DEFAULT {default_value}")
                    logger.info(f"Added {column_name} column to watchlist_artists table")

        except Exception as e:
            logger.error(f"Error adding album type filter columns to watchlist_artists: {e}")
            # Don't raise - this is a migration, database can still function

    def close(self):
        """Close database connection (no-op since we create connections per operation)"""
        # Each operation creates and closes its own connection, so nothing to do here
        pass
    
    def get_statistics(self) -> Dict[str, int]:
        """Get database statistics for all servers (legacy method)"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                cursor.execute("SELECT COUNT(DISTINCT name) FROM artists")
                artist_count = cursor.fetchone()[0]
                
                cursor.execute("SELECT COUNT(*) FROM albums")
                album_count = cursor.fetchone()[0]
                
                cursor.execute("SELECT COUNT(*) FROM tracks")
                track_count = cursor.fetchone()[0]
                
                return {
                    'artists': artist_count,
                    'albums': album_count,
                    'tracks': track_count
                }
        except Exception as e:
            logger.error(f"Error getting database statistics: {e}")
            return {'artists': 0, 'albums': 0, 'tracks': 0}
    
    def get_statistics_for_server(self, server_source: str = None) -> Dict[str, int]:
        """Get database statistics filtered by server source"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                if server_source:
                    # Get counts for specific server (deduplicate by name like general count)
                    cursor.execute("SELECT COUNT(DISTINCT name) FROM artists WHERE server_source = ?", (server_source,))
                    artist_count = cursor.fetchone()[0]
                    
                    cursor.execute("SELECT COUNT(*) FROM albums WHERE server_source = ?", (server_source,))
                    album_count = cursor.fetchone()[0]
                    
                    cursor.execute("SELECT COUNT(*) FROM tracks WHERE server_source = ?", (server_source,))
                    track_count = cursor.fetchone()[0]
                else:
                    # Get total counts (all servers)
                    cursor.execute("SELECT COUNT(*) FROM artists")
                    artist_count = cursor.fetchone()[0]
                    
                    cursor.execute("SELECT COUNT(*) FROM albums")
                    album_count = cursor.fetchone()[0]
                    
                    cursor.execute("SELECT COUNT(*) FROM tracks")
                    track_count = cursor.fetchone()[0]
                
                return {
                    'artists': artist_count,
                    'albums': album_count,
                    'tracks': track_count
                }
        except Exception as e:
            logger.error(f"Error getting database statistics for {server_source}: {e}")
            return {'artists': 0, 'albums': 0, 'tracks': 0}
    
    def clear_all_data(self):
        """Clear all data from database (for full refresh) - DEPRECATED: Use clear_server_data instead"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                cursor.execute("DELETE FROM tracks")
                cursor.execute("DELETE FROM albums")
                cursor.execute("DELETE FROM artists")
                
                conn.commit()
                
                # VACUUM to actually shrink the database file and reclaim disk space
                logger.info("Vacuuming database to reclaim disk space...")
                cursor.execute("VACUUM")
                
                logger.info("All database data cleared and file compacted")
                
        except Exception as e:
            logger.error(f"Error clearing database: {e}")
            raise
    
    def clear_server_data(self, server_source: str):
        """Clear data for specific server only (server-aware full refresh)"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # Delete only data from the specified server
                # Order matters: tracks -> albums -> artists (foreign key constraints)
                cursor.execute("DELETE FROM tracks WHERE server_source = ?", (server_source,))
                tracks_deleted = cursor.rowcount
                
                cursor.execute("DELETE FROM albums WHERE server_source = ?", (server_source,))
                albums_deleted = cursor.rowcount
                
                cursor.execute("DELETE FROM artists WHERE server_source = ?", (server_source,))
                artists_deleted = cursor.rowcount
                
                conn.commit()
                
                # Only VACUUM if we deleted a significant amount of data
                if tracks_deleted > 1000 or albums_deleted > 100:
                    logger.info("Vacuuming database to reclaim disk space...")
                    cursor.execute("VACUUM")
                
                logger.info(f"Cleared {server_source} data: {artists_deleted} artists, {albums_deleted} albums, {tracks_deleted} tracks")
                
                # Note: Watchlist and wishlist are preserved as they are server-agnostic
                
        except Exception as e:
            logger.error(f"Error clearing {server_source} database data: {e}")
            raise
    
    def cleanup_orphaned_records(self) -> Dict[str, int]:
        """Remove artists and albums that have no associated tracks"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # Find orphaned artists (no tracks)
                cursor.execute("""
                    SELECT COUNT(*) FROM artists 
                    WHERE id NOT IN (SELECT DISTINCT artist_id FROM tracks WHERE artist_id IS NOT NULL)
                """)
                orphaned_artists_count = cursor.fetchone()[0]
                
                # Find orphaned albums (no tracks)
                cursor.execute("""
                    SELECT COUNT(*) FROM albums 
                    WHERE id NOT IN (SELECT DISTINCT album_id FROM tracks WHERE album_id IS NOT NULL)
                """)
                orphaned_albums_count = cursor.fetchone()[0]
                
                # Delete orphaned artists
                if orphaned_artists_count > 0:
                    cursor.execute("""
                        DELETE FROM artists 
                        WHERE id NOT IN (SELECT DISTINCT artist_id FROM tracks WHERE artist_id IS NOT NULL)
                    """)
                    logger.info(f"🧹 Removed {orphaned_artists_count} orphaned artists")
                
                # Delete orphaned albums  
                if orphaned_albums_count > 0:
                    cursor.execute("""
                        DELETE FROM albums 
                        WHERE id NOT IN (SELECT DISTINCT album_id FROM tracks WHERE album_id IS NOT NULL)
                    """)
                    logger.info(f"🧹 Removed {orphaned_albums_count} orphaned albums")
                
                conn.commit()
                
                return {
                    'orphaned_artists_removed': orphaned_artists_count,
                    'orphaned_albums_removed': orphaned_albums_count
                }
                
        except Exception as e:
            logger.error(f"Error cleaning up orphaned records: {e}")
            return {'orphaned_artists_removed': 0, 'orphaned_albums_removed': 0}
    
    # Artist operations
    def insert_or_update_artist(self, plex_artist) -> bool:
        """Insert or update artist from Plex artist object - DEPRECATED: Use insert_or_update_media_artist instead"""
        return self.insert_or_update_media_artist(plex_artist, server_source='plex')
    
    def insert_or_update_media_artist(self, artist_obj, server_source: str = 'plex') -> bool:
        """Insert or update artist from media server artist object (Plex or Jellyfin)"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # Convert artist ID to string (handles both Plex integer IDs and Jellyfin GUIDs)
                artist_id = str(artist_obj.ratingKey)
                raw_name = artist_obj.title
                # Normalize artist name to handle quote variations and other inconsistencies
                name = self._normalize_artist_name(raw_name)

                # Debug logging to see if normalization is working
                if raw_name != name:
                    logger.info(f"Artist name normalized: '{raw_name}' -> '{name}'")
                thumb_url = getattr(artist_obj, 'thumb', None)
                
                # Only preserve timestamps and flags from summary, not full biography
                full_summary = getattr(artist_obj, 'summary', None) or ''
                summary = None
                if full_summary:
                    # Extract only our tracking markers (timestamps and ignore flags)
                    import re
                    markers = []
                    
                    # Extract timestamp marker
                    timestamp_match = re.search(r'-updatedAt\d{4}-\d{2}-\d{2}', full_summary)
                    if timestamp_match:
                        markers.append(timestamp_match.group(0))
                    
                    # Extract ignore flag
                    if '-IgnoreUpdate' in full_summary:
                        markers.append('-IgnoreUpdate')
                    
                    # Only store markers, not full biography
                    summary = '\n\n'.join(markers) if markers else None
                
                # Get genres (handle both Plex and Jellyfin formats)
                genres = []
                if hasattr(artist_obj, 'genres') and artist_obj.genres:
                    genres = [genre.tag if hasattr(genre, 'tag') else str(genre) 
                             for genre in artist_obj.genres]
                
                genres_json = json.dumps(genres) if genres else None
                
                # Check if artist exists with this ID and server source
                cursor.execute("SELECT id FROM artists WHERE id = ? AND server_source = ?", (artist_id, server_source))
                exists = cursor.fetchone()
                
                if exists:
                    # Update existing artist
                    cursor.execute("""
                        UPDATE artists
                        SET name = ?, thumb_url = ?, genres = ?, summary = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE id = ? AND server_source = ?
                    """, (name, thumb_url, genres_json, summary, artist_id, server_source))
                    logger.debug(f"Updated existing {server_source} artist: {name} (ID: {artist_id})")
                else:
                    # Insert new artist
                    cursor.execute("""
                        INSERT INTO artists (id, name, thumb_url, genres, summary, server_source)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (artist_id, name, thumb_url, genres_json, summary, server_source))
                    logger.debug(f"Inserted new {server_source} artist: {name} (ID: {artist_id})")

                conn.commit()
                rows_affected = cursor.rowcount
                if rows_affected == 0:
                    logger.warning(f"Database insertion returned 0 rows affected for {server_source} artist: {name} (ID: {artist_id})")

                return True
                
        except Exception as e:
            logger.error(f"Error inserting/updating {server_source} artist {getattr(artist_obj, 'title', 'Unknown')}: {e}")
            return False

    def _normalize_artist_name(self, name: str) -> str:
        """
        Normalize artist names to handle inconsistencies like quote variations.
        Converts Unicode smart quotes to ASCII quotes for consistency.
        """
        if not name:
            return name

        # Replace Unicode smart quotes with regular ASCII quotes
        normalized = name.replace('\u201c', '"').replace('\u201d', '"')  # Left and right double quotes
        normalized = normalized.replace('\u2018', "'").replace('\u2019', "'")  # Left and right single quotes
        normalized = normalized.replace('\u00ab', '"').replace('\u00bb', '"')  # « » guillemets

        return normalized
    
    def get_artist(self, artist_id: int) -> Optional[DatabaseArtist]:
        """Get artist by ID"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                cursor.execute("SELECT * FROM artists WHERE id = ?", (artist_id,))
                row = cursor.fetchone()
                
                if row:
                    genres = json.loads(row['genres']) if row['genres'] else None
                    return DatabaseArtist(
                        id=row['id'],
                        name=row['name'],
                        thumb_url=row['thumb_url'],
                        genres=genres,
                        summary=row['summary'],
                        created_at=datetime.fromisoformat(row['created_at']) if row['created_at'] else None,
                        updated_at=datetime.fromisoformat(row['updated_at']) if row['updated_at'] else None
                    )
                return None
                
        except Exception as e:
            logger.error(f"Error getting artist {artist_id}: {e}")
            return None
    
    # Album operations
    def insert_or_update_album(self, plex_album, artist_id: int) -> bool:
        """Insert or update album from Plex album object - DEPRECATED: Use insert_or_update_media_album instead"""
        return self.insert_or_update_media_album(plex_album, artist_id, server_source='plex')
    
    def insert_or_update_media_album(self, album_obj, artist_id: str, server_source: str = 'plex') -> bool:
        """Insert or update album from media server album object (Plex or Jellyfin)"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            # Convert album ID to string (handles both Plex integer IDs and Jellyfin GUIDs)
            album_id = str(album_obj.ratingKey)
            title = album_obj.title
            year = getattr(album_obj, 'year', None)
            thumb_url = getattr(album_obj, 'thumb', None)
            
            # Get track count and duration (handle different server attributes)
            track_count = getattr(album_obj, 'leafCount', None) or getattr(album_obj, 'childCount', None)
            duration = getattr(album_obj, 'duration', None)
            
            # Get genres (handle both Plex and Jellyfin formats)
            genres = []
            if hasattr(album_obj, 'genres') and album_obj.genres:
                genres = [genre.tag if hasattr(genre, 'tag') else str(genre) 
                         for genre in album_obj.genres]
            
            genres_json = json.dumps(genres) if genres else None
            
            # Check if album exists with this ID and server source
            cursor.execute("SELECT id FROM albums WHERE id = ? AND server_source = ?", (album_id, server_source))
            exists = cursor.fetchone()
            
            if exists:
                # Update existing album
                cursor.execute("""
                    UPDATE albums 
                    SET artist_id = ?, title = ?, year = ?, thumb_url = ?, genres = ?, 
                        track_count = ?, duration = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND server_source = ?
                """, (artist_id, title, year, thumb_url, genres_json, track_count, duration, album_id, server_source))
            else:
                # Insert new album
                cursor.execute("""
                    INSERT INTO albums (id, artist_id, title, year, thumb_url, genres, track_count, duration, server_source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (album_id, artist_id, title, year, thumb_url, genres_json, track_count, duration, server_source))
            
            conn.commit()
            return True
            
        except Exception as e:
            logger.error(f"Error inserting/updating {server_source} album {getattr(album_obj, 'title', 'Unknown')}: {e}")
            return False
    
    def get_albums_by_artist(self, artist_id: int) -> List[DatabaseAlbum]:
        """Get all albums by artist ID"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cursor.execute("SELECT * FROM albums WHERE artist_id = ? ORDER BY year, title", (artist_id,))
            rows = cursor.fetchall()
            
            albums = []
            for row in rows:
                genres = json.loads(row['genres']) if row['genres'] else None
                albums.append(DatabaseAlbum(
                    id=row['id'],
                    artist_id=row['artist_id'],
                    title=row['title'],
                    year=row['year'],
                    thumb_url=row['thumb_url'],
                    genres=genres,
                    track_count=row['track_count'],
                    duration=row['duration'],
                    created_at=datetime.fromisoformat(row['created_at']) if row['created_at'] else None,
                    updated_at=datetime.fromisoformat(row['updated_at']) if row['updated_at'] else None
                ))
            
            return albums
            
        except Exception as e:
            logger.error(f"Error getting albums for artist {artist_id}: {e}")
            return []
    
    # Track operations
    def insert_or_update_track(self, plex_track, album_id: int, artist_id: int) -> bool:
        """Insert or update track from Plex track object - DEPRECATED: Use insert_or_update_media_track instead"""
        return self.insert_or_update_media_track(plex_track, album_id, artist_id, server_source='plex')
    
    def insert_or_update_media_track(self, track_obj, album_id: str, artist_id: str, server_source: str = 'plex') -> bool:
        """Insert or update track from media server track object (Plex or Jellyfin) with retry logic"""
        max_retries = 3
        retry_count = 0
        
        while retry_count < max_retries:
            try:
                conn = self._get_connection()
                cursor = conn.cursor()
                
                # Set shorter timeout to prevent long locks
                cursor.execute("PRAGMA busy_timeout = 10000")  # 10 second timeout
                
                # Convert track ID to string (handles both Plex integer IDs and Jellyfin GUIDs)
                track_id = str(track_obj.ratingKey)
                title = track_obj.title
                track_number = getattr(track_obj, 'trackNumber', None)
                duration = getattr(track_obj, 'duration', None)
                
                # Get file path and media info (Plex-specific, Jellyfin may not have these)
                file_path = None
                bitrate = None
                if hasattr(track_obj, 'media') and track_obj.media:
                    media = track_obj.media[0] if track_obj.media else None
                    if media:
                        if hasattr(media, 'parts') and media.parts:
                            part = media.parts[0]
                            file_path = getattr(part, 'file', None)
                        bitrate = getattr(media, 'bitrate', None)
                
                # Use INSERT OR REPLACE to handle duplicate IDs gracefully
                cursor.execute("""
                    INSERT OR REPLACE INTO tracks 
                    (id, album_id, artist_id, title, track_number, duration, file_path, bitrate, server_source, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (track_id, album_id, artist_id, title, track_number, duration, file_path, bitrate, server_source))
                
                conn.commit()
                return True
                
            except Exception as e:
                retry_count += 1
                if "database is locked" in str(e).lower() and retry_count < max_retries:
                    logger.warning(f"Database locked on track '{getattr(track_obj, 'title', 'Unknown')}', retrying {retry_count}/{max_retries}...")
                    time.sleep(0.1 * retry_count)  # Exponential backoff
                    continue
                else:
                    logger.error(f"Error inserting/updating {server_source} track {getattr(track_obj, 'title', 'Unknown')}: {e}")
                    return False
        
        return False
    
    def track_exists(self, track_id) -> bool:
        """Check if a track exists in the database by ID (supports both int and string IDs)"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            # Convert to string to handle both Plex integers and Jellyfin GUIDs
            track_id_str = str(track_id)
            cursor.execute("SELECT 1 FROM tracks WHERE id = ? LIMIT 1", (track_id_str,))
            result = cursor.fetchone()
            
            return result is not None
            
        except Exception as e:
            logger.error(f"Error checking if track {track_id} exists: {e}")
            return False
    
    def track_exists_by_server(self, track_id, server_source: str) -> bool:
        """Check if a track exists in the database by ID and server source"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            # Convert to string to handle both Plex integers and Jellyfin GUIDs
            track_id_str = str(track_id)
            cursor.execute("SELECT 1 FROM tracks WHERE id = ? AND server_source = ? LIMIT 1", (track_id_str, server_source))
            result = cursor.fetchone()
            
            return result is not None
            
        except Exception as e:
            logger.error(f"Error checking if track {track_id} exists for server {server_source}: {e}")
            return False
    
    def get_track_by_id(self, track_id) -> Optional[DatabaseTrackWithMetadata]:
        """Get a track with artist and album names by ID (supports both int and string IDs)"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            # Convert to string to handle both Plex integers and Jellyfin GUIDs
            track_id_str = str(track_id)
            cursor.execute("""
                SELECT t.id, t.album_id, t.artist_id, t.title, t.track_number, 
                       t.duration, t.created_at, t.updated_at,
                       a.name as artist_name, al.title as album_title
                FROM tracks t
                JOIN artists a ON t.artist_id = a.id
                JOIN albums al ON t.album_id = al.id
                WHERE t.id = ?
            """, (track_id_str,))
            
            row = cursor.fetchone()
            if row:
                return DatabaseTrackWithMetadata(
                    id=row['id'],
                    album_id=row['album_id'],
                    artist_id=row['artist_id'],
                    title=row['title'],
                    artist_name=row['artist_name'],
                    album_title=row['album_title'],
                    track_number=row['track_number'],
                    duration=row['duration'],
                    created_at=row['created_at'],
                    updated_at=row['updated_at']
                )
            return None
            
        except Exception as e:
            logger.error(f"Error getting track {track_id}: {e}")
            return None
    
    def get_tracks_by_album(self, album_id: int) -> List[DatabaseTrack]:
        """Get all tracks by album ID"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cursor.execute("SELECT * FROM tracks WHERE album_id = ? ORDER BY track_number, title", (album_id,))
            rows = cursor.fetchall()
            
            tracks = []
            for row in rows:
                tracks.append(DatabaseTrack(
                    id=row['id'],
                    album_id=row['album_id'],
                    artist_id=row['artist_id'],
                    title=row['title'],
                    track_number=row['track_number'],
                    duration=row['duration'],
                    file_path=row['file_path'],
                    bitrate=row['bitrate'],
                    created_at=datetime.fromisoformat(row['created_at']) if row['created_at'] else None,
                    updated_at=datetime.fromisoformat(row['updated_at']) if row['updated_at'] else None
                ))
            
            return tracks
            
        except Exception as e:
            logger.error(f"Error getting tracks for album {album_id}: {e}")
            return []
    
    def search_artists(self, query: str, limit: int = 50) -> List[DatabaseArtist]:
        """Search artists by name"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT * FROM artists 
                WHERE name LIKE ? 
                ORDER BY name 
                LIMIT ?
            """, (f"%{query}%", limit))
            
            rows = cursor.fetchall()
            
            artists = []
            for row in rows:
                genres = json.loads(row['genres']) if row['genres'] else None
                artists.append(DatabaseArtist(
                    id=row['id'],
                    name=row['name'],
                    thumb_url=row['thumb_url'],
                    genres=genres,
                    summary=row['summary'],
                    created_at=datetime.fromisoformat(row['created_at']) if row['created_at'] else None,
                    updated_at=datetime.fromisoformat(row['updated_at']) if row['updated_at'] else None
                ))
            
            return artists
            
        except Exception as e:
            logger.error(f"Error searching artists with query '{query}': {e}")
            return []
    
    def search_tracks(self, title: str = "", artist: str = "", limit: int = 50, server_source: str = None) -> List[DatabaseTrack]:
        """Search tracks by title and/or artist name with Unicode-aware fuzzy matching"""
        try:
            if not title and not artist:
                return []
            
            conn = self._get_connection()
            cursor = conn.cursor()
            
            # STRATEGY 1: Try basic SQL LIKE search first (fastest)
            basic_results = self._search_tracks_basic(cursor, title, artist, limit, server_source)
            
            if basic_results:
                logger.debug(f"🔍 Basic search found {len(basic_results)} results")
                return basic_results
            
            # STRATEGY 2: If basic search fails and we have Unicode support, try normalized search
            try:
                from unidecode import unidecode
                unicode_support = True
            except ImportError:
                unicode_support = False
            
            if unicode_support:
                normalized_results = self._search_tracks_unicode_fallback(cursor, title, artist, limit, server_source)
                if normalized_results:
                    logger.debug(f"🔍 Unicode fallback search found {len(normalized_results)} results")
                    return normalized_results
            
            # STRATEGY 3: Last resort - broader fuzzy search with Python filtering
            fuzzy_results = self._search_tracks_fuzzy_fallback(cursor, title, artist, limit)
            if fuzzy_results:
                logger.debug(f"🔍 Fuzzy fallback search found {len(fuzzy_results)} results")
            
            return fuzzy_results
            
        except Exception as e:
            logger.error(f"Error searching tracks with title='{title}', artist='{artist}': {e}")
            return []
    
    def _search_tracks_basic(self, cursor, title: str, artist: str, limit: int, server_source: str = None) -> List[DatabaseTrack]:
        """Basic SQL LIKE search - fastest method"""
        where_conditions = []
        params = []
        
        if title:
            where_conditions.append("tracks.title LIKE ?")
            params.append(f"%{title}%")
        
        if artist:
            where_conditions.append("artists.name LIKE ?")
            params.append(f"%{artist}%")
        
        # Add server filter if specified
        if server_source:
            where_conditions.append("tracks.server_source = ?")
            params.append(server_source)
        
        if not where_conditions:
            return []
        
        where_clause = " AND ".join(where_conditions)
        params.append(limit)
        
        cursor.execute(f"""
            SELECT tracks.*, artists.name as artist_name, albums.title as album_title
            FROM tracks
            JOIN artists ON tracks.artist_id = artists.id
            JOIN albums ON tracks.album_id = albums.id
            WHERE {where_clause}
            ORDER BY tracks.title, artists.name
            LIMIT ?
        """, params)
        
        return self._rows_to_tracks(cursor.fetchall())
    
    def _search_tracks_unicode_fallback(self, cursor, title: str, artist: str, limit: int, server_source: str = None) -> List[DatabaseTrack]:
        """Unicode-aware fallback search - tries normalized versions"""
        from unidecode import unidecode
        
        # Normalize search terms
        if _matching_engine:
            title_norm = _matching_engine.normalize_string(title) if title else ""
            artist_norm = _matching_engine.normalize_string(artist) if artist else ""
        else:
            title_norm = unidecode(title).lower() if title else ""
            artist_norm = unidecode(artist).lower() if artist else ""
        
        # Try searching with normalized versions
        where_conditions = []
        params = []
        
        if title:
            where_conditions.append("LOWER(tracks.title) LIKE ?")
            params.append(f"%{title_norm}%")
        
        if artist:
            where_conditions.append("LOWER(artists.name) LIKE ?")
            params.append(f"%{artist_norm}%")
        
        # Add server filter if specified
        if server_source:
            where_conditions.append("tracks.server_source = ?")
            params.append(server_source)
        
        if not where_conditions:
            return []
        
        where_clause = " AND ".join(where_conditions)
        params.append(limit * 2)  # Get more results for filtering
        
        cursor.execute(f"""
            SELECT tracks.*, artists.name as artist_name, albums.title as album_title
            FROM tracks
            JOIN artists ON tracks.artist_id = artists.id
            JOIN albums ON tracks.album_id = albums.id
            WHERE {where_clause}
            ORDER BY tracks.title, artists.name
            LIMIT ?
        """, params)
        
        rows = cursor.fetchall()
        
        # Filter results with proper Unicode normalization
        filtered_tracks = []
        for row in rows:

            if _matching_engine:
                db_title_norm = _matching_engine.normalize_string(row['title']) if row['title'] else ""
                db_artist_norm = _matching_engine.normalize_string(row['artist_name']) if row['artist_name'] else ""
            else:
                db_title_norm = unidecode(row['title'].lower()) if row['title'] else ""
                db_artist_norm = unidecode(row['artist_name'].lower()) if row['artist_name'] else ""
            
            title_matches = not title or title_norm in db_title_norm
            artist_matches = not artist or artist_norm in db_artist_norm
            
            if title_matches and artist_matches:
                filtered_tracks.append(row)
                if len(filtered_tracks) >= limit:
                    break
        
        return self._rows_to_tracks(filtered_tracks)
    
    def _search_tracks_fuzzy_fallback(self, cursor, title: str, artist: str, limit: int) -> List[DatabaseTrack]:
        """Broadest fuzzy search - partial word matching"""
        # Get broader results by searching for individual words
        search_terms = []
        if title:
            # Split title into words and search for each
            title_words = [w.strip() for w in title.lower().split() if len(w.strip()) >= 3]
            search_terms.extend(title_words)
        
        if artist:
            # Split artist into words and search for each
            artist_words = [w.strip() for w in artist.lower().split() if len(w.strip()) >= 3]
            search_terms.extend(artist_words)
        
        if not search_terms:
            return []
        
        # Build a query that searches for any of the words
        like_conditions = []
        params = []
        
        for term in search_terms[:5]:  # Limit to 5 terms to avoid too broad search
            like_conditions.append("(LOWER(tracks.title) LIKE ? OR LOWER(artists.name) LIKE ?)")
            params.extend([f"%{term}%", f"%{term}%"])
        
        if not like_conditions:
            return []
        
        where_clause = " OR ".join(like_conditions)
        params.append(limit * 3)  # Get more results for scoring
        
        cursor.execute(f"""
            SELECT tracks.*, artists.name as artist_name, albums.title as album_title
            FROM tracks
            JOIN artists ON tracks.artist_id = artists.id
            JOIN albums ON tracks.album_id = albums.id
            WHERE {where_clause}
            ORDER BY tracks.title, artists.name
            LIMIT ?
        """, params)
        
        rows = cursor.fetchall()
        
        # Score and filter results
        scored_results = []
        for row in rows:
            # Simple scoring based on how many search terms match
            score = 0
            db_title_lower = row['title'].lower()
            db_artist_lower = row['artist_name'].lower()
            
            for term in search_terms:
                if term in db_title_lower or term in db_artist_lower:
                    score += 1
            
            if score > 0:
                scored_results.append((score, row))
        
        # Sort by score and take top results
        scored_results.sort(key=lambda x: x[0], reverse=True)
        top_rows = [row for score, row in scored_results[:limit]]
        
        return self._rows_to_tracks(top_rows)
    
    def _rows_to_tracks(self, rows) -> List[DatabaseTrack]:
        """Convert database rows to DatabaseTrack objects"""
        tracks = []
        for row in rows:
            track = DatabaseTrack(
                id=row['id'],
                album_id=row['album_id'],
                artist_id=row['artist_id'],
                title=row['title'],
                track_number=row['track_number'],
                duration=row['duration'],
                file_path=row['file_path'],
                bitrate=row['bitrate'],
                created_at=datetime.fromisoformat(row['created_at']) if row['created_at'] else None,
                updated_at=datetime.fromisoformat(row['updated_at']) if row['updated_at'] else None
            )
            # Add artist and album info for compatibility with Plex responses
            track.artist_name = row['artist_name']
            track.album_title = row['album_title']
            tracks.append(track)
        return tracks
    
    def search_albums(self, title: str = "", artist: str = "", limit: int = 50, server_source: Optional[str] = None) -> List[DatabaseAlbum]:
        """Search albums by title and/or artist name with fuzzy matching"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            # Build dynamic query based on provided parameters  
            where_conditions = []
            params = []
            
            if title:
                where_conditions.append("albums.title LIKE ?")
                params.append(f"%{title}%")
            
            if artist:
                where_conditions.append("artists.name LIKE ?")
                params.append(f"%{artist}%")
            
            if server_source:
                where_conditions.append("albums.server_source = ?")
                params.append(server_source)
            
            if not where_conditions:
                # If no search criteria, return empty list
                return []
            
            where_clause = " AND ".join(where_conditions)
            params.append(limit)
            
            cursor.execute(f"""
                SELECT albums.*, artists.name as artist_name
                FROM albums
                JOIN artists ON albums.artist_id = artists.id
                WHERE {where_clause}
                ORDER BY albums.title, artists.name
                LIMIT ?
            """, params)
            
            rows = cursor.fetchall()
            
            albums = []
            for row in rows:
                genres = json.loads(row['genres']) if row['genres'] else None
                album = DatabaseAlbum(
                    id=row['id'],
                    artist_id=row['artist_id'],
                    title=row['title'],
                    year=row['year'],
                    thumb_url=row['thumb_url'],
                    genres=genres,
                    track_count=row['track_count'],
                    duration=row['duration'],
                    created_at=datetime.fromisoformat(row['created_at']) if row['created_at'] else None,
                    updated_at=datetime.fromisoformat(row['updated_at']) if row['updated_at'] else None
                )
                # Add artist info for compatibility with Plex responses
                album.artist_name = row['artist_name']
                albums.append(album)
            
            return albums
            
        except Exception as e:
            logger.error(f"Error searching albums with title='{title}', artist='{artist}': {e}")
            return []
        


    def _get_artist_variations(self, artist_name: str) -> List[str]:
            """Returns a list of known variations for an artist's name."""
            variations = [artist_name]
            name_lower = artist_name.lower()

            # Add more aliases here in the future
            if "korn" in name_lower:
                if "KoЯn" not in variations:
                    variations.append("KoЯn")
                if "Korn" not in variations:
                    variations.append("Korn")
            
            # Return unique variations
            return list(set(variations))

    
    def check_track_exists(self, title: str, artist: str, confidence_threshold: float = 0.8, server_source: str = None) -> Tuple[Optional[DatabaseTrack], float]:
        """
        Check if a track exists in the database with enhanced fuzzy matching and confidence scoring.
        Now uses the same sophisticated matching approach as album checking for consistency.
        Returns (track, confidence) tuple where confidence is 0.0-1.0
        """
        try:
            # Generate title variations for better matching (similar to album approach)
            title_variations = self._generate_track_title_variations(title)
            
            logger.debug(f"🔍 Enhanced track matching for '{title}' by '{artist}': trying {len(title_variations)} variations")
            for i, var in enumerate(title_variations):
                logger.debug(f"  {i+1}. '{var}'")
            
            best_match = None
            best_confidence = 0.0
            
            # Try each title variation
            for title_variation in title_variations:
                # Search for potential matches with this variation
                potential_matches = []
                artist_variations = self._get_artist_variations(artist)
                for artist_variation in artist_variations:
                    potential_matches.extend(self.search_tracks(title=title_variation, artist=artist_variation, limit=20, server_source=server_source))
                
                if not potential_matches:
                    continue
                
                logger.debug(f"🎵 Found {len(potential_matches)} tracks for variation '{title_variation}'")
                
                # Score each potential match
                for track in potential_matches:
                    confidence = self._calculate_track_confidence(title, artist, track)
                    logger.debug(f"  🎯 '{track.title}' confidence: {confidence:.3f}")
                    
                    if confidence > best_confidence:
                        best_confidence = confidence
                        best_match = track
            
            # Return match only if it meets threshold
            if best_match and best_confidence >= confidence_threshold:
                logger.debug(f"✅ Enhanced track match found: '{title}' -> '{best_match.title}' (confidence: {best_confidence:.3f})")
                return best_match, best_confidence
            else:
                logger.debug(f"❌ No confident track match for '{title}' (best: {best_confidence:.3f}, threshold: {confidence_threshold})")
                return None, best_confidence
            
        except Exception as e:
            logger.error(f"Error checking track existence for '{title}' by '{artist}': {e}")
            return None, 0.0
    
    def check_album_exists(self, title: str, artist: str, confidence_threshold: float = 0.8) -> Tuple[Optional[DatabaseAlbum], float]:
        """
        Check if an album exists in the database with fuzzy matching and confidence scoring.
        Returns (album, confidence) tuple where confidence is 0.0-1.0
        """
        try:
            # Search for potential matches
            potential_matches = self.search_albums(title=title, artist=artist, limit=20)
            
            if not potential_matches:
                return None, 0.0
            
            # Simple confidence scoring based on string similarity
            def calculate_confidence(db_album: DatabaseAlbum) -> float:
                title_similarity = self._string_similarity(title.lower().strip(), db_album.title.lower().strip())
                artist_similarity = self._string_similarity(artist.lower().strip(), db_album.artist_name.lower().strip())
                
                # Weight title and artist equally for albums
                return (title_similarity * 0.5) + (artist_similarity * 0.5)
            
            # Find best match
            best_match = None
            best_confidence = 0.0
            
            for album in potential_matches:
                confidence = calculate_confidence(album)
                if confidence > best_confidence:
                    best_confidence = confidence
                    best_match = album
            
            # Return match only if it meets threshold
            if best_confidence >= confidence_threshold:
                return best_match, best_confidence
            else:
                return None, best_confidence
            
        except Exception as e:
            logger.error(f"Error checking album existence for '{title}' by '{artist}': {e}")
            return None, 0.0
    
    def _string_similarity(self, s1: str, s2: str) -> float:
        """
        Calculate string similarity using enhanced matching engine logic if available,
        otherwise falls back to Levenshtein distance.
        Returns value between 0.0 (no similarity) and 1.0 (identical)
        """
        if s1 == s2:
            return 1.0
        
        if not s1 or not s2:
            return 0.0
        
        # Use enhanced similarity from matching engine if available
        if _matching_engine:
            return _matching_engine.similarity_score(s1, s2)
        
        # Simple Levenshtein distance implementation
        len1, len2 = len(s1), len(s2)
        if len1 < len2:
            s1, s2 = s2, s1
            len1, len2 = len2, len1
        
        if len2 == 0:
            return 0.0
        
        # Create matrix
        previous_row = list(range(len2 + 1))
        for i, c1 in enumerate(s1):
            current_row = [i + 1]
            for j, c2 in enumerate(s2):
                insertions = previous_row[j + 1] + 1
                deletions = current_row[j] + 1
                substitutions = previous_row[j] + (c1 != c2)
                current_row.append(min(insertions, deletions, substitutions))
            previous_row = current_row
        
        max_len = max(len1, len2)
        distance = previous_row[-1]
        similarity = (max_len - distance) / max_len
        
        return max(0.0, similarity)
    
    def check_album_completeness(self, album_id: int, expected_track_count: Optional[int] = None) -> Tuple[int, int, bool]:
        """
        Check if we have all tracks for an album.
        Returns (owned_tracks, expected_tracks, is_complete)
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            # Get actual track count in our database
            cursor.execute("SELECT COUNT(*) FROM tracks WHERE album_id = ?", (album_id,))
            owned_tracks = cursor.fetchone()[0]
            
            # Get expected track count from album table
            cursor.execute("SELECT track_count FROM albums WHERE id = ?", (album_id,))
            result = cursor.fetchone()
            
            if not result:
                return 0, 0, False
            
            stored_track_count = result[0]
            
            # Use provided expected count if available, otherwise use stored count
            expected_tracks = expected_track_count if expected_track_count is not None else stored_track_count
            
            # Determine completeness with refined thresholds
            if expected_tracks and expected_tracks > 0:
                completion_ratio = owned_tracks / expected_tracks
                # Complete: 90%+, Nearly Complete: 80-89%, Partial: <80%
                is_complete = completion_ratio >= 0.9 and owned_tracks > 0
            else:
                # Fallback: if we have any tracks, consider it owned
                is_complete = owned_tracks > 0
            
            return owned_tracks, expected_tracks or 0, is_complete
            
        except Exception as e:
            logger.error(f"Error checking album completeness for album_id {album_id}: {e}")
            return 0, 0, False
    
    def check_album_exists_with_completeness(self, title: str, artist: str, expected_track_count: Optional[int] = None, confidence_threshold: float = 0.8, server_source: Optional[str] = None) -> Tuple[Optional[DatabaseAlbum], float, int, int, bool]:
        """
        Check if an album exists in the database with completeness information.
        Enhanced to handle edition matching (standard <-> deluxe variants).
        Returns (album, confidence, owned_tracks, expected_tracks, is_complete)
        """
        try:
            # Try enhanced edition-aware matching first with expected track count for Smart Edition Matching
            album, confidence = self.check_album_exists_with_editions(title, artist, confidence_threshold, expected_track_count, server_source)
            
            if not album:
                return None, 0.0, 0, 0, False
            
            # Now check completeness
            owned_tracks, expected_tracks, is_complete = self.check_album_completeness(album.id, expected_track_count)
            
            return album, confidence, owned_tracks, expected_tracks, is_complete
            
        except Exception as e:
            logger.error(f"Error checking album existence with completeness for '{title}' by '{artist}': {e}")
            return None, 0.0, 0, 0, False
    
    def check_album_exists_with_editions(self, title: str, artist: str, confidence_threshold: float = 0.8, expected_track_count: Optional[int] = None, server_source: Optional[str] = None) -> Tuple[Optional[DatabaseAlbum], float]:
        """
        Enhanced album existence check that handles edition variants.
        Matches standard albums with deluxe/platinum/special editions and vice versa.
        """
        try:
            # Generate album title variations for edition matching
            title_variations = self._generate_album_title_variations(title)
            
            logger.debug(f"🔍 Edition matching for '{title}' by '{artist}': trying {len(title_variations)} variations")
            for i, var in enumerate(title_variations):
                logger.debug(f"  {i+1}. '{var}'")
            
            best_match = None
            best_confidence = 0.0
            
            for variation in title_variations:
                # Search for this variation
                albums = []
                artist_variations = self._get_artist_variations(artist)
                for artist_variation in artist_variations:
                    found = self.search_albums(title=variation, artist=artist_variation, limit=10, server_source=server_source)
                    # Deduplicate by ID
                    existing_ids = {a.id for a in albums}
                    for album in found:
                        if album.id not in existing_ids:
                            albums.append(album)
                            existing_ids.add(album.id)
                
                if albums:
                    logger.debug(f"📀 Found {len(albums)} albums for variation '{variation}'")
                
                if not albums:
                    continue
                
                # Score each potential match with Smart Edition Matching
                for album in albums:
                    confidence = self._calculate_album_confidence(title, artist, album, expected_track_count)
                    logger.debug(f"  🎯 '{album.title}' confidence: {confidence:.3f}")
                    
                    if confidence > best_confidence:
                        best_confidence = confidence
                        best_match = album
            
            # Return match only if it meets threshold
            if best_match and best_confidence >= confidence_threshold:
                logger.debug(f"✅ Edition match found: '{title}' -> '{best_match.title}' (confidence: {best_confidence:.3f})")
                return best_match, best_confidence
            
            # Fallback: Check ALL albums by this artist (resolves SQL accent sensitivity issues #101)
            # If we haven't found a match yet, fetch broader list from artist and double check
            if best_confidence < confidence_threshold:
                logger.debug(f"⚠️ specific title search failed, trying broad artist search fallback for '{artist}'")
                try:
                    # Get ALL albums by this artist (limit 100 to be safe)
                    # This bypasses SQL 'LIKE' limitations for diacritics (e.g. 'ă' vs 'a')
                    # And relies on Python-side normalization in _calculate_album_confidence
                    artist_albums = []
                    artist_variations = self._get_artist_variations(artist)
                    for artist_var in artist_variations:
                        found_albums = self.search_albums(title="", artist=artist_var, limit=100, server_source=server_source)
                        # Deduplicate
                        existing_ids = {a.id for a in artist_albums}
                        for album in found_albums:
                            if album.id not in existing_ids:
                                artist_albums.append(album)
                                existing_ids.add(album.id)
                    
                    if artist_albums:
                        logger.debug(f"  Found {len(artist_albums)} total albums for artist fallback")
                    
                    for album in artist_albums:
                        confidence = self._calculate_album_confidence(title, artist, album, expected_track_count)
                        if confidence > best_confidence:
                            best_confidence = confidence
                            best_match = album
                            logger.debug(f"  🎯 Fallback match: '{album.title}' confidence: {confidence:.3f}")
                except Exception as fallback_error:
                     logger.warning(f"Fallback artist search failed: {fallback_error}")

            if best_match and best_confidence >= confidence_threshold:
                 logger.debug(f"✅ Fallback match succeeded: '{title}' -> '{best_match.title}' (confidence: {best_confidence:.3f})")
                 return best_match, best_confidence

            logger.debug(f"❌ No confident edition match for '{title}' (best: {best_confidence:.3f}, threshold: {confidence_threshold})")
            return None, best_confidence
                
        except Exception as e:
            logger.error(f"Error in edition-aware album matching for '{title}' by '{artist}': {e}")
            return None, 0.0
    
    def _generate_album_title_variations(self, title: str) -> List[str]:
        """Generate variations of album title to handle edition matching"""
        variations = [title]  # Always include original
        
        # Clean up the title
        title_lower = title.lower().strip()
        
        # Define edition patterns and their variations
        edition_patterns = {
            r'\s*\(deluxe\s*edition?\)': ['deluxe', 'deluxe edition'],
            r'\s*\(expanded\s*edition?\)': ['expanded', 'expanded edition'],
            r'\s*\(platinum\s*edition?\)': ['platinum', 'platinum edition'],
            r'\s*\(special\s*edition?\)': ['special', 'special edition'],
            r'\s*\(remastered?\)': ['remastered', 'remaster'],
            r'\s*\(anniversary\s*edition?\)': ['anniversary', 'anniversary edition'],
            r'\s*\(.*version\)': ['version'],
            r'\s+deluxe\s*edition?$': ['deluxe', 'deluxe edition'],
            r'\s+platinum\s*edition?$': ['platinum', 'platinum edition'],
            r'\s+special\s*edition?$': ['special', 'special edition'],
            r'\s*-\s*deluxe': ['deluxe'],
            r'\s*-\s*platinum\s*edition?': ['platinum', 'platinum edition'],
        }
        
        # Check if title contains any edition indicators
        base_title = title
        found_editions = []
        
        for pattern, edition_types in edition_patterns.items():
            if re.search(pattern, title_lower):
                # Remove the edition part to get base title
                base_title = re.sub(pattern, '', title, flags=re.IGNORECASE).strip()
                found_editions.extend(edition_types)
                break
        
        # Add base title (without edition markers)
        if base_title != title:
            variations.append(base_title)
        
        # If we found a base title, add common edition variants
        if base_title != title:
            # Add common deluxe/platinum/special variants
            common_editions = [
                'deluxe edition',
                'deluxe',
                'platinum edition',
                'platinum',
                'special edition', 
                'expanded edition',
                'remastered',
                'anniversary edition'
            ]
            
            for edition in common_editions:
                variations.extend([
                    f"{base_title} ({edition.title()})",
                    f"{base_title} ({edition})",
                    f"{base_title} - {edition.title()}",
                    f"{base_title} {edition.title()}",
                ])
        
        # If original title is base form, add edition variants  
        elif not any(re.search(pattern, title_lower) for pattern in edition_patterns.keys()):
            # This appears to be a base album, add deluxe variants
            common_editions = ['Deluxe Edition', 'Deluxe', 'Platinum Edition', 'Special Edition']
            for edition in common_editions:
                variations.extend([
                    f"{title} ({edition})",
                    f"{title} - {edition}",
                    f"{title} {edition}",
                ])
        
        # Remove duplicates while preserving order
        seen = set()
        unique_variations = []
        for var in variations:
            var_clean = var.strip()
            if var_clean and var_clean.lower() not in seen:
                seen.add(var_clean.lower())
                unique_variations.append(var_clean)
        
        return unique_variations
    
    def _calculate_album_confidence(self, search_title: str, search_artist: str, db_album: DatabaseAlbum, expected_track_count: Optional[int] = None) -> float:
        """Calculate confidence score for album match with Smart Edition Matching"""
        try:
            # Simple confidence based on string similarity
            title_similarity = self._string_similarity(search_title.lower(), db_album.title.lower())
            artist_similarity = self._string_similarity(search_artist.lower(), db_album.artist_name.lower())
            
            # Also try with cleaned versions (removing edition markers)
            clean_search_title = self._clean_album_title_for_comparison(search_title)
            clean_db_title = self._clean_album_title_for_comparison(db_album.title)
            clean_title_similarity = self._string_similarity(clean_search_title, clean_db_title)
            
            # Use the best title similarity
            best_title_similarity = max(title_similarity, clean_title_similarity)
            
            # Weight: 50% title, 50% artist (equal weight to prevent false positives)
            # Also require minimum artist similarity to prevent matching wrong artists
            confidence = (best_title_similarity * 0.5) + (artist_similarity * 0.5)
            
            # Apply artist similarity penalty: if artist match is too low, drastically reduce confidence
            if artist_similarity < 0.6:  # Less than 60% artist match
                confidence *= 0.3  # Reduce confidence by 70%
            
            # Smart Edition Matching: Boost confidence if we found a "better" edition
            if expected_track_count and db_album.track_count and clean_title_similarity >= 0.8:
                # If the cleaned titles match well, check if this is an edition upgrade
                if db_album.track_count >= expected_track_count:
                    # Found same/better edition (e.g., Deluxe when searching for Standard)
                    edition_bonus = min(0.15, (db_album.track_count - expected_track_count) / expected_track_count * 0.1)
                    confidence += edition_bonus
                    logger.debug(f"  📀 Edition upgrade bonus: +{edition_bonus:.3f} ({db_album.track_count} >= {expected_track_count} tracks)")
                elif db_album.track_count < expected_track_count * 0.8:
                    # Found significantly smaller edition, apply penalty
                    edition_penalty = 0.1
                    confidence -= edition_penalty
                    logger.debug(f"  📀 Edition downgrade penalty: -{edition_penalty:.3f} ({db_album.track_count} << {expected_track_count} tracks)")
            
            return min(confidence, 1.0)  # Cap at 1.0
            
        except Exception as e:
            logger.error(f"Error calculating album confidence: {e}")
            return 0.0
    
    def _generate_track_title_variations(self, title: str) -> List[str]:
        """Generate variations of track title for better matching"""
        variations = [title]  # Always include original
        
        # IMPORTANT: Generate bracket/dash style variations for better matching
        # Convert "Track - Instrumental" to "Track (Instrumental)" and vice versa
        if ' - ' in title:
            # Convert dash style to parentheses style
            dash_parts = title.split(' - ', 1)
            if len(dash_parts) == 2:
                paren_version = f"{dash_parts[0]} ({dash_parts[1]})"
                variations.append(paren_version)
        
        if '(' in title and ')' in title:
            # Convert parentheses style to dash style
            dash_version = re.sub(r'\s*\(([^)]+)\)\s*', r' - \1', title)
            if dash_version != title:
                variations.append(dash_version)
        
        # Clean up the title
        title_lower = title.lower().strip()
        
        # Conservative track title variations - only remove clear noise, preserve meaningful differences
        track_patterns = [
            # Remove explicit/clean markers only
            r'\s*\(explicit\)',
            r'\s*\(clean\)',
            r'\s*\[explicit\]',
            r'\s*\[clean\]',
            # Remove featuring artists in parentheses
            r'\s*\(.*feat\..*\)',
            r'\s*\(.*featuring.*\)',
            r'\s*\(.*ft\..*\)',
            # Remove radio/TV edit markers
            r'\s*\(radio\s*edit\)',
            r'\s*\(tv\s*edit\)',
            r'\s*\[radio\s*edit\]',
            r'\s*\[tv\s*edit\]',
        ]
        
        # DO NOT remove remixes, versions, or content after dashes
        # These are meaningful distinctions that should not be collapsed
        
        for pattern in track_patterns:
            # Apply pattern to original title
            cleaned = re.sub(pattern, '', title, flags=re.IGNORECASE).strip()
            if cleaned and cleaned.lower() != title_lower and cleaned not in variations:
                variations.append(cleaned)
            
            # Apply pattern to lowercase version
            cleaned_lower = re.sub(pattern, '', title_lower, flags=re.IGNORECASE).strip()
            if cleaned_lower and cleaned_lower != title_lower:
                # Convert back to proper case
                cleaned_proper = cleaned_lower.title()
                if cleaned_proper not in variations:
                    variations.append(cleaned_proper)
        
        # Remove duplicates while preserving order
        seen = set()
        unique_variations = []
        for var in variations:
            var_key = var.lower().strip()
            if var_key not in seen and var.strip():
                seen.add(var_key)
                unique_variations.append(var.strip())
        
        return unique_variations
    
    def _normalize_for_comparison(self, text: str) -> str:
        """Normalize text for comparison with Unicode accent handling"""
        if not text:
            return ""
        
        # Try to use unidecode for accent normalization, fallback to basic if not available
        try:
            from unidecode import unidecode
            # Convert accents: é→e, ñ→n, ü→u, etc.
            normalized = unidecode(text)
        except ImportError:
            # Fallback: basic normalization without accent handling
            normalized = text
            logger.warning("unidecode not available, accent matching may be limited")
        
        # Convert to lowercase and strip
        return normalized.lower().strip()
    
    def _calculate_track_confidence(self, search_title: str, search_artist: str, db_track: DatabaseTrack) -> float:
        """Calculate confidence score for track match with enhanced cleaning and Unicode normalization"""
        try:
            # Unicode-aware normalization for accent matching (é→e, ñ→n, etc.)
            search_title_norm = self._normalize_for_comparison(search_title)
            search_artist_norm = self._normalize_for_comparison(search_artist)
            db_title_norm = self._normalize_for_comparison(db_track.title)
            db_artist_norm = self._normalize_for_comparison(db_track.artist_name)
            
            # Debug logging for Unicode normalization
            if search_title != search_title_norm or search_artist != search_artist_norm or \
               db_track.title != db_title_norm or db_track.artist_name != db_artist_norm:
                logger.debug(f"🔤 Unicode normalization:")
                logger.debug(f"   Search: '{search_title}' → '{search_title_norm}' | '{search_artist}' → '{search_artist_norm}'")
                logger.debug(f"   Database: '{db_track.title}' → '{db_title_norm}' | '{db_track.artist_name}' → '{db_artist_norm}'")
            
            # Direct similarity with Unicode normalization
            title_similarity = self._string_similarity(search_title_norm, db_title_norm)
            artist_similarity = self._string_similarity(search_artist_norm, db_artist_norm)
            
            # Also try with cleaned versions (removing parentheses, brackets, etc.)
            clean_search_title = self._clean_track_title_for_comparison(search_title)
            clean_db_title = self._clean_track_title_for_comparison(db_track.title)
            clean_title_similarity = self._string_similarity(clean_search_title, clean_db_title)
            
            # Use the best title similarity (direct or cleaned)
            best_title_similarity = max(title_similarity, clean_title_similarity)
            
            # Weight: 50% title, 50% artist (equal weight to prevent false positives)
            # Also require minimum artist similarity to prevent matching wrong artists
            confidence = (best_title_similarity * 0.5) + (artist_similarity * 0.5)
            
            # Apply artist similarity penalty: if artist match is too low, drastically reduce confidence
            if artist_similarity < 0.6:  # Less than 60% artist match
                confidence *= 0.3  # Reduce confidence by 70%
            
            return confidence
            
        except Exception as e:
            logger.error(f"Error calculating track confidence: {e}")
            return 0.0
    
    def _clean_track_title_for_comparison(self, title: str) -> str:
        """Clean track title for comparison by normalizing brackets/dashes and removing noise"""
        cleaned = title.lower().strip()
        
        # STEP 1: Normalize bracket/dash styles for consistent matching
        # Convert all bracket styles to spaces for better matching
        cleaned = re.sub(r'\s*[\[\(]\s*', ' ', cleaned)  # Convert opening brackets/parens to space
        cleaned = re.sub(r'\s*[\]\)]\s*', ' ', cleaned)  # Convert closing brackets/parens to space
        cleaned = re.sub(r'\s*-\s*', ' ', cleaned)       # Convert dashes to spaces too
        
        # STEP 2: Remove clear noise patterns - very conservative approach
        patterns_to_remove = [
            r'\s*explicit\s*',      # Remove explicit markers (now without brackets)
            r'\s*clean\s*',         # Remove clean markers (now without brackets)  
            r'\s*feat\..*',         # Remove featuring (now without brackets)
            r'\s*featuring.*',      # Remove featuring (now without brackets)
            r'\s*ft\..*',           # Remove ft. (now without brackets)
            r'\s*edit\s*$',         # Remove "- edit" suffix only (specific case: "Reborn - edit" → "Reborn")
        ]
        
        for pattern in patterns_to_remove:
            cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE).strip()
        
        # STEP 3: Clean up extra spaces
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        
        return cleaned
    
    def _clean_album_title_for_comparison(self, title: str) -> str:
        """Clean album title by removing edition markers for comparison"""
        cleaned = title.lower()
        
        # Remove common edition patterns
        patterns = [
            r'\s*\(deluxe\s*edition?\)',
            r'\s*\(expanded\s*edition?\)', 
            r'\s*\(platinum\s*edition?\)',
            r'\s*\(special\s*edition?\)',
            r'\s*\(remastered?\)',
            r'\s*\(anniversary\s*edition?\)',
            r'\s*\(.*version\)',
            r'\s*-\s*deluxe\s*edition?',
            r'\s*-\s*platinum\s*edition?',
            r'\s+deluxe\s*edition?$',
            r'\s+platinum\s*edition?$',
        ]
        
        for pattern in patterns:
            cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE)
        
        return cleaned.strip()
    
    def get_album_completion_stats(self, artist_name: str) -> Dict[str, int]:
        """
        Get completion statistics for all albums by an artist.
        Returns dict with counts of complete, partial, and missing albums.
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            # Get all albums by this artist with track counts
            cursor.execute("""
                SELECT albums.id, albums.track_count, COUNT(tracks.id) as actual_tracks
                FROM albums
                JOIN artists ON albums.artist_id = artists.id
                LEFT JOIN tracks ON albums.id = tracks.album_id
                WHERE artists.name LIKE ?
                GROUP BY albums.id, albums.track_count
            """, (f"%{artist_name}%",))
            
            results = cursor.fetchall()
            stats = {
                'complete': 0,          # >=90% of tracks
                'nearly_complete': 0,   # 80-89% of tracks
                'partial': 0,           # 1-79% of tracks  
                'missing': 0,           # 0% of tracks
                'total': len(results)
            }
            
            for row in results:
                expected_tracks = row['track_count'] or 1  # Avoid division by zero
                actual_tracks = row['actual_tracks']
                completion_ratio = actual_tracks / expected_tracks
                
                if actual_tracks == 0:
                    stats['missing'] += 1
                elif completion_ratio >= 0.9:
                    stats['complete'] += 1
                elif completion_ratio >= 0.8:
                    stats['nearly_complete'] += 1
                else:
                    stats['partial'] += 1
            
            return stats
            
        except Exception as e:
            logger.error(f"Error getting album completion stats for artist '{artist_name}': {e}")
            return {'complete': 0, 'nearly_complete': 0, 'partial': 0, 'missing': 0, 'total': 0}
    
    def set_metadata(self, key: str, value: str):
        """Set a metadata value"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT OR REPLACE INTO metadata (key, value, updated_at) 
                    VALUES (?, ?, CURRENT_TIMESTAMP)
                """, (key, value))
                conn.commit()
        except Exception as e:
            logger.error(f"Error setting metadata {key}: {e}")
    
    def get_metadata(self, key: str) -> Optional[str]:
        """Get a metadata value"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT value FROM metadata WHERE key = ?", (key,))
                result = cursor.fetchone()
                return result['value'] if result else None
        except Exception as e:
            logger.error(f"Error getting metadata {key}: {e}")
            return None
    
    def record_full_refresh_completion(self):
        """Record when a full refresh was completed"""
        from datetime import datetime
        self.set_metadata('last_full_refresh', datetime.now().isoformat())
    
    def get_last_full_refresh(self) -> Optional[str]:
        """Get the date of the last full refresh"""
        return self.get_metadata('last_full_refresh')

    def set_preference(self, key: str, value: str):
        """Set a user preference (alias for set_metadata for clarity)"""
        self.set_metadata(key, value)

    def get_preference(self, key: str) -> Optional[str]:
        """Get a user preference (alias for get_metadata for clarity)"""
        return self.get_metadata(key)

    # Quality profile management methods

    def get_quality_profile(self) -> dict:
        """Get the quality profile configuration, returns default if not set"""
        import json

        profile_json = self.get_preference('quality_profile')

        if profile_json:
            try:
                return json.loads(profile_json)
            except json.JSONDecodeError:
                logger.error("Failed to parse quality profile JSON, returning default")

        # Return smart defaults (balanced preset)
        return {
            "version": 1,
            "preset": "balanced",
            "qualities": {
                "flac": {
                    "enabled": True,
                    "min_mb": 0,
                    "max_mb": 150,
                    "priority": 1
                },
                "mp3_320": {
                    "enabled": True,
                    "min_mb": 0,
                    "max_mb": 20,
                    "priority": 2
                },
                "mp3_256": {
                    "enabled": True,
                    "min_mb": 0,
                    "max_mb": 15,
                    "priority": 3
                },
                "mp3_192": {
                    "enabled": False,
                    "min_mb": 0,
                    "max_mb": 12,
                    "priority": 4
                }
            },
            "fallback_enabled": True
        }

    def set_quality_profile(self, profile: dict) -> bool:
        """Save quality profile configuration"""
        import json

        try:
            profile_json = json.dumps(profile)
            self.set_preference('quality_profile', profile_json)
            logger.info(f"Quality profile saved: preset={profile.get('preset', 'custom')}")
            return True
        except Exception as e:
            logger.error(f"Failed to save quality profile: {e}")
            return False

    def get_quality_preset(self, preset_name: str) -> dict:
        """Get a predefined quality preset"""
        presets = {
            "audiophile": {
                "version": 1,
                "preset": "audiophile",
                "qualities": {
                    "flac": {
                        "enabled": True,
                        "min_mb": 0,
                        "max_mb": 200,
                        "priority": 1
                    },
                    "mp3_320": {
                        "enabled": False,
                        "min_mb": 0,
                        "max_mb": 20,
                        "priority": 2
                    },
                    "mp3_256": {
                        "enabled": False,
                        "min_mb": 0,
                        "max_mb": 15,
                        "priority": 3
                    },
                    "mp3_192": {
                        "enabled": False,
                        "min_mb": 0,
                        "max_mb": 12,
                        "priority": 4
                    }
                },
                "fallback_enabled": False
            },
            "balanced": {
                "version": 1,
                "preset": "balanced",
                "qualities": {
                    "flac": {
                        "enabled": True,
                        "min_mb": 0,
                        "max_mb": 150,
                        "priority": 1
                    },
                    "mp3_320": {
                        "enabled": True,
                        "min_mb": 0,
                        "max_mb": 20,
                        "priority": 2
                    },
                    "mp3_256": {
                        "enabled": True,
                        "min_mb": 0,
                        "max_mb": 15,
                        "priority": 3
                    },
                    "mp3_192": {
                        "enabled": False,
                        "min_mb": 0,
                        "max_mb": 12,
                        "priority": 4
                    }
                },
                "fallback_enabled": True
            },
            "space_saver": {
                "version": 1,
                "preset": "space_saver",
                "qualities": {
                    "flac": {
                        "enabled": False,
                        "min_mb": 0,
                        "max_mb": 150,
                        "priority": 4
                    },
                    "mp3_320": {
                        "enabled": True,
                        "min_mb": 0,
                        "max_mb": 15,
                        "priority": 1
                    },
                    "mp3_256": {
                        "enabled": True,
                        "min_mb": 0,
                        "max_mb": 12,
                        "priority": 2
                    },
                    "mp3_192": {
                        "enabled": True,
                        "min_mb": 0,
                        "max_mb": 10,
                        "priority": 3
                    }
                },
                "fallback_enabled": True
            }
        }

        return presets.get(preset_name, presets["balanced"])

    # Wishlist management methods
    
    def add_to_wishlist(self, spotify_track_data: Dict[str, Any], failure_reason: str = "Download failed",
                       source_type: str = "unknown", source_info: Dict[str, Any] = None) -> bool:
        """Add a failed track to the wishlist for retry"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # Use Spotify track ID as unique identifier
                track_id = spotify_track_data.get('id')
                if not track_id:
                    logger.error("Cannot add track to wishlist: missing Spotify track ID")
                    return False

                track_name = spotify_track_data.get('name', 'Unknown Track')
                artists = spotify_track_data.get('artists', [])
                if artists:
                    first_artist = artists[0]
                    if isinstance(first_artist, str):
                        artist_name = first_artist
                    elif isinstance(first_artist, dict):
                        artist_name = first_artist.get('name', 'Unknown Artist')
                    else:
                        artist_name = 'Unknown Artist'
                else:
                    artist_name = 'Unknown Artist'

                # Check for duplicates by track name + artist (not just Spotify ID)
                # This prevents adding the same track multiple times with different IDs or edge cases
                cursor.execute("""
                    SELECT id, spotify_track_id, spotify_data FROM wishlist_tracks
                """)

                existing_tracks = cursor.fetchall()

                # Check if any existing track has matching name AND artist
                for existing in existing_tracks:
                    try:
                        existing_data = json.loads(existing['spotify_data'])
                        existing_name = existing_data.get('name', '')
                        existing_artists = existing_data.get('artists', [])
                        if existing_artists:
                            existing_first = existing_artists[0]
                            if isinstance(existing_first, str):
                                existing_artist = existing_first
                            elif isinstance(existing_first, dict):
                                existing_artist = existing_first.get('name', '')
                            else:
                                existing_artist = ''
                        else:
                            existing_artist = ''

                        # Case-insensitive comparison of track name and primary artist
                        if (existing_name.lower() == track_name.lower() and
                            existing_artist.lower() == artist_name.lower()):
                            logger.info(f"Skipping duplicate wishlist entry: '{track_name}' by {artist_name} (already exists as ID: {existing['id']})")
                            return False  # Already exists, don't add duplicate
                    except Exception as parse_error:
                        logger.warning(f"Error parsing existing wishlist track data: {parse_error}")
                        continue

                # Convert data to JSON strings
                spotify_json = json.dumps(spotify_track_data)
                source_json = json.dumps(source_info or {})

                # No duplicate found, insert the track
                cursor.execute("""
                    INSERT OR REPLACE INTO wishlist_tracks
                    (spotify_track_id, spotify_data, failure_reason, source_type, source_info, date_added)
                    VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (track_id, spotify_json, failure_reason, source_type, source_json))

                conn.commit()

                logger.info(f"Added track to wishlist: '{track_name}' by {artist_name}")
                return True

        except Exception as e:
            logger.error(f"Error adding track to wishlist: {e}")
            return False
    
    def remove_from_wishlist(self, spotify_track_id: str) -> bool:
        """Remove a track from the wishlist (typically after successful download)"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM wishlist_tracks WHERE spotify_track_id = ?", (spotify_track_id,))
                conn.commit()
                
                if cursor.rowcount > 0:
                    logger.info(f"Removed track from wishlist: {spotify_track_id}")
                    return True
                else:
                    logger.debug(f"Track not found in wishlist: {spotify_track_id}")
                    return False
                    
        except Exception as e:
            logger.error(f"Error removing track from wishlist: {e}")
            return False
    
    def get_wishlist_tracks(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Get all tracks in the wishlist, ordered by date added (oldest first for retry priority)"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                query = """
                    SELECT id, spotify_track_id, spotify_data, failure_reason, retry_count, 
                           last_attempted, date_added, source_type, source_info
                    FROM wishlist_tracks 
                    ORDER BY date_added
                """
                
                if limit:
                    query += f" LIMIT {limit}"
                
                cursor.execute(query)
                rows = cursor.fetchall()
                
                wishlist_tracks = []
                for row in rows:
                    try:
                        spotify_data = json.loads(row['spotify_data'])
                        source_info = json.loads(row['source_info']) if row['source_info'] else {}
                        
                        wishlist_tracks.append({
                            'id': row['id'],
                            'spotify_track_id': row['spotify_track_id'],
                            'spotify_data': spotify_data,
                            'failure_reason': row['failure_reason'],
                            'retry_count': row['retry_count'],
                            'last_attempted': row['last_attempted'],
                            'date_added': row['date_added'],
                            'source_type': row['source_type'],
                            'source_info': source_info
                        })
                    except json.JSONDecodeError as e:
                        logger.error(f"Error parsing wishlist track data: {e}")
                        continue
                
                return wishlist_tracks
                
        except Exception as e:
            logger.error(f"Error getting wishlist tracks: {e}")
            return []
    
    def update_wishlist_retry(self, spotify_track_id: str, success: bool, error_message: str = None) -> bool:
        """Update retry count and status for a wishlist track"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                if success:
                    # Remove from wishlist on success
                    cursor.execute("DELETE FROM wishlist_tracks WHERE spotify_track_id = ?", (spotify_track_id,))
                else:
                    # Increment retry count and update failure reason
                    cursor.execute("""
                        UPDATE wishlist_tracks 
                        SET retry_count = retry_count + 1, 
                            last_attempted = CURRENT_TIMESTAMP,
                            failure_reason = COALESCE(?, failure_reason)
                        WHERE spotify_track_id = ?
                    """, (error_message, spotify_track_id))
                
                conn.commit()
                return cursor.rowcount > 0
                
        except Exception as e:
            logger.error(f"Error updating wishlist retry status: {e}")
            return False
    
    def get_wishlist_count(self) -> int:
        """Get the total number of tracks in the wishlist"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM wishlist_tracks")
                result = cursor.fetchone()
                return result[0] if result else 0
        except Exception as e:
            logger.error(f"Error getting wishlist count: {e}")
            return 0
    
    def clear_wishlist(self) -> bool:
        """Clear all tracks from the wishlist"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM wishlist_tracks")
                conn.commit()
                logger.info(f"Cleared {cursor.rowcount} tracks from wishlist")
                return True
        except Exception as e:
            logger.error(f"Error clearing wishlist: {e}")
            return False

    def remove_wishlist_duplicates(self) -> int:
        """Remove duplicate tracks from wishlist based on track name + artist.
        Keeps the oldest entry (by date_added) for each duplicate set.
        Returns the number of duplicates removed."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # Get all wishlist tracks
                cursor.execute("""
                    SELECT id, spotify_track_id, spotify_data, date_added
                    FROM wishlist_tracks
                    ORDER BY date_added ASC
                """)
                all_tracks = cursor.fetchall()

                # Track seen tracks and duplicates to remove
                seen_tracks = {}  # Key: (track_name, artist_name), Value: track_id to keep
                duplicates_to_remove = []

                for track in all_tracks:
                    try:
                        track_data = json.loads(track['spotify_data'])
                        track_name = track_data.get('name', '').lower()
                        artists = track_data.get('artists', [])
                        artist_name = artists[0].get('name', '').lower() if artists else 'unknown'

                        key = (track_name, artist_name)

                        if key in seen_tracks:
                            # Duplicate found - mark for removal
                            duplicates_to_remove.append(track['id'])
                            logger.info(f"Found duplicate: '{track_name}' by {artist_name} (ID: {track['id']}, keeping ID: {seen_tracks[key]})")
                        else:
                            # First occurrence - keep this one
                            seen_tracks[key] = track['id']

                    except Exception as parse_error:
                        logger.warning(f"Error parsing wishlist track {track['id']}: {parse_error}")
                        continue

                # Remove all duplicates
                removed_count = 0
                for duplicate_id in duplicates_to_remove:
                    cursor.execute("DELETE FROM wishlist_tracks WHERE id = ?", (duplicate_id,))
                    removed_count += 1

                conn.commit()
                logger.info(f"Removed {removed_count} duplicate tracks from wishlist")
                return removed_count

        except Exception as e:
            logger.error(f"Error removing wishlist duplicates: {e}")
            return 0

    # Watchlist operations
    def add_artist_to_watchlist(self, spotify_artist_id: str, artist_name: str) -> bool:
        """Add an artist to the watchlist for monitoring new releases"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                cursor.execute("""
                    INSERT OR REPLACE INTO watchlist_artists 
                    (spotify_artist_id, artist_name, date_added, updated_at)
                    VALUES (?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """, (spotify_artist_id, artist_name))
                
                conn.commit()
                logger.info(f"Added artist '{artist_name}' to watchlist (Spotify ID: {spotify_artist_id})")
                return True
                
        except Exception as e:
            logger.error(f"Error adding artist '{artist_name}' to watchlist: {e}")
            return False

    def remove_artist_from_watchlist(self, spotify_artist_id: str) -> bool:
        """Remove an artist from the watchlist"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # Get artist name for logging
                cursor.execute("SELECT artist_name FROM watchlist_artists WHERE spotify_artist_id = ?", (spotify_artist_id,))
                result = cursor.fetchone()
                artist_name = result['artist_name'] if result else "Unknown"
                
                cursor.execute("DELETE FROM watchlist_artists WHERE spotify_artist_id = ?", (spotify_artist_id,))
                
                if cursor.rowcount > 0:
                    conn.commit()
                    logger.info(f"Removed artist '{artist_name}' from watchlist (Spotify ID: {spotify_artist_id})")
                    return True
                else:
                    logger.warning(f"Artist with Spotify ID {spotify_artist_id} not found in watchlist")
                    return False
                
        except Exception as e:
            logger.error(f"Error removing artist from watchlist (Spotify ID: {spotify_artist_id}): {e}")
            return False

    def is_artist_in_watchlist(self, spotify_artist_id: str) -> bool:
        """Check if an artist is currently in the watchlist"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                cursor.execute("SELECT 1 FROM watchlist_artists WHERE spotify_artist_id = ? LIMIT 1", (spotify_artist_id,))
                result = cursor.fetchone()
                
                return result is not None
                
        except Exception as e:
            logger.error(f"Error checking if artist is in watchlist (Spotify ID: {spotify_artist_id}): {e}")
            return False

    def get_watchlist_artists(self) -> List[WatchlistArtist]:
        """Get all artists in the watchlist"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # Check which columns exist (for migration compatibility)
                cursor.execute("PRAGMA table_info(watchlist_artists)")
                existing_columns = {column[1] for column in cursor.fetchall()}

                # Build SELECT query based on existing columns
                base_columns = ['id', 'spotify_artist_id', 'artist_name', 'date_added',
                               'last_scan_timestamp', 'created_at', 'updated_at']
                optional_columns = ['image_url', 'include_albums', 'include_eps', 'include_singles']

                columns_to_select = base_columns + [col for col in optional_columns if col in existing_columns]

                cursor.execute(f"""
                    SELECT {', '.join(columns_to_select)}
                    FROM watchlist_artists
                    ORDER BY date_added DESC
                """)

                rows = cursor.fetchall()

                watchlist_artists = []
                for row in rows:
                    # Safely get optional columns with defaults (sqlite3.Row uses dict-style access)
                    image_url = row['image_url'] if 'image_url' in existing_columns else None
                    include_albums = bool(row['include_albums']) if 'include_albums' in existing_columns else True
                    include_eps = bool(row['include_eps']) if 'include_eps' in existing_columns else True
                    include_singles = bool(row['include_singles']) if 'include_singles' in existing_columns else True

                    watchlist_artists.append(WatchlistArtist(
                        id=row['id'],
                        spotify_artist_id=row['spotify_artist_id'],
                        artist_name=row['artist_name'],
                        date_added=datetime.fromisoformat(row['date_added']),
                        last_scan_timestamp=datetime.fromisoformat(row['last_scan_timestamp']) if row['last_scan_timestamp'] else None,
                        created_at=datetime.fromisoformat(row['created_at']) if row['created_at'] else None,
                        updated_at=datetime.fromisoformat(row['updated_at']) if row['updated_at'] else None,
                        image_url=image_url,
                        include_albums=include_albums,
                        include_eps=include_eps,
                        include_singles=include_singles
                    ))

                return watchlist_artists

        except Exception as e:
            logger.error(f"Error getting watchlist artists: {e}")
            return []

    def get_watchlist_count(self) -> int:
        """Get the number of artists in the watchlist"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                cursor.execute("SELECT COUNT(*) as count FROM watchlist_artists")
                result = cursor.fetchone()
                
                return result['count'] if result else 0
                
        except Exception as e:
            logger.error(f"Error getting watchlist count: {e}")
            return 0

    def update_watchlist_artist_image(self, spotify_artist_id: str, image_url: str) -> bool:
        """Update the image URL for a watchlist artist"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # Check if image_url column exists (for migration compatibility)
                cursor.execute("PRAGMA table_info(watchlist_artists)")
                existing_columns = {column[1] for column in cursor.fetchall()}

                if 'image_url' not in existing_columns:
                    logger.warning("image_url column does not exist in watchlist_artists table. Skipping update. Please restart the app to apply migrations.")
                    return False

                cursor.execute("""
                    UPDATE watchlist_artists
                    SET image_url = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE spotify_artist_id = ?
                """, (image_url, spotify_artist_id))

                conn.commit()
                return cursor.rowcount > 0

        except Exception as e:
            logger.error(f"Error updating watchlist artist image: {e}")
            return False

    # === Discovery Feature Methods ===

    def add_or_update_similar_artist(self, source_artist_id: str, similar_artist_spotify_id: str,
                                      similar_artist_name: str, similarity_rank: int = 1) -> bool:
        """Add or update a similar artist recommendation"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                cursor.execute("""
                    INSERT INTO similar_artists
                    (source_artist_id, similar_artist_spotify_id, similar_artist_name, similarity_rank, occurrence_count, last_updated)
                    VALUES (?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
                    ON CONFLICT(source_artist_id, similar_artist_spotify_id)
                    DO UPDATE SET
                        similarity_rank = excluded.similarity_rank,
                        occurrence_count = occurrence_count + 1,
                        last_updated = CURRENT_TIMESTAMP
                """, (source_artist_id, similar_artist_spotify_id, similar_artist_name, similarity_rank))

                conn.commit()
                return True

        except Exception as e:
            logger.error(f"Error adding similar artist: {e}")
            return False

    def get_similar_artists_for_source(self, source_artist_id: str) -> List[SimilarArtist]:
        """Get all similar artists for a given source artist"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                cursor.execute("""
                    SELECT * FROM similar_artists
                    WHERE source_artist_id = ?
                    ORDER BY similarity_rank ASC
                """, (source_artist_id,))

                rows = cursor.fetchall()
                return [SimilarArtist(
                    id=row['id'],
                    source_artist_id=row['source_artist_id'],
                    similar_artist_spotify_id=row['similar_artist_spotify_id'],
                    similar_artist_name=row['similar_artist_name'],
                    similarity_rank=row['similarity_rank'],
                    occurrence_count=row['occurrence_count'],
                    last_updated=datetime.fromisoformat(row['last_updated'])
                ) for row in rows]

        except Exception as e:
            logger.error(f"Error getting similar artists: {e}")
            return []

    def has_fresh_similar_artists(self, source_artist_id: str, days_threshold: int = 30) -> bool:
        """
        Check if we have cached similar artists that are still fresh (< days_threshold old).
        Returns True if we have recent data, False if data is stale or missing.
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                cursor.execute("""
                    SELECT COUNT(*) as count, MAX(last_updated) as last_updated
                    FROM similar_artists
                    WHERE source_artist_id = ?
                """, (source_artist_id,))

                row = cursor.fetchone()

                if not row or row['count'] == 0:
                    # No similar artists cached
                    return False

                # Check if data is fresh
                last_updated = datetime.fromisoformat(row['last_updated'])
                days_since_update = (datetime.now() - last_updated).total_seconds() / 86400  # seconds to days

                return days_since_update < days_threshold

        except Exception as e:
            logger.error(f"Error checking similar artists freshness: {e}")
            return False  # Default to re-fetching on error

    def get_top_similar_artists(self, limit: int = 50) -> List[SimilarArtist]:
        """Get top similar artists across all watchlist artists, ordered by occurrence count"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                cursor.execute("""
                    SELECT
                        MAX(id) as id,
                        MAX(source_artist_id) as source_artist_id,
                        similar_artist_spotify_id,
                        similar_artist_name,
                        AVG(similarity_rank) as similarity_rank,
                        SUM(occurrence_count) as occurrence_count,
                        MAX(last_updated) as last_updated
                    FROM similar_artists
                    GROUP BY similar_artist_spotify_id, similar_artist_name
                    ORDER BY occurrence_count DESC, similarity_rank ASC
                    LIMIT ?
                """, (limit,))

                rows = cursor.fetchall()
                return [SimilarArtist(
                    id=row['id'],
                    source_artist_id=row['source_artist_id'],
                    similar_artist_spotify_id=row['similar_artist_spotify_id'],
                    similar_artist_name=row['similar_artist_name'],
                    similarity_rank=int(row['similarity_rank']),
                    occurrence_count=row['occurrence_count'],
                    last_updated=datetime.fromisoformat(row['last_updated'])
                ) for row in rows]

        except Exception as e:
            logger.error(f"Error getting top similar artists: {e}")
            return []

    def add_to_discovery_pool(self, track_data: Dict[str, Any]) -> bool:
        """Add a track to the discovery pool"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # Check if track already exists
                cursor.execute("SELECT COUNT(*) as count FROM discovery_pool WHERE spotify_track_id = ?",
                              (track_data['spotify_track_id'],))
                if cursor.fetchone()['count'] > 0:
                    return True  # Already in pool

                # Get artist genres if available
                artist_genres = track_data.get('artist_genres')
                artist_genres_json = json.dumps(artist_genres) if artist_genres else None

                cursor.execute("""
                    INSERT INTO discovery_pool
                    (spotify_track_id, spotify_album_id, spotify_artist_id, track_name, artist_name,
                     album_name, album_cover_url, duration_ms, popularity, release_date,
                     is_new_release, track_data_json, artist_genres, added_date)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (
                    track_data['spotify_track_id'],
                    track_data['spotify_album_id'],
                    track_data['spotify_artist_id'],
                    track_data['track_name'],
                    track_data['artist_name'],
                    track_data['album_name'],
                    track_data.get('album_cover_url'),
                    track_data['duration_ms'],
                    track_data.get('popularity', 0),
                    track_data['release_date'],
                    track_data.get('is_new_release', False),
                    json.dumps(track_data['track_data_json']),
                    artist_genres_json
                ))

                conn.commit()
                return True

        except Exception as e:
            logger.error(f"Error adding to discovery pool: {e}")
            return False

    def rotate_discovery_pool(self, max_tracks: int = 2000, remove_count: int = 500):
        """Remove oldest tracks from discovery pool if it exceeds max_tracks"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # Check current count
                cursor.execute("SELECT COUNT(*) as count FROM discovery_pool")
                current_count = cursor.fetchone()['count']

                if current_count > max_tracks:
                    # Remove oldest tracks
                    cursor.execute("""
                        DELETE FROM discovery_pool
                        WHERE id IN (
                            SELECT id FROM discovery_pool
                            ORDER BY added_date ASC
                            LIMIT ?
                        )
                    """, (remove_count,))

                    conn.commit()
                    logger.info(f"Removed {remove_count} oldest tracks from discovery pool")

        except Exception as e:
            logger.error(f"Error rotating discovery pool: {e}")

    def get_discovery_pool_tracks(self, limit: int = 100, new_releases_only: bool = False) -> List[DiscoveryTrack]:
        """Get tracks from discovery pool"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                if new_releases_only:
                    cursor.execute("""
                        SELECT * FROM discovery_pool
                        WHERE is_new_release = 1
                        ORDER BY added_date DESC
                        LIMIT ?
                    """, (limit,))
                else:
                    cursor.execute("""
                        SELECT * FROM discovery_pool
                        ORDER BY added_date DESC
                        LIMIT ?
                    """, (limit,))

                rows = cursor.fetchall()
                return [DiscoveryTrack(
                    id=row['id'],
                    spotify_track_id=row['spotify_track_id'],
                    spotify_album_id=row['spotify_album_id'],
                    spotify_artist_id=row['spotify_artist_id'],
                    track_name=row['track_name'],
                    artist_name=row['artist_name'],
                    album_name=row['album_name'],
                    album_cover_url=row['album_cover_url'],
                    duration_ms=row['duration_ms'],
                    popularity=row['popularity'],
                    release_date=row['release_date'],
                    is_new_release=bool(row['is_new_release']),
                    track_data_json=row['track_data_json'],
                    added_date=datetime.fromisoformat(row['added_date'])
                ) for row in rows]

        except Exception as e:
            logger.error(f"Error getting discovery pool tracks: {e}")
            return []

    def cache_discovery_recent_album(self, album_data: Dict[str, Any]) -> bool:
        """Cache a recent album for the discover page (from watchlist or similar artists)"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                cursor.execute("""
                    INSERT OR REPLACE INTO discovery_recent_albums
                    (album_spotify_id, album_name, artist_name, artist_spotify_id, album_cover_url, release_date, album_type, cached_date)
                    VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (
                    album_data['album_spotify_id'],
                    album_data['album_name'],
                    album_data['artist_name'],
                    album_data['artist_spotify_id'],
                    album_data.get('album_cover_url'),
                    album_data['release_date'],
                    album_data.get('album_type', 'album')
                ))

                conn.commit()
                return True

        except Exception as e:
            logger.error(f"Error caching discovery recent album: {e}")
            return False

    def get_discovery_recent_albums(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get cached recent albums for discover page"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                cursor.execute("""
                    SELECT * FROM discovery_recent_albums
                    ORDER BY release_date DESC
                    LIMIT ?
                """, (limit,))

                rows = cursor.fetchall()
                return [{
                    'album_spotify_id': row['album_spotify_id'],
                    'album_name': row['album_name'],
                    'artist_name': row['artist_name'],
                    'artist_spotify_id': row['artist_spotify_id'],
                    'album_cover_url': row['album_cover_url'],
                    'release_date': row['release_date'],
                    'album_type': row['album_type']
                } for row in rows]

        except Exception as e:
            logger.error(f"Error getting discovery recent albums: {e}")
            return []

    def clear_discovery_recent_albums(self) -> bool:
        """Clear all cached recent albums"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM discovery_recent_albums")
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Error clearing discovery recent albums: {e}")
            return False

    def save_curated_playlist(self, playlist_type: str, track_ids: List[str]) -> bool:
        """Save a curated playlist selection (stays same until next discovery pool update)"""
        try:
            import json
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT OR REPLACE INTO discovery_curated_playlists
                    (playlist_type, track_ids_json, curated_date)
                    VALUES (?, ?, CURRENT_TIMESTAMP)
                """, (playlist_type, json.dumps(track_ids)))
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Error saving curated playlist {playlist_type}: {e}")
            return False

    def get_curated_playlist(self, playlist_type: str) -> Optional[List[str]]:
        """Get saved curated playlist track IDs"""
        try:
            import json
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT track_ids_json FROM discovery_curated_playlists
                    WHERE playlist_type = ?
                """, (playlist_type,))
                row = cursor.fetchone()
                if row:
                    return json.loads(row['track_ids_json'])
                return None
        except Exception as e:
            logger.error(f"Error getting curated playlist {playlist_type}: {e}")
            return None

    def should_populate_discovery_pool(self, hours_threshold: int = 24) -> bool:
        """Check if discovery pool should be populated (hasn't been updated in X hours)"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT last_populated_timestamp
                    FROM discovery_pool_metadata
                    WHERE id = 1
                """)
                row = cursor.fetchone()

                if not row:
                    # Never populated before
                    return True

                last_populated = datetime.fromisoformat(row['last_populated_timestamp'])
                hours_since_update = (datetime.now() - last_populated).total_seconds() / 3600

                return hours_since_update >= hours_threshold

        except Exception as e:
            logger.error(f"Error checking discovery pool timestamp: {e}")
            return True  # Default to allowing population on error

    def update_discovery_pool_timestamp(self, track_count: int) -> bool:
        """Update the last populated timestamp and track count"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT OR REPLACE INTO discovery_pool_metadata
                    (id, last_populated_timestamp, track_count, updated_at)
                    VALUES (1, ?, ?, CURRENT_TIMESTAMP)
                """, (datetime.now().isoformat(), track_count))
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Error updating discovery pool timestamp: {e}")
            return False

    def cleanup_old_discovery_tracks(self, days_threshold: int = 365) -> int:
        """Remove tracks from discovery pool older than X days. Returns count of deleted tracks."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # Delete tracks older than threshold
                cursor.execute("""
                    DELETE FROM discovery_pool
                    WHERE added_date < datetime('now', '-' || ? || ' days')
                """, (days_threshold,))

                deleted_count = cursor.rowcount
                conn.commit()

                if deleted_count > 0:
                    logger.info(f"Cleaned up {deleted_count} discovery tracks older than {days_threshold} days")

                return deleted_count

        except Exception as e:
            logger.error(f"Error cleaning up old discovery tracks: {e}")
            return 0

    def add_recent_release(self, watchlist_artist_id: int, album_data: Dict[str, Any]) -> bool:
        """Add a recent release to the recent_releases table"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                cursor.execute("""
                    INSERT OR IGNORE INTO recent_releases
                    (watchlist_artist_id, album_spotify_id, album_name, release_date, album_cover_url, track_count, added_date)
                    VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (
                    watchlist_artist_id,
                    album_data['album_spotify_id'],
                    album_data['album_name'],
                    album_data['release_date'],
                    album_data.get('album_cover_url'),
                    album_data.get('track_count', 0)
                ))

                conn.commit()
                return True

        except Exception as e:
            logger.error(f"Error adding recent release: {e}")
            return False

    def get_recent_releases(self, limit: int = 50) -> List[RecentRelease]:
        """Get recent releases from watchlist artists"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                cursor.execute("""
                    SELECT * FROM recent_releases
                    ORDER BY release_date DESC, added_date DESC
                    LIMIT ?
                """, (limit,))

                rows = cursor.fetchall()
                return [RecentRelease(
                    id=row['id'],
                    watchlist_artist_id=row['watchlist_artist_id'],
                    album_spotify_id=row['album_spotify_id'],
                    album_name=row['album_name'],
                    release_date=row['release_date'],
                    album_cover_url=row['album_cover_url'],
                    track_count=row['track_count'],
                    added_date=datetime.fromisoformat(row['added_date'])
                ) for row in rows]

        except Exception as e:
            logger.error(f"Error getting recent releases: {e}")
            return []

    def get_database_info(self) -> Dict[str, Any]:
        """Get comprehensive database information for all servers (legacy method)"""
        try:
            stats = self.get_statistics()
            
            # Get database file size
            db_size = self.database_path.stat().st_size if self.database_path.exists() else 0
            db_size_mb = db_size / (1024 * 1024)
            
            # Get last update time (most recent updated_at timestamp)
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT MAX(updated_at) as last_update 
                FROM (
                    SELECT updated_at FROM artists
                    UNION ALL
                    SELECT updated_at FROM albums
                    UNION ALL
                    SELECT updated_at FROM tracks
                )
            """)
            
            result = cursor.fetchone()
            last_update = result['last_update'] if result and result['last_update'] else None
            
            # Get last full refresh
            last_full_refresh = self.get_last_full_refresh()
            
            return {
                **stats,
                'database_size_mb': round(db_size_mb, 2),
                'database_path': str(self.database_path),
                'last_update': last_update,
                'last_full_refresh': last_full_refresh
            }
            
        except Exception as e:
            logger.error(f"Error getting database info: {e}")
            return {
                'artists': 0,
                'albums': 0,
                'tracks': 0,
                'database_size_mb': 0.0,
                'database_path': str(self.database_path),
                'last_update': None,
                'last_full_refresh': None
            }
    
    def get_database_info_for_server(self, server_source: str = None) -> Dict[str, Any]:
        """Get comprehensive database information filtered by server source"""
        try:
            # Import here to avoid circular imports
            from config.settings import config_manager
            
            # If no server specified, use active server
            if server_source is None:
                server_source = config_manager.get_active_media_server()
            
            stats = self.get_statistics_for_server(server_source)
            
            # Get database file size (always total, not server-specific)
            db_size = self.database_path.stat().st_size if self.database_path.exists() else 0
            db_size_mb = db_size / (1024 * 1024)
            
            # Get last update time for this server
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT MAX(updated_at) as last_update 
                FROM (
                    SELECT updated_at FROM artists WHERE server_source = ?
                    UNION ALL
                    SELECT updated_at FROM albums WHERE server_source = ?
                    UNION ALL
                    SELECT updated_at FROM tracks WHERE server_source = ?
                )
            """, (server_source, server_source, server_source))
            
            result = cursor.fetchone()
            last_update = result['last_update'] if result and result['last_update'] else None
            
            # Get last full refresh (global setting, not server-specific)
            last_full_refresh = self.get_last_full_refresh()
            
            return {
                **stats,
                'database_size_mb': round(db_size_mb, 2),
                'database_path': str(self.database_path),
                'last_update': last_update,
                'last_full_refresh': last_full_refresh,
                'server_source': server_source
            }
            
        except Exception as e:
            logger.error(f"Error getting database info for {server_source}: {e}")
            return {
                'artists': 0,
                'albums': 0,
                'tracks': 0,
                'database_size_mb': 0.0,
                'database_path': str(self.database_path),
                'last_update': None,
                'last_full_refresh': None,
                'server_source': server_source
            }

    def get_library_artists(self, search_query: str = "", letter: str = "", page: int = 1, limit: int = 50) -> Dict[str, Any]:
        """
        Get artists for the library page with search, filtering, and pagination

        Args:
            search_query: Search term to filter artists by name
            letter: Filter by first letter (a-z, #, or "" for all)
            page: Page number (1-based)
            limit: Number of results per page

        Returns:
            Dict containing artists list, pagination info, and total count
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # Build WHERE clause
                where_conditions = []
                params = []

                if search_query:
                    where_conditions.append("LOWER(name) LIKE LOWER(?)")
                    params.append(f"%{search_query}%")

                if letter and letter != "all":
                    if letter == "#":
                        # Numbers and special characters
                        where_conditions.append("SUBSTR(UPPER(name), 1, 1) NOT GLOB '[A-Z]'")
                    else:
                        # Specific letter
                        where_conditions.append("UPPER(SUBSTR(name, 1, 1)) = UPPER(?)")
                        params.append(letter)

                # Get active server for filtering
                from config.settings import config_manager
                active_server = config_manager.get_active_media_server()

                # Add active server filter to where conditions
                where_conditions.append("a.server_source = ?")
                params.append(active_server)

                where_clause = " AND ".join(where_conditions) if where_conditions else "1=1"

                # Get total count (matching dashboard method)
                count_query = f"""
                    SELECT COUNT(*) as total_count
                    FROM artists a
                    WHERE {where_clause}
                """
                cursor.execute(count_query, params)
                total_count = cursor.fetchone()['total_count']

                # Get artists with pagination
                offset = (page - 1) * limit

                artists_query = f"""
                    SELECT
                        a.id,
                        a.name,
                        a.thumb_url,
                        a.genres,
                        COUNT(DISTINCT al.id) as album_count,
                        COUNT(DISTINCT t.id) as track_count
                    FROM artists a
                    LEFT JOIN albums al ON a.id = al.artist_id
                    LEFT JOIN tracks t ON al.id = t.album_id
                    WHERE {where_clause}
                    GROUP BY a.id, a.name, a.thumb_url, a.genres
                    ORDER BY a.name COLLATE NOCASE
                    LIMIT ? OFFSET ?
                """
                # No need for complex query params now
                query_params = params + [limit, offset]

                cursor.execute(artists_query, query_params)
                rows = cursor.fetchall()

                # Convert to artist objects
                artists = []
                for row in rows:
                    # Parse genres from GROUP_CONCAT result
                    genres_str = row['genres'] or ''
                    genres = []
                    if genres_str:
                        # Split by comma and clean up duplicates
                        genre_set = set()
                        for genre in genres_str.split(','):
                            if genre and genre.strip():
                                genre_set.update(g.strip() for g in genre.split(',') if g.strip())
                        genres = list(genre_set)

                    artist = DatabaseArtist(
                        id=row['id'],
                        name=row['name'],
                        thumb_url=row['thumb_url'] if row['thumb_url'] else None,
                        genres=genres
                    )

                    # Add stats
                    artist_data = {
                        'id': artist.id,
                        'name': artist.name,
                        'image_url': artist.thumb_url,
                        'genres': artist.genres,
                        'album_count': row['album_count'] or 0,
                        'track_count': row['track_count'] or 0
                    }
                    artists.append(artist_data)

                # Calculate pagination info
                total_pages = (total_count + limit - 1) // limit
                has_prev = page > 1
                has_next = page < total_pages

                return {
                    'artists': artists,
                    'pagination': {
                        'page': page,
                        'limit': limit,
                        'total_count': total_count,
                        'total_pages': total_pages,
                        'has_prev': has_prev,
                        'has_next': has_next
                    }
                }

        except Exception as e:
            logger.error(f"Error getting library artists: {e}")
            return {
                'artists': [],
                'pagination': {
                    'page': 1,
                    'limit': limit,
                    'total_count': 0,
                    'total_pages': 0,
                    'has_prev': False,
                    'has_next': False
                }
            }

    def get_artist_discography(self, artist_id) -> Dict[str, Any]:
        """
        Get complete artist information and their releases from the database.
        This will be combined with Spotify data for the full discography view.

        Args:
            artist_id: The artist ID from the database (string or int)

        Returns:
            Dict containing artist info and their owned releases
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # Get artist information
                cursor.execute("""
                    SELECT
                        id, name, thumb_url, genres, server_source
                    FROM artists
                    WHERE id = ?
                """, (artist_id,))

                artist_row = cursor.fetchone()

                if not artist_row:
                    return {
                        'success': False,
                        'error': f'Artist with ID {artist_id} not found'
                    }

                # Parse genres
                genres_str = artist_row['genres'] or ''
                genres = []
                if genres_str:
                    # Try to parse as JSON first (new format)
                    try:
                        import json
                        parsed_genres = json.loads(genres_str)
                        if isinstance(parsed_genres, list):
                            genres = parsed_genres
                        else:
                            genres = [str(parsed_genres)]
                    except (json.JSONDecodeError, ValueError):
                        # Fall back to comma-separated format (old format)
                        genre_set = set()
                        for genre in genres_str.split(','):
                            if genre and genre.strip():
                                genre_set.add(genre.strip())
                        genres = list(genre_set)

                # Get artist's albums with track counts and completion
                # Include albums from ALL artists with the same name (fixes duplicate artist issue)
                cursor.execute("""
                    SELECT
                        a.id,
                        a.title,
                        a.year,
                        a.track_count,
                        a.thumb_url,
                        COUNT(t.id) as owned_tracks
                    FROM albums a
                    LEFT JOIN tracks t ON a.id = t.album_id
                    WHERE a.artist_id IN (
                        SELECT id FROM artists
                        WHERE name = (SELECT name FROM artists WHERE id = ?)
                        AND server_source = (SELECT server_source FROM artists WHERE id = ?)
                    )
                    GROUP BY a.id, a.title, a.year, a.track_count, a.thumb_url
                    ORDER BY a.year DESC, a.title
                """, (artist_id, artist_id))

                album_rows = cursor.fetchall()

                # Process albums and categorize by type
                albums = []
                eps = []
                singles = []

                # Get total stats for the artist (including all artists with same name)
                cursor.execute("""
                    SELECT
                        COUNT(DISTINCT a.id) as album_count,
                        COUNT(DISTINCT t.id) as track_count
                    FROM albums a
                    LEFT JOIN tracks t ON a.id = t.album_id
                    WHERE a.artist_id IN (
                        SELECT id FROM artists
                        WHERE name = (SELECT name FROM artists WHERE id = ?)
                        AND server_source = (SELECT server_source FROM artists WHERE id = ?)
                    )
                """, (artist_id, artist_id))

                stats_row = cursor.fetchone()
                album_count = stats_row['album_count'] if stats_row else 0
                track_count = stats_row['track_count'] if stats_row else 0

                for album_row in album_rows:
                    # Calculate completion percentage
                    expected_tracks = album_row['track_count'] or 1
                    owned_tracks = album_row['owned_tracks'] or 0
                    completion_percentage = min(100, round((owned_tracks / expected_tracks) * 100))

                    album_data = {
                        'id': album_row['id'],
                        'title': album_row['title'],
                        'year': album_row['year'],
                        'image_url': album_row['thumb_url'],
                        'owned': True,  # All albums in our DB are owned
                        'track_count': album_row['track_count'],
                        'owned_tracks': owned_tracks,
                        'track_completion': completion_percentage
                    }

                    # Categorize based on actual track count and title patterns
                    # Use actual owned tracks, fallback to expected track count, then to 0
                    actual_track_count = owned_tracks or album_row['track_count'] or 0
                    title_lower = album_row['title'].lower()

                    # Check for single indicators in title
                    single_indicators = ['single', ' - single', '(single)']
                    is_single_by_title = any(indicator in title_lower for indicator in single_indicators)

                    # Check for EP indicators in title
                    ep_indicators = ['ep', ' - ep', '(ep)', 'extended play']
                    is_ep_by_title = any(indicator in title_lower for indicator in ep_indicators)

                    # Categorization logic - be more conservative about singles
                    # Only treat as single if explicitly labeled as single AND has few tracks
                    if is_single_by_title and actual_track_count <= 3:
                        singles.append(album_data)
                    elif is_ep_by_title or (4 <= actual_track_count <= 7):
                        eps.append(album_data)
                    else:
                        # Default to album for most releases, especially if track count is unknown
                        albums.append(album_data)

                # Fix image URLs if needed
                artist_image_url = artist_row['thumb_url']
                if artist_image_url and artist_image_url.startswith('/library/'):
                    # This will be fixed in the API layer
                    pass

                return {
                    'success': True,
                    'artist': {
                        'id': artist_row['id'],
                        'name': artist_row['name'],
                        'image_url': artist_image_url,
                        'genres': genres,
                        'server_source': artist_row['server_source'],
                        'album_count': album_count,
                        'track_count': track_count
                    },
                    'owned_releases': {
                        'albums': albums,
                        'eps': eps,
                        'singles': singles
                    }
                }

        except Exception as e:
            logger.error(f"Error getting artist discography for ID {artist_id}: {e}")
            return {
                'success': False,
                'error': str(e)
            }

# Thread-safe singleton pattern for database access
_database_instances: Dict[int, MusicDatabase] = {}  # Thread ID -> Database instance
_database_lock = threading.Lock()

def get_database(database_path: str = None) -> MusicDatabase:
    """Get thread-local database instance

    Args:
        database_path: Path to database file. If None or default path, uses DATABASE_PATH env var
                      or defaults to "database/music_library.db". Custom paths are used as-is.
    """
    import os
    # Use env var if path is None OR if it's the default path
    # This ensures Docker containers use the correct mounted volume location
    if database_path is None or database_path == "database/music_library.db":
        database_path = os.environ.get('DATABASE_PATH', 'database/music_library.db')

    thread_id = threading.get_ident()

    with _database_lock:
        if thread_id not in _database_instances:
            _database_instances[thread_id] = MusicDatabase(database_path)
        return _database_instances[thread_id]

def close_database():
    """Close database instances (safe to call from any thread)"""
    global _database_instances
    
    with _database_lock:
        # Close all database instances
        for thread_id, db_instance in list(_database_instances.items()):
            try:
                db_instance.close()
            except Exception as e:
                # Ignore threading errors during shutdown
                pass
        _database_instances.clear()