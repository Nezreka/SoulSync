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
    spotify_artist_id: Optional[str]  # Can be None if added via iTunes
    artist_name: str
    date_added: datetime
    last_scan_timestamp: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    image_url: Optional[str] = None
    itunes_artist_id: Optional[str] = None  # Cross-provider support
    include_albums: bool = True
    include_eps: bool = True
    include_singles: bool = True
    include_live: bool = False
    include_remixes: bool = False
    include_acoustic: bool = False
    include_compilations: bool = False

@dataclass
class SimilarArtist:
    """Similar artist recommendation from Spotify/iTunes"""
    id: int
    source_artist_id: str  # Watchlist artist's database ID
    similar_artist_spotify_id: Optional[str]  # Spotify artist ID (may be None if iTunes-only)
    similar_artist_itunes_id: Optional[str]  # iTunes artist ID (may be None if Spotify-only)
    similar_artist_name: str
    similarity_rank: int  # 1-10, where 1 is most similar
    occurrence_count: int  # How many watchlist artists share this similar artist
    last_updated: datetime

@dataclass
class DiscoveryTrack:
    """Track in the discovery pool for recommendations"""
    id: int
    spotify_track_id: Optional[str]  # Spotify track ID (None if iTunes source)
    spotify_album_id: Optional[str]  # Spotify album ID (None if iTunes source)
    spotify_artist_id: Optional[str]  # Spotify artist ID (None if iTunes source)
    itunes_track_id: Optional[str]  # iTunes track ID (None if Spotify source)
    itunes_album_id: Optional[str]  # iTunes album ID (None if Spotify source)
    itunes_artist_id: Optional[str]  # iTunes artist ID (None if Spotify source)
    source: str  # 'spotify' or 'itunes'
    track_name: str
    artist_name: str
    album_name: str
    album_cover_url: Optional[str]
    duration_ms: int
    popularity: int
    release_date: str
    is_new_release: bool  # Released within last 30 days
    track_data_json: str  # Full track object for modal (Spotify or iTunes format)
    added_date: datetime

@dataclass
class RecentRelease:
    """Recent album release from watchlist artist"""
    id: int
    watchlist_artist_id: int
    album_spotify_id: Optional[str]  # Spotify album ID (None if iTunes source)
    album_itunes_id: Optional[str]  # iTunes album ID (None if Spotify source)
    source: str  # 'spotify' or 'itunes'
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
        # Register Unicode-normalizing function for diacritics-aware LIKE queries
        try:
            from unidecode import unidecode as _ud
            connection.create_function("unidecode_lower", 1, lambda x: _ud(x).lower() if x else "")
        except ImportError:
            connection.create_function("unidecode_lower", 1, lambda x: x.lower() if x else "")
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

            # Add content type filter columns to watchlist_artists (migration)
            self._add_watchlist_content_type_filters(cursor)

            # Add iTunes artist ID column to watchlist_artists (migration)
            self._add_watchlist_itunes_id_column(cursor)

            # Make spotify_artist_id nullable for iTunes-only artists (migration)
            self._fix_watchlist_spotify_id_nullable(cursor)

            # Add MusicBrainz columns to library tables (migration)
            self._add_musicbrainz_columns(cursor)

            # Add external ID columns (Spotify/iTunes) to library tables (migration)
            self._add_external_id_columns(cursor)

            # Add AudioDB columns to artists table (migration)
            self._add_audiodb_columns(cursor)

            # Add Deezer columns to library tables (migration)
            self._add_deezer_columns(cursor)

            # Add Spotify/iTunes enrichment tracking columns (migration)
            self._add_spotify_itunes_enrichment_columns(cursor)

            # Bubble snapshots table for persisting UI state across page refreshes
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS bubble_snapshots (
                    type TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    snapshot_id TEXT NOT NULL
                )
            """)

            # Retag tool tables for tracking processed downloads (migration)
            self._add_retag_tables(cursor)

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
            # Supports both Spotify and iTunes IDs for dual-source discovery
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS similar_artists (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_artist_id TEXT NOT NULL,
                    similar_artist_spotify_id TEXT,
                    similar_artist_itunes_id TEXT,
                    similar_artist_name TEXT NOT NULL,
                    similarity_rank INTEGER DEFAULT 1,
                    occurrence_count INTEGER DEFAULT 1,
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(source_artist_id, similar_artist_name)
                )
            """)

            # Discovery Pool table - rotating pool of 1000-2000 tracks for recommendations
            # Supports both Spotify and iTunes sources for dual-source discovery
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS discovery_pool (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    spotify_track_id TEXT,
                    spotify_album_id TEXT,
                    spotify_artist_id TEXT,
                    itunes_track_id TEXT,
                    itunes_album_id TEXT,
                    itunes_artist_id TEXT,
                    source TEXT NOT NULL DEFAULT 'spotify',
                    track_name TEXT NOT NULL,
                    artist_name TEXT NOT NULL,
                    album_name TEXT NOT NULL,
                    album_cover_url TEXT,
                    duration_ms INTEGER,
                    popularity INTEGER DEFAULT 0,
                    release_date TEXT,
                    is_new_release BOOLEAN DEFAULT 0,
                    track_data_json TEXT NOT NULL,
                    added_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(spotify_track_id, itunes_track_id, source)
                )
            """)

            # Recent Releases table - tracks new releases from watchlist artists
            # Supports both Spotify and iTunes sources for dual-source discovery
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS recent_releases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    watchlist_artist_id INTEGER NOT NULL,
                    album_spotify_id TEXT,
                    album_itunes_id TEXT,
                    source TEXT NOT NULL DEFAULT 'spotify',
                    album_name TEXT NOT NULL,
                    release_date TEXT NOT NULL,
                    album_cover_url TEXT,
                    track_count INTEGER DEFAULT 0,
                    added_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(watchlist_artist_id, album_spotify_id, album_itunes_id),
                    FOREIGN KEY (watchlist_artist_id) REFERENCES watchlist_artists (id) ON DELETE CASCADE
                )
            """)

            # Discovery Recent Albums cache - for discover page recent releases section
            # Supports both Spotify and iTunes sources for dual-source discovery
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS discovery_recent_albums (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    album_spotify_id TEXT,
                    album_itunes_id TEXT,
                    artist_spotify_id TEXT,
                    artist_itunes_id TEXT,
                    source TEXT NOT NULL DEFAULT 'spotify',
                    album_name TEXT NOT NULL,
                    artist_name TEXT NOT NULL,
                    album_cover_url TEXT,
                    release_date TEXT NOT NULL,
                    album_type TEXT DEFAULT 'album',
                    cached_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(album_spotify_id, album_itunes_id, source)
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

            # ============== MIGRATIONS (must run BEFORE index creation on new columns) ==============

            # Add genres column to discovery_pool if it doesn't exist (migration)
            cursor.execute("PRAGMA table_info(discovery_pool)")
            discovery_pool_columns = [column[1] for column in cursor.fetchall()]

            if 'artist_genres' not in discovery_pool_columns:
                cursor.execute("ALTER TABLE discovery_pool ADD COLUMN artist_genres TEXT")
                logger.info("Added artist_genres column to discovery_pool table")

            # Migration: Add iTunes columns to discovery_pool for dual-source discovery
            if 'itunes_track_id' not in discovery_pool_columns:
                cursor.execute("ALTER TABLE discovery_pool ADD COLUMN itunes_track_id TEXT")
                cursor.execute("ALTER TABLE discovery_pool ADD COLUMN itunes_album_id TEXT")
                cursor.execute("ALTER TABLE discovery_pool ADD COLUMN itunes_artist_id TEXT")
                cursor.execute("ALTER TABLE discovery_pool ADD COLUMN source TEXT DEFAULT 'spotify'")
                logger.info("Added iTunes columns to discovery_pool table for dual-source discovery")

            # Migration: Add iTunes ID to similar_artists for dual-source discovery
            cursor.execute("PRAGMA table_info(similar_artists)")
            similar_artists_columns = [column[1] for column in cursor.fetchall()]

            if 'similar_artist_itunes_id' not in similar_artists_columns:
                cursor.execute("ALTER TABLE similar_artists ADD COLUMN similar_artist_itunes_id TEXT")
                logger.info("Added similar_artist_itunes_id column to similar_artists table")

            # Migration: Add iTunes columns to recent_releases for dual-source discovery
            cursor.execute("PRAGMA table_info(recent_releases)")
            recent_releases_columns = [column[1] for column in cursor.fetchall()]

            if 'album_itunes_id' not in recent_releases_columns:
                cursor.execute("ALTER TABLE recent_releases ADD COLUMN album_itunes_id TEXT")
                cursor.execute("ALTER TABLE recent_releases ADD COLUMN source TEXT DEFAULT 'spotify'")
                logger.info("Added iTunes columns to recent_releases table for dual-source discovery")

            # Migration: Add iTunes columns to discovery_recent_albums for dual-source discovery
            cursor.execute("PRAGMA table_info(discovery_recent_albums)")
            discovery_recent_albums_columns = [column[1] for column in cursor.fetchall()]

            if 'album_itunes_id' not in discovery_recent_albums_columns:
                cursor.execute("ALTER TABLE discovery_recent_albums ADD COLUMN album_itunes_id TEXT")
                cursor.execute("ALTER TABLE discovery_recent_albums ADD COLUMN artist_itunes_id TEXT")
                cursor.execute("ALTER TABLE discovery_recent_albums ADD COLUMN source TEXT DEFAULT 'spotify'")
                logger.info("Added iTunes columns to discovery_recent_albums table for dual-source discovery")

            # Migration: Fix NOT NULL constraint on album_spotify_id (required for iTunes-only albums)
            # Check if album_spotify_id has NOT NULL constraint by checking table schema
            cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='discovery_recent_albums'")
            table_schema = cursor.fetchone()
            if table_schema and 'album_spotify_id TEXT NOT NULL' in (table_schema[0] or ''):
                logger.info("Migrating discovery_recent_albums to allow NULL album_spotify_id for iTunes support...")
                # SQLite doesn't support ALTER COLUMN, so recreate table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS discovery_recent_albums_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        album_spotify_id TEXT,
                        album_itunes_id TEXT,
                        artist_spotify_id TEXT,
                        artist_itunes_id TEXT,
                        source TEXT NOT NULL DEFAULT 'spotify',
                        album_name TEXT NOT NULL,
                        artist_name TEXT NOT NULL,
                        album_cover_url TEXT,
                        release_date TEXT NOT NULL,
                        album_type TEXT DEFAULT 'album',
                        cached_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(album_spotify_id, album_itunes_id, source)
                    )
                """)
                cursor.execute("""
                    INSERT OR IGNORE INTO discovery_recent_albums_new
                    SELECT * FROM discovery_recent_albums
                """)
                cursor.execute("DROP TABLE discovery_recent_albums")
                cursor.execute("ALTER TABLE discovery_recent_albums_new RENAME TO discovery_recent_albums")
                conn.commit()
                logger.info("Successfully migrated discovery_recent_albums table for iTunes support")

            # Migration: Add UNIQUE constraint to similar_artists table
            # Test if ON CONFLICT works by trying a dummy operation
            needs_similar_migration = False
            try:
                cursor.execute("""
                    INSERT INTO similar_artists
                    (source_artist_id, similar_artist_name, similarity_rank, occurrence_count, last_updated)
                    VALUES ('__migration_test__', '__migration_test__', 1, 1, CURRENT_TIMESTAMP)
                    ON CONFLICT(source_artist_id, similar_artist_name)
                    DO UPDATE SET occurrence_count = occurrence_count
                """)
                # Clean up test row
                cursor.execute("DELETE FROM similar_artists WHERE source_artist_id = '__migration_test__'")
                logger.info("similar_artists table has correct UNIQUE constraint")
            except Exception as constraint_error:
                logger.info(f"similar_artists needs migration (constraint test failed: {constraint_error})")
                needs_similar_migration = True

            if needs_similar_migration:
                logger.info("Migrating similar_artists to add UNIQUE constraint...")
                # Get a fresh connection for the migration
                with self._get_connection() as migration_conn:
                    migration_cursor = migration_conn.cursor()
                    # SQLite doesn't support adding constraints, so recreate table
                    migration_cursor.execute("DROP TABLE IF EXISTS similar_artists_new")
                    migration_cursor.execute("""
                        CREATE TABLE similar_artists_new (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            source_artist_id TEXT NOT NULL,
                            similar_artist_spotify_id TEXT,
                            similar_artist_itunes_id TEXT,
                            similar_artist_name TEXT NOT NULL,
                            similarity_rank INTEGER DEFAULT 1,
                            occurrence_count INTEGER DEFAULT 1,
                            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            UNIQUE(source_artist_id, similar_artist_name)
                        )
                    """)
                    migration_cursor.execute("""
                        INSERT OR IGNORE INTO similar_artists_new
                        (source_artist_id, similar_artist_spotify_id, similar_artist_itunes_id,
                         similar_artist_name, similarity_rank, occurrence_count, last_updated)
                        SELECT source_artist_id, similar_artist_spotify_id, similar_artist_itunes_id,
                               similar_artist_name, similarity_rank, occurrence_count, last_updated
                        FROM similar_artists
                    """)
                    migration_cursor.execute("DROP TABLE similar_artists")
                    migration_cursor.execute("ALTER TABLE similar_artists_new RENAME TO similar_artists")
                    migration_conn.commit()
                    logger.info("Successfully migrated similar_artists table with UNIQUE constraint")

            # ============== INDEXES (after migrations to ensure columns exist) ==============
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_similar_artists_source ON similar_artists (source_artist_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_similar_artists_spotify ON similar_artists (similar_artist_spotify_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_similar_artists_itunes ON similar_artists (similar_artist_itunes_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_similar_artists_occurrence ON similar_artists (occurrence_count)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_similar_artists_name ON similar_artists (similar_artist_name)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_discovery_pool_spotify_track ON discovery_pool (spotify_track_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_discovery_pool_itunes_track ON discovery_pool (itunes_track_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_discovery_pool_artist ON discovery_pool (spotify_artist_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_discovery_pool_itunes_artist ON discovery_pool (itunes_artist_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_discovery_pool_source ON discovery_pool (source)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_discovery_pool_added_date ON discovery_pool (added_date)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_discovery_pool_is_new ON discovery_pool (is_new_release)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_recent_releases_watchlist ON recent_releases (watchlist_artist_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_recent_releases_date ON recent_releases (release_date)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_recent_releases_source ON recent_releases (source)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_discovery_recent_albums_source ON discovery_recent_albums (source)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_discovery_recent_albums_date ON discovery_recent_albums (release_date)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_listenbrainz_playlists_type ON listenbrainz_playlists (playlist_type)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_listenbrainz_playlists_mbid ON listenbrainz_playlists (playlist_mbid)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_listenbrainz_tracks_playlist ON listenbrainz_tracks (playlist_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_listenbrainz_tracks_position ON listenbrainz_tracks (playlist_id, position)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_discovery_recent_albums_artist ON discovery_recent_albums (artist_spotify_id)")

            # Discovery Match Cache - caches successful discovery matches across all sources
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS discovery_match_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    normalized_title TEXT NOT NULL,
                    normalized_artist TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    match_confidence REAL NOT NULL,
                    matched_data_json TEXT NOT NULL,
                    original_title TEXT,
                    original_artist TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    use_count INTEGER DEFAULT 1,
                    UNIQUE(normalized_title, normalized_artist, provider)
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_discovery_cache_lookup ON discovery_match_cache (normalized_title, normalized_artist, provider)")

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

    def _add_watchlist_content_type_filters(self, cursor):
        """Add content type filter columns to watchlist_artists table"""
        try:
            cursor.execute("PRAGMA table_info(watchlist_artists)")
            columns = [column[1] for column in cursor.fetchall()]

            columns_to_add = {
                'include_live': ('INTEGER', '0'),          # 0 = False (exclude live versions by default)
                'include_remixes': ('INTEGER', '0'),        # 0 = False (exclude remixes by default)
                'include_acoustic': ('INTEGER', '0'),       # 0 = False (exclude acoustic by default)
                'include_compilations': ('INTEGER', '0')    # 0 = False (exclude compilations by default)
            }

            for column_name, (column_type, default_value) in columns_to_add.items():
                if column_name not in columns:
                    cursor.execute(f"ALTER TABLE watchlist_artists ADD COLUMN {column_name} {column_type} DEFAULT {default_value}")
                    logger.info(f"Added {column_name} column to watchlist_artists table")

        except Exception as e:
            logger.error(f"Error adding content type filter columns to watchlist_artists: {e}")
            # Don't raise - this is a migration, database can still function

    def _add_watchlist_itunes_id_column(self, cursor):
        """Add iTunes artist ID column to watchlist_artists table for cross-provider support"""
        try:
            cursor.execute("PRAGMA table_info(watchlist_artists)")
            columns = [column[1] for column in cursor.fetchall()]

            if 'itunes_artist_id' not in columns:
                cursor.execute("ALTER TABLE watchlist_artists ADD COLUMN itunes_artist_id TEXT")
                logger.info("Added itunes_artist_id column to watchlist_artists table for cross-provider support")

        except Exception as e:
            logger.error(f"Error adding itunes_artist_id column to watchlist_artists: {e}")
            # Don't raise - this is a migration, database can still function

    def _fix_watchlist_spotify_id_nullable(self, cursor):
        """
        Make spotify_artist_id nullable in watchlist_artists table.
        This allows adding iTunes-only artists without Spotify IDs.
        
        Since SQLite doesn't support modifying column constraints directly,
        we need to recreate the table if the constraint needs to be changed.
        """
        try:
            # Check if spotify_artist_id is currently NOT NULL
            cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='watchlist_artists'")
            result = cursor.fetchone()
            
            if result and 'spotify_artist_id TEXT UNIQUE NOT NULL' in result[0]:
                logger.info("Migrating watchlist_artists table to make spotify_artist_id nullable...")
                
                # Create new table with nullable spotify_artist_id
                cursor.execute("""
                    CREATE TABLE watchlist_artists_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        spotify_artist_id TEXT UNIQUE,
                        artist_name TEXT NOT NULL,
                        date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        last_scan_timestamp TIMESTAMP,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        image_url TEXT,
                        include_albums INTEGER DEFAULT 1,
                        include_eps INTEGER DEFAULT 1,
                        include_singles INTEGER DEFAULT 1,
                        include_live INTEGER DEFAULT 0,
                        include_remixes INTEGER DEFAULT 0,
                        include_acoustic INTEGER DEFAULT 0,
                        include_compilations INTEGER DEFAULT 0,
                        itunes_artist_id TEXT
                    )
                """)
                
                # Copy data from old table
                cursor.execute("""
                    INSERT INTO watchlist_artists_new 
                    SELECT * FROM watchlist_artists
                """)
                
                # Drop old table
                cursor.execute("DROP TABLE watchlist_artists")
                
                # Rename new table
                cursor.execute("ALTER TABLE watchlist_artists_new RENAME TO watchlist_artists")
                
                # Recreate indexes
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_watchlist_spotify_id ON watchlist_artists (spotify_artist_id)")
                
                logger.info("Successfully migrated watchlist_artists table - spotify_artist_id is now nullable")
            else:
                logger.debug("watchlist_artists table already has nullable spotify_artist_id or custom schema")
                
        except Exception as e:
            logger.error(f"Error making spotify_artist_id nullable in watchlist_artists: {e}")
            # Don't raise - this is a migration, database can still function

    def _add_musicbrainz_columns(self, cursor):
        """Add MusicBrainz tracking columns to library tables for metadata enrichment"""
        columns_added = False
        try:
            # Check if musicbrainz_id column exists in artists table
            cursor.execute("PRAGMA table_info(artists)")
            artists_columns = [column[1] for column in cursor.fetchall()]
            
            if 'musicbrainz_id' not in artists_columns:
                logger.info("Adding MusicBrainz columns to artists table...")
                cursor.execute("ALTER TABLE artists ADD COLUMN musicbrainz_id TEXT")
                cursor.execute("ALTER TABLE artists ADD COLUMN musicbrainz_last_attempted TIMESTAMP")
                cursor.execute("ALTER TABLE artists ADD COLUMN musicbrainz_match_status TEXT")
                columns_added = True
                logger.info("✅ Added MusicBrainz columns to artists table")
            
            # Check if musicbrainz_release_id column exists in albums table
            cursor.execute("PRAGMA table_info(albums)")
            albums_columns = [column[1] for column in cursor.fetchall()]
            
            if 'musicbrainz_release_id' not in albums_columns:
                logger.info("Adding MusicBrainz columns to albums table...")
                cursor.execute("ALTER TABLE albums ADD COLUMN musicbrainz_release_id TEXT")
                cursor.execute("ALTER TABLE albums ADD COLUMN musicbrainz_last_attempted TIMESTAMP")
                cursor.execute("ALTER TABLE albums ADD COLUMN musicbrainz_match_status TEXT")
                columns_added = True
                logger.info("✅ Added MusicBrainz columns to albums table")
            
            # Check if musicbrainz_recording_id column exists in tracks table
            cursor.execute("PRAGMA table_info(tracks)")
            tracks_columns = [column[1] for column in cursor.fetchall()]
            
            if 'musicbrainz_recording_id' not in tracks_columns:
                logger.info("Adding MusicBrainz columns to tracks table...")
                cursor.execute("ALTER TABLE tracks ADD COLUMN musicbrainz_recording_id TEXT")
                cursor.execute("ALTER TABLE tracks ADD COLUMN musicbrainz_last_attempted TIMESTAMP")
                cursor.execute("ALTER TABLE tracks ADD COLUMN musicbrainz_match_status TEXT")
                columns_added = True
                logger.info("✅ Added MusicBrainz columns to tracks table")
            
            # Create MusicBrainz cache table for storing API results
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS musicbrainz_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entity_type TEXT NOT NULL,
                    entity_name TEXT NOT NULL,
                    artist_name TEXT,
                    musicbrainz_id TEXT,
                    spotify_id TEXT,
                    itunes_id TEXT,
                    metadata_json TEXT,
                    match_confidence INTEGER,
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(entity_type, entity_name, artist_name)
                )
            """)
            
            # Only proceed with index creation if we successfully checked for columns
            # Re-verify columns exist before creating indexes to prevent errors
            cursor.execute("PRAGMA table_info(artists)")
            artists_columns = [column[1] for column in cursor.fetchall()]
            cursor.execute("PRAGMA table_info(albums)")
            albums_columns = [column[1] for column in cursor.fetchall()]
            cursor.execute("PRAGMA table_info(tracks)")
            tracks_columns = [column[1] for column in cursor.fetchall()]
            
            # Verify all required columns exist before creating indexes
            if 'musicbrainz_match_status' in artists_columns:
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_artists_mbid ON artists (musicbrainz_id)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_artists_mb_status ON artists (musicbrainz_match_status)")
            else:
                logger.error("❌ CRITICAL: musicbrainz_match_status column missing from artists table after migration!")
                raise Exception("MusicBrainz artists column migration failed - column not found after ALTER TABLE")
            
            if 'musicbrainz_match_status' in albums_columns:
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_albums_mbid ON albums (musicbrainz_release_id)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_albums_mb_status ON albums (musicbrainz_match_status)")
            else:
                logger.error("❌ CRITICAL: musicbrainz_match_status column missing from albums table after migration!")
                raise Exception("MusicBrainz albums column migration failed - column not found after ALTER TABLE")
            
            if 'musicbrainz_match_status' in tracks_columns:
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_tracks_mbid ON tracks (musicbrainz_recording_id)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_tracks_mb_status ON tracks (musicbrainz_match_status)")
            else:
                logger.error("❌ CRITICAL: musicbrainz_match_status column missing from tracks table after migration!")
                raise Exception("MusicBrainz tracks column migration failed - column not found after ALTER TABLE")
            
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_mb_cache_entity ON musicbrainz_cache (entity_type, entity_name)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_mb_cache_mbid ON musicbrainz_cache (musicbrainz_id)")
            
            if columns_added:
                logger.info("🎉 MusicBrainz migration completed successfully")
            
        except Exception as e:
            logger.error(f"❌ CRITICAL ERROR in MusicBrainz migration: {e}")
            logger.error("⚠️  Database may be in an inconsistent state - MusicBrainz features will not work")
            logger.error("⚠️  You may need to manually add the missing columns or reset your database")
            # Re-raise the exception to prevent the application from starting with a broken database
            raise

    def _add_external_id_columns(self, cursor):
        """Add Spotify/iTunes external ID columns to library tables for enrichment"""
        try:
            # Artists table
            cursor.execute("PRAGMA table_info(artists)")
            artists_columns = [column[1] for column in cursor.fetchall()]

            if 'spotify_artist_id' not in artists_columns:
                cursor.execute("ALTER TABLE artists ADD COLUMN spotify_artist_id TEXT")
                cursor.execute("ALTER TABLE artists ADD COLUMN itunes_artist_id TEXT")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_artists_spotify_id ON artists (spotify_artist_id)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_artists_itunes_id ON artists (itunes_artist_id)")
                logger.info("Added external ID columns to artists table")

            # Albums table
            cursor.execute("PRAGMA table_info(albums)")
            albums_columns = [column[1] for column in cursor.fetchall()]

            if 'spotify_album_id' not in albums_columns:
                cursor.execute("ALTER TABLE albums ADD COLUMN spotify_album_id TEXT")
                cursor.execute("ALTER TABLE albums ADD COLUMN itunes_album_id TEXT")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_albums_spotify_id ON albums (spotify_album_id)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_albums_itunes_id ON albums (itunes_album_id)")
                logger.info("Added external ID columns to albums table")

            # Tracks table
            cursor.execute("PRAGMA table_info(tracks)")
            tracks_columns = [column[1] for column in cursor.fetchall()]

            if 'spotify_track_id' not in tracks_columns:
                cursor.execute("ALTER TABLE tracks ADD COLUMN spotify_track_id TEXT")
                cursor.execute("ALTER TABLE tracks ADD COLUMN itunes_track_id TEXT")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_tracks_spotify_id ON tracks (spotify_track_id)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_tracks_itunes_id ON tracks (itunes_track_id)")
                logger.info("Added external ID columns to tracks table")

        except Exception as e:
            logger.error(f"Error adding external ID columns: {e}")
            # Don't raise - this is a migration, database can still function

    def _add_audiodb_columns(self, cursor):
        """Add AudioDB tracking + generic metadata columns for enrichment (artists, albums, tracks)"""
        try:
            # --- Artists ---
            cursor.execute("PRAGMA table_info(artists)")
            artists_columns = [column[1] for column in cursor.fetchall()]

            if 'audiodb_id' not in artists_columns:
                cursor.execute("ALTER TABLE artists ADD COLUMN audiodb_id TEXT")
            if 'audiodb_match_status' not in artists_columns:
                cursor.execute("ALTER TABLE artists ADD COLUMN audiodb_match_status TEXT")
            if 'audiodb_last_attempted' not in artists_columns:
                cursor.execute("ALTER TABLE artists ADD COLUMN audiodb_last_attempted TIMESTAMP")

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_artists_audiodb_id ON artists (audiodb_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_artists_audiodb_status ON artists (audiodb_match_status)")

            if 'style' not in artists_columns:
                cursor.execute("ALTER TABLE artists ADD COLUMN style TEXT")
            if 'mood' not in artists_columns:
                cursor.execute("ALTER TABLE artists ADD COLUMN mood TEXT")
            if 'label' not in artists_columns:
                cursor.execute("ALTER TABLE artists ADD COLUMN label TEXT")
            if 'banner_url' not in artists_columns:
                cursor.execute("ALTER TABLE artists ADD COLUMN banner_url TEXT")

            # --- Albums ---
            cursor.execute("PRAGMA table_info(albums)")
            albums_columns = [column[1] for column in cursor.fetchall()]

            if 'audiodb_id' not in albums_columns:
                cursor.execute("ALTER TABLE albums ADD COLUMN audiodb_id TEXT")
            if 'audiodb_match_status' not in albums_columns:
                cursor.execute("ALTER TABLE albums ADD COLUMN audiodb_match_status TEXT")
            if 'audiodb_last_attempted' not in albums_columns:
                cursor.execute("ALTER TABLE albums ADD COLUMN audiodb_last_attempted TIMESTAMP")

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_albums_audiodb_id ON albums (audiodb_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_albums_audiodb_status ON albums (audiodb_match_status)")

            if 'style' not in albums_columns:
                cursor.execute("ALTER TABLE albums ADD COLUMN style TEXT")
            if 'mood' not in albums_columns:
                cursor.execute("ALTER TABLE albums ADD COLUMN mood TEXT")

            # --- Tracks ---
            cursor.execute("PRAGMA table_info(tracks)")
            tracks_columns = [column[1] for column in cursor.fetchall()]

            if 'audiodb_id' not in tracks_columns:
                cursor.execute("ALTER TABLE tracks ADD COLUMN audiodb_id TEXT")
            if 'audiodb_match_status' not in tracks_columns:
                cursor.execute("ALTER TABLE tracks ADD COLUMN audiodb_match_status TEXT")
            if 'audiodb_last_attempted' not in tracks_columns:
                cursor.execute("ALTER TABLE tracks ADD COLUMN audiodb_last_attempted TIMESTAMP")

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tracks_audiodb_id ON tracks (audiodb_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tracks_audiodb_status ON tracks (audiodb_match_status)")

            if 'style' not in tracks_columns:
                cursor.execute("ALTER TABLE tracks ADD COLUMN style TEXT")
            if 'mood' not in tracks_columns:
                cursor.execute("ALTER TABLE tracks ADD COLUMN mood TEXT")

        except Exception as e:
            logger.error(f"Error adding AudioDB columns: {e}")
            # Don't raise - this is a migration, database can still function

    def _add_deezer_columns(self, cursor):
        """Add Deezer tracking + generic metadata columns for enrichment (artists, albums, tracks)"""
        try:
            # --- Artists ---
            cursor.execute("PRAGMA table_info(artists)")
            artists_columns = [column[1] for column in cursor.fetchall()]

            if 'deezer_id' not in artists_columns:
                cursor.execute("ALTER TABLE artists ADD COLUMN deezer_id TEXT")
            if 'deezer_match_status' not in artists_columns:
                cursor.execute("ALTER TABLE artists ADD COLUMN deezer_match_status TEXT")
            if 'deezer_last_attempted' not in artists_columns:
                cursor.execute("ALTER TABLE artists ADD COLUMN deezer_last_attempted TIMESTAMP")

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_artists_deezer_id ON artists (deezer_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_artists_deezer_status ON artists (deezer_match_status)")

            # --- Albums ---
            cursor.execute("PRAGMA table_info(albums)")
            albums_columns = [column[1] for column in cursor.fetchall()]

            if 'deezer_id' not in albums_columns:
                cursor.execute("ALTER TABLE albums ADD COLUMN deezer_id TEXT")
            if 'deezer_match_status' not in albums_columns:
                cursor.execute("ALTER TABLE albums ADD COLUMN deezer_match_status TEXT")
            if 'deezer_last_attempted' not in albums_columns:
                cursor.execute("ALTER TABLE albums ADD COLUMN deezer_last_attempted TIMESTAMP")

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_albums_deezer_id ON albums (deezer_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_albums_deezer_status ON albums (deezer_match_status)")

            if 'label' not in albums_columns:
                cursor.execute("ALTER TABLE albums ADD COLUMN label TEXT")
            if 'explicit' not in albums_columns:
                cursor.execute("ALTER TABLE albums ADD COLUMN explicit INTEGER")
            if 'record_type' not in albums_columns:
                cursor.execute("ALTER TABLE albums ADD COLUMN record_type TEXT")

            # --- Tracks ---
            cursor.execute("PRAGMA table_info(tracks)")
            tracks_columns = [column[1] for column in cursor.fetchall()]

            if 'deezer_id' not in tracks_columns:
                cursor.execute("ALTER TABLE tracks ADD COLUMN deezer_id TEXT")
            if 'deezer_match_status' not in tracks_columns:
                cursor.execute("ALTER TABLE tracks ADD COLUMN deezer_match_status TEXT")
            if 'deezer_last_attempted' not in tracks_columns:
                cursor.execute("ALTER TABLE tracks ADD COLUMN deezer_last_attempted TIMESTAMP")

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tracks_deezer_id ON tracks (deezer_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tracks_deezer_status ON tracks (deezer_match_status)")

            if 'bpm' not in tracks_columns:
                cursor.execute("ALTER TABLE tracks ADD COLUMN bpm REAL")
            if 'explicit' not in tracks_columns:
                cursor.execute("ALTER TABLE tracks ADD COLUMN explicit INTEGER")

        except Exception as e:
            logger.error(f"Error adding Deezer columns: {e}")
            # Don't raise - this is a migration, database can still function

        # --- Repair worker columns ---
        try:
            if 'repair_status' not in tracks_columns:
                cursor.execute("ALTER TABLE tracks ADD COLUMN repair_status TEXT")
            if 'repair_last_checked' not in tracks_columns:
                cursor.execute("ALTER TABLE tracks ADD COLUMN repair_last_checked TIMESTAMP")

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tracks_repair_status ON tracks (repair_status)")

        except Exception as e:
            logger.error(f"Error adding repair columns: {e}")

    def _add_spotify_itunes_enrichment_columns(self, cursor):
        """Add Spotify/iTunes enrichment tracking columns (match_status + last_attempted) to artists, albums, tracks"""
        try:
            # --- Artists ---
            cursor.execute("PRAGMA table_info(artists)")
            artists_columns = [column[1] for column in cursor.fetchall()]

            if 'spotify_match_status' not in artists_columns:
                cursor.execute("ALTER TABLE artists ADD COLUMN spotify_match_status TEXT")
            if 'spotify_last_attempted' not in artists_columns:
                cursor.execute("ALTER TABLE artists ADD COLUMN spotify_last_attempted TIMESTAMP")
            if 'itunes_match_status' not in artists_columns:
                cursor.execute("ALTER TABLE artists ADD COLUMN itunes_match_status TEXT")
            if 'itunes_last_attempted' not in artists_columns:
                cursor.execute("ALTER TABLE artists ADD COLUMN itunes_last_attempted TIMESTAMP")

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_artists_spotify_match_status ON artists (spotify_match_status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_artists_itunes_match_status ON artists (itunes_match_status)")

            # --- Albums ---
            cursor.execute("PRAGMA table_info(albums)")
            albums_columns = [column[1] for column in cursor.fetchall()]

            if 'spotify_match_status' not in albums_columns:
                cursor.execute("ALTER TABLE albums ADD COLUMN spotify_match_status TEXT")
            if 'spotify_last_attempted' not in albums_columns:
                cursor.execute("ALTER TABLE albums ADD COLUMN spotify_last_attempted TIMESTAMP")
            if 'itunes_match_status' not in albums_columns:
                cursor.execute("ALTER TABLE albums ADD COLUMN itunes_match_status TEXT")
            if 'itunes_last_attempted' not in albums_columns:
                cursor.execute("ALTER TABLE albums ADD COLUMN itunes_last_attempted TIMESTAMP")

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_albums_spotify_match_status ON albums (spotify_match_status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_albums_itunes_match_status ON albums (itunes_match_status)")

            # --- Tracks ---
            cursor.execute("PRAGMA table_info(tracks)")
            tracks_columns = [column[1] for column in cursor.fetchall()]

            if 'spotify_match_status' not in tracks_columns:
                cursor.execute("ALTER TABLE tracks ADD COLUMN spotify_match_status TEXT")
            if 'spotify_last_attempted' not in tracks_columns:
                cursor.execute("ALTER TABLE tracks ADD COLUMN spotify_last_attempted TIMESTAMP")
            if 'itunes_match_status' not in tracks_columns:
                cursor.execute("ALTER TABLE tracks ADD COLUMN itunes_match_status TEXT")
            if 'itunes_last_attempted' not in tracks_columns:
                cursor.execute("ALTER TABLE tracks ADD COLUMN itunes_last_attempted TIMESTAMP")

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tracks_spotify_match_status ON tracks (spotify_match_status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tracks_itunes_match_status ON tracks (itunes_match_status)")

        except Exception as e:
            logger.error(f"Error adding Spotify/iTunes enrichment columns: {e}")
            # Don't raise - this is a migration, database can still function

    def _add_retag_tables(self, cursor):
        """Add retag tool tables for tracking processed downloads"""
        try:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS retag_groups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_type TEXT NOT NULL DEFAULT 'album',
                    artist_name TEXT NOT NULL,
                    album_name TEXT NOT NULL,
                    image_url TEXT,
                    spotify_album_id TEXT,
                    itunes_album_id TEXT,
                    total_tracks INTEGER DEFAULT 1,
                    release_date TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS retag_tracks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id INTEGER NOT NULL,
                    track_number INTEGER,
                    disc_number INTEGER DEFAULT 1,
                    title TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    file_format TEXT,
                    spotify_track_id TEXT,
                    itunes_track_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (group_id) REFERENCES retag_groups (id) ON DELETE CASCADE
                )
            """)

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_retag_groups_artist ON retag_groups (artist_name)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_retag_tracks_group ON retag_tracks (group_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_retag_tracks_path ON retag_tracks (file_path)")

        except Exception as e:
            logger.error(f"Error adding retag tables: {e}")

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
    
    def merge_duplicate_artists(self) -> Dict[str, int]:
        """
        Find and merge duplicate artists that share the same name + server_source.
        Keeps the artist with the most enrichment data, migrates albums/tracks,
        and merges enrichment columns.
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # Find duplicate artist groups (same name + server_source, different IDs)
                cursor.execute("""
                    SELECT name, server_source, GROUP_CONCAT(id) as ids, COUNT(*) as cnt
                    FROM artists
                    GROUP BY name, server_source
                    HAVING cnt > 1
                """)
                duplicate_groups = cursor.fetchall()

                if not duplicate_groups:
                    logger.debug("🧹 No duplicate artists found")
                    return {'artists_merged': 0, 'albums_migrated': 0}

                total_merged = 0
                total_albums_migrated = 0

                enrichment_cols = [
                    'musicbrainz_id', 'musicbrainz_last_attempted', 'musicbrainz_match_status',
                    'spotify_artist_id', 'spotify_match_status', 'spotify_last_attempted',
                    'itunes_artist_id', 'itunes_match_status', 'itunes_last_attempted',
                    'audiodb_id', 'audiodb_match_status', 'audiodb_last_attempted',
                    'style', 'mood', 'label', 'banner_url',
                    'deezer_id', 'deezer_match_status', 'deezer_last_attempted',
                ]

                for group in duplicate_groups:
                    artist_name = group['name']
                    server_source = group['server_source']
                    ids = group['ids'].split(',')

                    logger.info(f"🔄 Merging duplicate artist '{artist_name}' ({server_source}): IDs {ids}")

                    # Pick the keeper: the one with the most enrichment data
                    best_id = ids[0]
                    best_score = 0
                    for aid in ids:
                        cursor.execute("SELECT * FROM artists WHERE id = ?", (aid,))
                        row = cursor.fetchone()
                        if row:
                            score = 0
                            for col in enrichment_cols:
                                try:
                                    if row[col] is not None:
                                        score += 1
                                except (IndexError, KeyError):
                                    continue
                            if score > best_score:
                                best_score = score
                                best_id = aid

                    # Merge enrichment data from all duplicates into the keeper
                    for aid in ids:
                        if aid == best_id:
                            continue
                        cursor.execute("SELECT * FROM artists WHERE id = ?", (aid,))
                        donor = cursor.fetchone()
                        if not donor:
                            continue

                        # Fill NULL enrichment columns on keeper from this duplicate
                        set_parts = []
                        values = []
                        for col in enrichment_cols:
                            try:
                                donor_val = donor[col]
                                if donor_val is not None:
                                    # Only fill if keeper's value is NULL
                                    set_parts.append(f"{col} = COALESCE({col}, ?)")
                                    values.append(donor_val)
                            except (IndexError, KeyError):
                                continue

                        if set_parts:
                            values.append(best_id)
                            cursor.execute(f"""
                                UPDATE artists SET {', '.join(set_parts)}
                                WHERE id = ?
                            """, values)

                        # Migrate albums and tracks from duplicate to keeper
                        cursor.execute("UPDATE albums SET artist_id = ? WHERE artist_id = ?", (best_id, aid))
                        migrated = cursor.rowcount
                        total_albums_migrated += migrated
                        cursor.execute("UPDATE tracks SET artist_id = ? WHERE artist_id = ?", (best_id, aid))

                        # Delete the duplicate artist
                        cursor.execute("SELECT COUNT(*) FROM albums WHERE artist_id = ?", (aid,))
                        remaining = cursor.fetchone()[0]
                        if remaining == 0:
                            cursor.execute("DELETE FROM artists WHERE id = ?", (aid,))
                            total_merged += 1
                            logger.info(f"   Merged '{artist_name}' ID {aid} → {best_id} ({migrated} albums migrated)")
                        else:
                            logger.warning(f"   Could not delete duplicate {aid}: {remaining} albums still reference it")

                conn.commit()

                if total_merged > 0:
                    logger.info(f"🧹 Duplicate merge complete: {total_merged} duplicates merged, {total_albums_migrated} albums migrated")

                return {'artists_merged': total_merged, 'albums_migrated': total_albums_migrated}

        except Exception as e:
            logger.error(f"Error merging duplicate artists: {e}")
            return {'artists_merged': 0, 'albums_migrated': 0}

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
                    # Before inserting, check if an artist with the same name already exists
                    # for this server source (ratingKey may have changed after a library rescan)
                    cursor.execute("SELECT id FROM artists WHERE name = ? AND server_source = ?", (name, server_source))
                    existing_by_name = cursor.fetchone()

                    if existing_by_name:
                        old_id = existing_by_name['id']
                        # ratingKey changed — migrate old artist to new ID, preserving enrichment data
                        logger.info(f"🔄 Artist ratingKey migrated: '{name}' ({old_id} → {artist_id})")

                        # Step 1: Insert new artist record, copying enrichment data from old
                        enrichment_cols = [
                            'musicbrainz_id', 'musicbrainz_last_attempted', 'musicbrainz_match_status',
                            'spotify_artist_id', 'spotify_match_status', 'spotify_last_attempted',
                            'itunes_artist_id', 'itunes_match_status', 'itunes_last_attempted',
                            'audiodb_id', 'audiodb_match_status', 'audiodb_last_attempted',
                            'style', 'mood', 'label', 'banner_url',
                            'deezer_id', 'deezer_match_status', 'deezer_last_attempted',
                        ]

                        # Read enrichment data from old artist
                        cursor.execute("SELECT * FROM artists WHERE id = ? AND server_source = ?", (old_id, server_source))
                        old_row = cursor.fetchone()

                        # Insert new artist with fresh server metadata + preserved created_at
                        old_created = old_row['created_at'] if old_row else None
                        cursor.execute("""
                            INSERT INTO artists (id, name, thumb_url, genres, summary, server_source, created_at, updated_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                        """, (artist_id, name, thumb_url, genres_json, summary, server_source, old_created))

                        # Copy enrichment data from old record to new record
                        if old_row:
                            set_parts = []
                            values = []
                            for col in enrichment_cols:
                                try:
                                    val = old_row[col]
                                    if val is not None:
                                        set_parts.append(f"{col} = ?")
                                        values.append(val)
                                except (IndexError, KeyError):
                                    continue  # Column doesn't exist in this DB version

                            if set_parts:
                                values.append(artist_id)
                                cursor.execute(f"""
                                    UPDATE artists SET {', '.join(set_parts)}
                                    WHERE id = ?
                                """, values)

                        # Step 2: Migrate album and track references to new artist ID
                        cursor.execute("UPDATE albums SET artist_id = ? WHERE artist_id = ?", (artist_id, old_id))
                        migrated_albums = cursor.rowcount
                        cursor.execute("UPDATE tracks SET artist_id = ? WHERE artist_id = ?", (artist_id, old_id))
                        migrated_tracks = cursor.rowcount

                        # Step 3: Safely delete old artist (verify no remaining references first)
                        cursor.execute("SELECT COUNT(*) FROM albums WHERE artist_id = ?", (old_id,))
                        remaining = cursor.fetchone()[0]
                        if remaining == 0:
                            cursor.execute("DELETE FROM artists WHERE id = ? AND server_source = ?", (old_id, server_source))
                        else:
                            logger.warning(f"Could not delete old artist {old_id}: {remaining} albums still reference it")

                        if migrated_albums > 0 or migrated_tracks > 0:
                            logger.info(f"   Migrated {migrated_albums} albums, {migrated_tracks} tracks to new ID")
                    else:
                        # Genuinely new artist — insert fresh record
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
            
            # Check if album exists with this ID (PRIMARY KEY check)
            cursor.execute("SELECT id, server_source FROM albums WHERE id = ?", (album_id,))
            existing = cursor.fetchone()

            if existing:
                # Album exists - update it (update server_source if different)
                cursor.execute("""
                    UPDATE albums
                    SET artist_id = ?, title = ?, year = ?, thumb_url = ?, genres = ?,
                        track_count = ?, duration = ?, server_source = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (artist_id, title, year, thumb_url, genres_json, track_count, duration, server_source, album_id))
            else:
                # Before inserting, check if an album with the same title already exists
                # under this artist (ratingKey may have changed after a library rescan)
                cursor.execute(
                    "SELECT id FROM albums WHERE title = ? AND artist_id = ? AND server_source = ?",
                    (title, artist_id, server_source))
                existing_by_title = cursor.fetchone()

                if existing_by_title:
                    old_id = existing_by_title['id']
                    # ratingKey changed — migrate old album to new ID, preserving enrichment data
                    logger.info(f"🔄 Album ratingKey migrated: '{title}' ({old_id} → {album_id})")

                    enrichment_cols = [
                        'musicbrainz_release_id', 'musicbrainz_last_attempted', 'musicbrainz_match_status',
                        'spotify_album_id', 'spotify_match_status', 'spotify_last_attempted',
                        'itunes_album_id', 'itunes_match_status', 'itunes_last_attempted',
                        'audiodb_id', 'audiodb_match_status', 'audiodb_last_attempted',
                        'style', 'mood', 'label', 'explicit', 'record_type',
                        'deezer_id', 'deezer_match_status', 'deezer_last_attempted',
                    ]

                    # Read enrichment data from old album
                    cursor.execute("SELECT * FROM albums WHERE id = ?", (old_id,))
                    old_row = cursor.fetchone()

                    # Insert new album with fresh server metadata + preserved created_at
                    old_created = old_row['created_at'] if old_row else None
                    cursor.execute("""
                        INSERT INTO albums (id, artist_id, title, year, thumb_url, genres,
                                            track_count, duration, server_source, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """, (album_id, artist_id, title, year, thumb_url, genres_json,
                          track_count, duration, server_source, old_created))

                    # Copy enrichment data from old record to new record
                    if old_row:
                        set_parts = []
                        values = []
                        for col in enrichment_cols:
                            try:
                                val = old_row[col]
                                if val is not None:
                                    set_parts.append(f"{col} = ?")
                                    values.append(val)
                            except (IndexError, KeyError):
                                continue  # Column doesn't exist in this DB version

                        if set_parts:
                            values.append(album_id)
                            cursor.execute(f"""
                                UPDATE albums SET {', '.join(set_parts)}
                                WHERE id = ?
                            """, values)

                    # Migrate track references to new album ID
                    cursor.execute("UPDATE tracks SET album_id = ? WHERE album_id = ?", (album_id, old_id))
                    migrated_tracks = cursor.rowcount

                    # Safely delete old album (verify no remaining references first)
                    cursor.execute("SELECT COUNT(*) FROM tracks WHERE album_id = ?", (old_id,))
                    remaining = cursor.fetchone()[0]
                    if remaining == 0:
                        cursor.execute("DELETE FROM albums WHERE id = ?", (old_id,))
                    else:
                        logger.warning(f"Could not delete old album {old_id}: {remaining} tracks still reference it")

                    if migrated_tracks > 0:
                        logger.info(f"   Migrated {migrated_tracks} tracks to new album ID")
                else:
                    # Genuinely new album — insert fresh record
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

                # Fallback for Navidrome/Subsonic tracks
                if file_path is None and hasattr(track_obj, 'path') and track_obj.path:
                    file_path = track_obj.path
                if bitrate is None and hasattr(track_obj, 'bitRate') and track_obj.bitRate:
                    bitrate = track_obj.bitRate
                if file_path is None and hasattr(track_obj, 'suffix') and track_obj.suffix:
                    file_path = f"{track_obj.title}.{track_obj.suffix}"
                
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

            # STRATEGY 2: Broader fuzzy search - splits into individual words with OR matching
            fuzzy_results = self._search_tracks_fuzzy_fallback(cursor, title, artist, limit, server_source)
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
            where_conditions.append("unidecode_lower(tracks.title) LIKE ?")
            params.append(f"%{self._normalize_for_comparison(title)}%")

        if artist:
            where_conditions.append("unidecode_lower(artists.name) LIKE ?")
            params.append(f"%{self._normalize_for_comparison(artist)}%")
        
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
    
    def _search_tracks_fuzzy_fallback(self, cursor, title: str, artist: str, limit: int, server_source: str = None) -> List[DatabaseTrack]:
        """Broadest fuzzy search - partial word matching"""
        # Get broader results by searching for individual words
        search_terms = []
        if title:
            # Split title into words and search for each (normalized for diacritics)
            title_words = [w.strip() for w in self._normalize_for_comparison(title).split() if len(w.strip()) >= 3]
            search_terms.extend(title_words)

        if artist:
            # Split artist into words and search for each (normalized for diacritics)
            artist_words = [w.strip() for w in self._normalize_for_comparison(artist).split() if len(w.strip()) >= 3]
            search_terms.extend(artist_words)
        
        if not search_terms:
            return []
        
        # Build a query that searches for any of the words
        like_conditions = []
        params = []
        
        for term in search_terms[:5]:  # Limit to 5 terms to avoid too broad search
            like_conditions.append("(unidecode_lower(tracks.title) LIKE ? OR unidecode_lower(artists.name) LIKE ?)")
            params.extend([f"%{term}%", f"%{term}%"])
        
        if not like_conditions:
            return []
        
        # Build WHERE clause with optional server filter
        where_parts = [f"({' OR '.join(like_conditions)})"]
        if server_source:
            where_parts.append("tracks.server_source = ?")
            params.insert(-1 if params else 0, server_source)  # Insert before limit
        
        where_clause = " AND ".join(where_parts)
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
            db_title_lower = self._normalize_for_comparison(row['title'])
            db_artist_lower = self._normalize_for_comparison(row['artist_name'])
            
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
                where_conditions.append("unidecode_lower(albums.title) LIKE ?")
                params.append(f"%{self._normalize_for_comparison(title)}%")

            if artist:
                where_conditions.append("unidecode_lower(artists.name) LIKE ?")
                params.append(f"%{self._normalize_for_comparison(artist)}%")

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

            # Add diacritic-normalized variation (fixes #101)
            # This allows "Subcarpaţi" to match "Subcarpati" in SQL LIKE queries
            normalized_name = self._normalize_for_comparison(artist_name)
            # Only add if it's different from original (avoid duplicates)
            if normalized_name != artist_name.lower():
                # Add with original casing style if possible
                variations.append(normalized_name.title())
                variations.append(normalized_name)

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
    
    def check_album_completeness(self, album_id: int, expected_track_count: Optional[int] = None) -> Tuple[int, int, bool, List[str]]:
        """
        Check if we have all tracks for an album.
        Merges counts across split album entries (same title+year+artist) so that
        albums split by the media server (e.g. Navidrome) are treated as one.
        Returns (owned_tracks, expected_tracks, is_complete, formats)
        where formats is a list of distinct format strings like ["FLAC"] or ["FLAC", "MP3-320"]
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # Look up this album's title, year, and artist to find all sibling entries
            cursor.execute("SELECT title, year, artist_id FROM albums WHERE id = ?", (album_id,))
            album_info = cursor.fetchone()

            if not album_info:
                return 0, 0, False, []

            # Find all album IDs that share the same title, year, and artist
            # This merges split albums (e.g. Navidrome splitting one album into multiple entries)
            cursor.execute("""
                SELECT id FROM albums
                WHERE title = ? AND artist_id = ? AND (year IS ? OR (year IS NULL AND ? IS NULL))
            """, (album_info['title'], album_info['artist_id'], album_info['year'], album_info['year']))
            sibling_ids = [row['id'] for row in cursor.fetchall()]

            # Get actual track count across all sibling album entries
            placeholders = ','.join('?' for _ in sibling_ids)
            cursor.execute(f"SELECT COUNT(*) FROM tracks WHERE album_id IN ({placeholders})", sibling_ids)
            owned_tracks = cursor.fetchone()[0]

            # Get combined expected track count from all sibling album entries
            cursor.execute(f"SELECT SUM(track_count) FROM albums WHERE id IN ({placeholders})", sibling_ids)
            result = cursor.fetchone()
            stored_track_count = result[0] if result and result[0] else 0

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

            # Get distinct format strings for owned tracks
            formats = self._get_album_formats(cursor, sibling_ids)

            return owned_tracks, expected_tracks or 0, is_complete, formats

        except Exception as e:
            logger.error(f"Error checking album completeness for album_id {album_id}: {e}")
            return 0, 0, False, []

    def _get_album_formats(self, cursor, sibling_ids: list) -> List[str]:
        """Get distinct format strings for tracks in the given album IDs."""
        import os
        try:
            placeholders = ','.join('?' for _ in sibling_ids)
            cursor.execute(f"""
                SELECT file_path, bitrate FROM tracks
                WHERE album_id IN ({placeholders}) AND file_path IS NOT NULL
            """, sibling_ids)

            format_set = set()
            for row in cursor.fetchall():
                ext = os.path.splitext(row['file_path'] or '')[1].lstrip('.').upper()
                if not ext:
                    continue
                if ext == 'MP3' and row['bitrate']:
                    format_set.add(f"MP3-{row['bitrate']}")
                elif ext == 'MP3':
                    format_set.add('MP3')
                else:
                    format_set.add(ext)
            return sorted(format_set)
        except Exception as e:
            logger.error(f"Error getting album formats: {e}")
            return []
    
    def check_album_exists_with_completeness(self, title: str, artist: str, expected_track_count: Optional[int] = None, confidence_threshold: float = 0.8, server_source: Optional[str] = None) -> Tuple[Optional[DatabaseAlbum], float, int, int, bool, List[str]]:
        """
        Check if an album exists in the database with completeness information.
        Enhanced to handle edition matching (standard <-> deluxe variants).
        Returns (album, confidence, owned_tracks, expected_tracks, is_complete, formats)
        """
        try:
            # Try enhanced edition-aware matching first with expected track count for Smart Edition Matching
            album, confidence = self.check_album_exists_with_editions(title, artist, confidence_threshold, expected_track_count, server_source)

            if not album:
                return None, 0.0, 0, 0, False, []

            # Now check completeness (includes formats)
            owned_tracks, expected_tracks, is_complete, formats = self.check_album_completeness(album.id, expected_track_count)

            return album, confidence, owned_tracks, expected_tracks, is_complete, formats

        except Exception as e:
            logger.error(f"Error checking album existence with completeness for '{title}' by '{artist}': {e}")
            return None, 0.0, 0, 0, False, []
    
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

        # Add diacritic-normalized variation (fixes #101)
        # SQLite LIKE is not Unicode-aware, so "găină" won't match "gaina"
        # Adding the normalized form lets the SQL query catch both
        normalized_title = self._normalize_for_comparison(title)
        if normalized_title != title.lower():
            variations.append(normalized_title)

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

            # Also try with normalized versions (handling diacritics) - fixes #101
            normalized_search_title = self._normalize_for_comparison(search_title)
            normalized_db_title = self._normalize_for_comparison(db_album.title)
            normalized_title_similarity = self._string_similarity(normalized_search_title, normalized_db_title)

            # Use the best title similarity
            best_title_similarity = max(title_similarity, clean_title_similarity, normalized_title_similarity)

            # Log when normalized matching helps (only if it's the best score and better than others)
            if normalized_title_similarity == best_title_similarity and normalized_title_similarity > max(title_similarity, clean_title_similarity):
                logger.debug(f"  🌍 Diacritic normalization improved match: '{search_title}' -> '{db_album.title}' (normalized: {normalized_title_similarity:.3f} vs raw: {title_similarity:.3f})")

            # Require minimum title similarity to prevent a perfect artist match from
            # carrying a bad title match over the threshold (e.g. "divisions" vs "silos")
            if best_title_similarity < 0.6:
                return best_title_similarity * 0.5  # Can never exceed 0.3, well below any threshold

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

        # Add diacritic-normalized variation (fixes #101)
        normalized_title = self._normalize_for_comparison(title)
        if normalized_title != title.lower():
            variations.append(normalized_title)

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

        # STEP 2: Remove metadata noise for better matching
        # IMPORTANT: Only remove markers that describe the SAME recording with different metadata
        # DO NOT remove markers that indicate DIFFERENT versions (live, remix, acoustic, etc.)
        # Those are handled by the matching engine's version detection system
        patterns_to_remove = [
            # Basic markers (content/parental ratings)
            r'\s*explicit\s*',      # Remove explicit markers
            r'\s*clean\s*',         # Remove clean markers

            # Featuring/collaboration (metadata, not different version)
            r'\s*feat\..*',         # Remove featuring
            r'\s*featuring.*',      # Remove featuring
            r'\s*ft\..*',           # Remove ft.
            r'\s*with\s+.*',        # Remove "with Artist"

            # Edit versions (same recording, different edit for format)
            r'\s*radio\s+edit.*',   # Remove "radio edit" - same song, radio format
            r'\s*single\s+edit.*',  # Remove "single edit" - same song, single format
            r'\s*album\s+edit.*',   # Remove "album edit" - same song, album format
            r'\s*edit\s*$',         # Remove trailing "edit"

            # Remasters (same recording, different mastering)
            r'\s*\d{4}\s*remaster.*',  # Remove "2015 remaster"
            r'\s*remaster.*',       # Remove "remaster/remastered"
            r'\s*remastered.*',     # Remove "remastered"

            # Version clarifications (metadata, not different recordings)
            r'\s*original\s+version.*',  # Remove "original version" - clarification
            r'\s*album\s+version.*',     # Remove "album version" - clarification
            r'\s*single\s+version.*',    # Remove "single version" - clarification
            r'\s*version\s*$',           # Remove trailing "version"

            # Soundtrack/source info (metadata about source)
            r'\s*from\s+.*soundtrack.*', # Remove "from ... soundtrack"
            r'\s*from\s+".*".*',         # Remove "from 'Movie Title'"
            r'\s*soundtrack.*',          # Remove "soundtrack"
        ]

        # NOTE: We do NOT remove these - they indicate DIFFERENT recordings:
        # - live, live at, live from, unplugged (different performance)
        # - remix, mix (different mix)
        # - acoustic (different arrangement)
        # - instrumental (different version)
        # - demo (different recording)
        # - extended (different length/content)
        # These are handled by matching_engine.similarity_score() which applies penalties

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

    # --- Bubble Snapshot Methods ---

    def save_bubble_snapshot(self, snapshot_type: str, data_dict: dict):
        """Save a bubble snapshot (upserts by type).

        Args:
            snapshot_type: One of 'artist_bubbles', 'search_bubbles', 'discover_downloads'
            data_dict: The bubbles/downloads dict to persist
        """
        from datetime import datetime
        now = datetime.now()
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT OR REPLACE INTO bubble_snapshots (type, data, timestamp, snapshot_id) VALUES (?, ?, ?, ?)",
                    (snapshot_type, json.dumps(data_dict), now.isoformat(), now.strftime('%Y%m%d_%H%M%S'))
                )
                conn.commit()
        except Exception as e:
            logger.error(f"Error saving bubble snapshot '{snapshot_type}': {e}")
            raise

    def get_bubble_snapshot(self, snapshot_type: str) -> Optional[Dict[str, Any]]:
        """Load a bubble snapshot.

        Returns:
            {'data': dict, 'timestamp': str} or None if not found
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT data, timestamp FROM bubble_snapshots WHERE type = ?", (snapshot_type,))
                row = cursor.fetchone()
                if row:
                    return {'data': json.loads(row['data']), 'timestamp': row['timestamp']}
                return None
        except Exception as e:
            logger.error(f"Error getting bubble snapshot '{snapshot_type}': {e}")
            return None

    def delete_bubble_snapshot(self, snapshot_type: str):
        """Delete a bubble snapshot."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM bubble_snapshots WHERE type = ?", (snapshot_type,))
                conn.commit()
        except Exception as e:
            logger.error(f"Error deleting bubble snapshot '{snapshot_type}': {e}")

    # Quality profile management methods

    def get_quality_profile(self) -> dict:
        """Get the quality profile configuration, returns default if not set"""
        import json

        profile_json = self.get_preference('quality_profile')

        if profile_json:
            try:
                profile = json.loads(profile_json)
                # Migrate v1 profiles (min_mb/max_mb) to v2 (min_kbps/max_kbps)
                if profile.get('version', 1) < 2:
                    logger.info("Migrating quality profile from v1 (file size) to v2 (bitrate density)")
                    return self._get_default_quality_profile()
                return profile
            except json.JSONDecodeError:
                logger.error("Failed to parse quality profile JSON, returning default")

        return self._get_default_quality_profile()

    def _get_default_quality_profile(self) -> dict:
        """Return the default v2 quality profile (balanced preset)"""
        return {
            "version": 2,
            "preset": "balanced",
            "qualities": {
                "flac": {
                    "enabled": True,
                    "min_kbps": 500,
                    "max_kbps": 10000,
                    "priority": 1,
                    "bit_depth": "any"
                },
                "mp3_320": {
                    "enabled": True,
                    "min_kbps": 280,
                    "max_kbps": 500,
                    "priority": 2
                },
                "mp3_256": {
                    "enabled": True,
                    "min_kbps": 200,
                    "max_kbps": 400,
                    "priority": 3
                },
                "mp3_192": {
                    "enabled": False,
                    "min_kbps": 150,
                    "max_kbps": 300,
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
                "version": 2,
                "preset": "audiophile",
                "qualities": {
                    "flac": {
                        "enabled": True,
                        "min_kbps": 500,
                        "max_kbps": 10000,
                        "priority": 1,
                        "bit_depth": "any"
                    },
                    "mp3_320": {
                        "enabled": False,
                        "min_kbps": 280,
                        "max_kbps": 500,
                        "priority": 2
                    },
                    "mp3_256": {
                        "enabled": False,
                        "min_kbps": 200,
                        "max_kbps": 400,
                        "priority": 3
                    },
                    "mp3_192": {
                        "enabled": False,
                        "min_kbps": 150,
                        "max_kbps": 300,
                        "priority": 4
                    }
                },
                "fallback_enabled": False
            },
            "balanced": {
                "version": 2,
                "preset": "balanced",
                "qualities": {
                    "flac": {
                        "enabled": True,
                        "min_kbps": 500,
                        "max_kbps": 10000,
                        "priority": 1,
                        "bit_depth": "any"
                    },
                    "mp3_320": {
                        "enabled": True,
                        "min_kbps": 280,
                        "max_kbps": 500,
                        "priority": 2
                    },
                    "mp3_256": {
                        "enabled": True,
                        "min_kbps": 200,
                        "max_kbps": 400,
                        "priority": 3
                    },
                    "mp3_192": {
                        "enabled": False,
                        "min_kbps": 150,
                        "max_kbps": 300,
                        "priority": 4
                    }
                },
                "fallback_enabled": True
            },
            "space_saver": {
                "version": 2,
                "preset": "space_saver",
                "qualities": {
                    "flac": {
                        "enabled": False,
                        "min_kbps": 500,
                        "max_kbps": 10000,
                        "priority": 4,
                        "bit_depth": "any"
                    },
                    "mp3_320": {
                        "enabled": True,
                        "min_kbps": 280,
                        "max_kbps": 500,
                        "priority": 1
                    },
                    "mp3_256": {
                        "enabled": True,
                        "min_kbps": 200,
                        "max_kbps": 400,
                        "priority": 2
                    },
                    "mp3_192": {
                        "enabled": True,
                        "min_kbps": 150,
                        "max_kbps": 300,
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
        """Clear all tracks from the wishlist and reset scan timestamps so next scan re-discovers everything"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM wishlist_tracks")
                cleared_count = cursor.rowcount
                # Reset last_scan_timestamp on all watchlist artists so the next scan
                # uses the lookback period setting (e.g. "entire discography") instead
                # of only finding albums released after the old scan date
                cursor.execute("UPDATE watchlist_artists SET last_scan_timestamp = NULL")
                reset_count = cursor.rowcount
                conn.commit()
                logger.info(f"Cleared {cleared_count} tracks from wishlist, reset scan timestamps on {reset_count} artists")
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
                        if artists and isinstance(artists[0], dict):
                            artist_name = artists[0].get('name', '').lower()
                        elif artists:
                            artist_name = str(artists[0]).lower()
                        else:
                            artist_name = 'unknown'

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
    def add_artist_to_watchlist(self, artist_id: str, artist_name: str) -> bool:
        """Add an artist to the watchlist for monitoring new releases.

        Automatically detects if artist_id is a Spotify ID (alphanumeric) or iTunes ID (numeric).
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # Detect ID type: iTunes IDs are purely numeric, Spotify IDs are alphanumeric
                is_itunes_id = artist_id.isdigit()

                if is_itunes_id:
                    cursor.execute("""
                        INSERT OR REPLACE INTO watchlist_artists
                        (itunes_artist_id, artist_name, date_added, updated_at)
                        VALUES (?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """, (artist_id, artist_name))
                    logger.info(f"Added artist '{artist_name}' to watchlist (iTunes ID: {artist_id})")
                else:
                    cursor.execute("""
                        INSERT OR REPLACE INTO watchlist_artists
                        (spotify_artist_id, artist_name, date_added, updated_at)
                        VALUES (?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """, (artist_id, artist_name))
                    logger.info(f"Added artist '{artist_name}' to watchlist (Spotify ID: {artist_id})")

                conn.commit()
                return True

        except Exception as e:
            logger.error(f"Error adding artist '{artist_name}' to watchlist: {e}")
            return False

    def remove_artist_from_watchlist(self, artist_id: str) -> bool:
        """Remove an artist from the watchlist (checks both Spotify and iTunes IDs)"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # Get artist name for logging (check both ID columns)
                cursor.execute("""
                    SELECT artist_name FROM watchlist_artists
                    WHERE spotify_artist_id = ? OR itunes_artist_id = ?
                """, (artist_id, artist_id))
                result = cursor.fetchone()
                artist_name = result['artist_name'] if result else "Unknown"

                cursor.execute("""
                    DELETE FROM watchlist_artists
                    WHERE spotify_artist_id = ? OR itunes_artist_id = ?
                """, (artist_id, artist_id))

                if cursor.rowcount > 0:
                    conn.commit()
                    logger.info(f"Removed artist '{artist_name}' from watchlist (ID: {artist_id})")
                    return True
                else:
                    logger.warning(f"Artist with ID {artist_id} not found in watchlist")
                    return False

        except Exception as e:
            logger.error(f"Error removing artist from watchlist (ID: {artist_id}): {e}")
            return False

    def is_artist_in_watchlist(self, artist_id: str) -> bool:
        """Check if an artist is currently in the watchlist (checks both Spotify and iTunes IDs)"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # Check both spotify_artist_id and itunes_artist_id columns
                cursor.execute("""
                    SELECT 1 FROM watchlist_artists
                    WHERE spotify_artist_id = ? OR itunes_artist_id = ?
                    LIMIT 1
                """, (artist_id, artist_id))
                result = cursor.fetchone()

                return result is not None

        except Exception as e:
            logger.error(f"Error checking if artist is in watchlist (ID: {artist_id}): {e}")
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
                optional_columns = ['image_url', 'itunes_artist_id', 'include_albums', 'include_eps', 'include_singles',
                                   'include_live', 'include_remixes', 'include_acoustic', 'include_compilations']

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
                    itunes_artist_id = row['itunes_artist_id'] if 'itunes_artist_id' in existing_columns else None
                    include_albums = bool(row['include_albums']) if 'include_albums' in existing_columns else True
                    include_eps = bool(row['include_eps']) if 'include_eps' in existing_columns else True
                    include_singles = bool(row['include_singles']) if 'include_singles' in existing_columns else True
                    include_live = bool(row['include_live']) if 'include_live' in existing_columns else False
                    include_remixes = bool(row['include_remixes']) if 'include_remixes' in existing_columns else False
                    include_acoustic = bool(row['include_acoustic']) if 'include_acoustic' in existing_columns else False
                    include_compilations = bool(row['include_compilations']) if 'include_compilations' in existing_columns else False

                    watchlist_artists.append(WatchlistArtist(
                        id=row['id'],
                        spotify_artist_id=row['spotify_artist_id'],
                        artist_name=row['artist_name'],
                        date_added=datetime.fromisoformat(row['date_added']),
                        last_scan_timestamp=datetime.fromisoformat(row['last_scan_timestamp']) if row['last_scan_timestamp'] else None,
                        created_at=datetime.fromisoformat(row['created_at']) if row['created_at'] else None,
                        updated_at=datetime.fromisoformat(row['updated_at']) if row['updated_at'] else None,
                        image_url=image_url,
                        itunes_artist_id=itunes_artist_id,
                        include_albums=include_albums,
                        include_eps=include_eps,
                        include_singles=include_singles,
                        include_live=include_live,
                        include_remixes=include_remixes,
                        include_acoustic=include_acoustic,
                        include_compilations=include_compilations
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

    def update_watchlist_artist_image(self, artist_id: str, image_url: str) -> bool:
        """Update the image URL for a watchlist artist (checks both Spotify and iTunes IDs)"""
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
                    WHERE spotify_artist_id = ? OR itunes_artist_id = ?
                """, (image_url, artist_id, artist_id))

                conn.commit()
                return cursor.rowcount > 0

        except Exception as e:
            logger.error(f"Error updating watchlist artist image: {e}")
            return False

    def update_watchlist_spotify_id(self, watchlist_id: int, spotify_id: str) -> bool:
        """Update the Spotify artist ID for a watchlist artist (cross-provider support)"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                cursor.execute("""
                    UPDATE watchlist_artists
                    SET spotify_artist_id = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (spotify_id, watchlist_id))

                conn.commit()
                logger.info(f"Updated Spotify ID for watchlist artist {watchlist_id}: {spotify_id}")
                return cursor.rowcount > 0

        except Exception as e:
            logger.error(f"Error updating watchlist Spotify ID: {e}")
            return False

    def update_watchlist_itunes_id(self, watchlist_id: int, itunes_id: str) -> bool:
        """Update the iTunes artist ID for a watchlist artist (cross-provider support)"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                cursor.execute("""
                    UPDATE watchlist_artists
                    SET itunes_artist_id = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (itunes_id, watchlist_id))

                conn.commit()
                logger.info(f"Updated iTunes ID for watchlist artist {watchlist_id}: {itunes_id}")
                return cursor.rowcount > 0

        except Exception as e:
            logger.error(f"Error updating watchlist iTunes ID: {e}")
            return False

    def update_watchlist_artist_itunes_id(self, spotify_artist_id: str, itunes_id: str) -> bool:
        """Update the iTunes artist ID for a watchlist artist by Spotify ID (for cross-provider caching)"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                cursor.execute("""
                    UPDATE watchlist_artists
                    SET itunes_artist_id = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE spotify_artist_id = ?
                """, (itunes_id, spotify_artist_id))

                conn.commit()
                if cursor.rowcount > 0:
                    logger.info(f"Cached iTunes ID {itunes_id} for Spotify artist {spotify_artist_id}")
                return cursor.rowcount > 0

        except Exception as e:
            logger.error(f"Error caching watchlist iTunes ID: {e}")
            return False

    # === Discovery Feature Methods ===

    def add_or_update_similar_artist(self, source_artist_id: str, similar_artist_name: str,
                                      similar_artist_spotify_id: Optional[str] = None,
                                      similar_artist_itunes_id: Optional[str] = None,
                                      similarity_rank: int = 1) -> bool:
        """Add or update a similar artist recommendation (supports both Spotify and iTunes IDs)"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # Use artist name as the unique key (allows storing both IDs for same artist)
                cursor.execute("""
                    INSERT INTO similar_artists
                    (source_artist_id, similar_artist_spotify_id, similar_artist_itunes_id, similar_artist_name, similarity_rank, occurrence_count, last_updated)
                    VALUES (?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
                    ON CONFLICT(source_artist_id, similar_artist_name)
                    DO UPDATE SET
                        similar_artist_spotify_id = COALESCE(excluded.similar_artist_spotify_id, similar_artist_spotify_id),
                        similar_artist_itunes_id = COALESCE(excluded.similar_artist_itunes_id, similar_artist_itunes_id),
                        similarity_rank = excluded.similarity_rank,
                        occurrence_count = occurrence_count + 1,
                        last_updated = CURRENT_TIMESTAMP
                """, (source_artist_id, similar_artist_spotify_id, similar_artist_itunes_id, similar_artist_name, similarity_rank))

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
                    similar_artist_itunes_id=row['similar_artist_itunes_id'] if 'similar_artist_itunes_id' in row.keys() else None,
                    similar_artist_name=row['similar_artist_name'],
                    similarity_rank=row['similarity_rank'],
                    occurrence_count=row['occurrence_count'],
                    last_updated=datetime.fromisoformat(row['last_updated'])
                ) for row in rows]

        except Exception as e:
            logger.error(f"Error getting similar artists: {e}")
            return []

    def get_similar_artists_missing_itunes_ids(self, source_artist_id: str) -> List[SimilarArtist]:
        """Get similar artists for a source that are missing iTunes IDs (for backfill)"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                cursor.execute("""
                    SELECT * FROM similar_artists
                    WHERE source_artist_id = ?
                    AND (similar_artist_itunes_id IS NULL OR similar_artist_itunes_id = '')
                    ORDER BY occurrence_count DESC
                    LIMIT 50
                """, (source_artist_id,))

                rows = cursor.fetchall()
                return [SimilarArtist(
                    id=row['id'],
                    source_artist_id=row['source_artist_id'],
                    similar_artist_spotify_id=row['similar_artist_spotify_id'],
                    similar_artist_itunes_id=None,
                    similar_artist_name=row['similar_artist_name'],
                    similarity_rank=row['similarity_rank'],
                    occurrence_count=row['occurrence_count'],
                    last_updated=datetime.fromisoformat(row['last_updated'])
                ) for row in rows]

        except Exception as e:
            logger.error(f"Error getting similar artists missing iTunes IDs: {e}")
            return []

    def update_similar_artist_itunes_id(self, similar_artist_id: int, itunes_id: str) -> bool:
        """Update a similar artist's iTunes ID (for backfill)"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                cursor.execute("""
                    UPDATE similar_artists
                    SET similar_artist_itunes_id = ?
                    WHERE id = ?
                """, (itunes_id, similar_artist_id))

                conn.commit()
                return cursor.rowcount > 0

        except Exception as e:
            logger.error(f"Error updating similar artist iTunes ID: {e}")
            return False

    def has_fresh_similar_artists(self, source_artist_id: str, days_threshold: int = 30, require_itunes: bool = True, require_spotify: bool = False) -> bool:
        """
        Check if we have cached similar artists that are still fresh (<days_threshold old).
        Also checks that similar artists have the required provider IDs.

        Args:
            source_artist_id: The source artist ID to check
            days_threshold: Maximum age in days to consider fresh
            require_itunes: If True, also requires iTunes IDs to be present (for seamless provider switching)
            require_spotify: If True, also requires Spotify IDs to be present (for Spotify discovery)

        Returns True if we have recent data with required IDs, False if data is stale, missing, or incomplete.
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

                if days_since_update >= days_threshold:
                    return False

                # Check if we have iTunes IDs (for seamless provider switching)
                if require_itunes:
                    cursor.execute("""
                        SELECT COUNT(*) as total,
                               SUM(CASE WHEN similar_artist_itunes_id IS NOT NULL AND similar_artist_itunes_id != '' THEN 1 ELSE 0 END) as has_itunes
                        FROM similar_artists
                        WHERE source_artist_id = ?
                    """, (source_artist_id,))
                    id_row = cursor.fetchone()

                    if id_row and id_row['total'] > 0:
                        # If less than 50% have iTunes IDs, consider stale and refetch
                        itunes_ratio = id_row['has_itunes'] / id_row['total']
                        if itunes_ratio < 0.5:
                            logger.debug(f"Similar artists for {source_artist_id} missing iTunes IDs ({id_row['has_itunes']}/{id_row['total']}), will refetch")
                            return False

                # Check if we have Spotify IDs (for Spotify discovery)
                if require_spotify:
                    cursor.execute("""
                        SELECT COUNT(*) as total,
                               SUM(CASE WHEN similar_artist_spotify_id IS NOT NULL AND similar_artist_spotify_id != '' THEN 1 ELSE 0 END) as has_spotify
                        FROM similar_artists
                        WHERE source_artist_id = ?
                    """, (source_artist_id,))
                    id_row = cursor.fetchone()

                    if id_row and id_row['total'] > 0:
                        # If less than 50% have Spotify IDs, consider stale and refetch
                        spotify_ratio = id_row['has_spotify'] / id_row['total']
                        if spotify_ratio < 0.5:
                            logger.debug(f"Similar artists for {source_artist_id} missing Spotify IDs ({id_row['has_spotify']}/{id_row['total']}), will refetch")
                            return False

                return True

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
                        MAX(similar_artist_spotify_id) as similar_artist_spotify_id,
                        MAX(similar_artist_itunes_id) as similar_artist_itunes_id,
                        similar_artist_name,
                        AVG(similarity_rank) as similarity_rank,
                        SUM(occurrence_count) as occurrence_count,
                        MAX(last_updated) as last_updated
                    FROM similar_artists
                    GROUP BY similar_artist_name
                    ORDER BY occurrence_count DESC, similarity_rank ASC
                    LIMIT ?
                """, (limit,))

                rows = cursor.fetchall()
                return [SimilarArtist(
                    id=row['id'],
                    source_artist_id=row['source_artist_id'],
                    similar_artist_spotify_id=row['similar_artist_spotify_id'],
                    similar_artist_itunes_id=row['similar_artist_itunes_id'] if 'similar_artist_itunes_id' in row.keys() else None,
                    similar_artist_name=row['similar_artist_name'],
                    similarity_rank=int(row['similarity_rank']),
                    occurrence_count=row['occurrence_count'],
                    last_updated=datetime.fromisoformat(row['last_updated'])
                ) for row in rows]

        except Exception as e:
            logger.error(f"Error getting top similar artists: {e}")
            return []

    def add_to_discovery_pool(self, track_data: Dict[str, Any], source: str = 'spotify') -> bool:
        """Add a track to the discovery pool (supports both Spotify and iTunes sources)"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # Check if track already exists based on source
                if source == 'spotify' and track_data.get('spotify_track_id'):
                    cursor.execute("SELECT COUNT(*) as count FROM discovery_pool WHERE spotify_track_id = ? AND source = 'spotify'",
                                  (track_data['spotify_track_id'],))
                elif source == 'itunes' and track_data.get('itunes_track_id'):
                    cursor.execute("SELECT COUNT(*) as count FROM discovery_pool WHERE itunes_track_id = ? AND source = 'itunes'",
                                  (track_data['itunes_track_id'],))
                else:
                    # Fallback check by track name and artist
                    cursor.execute("SELECT COUNT(*) as count FROM discovery_pool WHERE track_name = ? AND artist_name = ? AND source = ?",
                                  (track_data['track_name'], track_data['artist_name'], source))

                if cursor.fetchone()['count'] > 0:
                    return True  # Already in pool

                # Get artist genres if available
                artist_genres = track_data.get('artist_genres')
                artist_genres_json = json.dumps(artist_genres) if artist_genres else None

                cursor.execute("""
                    INSERT INTO discovery_pool
                    (spotify_track_id, spotify_album_id, spotify_artist_id,
                     itunes_track_id, itunes_album_id, itunes_artist_id,
                     source, track_name, artist_name, album_name, album_cover_url,
                     duration_ms, popularity, release_date, is_new_release, track_data_json, artist_genres, added_date)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (
                    track_data.get('spotify_track_id'),
                    track_data.get('spotify_album_id'),
                    track_data.get('spotify_artist_id'),
                    track_data.get('itunes_track_id'),
                    track_data.get('itunes_album_id'),
                    track_data.get('itunes_artist_id'),
                    source,
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

    def get_discovery_pool_tracks(self, limit: int = 100, new_releases_only: bool = False, source: Optional[str] = None) -> List[DiscoveryTrack]:
        """Get tracks from discovery pool, optionally filtered by source ('spotify' or 'itunes')"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # Build query with optional source filter
                where_clauses = []
                params = []

                if new_releases_only:
                    where_clauses.append("is_new_release = 1")

                if source:
                    where_clauses.append("source = ?")
                    params.append(source)

                where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
                params.append(limit)

                cursor.execute(f"""
                    SELECT * FROM discovery_pool
                    {where_sql}
                    ORDER BY added_date DESC
                    LIMIT ?
                """, params)

                rows = cursor.fetchall()
                row_keys = rows[0].keys() if rows else []

                return [DiscoveryTrack(
                    id=row['id'],
                    spotify_track_id=row['spotify_track_id'],
                    spotify_album_id=row['spotify_album_id'],
                    spotify_artist_id=row['spotify_artist_id'],
                    itunes_track_id=row['itunes_track_id'] if 'itunes_track_id' in row_keys else None,
                    itunes_album_id=row['itunes_album_id'] if 'itunes_album_id' in row_keys else None,
                    itunes_artist_id=row['itunes_artist_id'] if 'itunes_artist_id' in row_keys else None,
                    source=row['source'] if 'source' in row_keys else 'spotify',
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

    def cache_discovery_recent_album(self, album_data: Dict[str, Any], source: str = 'spotify') -> bool:
        """Cache a recent album for the discover page (supports both Spotify and iTunes sources)"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                cursor.execute("""
                    INSERT OR REPLACE INTO discovery_recent_albums
                    (album_spotify_id, album_itunes_id, artist_spotify_id, artist_itunes_id, source,
                     album_name, artist_name, album_cover_url, release_date, album_type, cached_date)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (
                    album_data.get('album_spotify_id'),
                    album_data.get('album_itunes_id'),
                    album_data.get('artist_spotify_id'),
                    album_data.get('artist_itunes_id'),
                    source,
                    album_data['album_name'],
                    album_data['artist_name'],
                    album_data.get('album_cover_url'),
                    album_data['release_date'],
                    album_data.get('album_type', 'album')
                ))

                conn.commit()
                return True

        except Exception as e:
            logger.error(f"Error caching discovery recent album: {e}")
            return False

    def get_discovery_recent_albums(self, limit: int = 10, source: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get cached recent albums for discover page, optionally filtered by source"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                if source:
                    cursor.execute("""
                        SELECT * FROM discovery_recent_albums
                        WHERE source = ?
                        ORDER BY release_date DESC
                        LIMIT ?
                    """, (source, limit))
                else:
                    cursor.execute("""
                        SELECT * FROM discovery_recent_albums
                        ORDER BY release_date DESC
                        LIMIT ?
                    """, (limit,))

                rows = cursor.fetchall()
                row_keys = rows[0].keys() if rows else []

                return [{
                    'album_spotify_id': row['album_spotify_id'],
                    'album_itunes_id': row['album_itunes_id'] if 'album_itunes_id' in row_keys else None,
                    'album_name': row['album_name'],
                    'artist_name': row['artist_name'],
                    'artist_spotify_id': row['artist_spotify_id'],
                    'artist_itunes_id': row['artist_itunes_id'] if 'artist_itunes_id' in row_keys else None,
                    'album_cover_url': row['album_cover_url'],
                    'release_date': row['release_date'],
                    'album_type': row['album_type'],
                    'source': row['source'] if 'source' in row_keys else 'spotify'
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

    def get_library_artists(self, search_query: str = "", letter: str = "", page: int = 1, limit: int = 50, watchlist_filter: str = "all") -> Dict[str, Any]:
        """
        Get artists for the library page with search, filtering, and pagination

        Args:
            search_query: Search term to filter artists by name
            letter: Filter by first letter (a-z, #, or "" for all)
            page: Page number (1-based)
            limit: Number of results per page
            watchlist_filter: Filter by watchlist status ("all", "watched", "unwatched")

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

                # Pre-fetch watchlist data (small table, single fast query)
                cursor.execute("SELECT spotify_artist_id, itunes_artist_id, LOWER(artist_name) as name_lower FROM watchlist_artists")
                watchlist_rows = cursor.fetchall()
                wl_spotify = {r['spotify_artist_id'] for r in watchlist_rows if r['spotify_artist_id']}
                wl_itunes = {r['itunes_artist_id'] for r in watchlist_rows if r['itunes_artist_id']}
                wl_names = {r['name_lower'] for r in watchlist_rows if r['name_lower']}

                # Apply watchlist filter as WHERE conditions using IN clauses
                if watchlist_filter in ("watched", "unwatched"):
                    match_parts = []
                    match_params = []
                    if wl_spotify:
                        match_parts.append(f"(a.spotify_artist_id IS NOT NULL AND a.spotify_artist_id IN ({','.join('?' * len(wl_spotify))}))")
                        match_params.extend(wl_spotify)
                    if wl_itunes:
                        match_parts.append(f"(a.itunes_artist_id IS NOT NULL AND a.itunes_artist_id IN ({','.join('?' * len(wl_itunes))}))")
                        match_params.extend(wl_itunes)
                    if wl_names:
                        match_parts.append(f"LOWER(a.name) IN ({','.join('?' * len(wl_names))})")
                        match_params.extend(wl_names)

                    if match_parts:
                        combined = ' OR '.join(match_parts)
                        if watchlist_filter == "watched":
                            where_clause += f" AND ({combined})"
                        else:
                            where_clause += f" AND NOT ({combined})"
                        params.extend(match_params)
                    elif watchlist_filter == "watched":
                        # Empty watchlist, no artists can match
                        where_clause += " AND 0"

                # Deduplicate: only count canonical artist rows (one per name+server_source)
                count_query = f"""
                    SELECT COUNT(*) as total_count
                    FROM artists a
                    WHERE {where_clause}
                        AND a.id = (SELECT MIN(a2.id) FROM artists a2
                                    WHERE a2.name = a.name AND a2.server_source = a.server_source)
                """
                cursor.execute(count_query, params)
                total_count = cursor.fetchone()['total_count']

                # Get artists with pagination
                offset = (page - 1) * limit

                # Deduplicate: select canonical row per name, but count albums/tracks
                # across ALL same-name artist IDs (fixes duplicate artist display)
                artists_query = f"""
                    SELECT
                        a.id,
                        a.name,
                        a.thumb_url,
                        a.genres,
                        a.musicbrainz_id,
                        a.spotify_artist_id,
                        a.itunes_artist_id,
                        a.deezer_id,
                        a.audiodb_id,
                        COUNT(DISTINCT al.id) as album_count,
                        COUNT(DISTINCT t.id) as track_count
                    FROM artists a
                    LEFT JOIN albums al ON al.artist_id IN (
                        SELECT id FROM artists WHERE name = a.name AND server_source = a.server_source
                    )
                    LEFT JOIN tracks t ON al.id = t.album_id
                    WHERE {where_clause}
                        AND a.id = (SELECT MIN(a2.id) FROM artists a2
                                    WHERE a2.name = a.name AND a2.server_source = a.server_source)
                    GROUP BY a.id, a.name, a.thumb_url, a.genres, a.musicbrainz_id, a.deezer_id, a.audiodb_id
                    ORDER BY a.name COLLATE NOCASE
                    LIMIT ? OFFSET ?
                """
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

                    # Determine watchlist status via set lookups
                    is_watched = (
                        (row['spotify_artist_id'] and row['spotify_artist_id'] in wl_spotify)
                        or (row['itunes_artist_id'] and row['itunes_artist_id'] in wl_itunes)
                        or (row['name'] and row['name'].lower() in wl_names)
                    )

                    # Add stats
                    artist_data = {
                        'id': artist.id,
                        'name': artist.name,
                        'image_url': artist.thumb_url,
                        'genres': artist.genres,
                        'musicbrainz_id': row['musicbrainz_id'],
                        'deezer_id': row['deezer_id'],
                        'audiodb_id': row['audiodb_id'],
                        'album_count': row['album_count'] or 0,
                        'track_count': row['track_count'] or 0,
                        'is_watched': bool(is_watched)
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
                        id, name, thumb_url, genres, server_source, musicbrainz_id, deezer_id, audiodb_id
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
                # Group by title+year to merge split albums (e.g. Navidrome splitting one album into multiple entries)
                cursor.execute("""
                    SELECT
                        MIN(a.id) as id,
                        a.title,
                        a.year,
                        SUM(a.track_count) as track_count,
                        MAX(a.thumb_url) as thumb_url,
                        MAX(a.musicbrainz_release_id) as musicbrainz_release_id,
                        COUNT(t.id) as owned_tracks
                    FROM albums a
                    LEFT JOIN tracks t ON a.id = t.album_id
                    WHERE a.artist_id IN (
                        SELECT id FROM artists
                        WHERE name = (SELECT name FROM artists WHERE id = ?)
                        AND server_source = (SELECT server_source FROM artists WHERE id = ?)
                    )
                    GROUP BY a.title, a.year
                    ORDER BY a.year DESC, a.title
                """, (artist_id, artist_id))

                album_rows = cursor.fetchall()

                # Process albums and categorize by type
                albums = []
                eps = []
                singles = []

                # Get total stats for the artist (including all artists with same name)
                # Count distinct title+year pairs to avoid overcounting split albums
                cursor.execute("""
                    SELECT
                        COUNT(DISTINCT a.title || '::' || COALESCE(CAST(a.year AS TEXT), '')) as album_count,
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
                        'musicbrainz_release_id': album_row['musicbrainz_release_id'],
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
                        'musicbrainz_id': artist_row['musicbrainz_id'],
                        'deezer_id': artist_row['deezer_id'],
                        'audiodb_id': artist_row['audiodb_id'],
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

    # ==================== Discovery Match Cache Methods ====================

    def get_discovery_cache_match(self, normalized_title: str, normalized_artist: str, provider: str) -> Optional[Dict]:
        """Look up a cached discovery match. Returns the matched_data dict or None.
        Also bumps last_used_at and use_count on hit."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT matched_data_json, match_confidence FROM discovery_match_cache
                WHERE normalized_title = ? AND normalized_artist = ? AND provider = ?
            """, (normalized_title, normalized_artist, provider))
            row = cursor.fetchone()
            if row:
                # Bump usage stats
                cursor.execute("""
                    UPDATE discovery_match_cache
                    SET last_used_at = CURRENT_TIMESTAMP, use_count = use_count + 1
                    WHERE normalized_title = ? AND normalized_artist = ? AND provider = ?
                """, (normalized_title, normalized_artist, provider))
                conn.commit()
                return json.loads(row['matched_data_json'])
            return None
        except Exception as e:
            logger.error(f"Error reading discovery cache: {e}")
            return None

    def save_discovery_cache_match(self, normalized_title: str, normalized_artist: str,
                                    provider: str, confidence: float, matched_data: Dict,
                                    original_title: str = None, original_artist: str = None) -> bool:
        """Save a discovery match to cache. Uses INSERT OR REPLACE for upsert."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO discovery_match_cache
                (normalized_title, normalized_artist, provider, match_confidence,
                 matched_data_json, original_title, original_artist,
                 created_at, last_used_at, use_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 1)
            """, (normalized_title, normalized_artist, provider, confidence,
                  json.dumps(matched_data), original_title, original_artist))
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error saving discovery cache: {e}")
            return False

    # ==================== Retag Tool Methods ====================

    def add_retag_group(self, group_type: str, artist_name: str, album_name: str,
                        image_url: str = None, spotify_album_id: str = None,
                        itunes_album_id: str = None, total_tracks: int = 1,
                        release_date: str = None) -> Optional[int]:
        """Insert a retag group and return its ID."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO retag_groups (group_type, artist_name, album_name, image_url,
                    spotify_album_id, itunes_album_id, total_tracks, release_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (group_type, artist_name, album_name, image_url,
                  spotify_album_id, itunes_album_id, total_tracks, release_date))
            conn.commit()
            return cursor.lastrowid
        except Exception as e:
            logger.error(f"Error adding retag group: {e}")
            return None

    def add_retag_track(self, group_id: int, track_number: int, disc_number: int,
                        title: str, file_path: str, file_format: str = None,
                        spotify_track_id: str = None, itunes_track_id: str = None) -> Optional[int]:
        """Insert a retag track record and return its ID."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO retag_tracks (group_id, track_number, disc_number, title,
                    file_path, file_format, spotify_track_id, itunes_track_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (group_id, track_number, disc_number, title, file_path,
                  file_format, spotify_track_id, itunes_track_id))
            conn.commit()
            return cursor.lastrowid
        except Exception as e:
            logger.error(f"Error adding retag track: {e}")
            return None

    def get_retag_groups(self) -> List[Dict[str, Any]]:
        """Return all retag groups ordered by artist_name, created_at DESC."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT g.*, COUNT(t.id) as track_count
                FROM retag_groups g
                LEFT JOIN retag_tracks t ON t.group_id = g.id
                GROUP BY g.id
                ORDER BY g.artist_name ASC, g.created_at DESC
            """)
            columns = [desc[0] for desc in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Error getting retag groups: {e}")
            return []

    def get_retag_tracks(self, group_id: int) -> List[Dict[str, Any]]:
        """Return all tracks for a given group_id ordered by disc_number, track_number."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM retag_tracks
                WHERE group_id = ?
                ORDER BY disc_number ASC, track_number ASC
            """, (group_id,))
            columns = [desc[0] for desc in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Error getting retag tracks: {e}")
            return []

    def get_retag_stats(self) -> Dict[str, int]:
        """Return retag statistics: groups, tracks, artists counts."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM retag_groups")
            groups = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM retag_tracks")
            tracks = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(DISTINCT artist_name) FROM retag_groups")
            artists = cursor.fetchone()[0]
            return {"groups": groups, "tracks": tracks, "artists": artists}
        except Exception as e:
            logger.error(f"Error getting retag stats: {e}")
            return {"groups": 0, "tracks": 0, "artists": 0}

    def find_retag_group(self, artist_name: str, album_name: str) -> Optional[int]:
        """Find an existing retag group by artist + album name. Returns group ID or None."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id FROM retag_groups WHERE artist_name = ? AND album_name = ?",
                (artist_name, album_name)
            )
            row = cursor.fetchone()
            return row[0] if row else None
        except Exception as e:
            logger.error(f"Error finding retag group: {e}")
            return None

    def retag_track_exists(self, group_id: int, file_path: str) -> bool:
        """Check if a retag track already exists for a group + file path."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM retag_tracks WHERE group_id = ? AND file_path = ?",
                (group_id, file_path)
            )
            return cursor.fetchone() is not None
        except Exception as e:
            logger.error(f"Error checking retag track existence: {e}")
            return False

    def update_retag_track_path(self, track_id: int, new_file_path: str) -> bool:
        """Update file_path for a retag track after re-tag move."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE retag_tracks SET file_path = ? WHERE id = ?",
                (new_file_path, track_id)
            )
            conn.commit()
            return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error updating retag track path: {e}")
            return False

    def update_retag_group(self, group_id: int, **kwargs) -> bool:
        """Update retag group fields. Accepts keyword args for columns to update."""
        allowed = {'group_type', 'artist_name', 'album_name', 'image_url',
                    'spotify_album_id', 'itunes_album_id', 'total_tracks', 'release_date'}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            values = list(updates.values()) + [group_id]
            cursor.execute(f"UPDATE retag_groups SET {set_clause} WHERE id = ?", values)
            conn.commit()
            return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error updating retag group: {e}")
            return False

    def trim_retag_groups(self, max_groups: int = 100):
        """Remove oldest retag groups if count exceeds max_groups."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM retag_groups")
            count = cursor.fetchone()[0]
            if count <= max_groups:
                return
            excess = count - max_groups
            cursor.execute(
                "SELECT id FROM retag_groups ORDER BY created_at ASC LIMIT ?", (excess,)
            )
            old_ids = [row[0] for row in cursor.fetchall()]
            for gid in old_ids:
                cursor.execute("DELETE FROM retag_tracks WHERE group_id = ?", (gid,))
                cursor.execute("DELETE FROM retag_groups WHERE id = ?", (gid,))
            conn.commit()
            logger.info(f"Trimmed {len(old_ids)} oldest retag groups (cap: {max_groups})")
        except Exception as e:
            logger.error(f"Error trimming retag groups: {e}")

    def delete_retag_group(self, group_id: int) -> bool:
        """Delete a retag group and its tracks (CASCADE)."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            # Manually delete tracks first since SQLite CASCADE requires PRAGMA foreign_keys=ON
            cursor.execute("DELETE FROM retag_tracks WHERE group_id = ?", (group_id,))
            cursor.execute("DELETE FROM retag_groups WHERE id = ?", (group_id,))
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error deleting retag group: {e}")
            return False

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