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

_database_initialized_paths = set()
_database_sidecar_warnings = set()
_database_initialization_lock = threading.Lock()

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
    deezer_artist_id: Optional[str] = None  # Cross-provider support
    discogs_artist_id: Optional[str] = None  # Cross-provider support
    include_albums: bool = True
    include_eps: bool = True
    include_singles: bool = True
    include_live: bool = False
    include_remixes: bool = False
    include_acoustic: bool = False
    include_compilations: bool = False
    include_instrumentals: bool = False
    lookback_days: Optional[int] = None  # Per-artist override; None = use global setting
    profile_id: int = 1

@dataclass
class SimilarArtist:
    """Similar artist recommendation from Spotify/iTunes/Deezer"""
    id: int
    source_artist_id: str  # Watchlist artist's database ID
    similar_artist_spotify_id: Optional[str]  # Spotify artist ID (may be None if iTunes-only)
    similar_artist_itunes_id: Optional[str]  # iTunes artist ID (may be None if Spotify-only)
    similar_artist_name: str
    similarity_rank: int  # 1-10, where 1 is most similar
    occurrence_count: int  # How many watchlist artists share this similar artist
    last_updated: datetime
    image_url: Optional[str] = None  # Cached artist image
    genres: Optional[List[str]] = None  # Cached genres
    popularity: int = 0  # Cached popularity score
    similar_artist_deezer_id: Optional[str] = None  # Deezer artist ID

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
    deezer_track_id: Optional[str]  # Deezer track ID (None if non-Deezer source)
    deezer_album_id: Optional[str]  # Deezer album ID (None if non-Deezer source)
    deezer_artist_id: Optional[str]  # Deezer artist ID (None if non-Deezer source)
    source: str  # 'spotify', 'itunes', or 'deezer'
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
    album_deezer_id: Optional[str]  # Deezer album ID (None if non-Deezer source)
    source: str  # 'spotify', 'itunes', or 'deezer'
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
        self._warn_about_stale_sqlite_sidecars()
        
        # Initialize database once per process for this path
        self._initialize_database_once()

    def _warn_about_stale_sqlite_sidecars(self):
        """Warn if SQLite sidecars are present and the database looks unhealthy."""
        db_key = str(self.database_path.resolve())
        with _database_initialization_lock:
            if db_key in _database_sidecar_warnings:
                return
            _database_sidecar_warnings.add(db_key)

        wal_path = Path(f"{self.database_path}-wal")
        shm_path = Path(f"{self.database_path}-shm")
        existing = [p.name for p in (wal_path, shm_path) if p.exists()]

        if existing:
            check_result = None
            try:
                conn = sqlite3.connect(f"file:{self.database_path}?mode=ro", uri=True, timeout=5.0)
                try:
                    row = conn.execute("PRAGMA quick_check").fetchone()
                    check_result = row[0] if row else None
                finally:
                    conn.close()
            except Exception as e:
                logger.warning(
                    "SQLite sidecar files detected for %s: %s, and database health check could not be run (%s). "
                    "This usually means the previous shutdown was not clean.",
                    self.database_path,
                    ", ".join(existing),
                    e,
                )
                return

            if check_result != "ok":
                logger.warning(
                    "SQLite sidecar files detected for %s: %s, and quick_check returned %r. "
                    "This usually means the previous shutdown was not clean.",
                    self.database_path,
                    ", ".join(existing),
                    check_result,
                )
            else:
                logger.debug(
                    "SQLite sidecar files present for %s (%s) but quick_check returned ok.",
                    self.database_path,
                    ", ".join(existing),
                )

    def _initialize_database_once(self):
        """Run schema setup and migrations once per database path per process."""
        db_key = str(self.database_path.resolve())

        with _database_initialization_lock:
            if db_key in _database_initialized_paths:
                return

            self._initialize_database()
            _database_initialized_paths.add(db_key)
    
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
                    spotify_artist_id TEXT UNIQUE,
                    itunes_artist_id TEXT,
                    deezer_artist_id TEXT,
                    discogs_artist_id TEXT,
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

            # Add per-artist lookback_days column to watchlist_artists (migration)
            self._add_watchlist_lookback_days_column(cursor)

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

            # Add Last.fm and Genius enrichment columns (migration)
            self._add_lastfm_genius_columns(cursor)

            # Add Tidal and Qobuz enrichment columns (migration)
            self._add_tidal_qobuz_enrichment_columns(cursor)

            # Add Discogs enrichment columns (migration)
            self._add_discogs_columns(cursor)

            # Bubble snapshots table for persisting UI state across page refreshes
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS bubble_snapshots (
                    type TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    snapshot_id TEXT NOT NULL
                )
            """)

            # Add last_featured column to similar_artists for hero cycling (migration)
            self._add_similar_artists_last_featured_column(cursor)

            # Retag tool tables for tracking processed downloads (migration)
            self._add_retag_tables(cursor)

            # Multi-profile support (migration)
            self._add_profile_support(cursor)
            self._add_profile_support_v2(cursor)
            self._add_profile_support_v3(cursor)
            self._add_profile_support_v4(cursor)
            self._add_profile_settings(cursor)
            self._add_profile_listenbrainz_support(cursor)
            self._add_profile_service_credentials(cursor)
            self._add_soul_id_columns(cursor)
            self._add_listening_history_table(cursor)

            # Spotify library cache
            self._add_spotify_library_cache_table(cursor)

            # Universal metadata cache (Spotify + iTunes API responses)
            self._add_metadata_cache_tables(cursor)

            # Repair worker v2 tables (findings + job runs)
            self._add_repair_worker_tables(cursor)

            # Mirrored playlists — persistent backup of parsed playlists from any service
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS mirrored_playlists (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    source_playlist_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT,
                    owner TEXT,
                    image_url TEXT,
                    track_count INTEGER DEFAULT 0,
                    profile_id INTEGER DEFAULT 1,
                    mirrored_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(source, source_playlist_id, profile_id)
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS mirrored_playlist_tracks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    playlist_id INTEGER NOT NULL,
                    position INTEGER NOT NULL,
                    track_name TEXT NOT NULL,
                    artist_name TEXT NOT NULL,
                    album_name TEXT DEFAULT '',
                    duration_ms INTEGER DEFAULT 0,
                    image_url TEXT,
                    source_track_id TEXT,
                    extra_data TEXT,
                    FOREIGN KEY (playlist_id) REFERENCES mirrored_playlists(id) ON DELETE CASCADE,
                    UNIQUE(playlist_id, position)
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_mirrored_playlists_profile ON mirrored_playlists (profile_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_mirrored_playlists_source ON mirrored_playlists (source, source_playlist_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_mirrored_tracks_playlist ON mirrored_playlist_tracks (playlist_id)")

            # Automations table — trigger → action scheduled tasks
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS automations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    enabled INTEGER DEFAULT 1,
                    trigger_type TEXT NOT NULL,
                    trigger_config TEXT DEFAULT '{}',
                    action_type TEXT NOT NULL,
                    action_config TEXT DEFAULT '{}',
                    last_run TIMESTAMP,
                    next_run TIMESTAMP,
                    run_count INTEGER DEFAULT 0,
                    last_error TEXT,
                    profile_id INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_automations_profile ON automations (profile_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_automations_enabled ON automations (enabled)")

            # Automation run history table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS automation_run_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    automation_id INTEGER NOT NULL,
                    started_at TIMESTAMP,
                    finished_at TIMESTAMP,
                    duration_seconds REAL,
                    status TEXT NOT NULL,
                    summary TEXT,
                    result_json TEXT,
                    log_lines TEXT,
                    FOREIGN KEY (automation_id) REFERENCES automations(id) ON DELETE CASCADE
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_arh_automation_id ON automation_run_history(automation_id)")

            # Add explored_at to mirrored_playlists (migration)
            self._add_mirrored_playlist_explored_column(cursor)

            # Add notification columns to automations (migration)
            self._add_automation_notify_columns(cursor)
            self._add_automation_system_column(cursor)
            self._add_automation_then_actions_column(cursor)
            self._add_automation_group_name_column(cursor)

            # Library issues — user-reported problems with tracks/albums/artists
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS library_issues (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile_id INTEGER NOT NULL DEFAULT 1,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    category TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT,
                    snapshot_data TEXT DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'open',
                    priority TEXT NOT NULL DEFAULT 'normal',
                    admin_response TEXT,
                    resolved_by INTEGER,
                    resolved_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (profile_id) REFERENCES profiles (id) ON DELETE CASCADE
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_library_issues_profile ON library_issues (profile_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_library_issues_status ON library_issues (status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_library_issues_entity ON library_issues (entity_type, entity_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_library_issues_created ON library_issues (created_at)")

            # Library history — persistent log of downloads and server imports
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS library_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    artist_name TEXT,
                    album_name TEXT,
                    quality TEXT,
                    server_source TEXT,
                    file_path TEXT,
                    thumb_url TEXT,
                    download_source TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_lh_event_type ON library_history (event_type)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_lh_created_at ON library_history (created_at DESC)")

            # Migration: add download_source column
            cursor.execute("PRAGMA table_info(library_history)")
            lh_cols = {c[1] for c in cursor.fetchall()}
            if 'download_source' not in lh_cols:
                cursor.execute("ALTER TABLE library_history ADD COLUMN download_source TEXT")
                logger.info("Added download_source column to library_history")
            for _col in ['source_track_id', 'source_track_title', 'source_filename', 'acoustid_result', 'source_artist']:
                if _col not in lh_cols:
                    cursor.execute(f"ALTER TABLE library_history ADD COLUMN {_col} TEXT")
                    logger.info(f"Added {_col} column to library_history")

            # Sync history table — tracks the last 100 sync operations with cached context for re-trigger
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sync_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    batch_id TEXT NOT NULL,
                    playlist_id TEXT,
                    playlist_name TEXT NOT NULL,
                    source TEXT NOT NULL,
                    sync_type TEXT NOT NULL,
                    artist_context TEXT,
                    album_context TEXT,
                    tracks_json TEXT NOT NULL,
                    total_tracks INTEGER DEFAULT 0,
                    tracks_found INTEGER DEFAULT 0,
                    tracks_downloaded INTEGER DEFAULT 0,
                    tracks_failed INTEGER DEFAULT 0,
                    thumb_url TEXT,
                    is_album_download INTEGER DEFAULT 0,
                    playlist_folder_mode INTEGER DEFAULT 0,
                    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    completed_at TIMESTAMP,
                    track_results TEXT
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_sh_started_at ON sync_history (started_at DESC)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_sh_source ON sync_history (source)")

            # Migration: add track_results column to existing sync_history tables
            try:
                cursor.execute("SELECT track_results FROM sync_history LIMIT 1")
            except Exception:
                try:
                    cursor.execute("ALTER TABLE sync_history ADD COLUMN track_results TEXT")
                    logger.info("Added track_results column to sync_history table")
                except Exception:
                    pass

            # Migration: add track_artist column for per-track artist on compilations/DJ mixes
            try:
                cursor.execute("SELECT track_artist FROM tracks LIMIT 1")
            except Exception:
                try:
                    cursor.execute("ALTER TABLE tracks ADD COLUMN track_artist TEXT")
                    logger.info("Added track_artist column to tracks table")
                except Exception:
                    pass

            # One-time migration: purge discovery cache entries that lack track_number.
            # Prior versions cached discovery results without track_number/disc_number/release_date,
            # causing incorrect file organization (all tracks as "01", missing album year).
            # Purged entries get re-populated with complete data on next discovery.
            try:
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='_discovery_cache_v2_migrated'")
                if not cursor.fetchone():
                    cursor.execute("DELETE FROM discovery_match_cache WHERE id IN ("
                                   "SELECT id FROM discovery_match_cache WHERE "
                                   "matched_data_json NOT LIKE '%track_number%')")
                    purged = cursor.rowcount
                    cursor.execute("CREATE TABLE _discovery_cache_v2_migrated (applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
                    if purged > 0:
                        logger.info(f"Purged {purged} stale discovery cache entries (missing track_number)")
            except Exception:
                pass

            # One-time migration: purge Deezer album/track cache entries with missing data.
            # Deezer's /artist/{id}/albums returns albums without artist info, and search
            # results cache tracks without track_position — both produce bad metadata.
            try:
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='_deezer_cache_v2_migrated'")
                if not cursor.fetchone():
                    cursor.execute("""DELETE FROM metadata_cache_entities
                                     WHERE source = 'deezer' AND entity_type IN ('album', 'track')""")
                    purged = cursor.rowcount
                    cursor.execute("""DELETE FROM metadata_cache_searches
                                     WHERE source = 'deezer' AND search_type IN ('album', 'track')""")
                    cursor.execute("CREATE TABLE _deezer_cache_v2_migrated (applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
                    if purged > 0:
                        logger.info(f"Purged {purged} stale Deezer cache entries (missing artist/track_position)")
            except Exception:
                pass

            # One-time migration: purge cached tracks/albums with junk artist names.
            # The cache gate now rejects these, but existing entries need cleaning.
            try:
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='_cache_junk_artist_purged'")
                if not cursor.fetchone():
                    cursor.execute("""DELETE FROM metadata_cache_entities
                                     WHERE entity_type IN ('track', 'album')
                                       AND (artist_name IS NULL
                                         OR TRIM(artist_name) = ''
                                         OR LOWER(TRIM(artist_name)) IN ('unknown', 'unknown artist', 'none', 'null'))""")
                    purged = cursor.rowcount
                    cursor.execute("CREATE TABLE _cache_junk_artist_purged (applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
                    if purged > 0:
                        logger.info(f"Purged {purged} cached tracks/albums with junk artist names")
            except Exception:
                pass

            conn.commit()
            logger.info("Database initialized successfully")

        except Exception as e:
            logger.error(f"Error initializing database: {e}")
            raise
    
    def _add_mirrored_playlist_explored_column(self, cursor):
        """Add explored_at column to mirrored_playlists to persist explore badge."""
        try:
            cursor.execute("PRAGMA table_info(mirrored_playlists)")
            cols = [c[1] for c in cursor.fetchall()]
            if 'explored_at' not in cols:
                cursor.execute("ALTER TABLE mirrored_playlists ADD COLUMN explored_at TIMESTAMP DEFAULT NULL")
                logger.info("Added explored_at column to mirrored_playlists table")
        except Exception as e:
            logger.error(f"Error adding explored_at column to mirrored_playlists: {e}")

    def _add_automation_notify_columns(self, cursor):
        """Add notification and result columns to automations table."""
        try:
            cursor.execute("PRAGMA table_info(automations)")
            cols = [c[1] for c in cursor.fetchall()]
            for col, typedef in [('notify_type', 'TEXT DEFAULT NULL'), ('notify_config', "TEXT DEFAULT '{}'"), ('last_result', 'TEXT DEFAULT NULL')]:
                if col not in cols:
                    cursor.execute(f"ALTER TABLE automations ADD COLUMN {col} {typedef}")
                    logger.info(f"Added {col} column to automations table")
        except Exception as e:
            logger.error(f"Error adding automation notify columns: {e}")

    def _add_automation_system_column(self, cursor):
        """Add is_system column to automations table for non-deletable system automations."""
        try:
            cursor.execute("PRAGMA table_info(automations)")
            cols = [c[1] for c in cursor.fetchall()]
            if 'is_system' not in cols:
                cursor.execute("ALTER TABLE automations ADD COLUMN is_system INTEGER DEFAULT 0")
                logger.info("Added is_system column to automations table")
        except Exception as e:
            logger.error(f"Error adding automation system column: {e}")

    def _add_automation_group_name_column(self, cursor):
        """Add group_name column to automations table for folder-style grouping."""
        try:
            cursor.execute("PRAGMA table_info(automations)")
            cols = [c[1] for c in cursor.fetchall()]
            if 'group_name' not in cols:
                cursor.execute("ALTER TABLE automations ADD COLUMN group_name TEXT DEFAULT NULL")
                logger.info("Added group_name column to automations table")
        except Exception as e:
            logger.error(f"Error adding automation group_name column: {e}")

    def _add_automation_then_actions_column(self, cursor):
        """Add then_actions column to automations table and migrate existing notify data."""
        try:
            cursor.execute("PRAGMA table_info(automations)")
            cols = [c[1] for c in cursor.fetchall()]
            if 'then_actions' not in cols:
                cursor.execute("ALTER TABLE automations ADD COLUMN then_actions TEXT DEFAULT '[]'")
                logger.info("Added then_actions column to automations table")
                # Migrate existing notify_type/notify_config into then_actions
                cursor.execute("SELECT id, notify_type, notify_config FROM automations WHERE notify_type IS NOT NULL AND notify_type != ''")
                for row in cursor.fetchall():
                    try:
                        config = json.loads(row[2]) if row[2] else {}
                        then_actions = json.dumps([{'type': row[1], 'config': config}])
                        cursor.execute("UPDATE automations SET then_actions = ? WHERE id = ?", (then_actions, row[0]))
                    except Exception:
                        pass
                logger.info("Migrated existing notify data to then_actions")
        except Exception as e:
            logger.error(f"Error adding automation then_actions column: {e}")

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

            if 'source' not in discovery_pool_columns:
                cursor.execute("ALTER TABLE discovery_pool ADD COLUMN source TEXT DEFAULT 'spotify'")
                logger.info("Added source column to discovery_pool table")

            # Migration: Add iTunes columns to discovery_pool for dual-source discovery
            if 'itunes_track_id' not in discovery_pool_columns:
                cursor.execute("ALTER TABLE discovery_pool ADD COLUMN itunes_track_id TEXT")
                cursor.execute("ALTER TABLE discovery_pool ADD COLUMN itunes_album_id TEXT")
                cursor.execute("ALTER TABLE discovery_pool ADD COLUMN itunes_artist_id TEXT")
                logger.info("Added iTunes columns to discovery_pool table for dual-source discovery")

            # Migration: Add Deezer columns to discovery_pool for tri-source discovery
            if 'deezer_track_id' not in discovery_pool_columns:
                cursor.execute("ALTER TABLE discovery_pool ADD COLUMN deezer_track_id TEXT")
                cursor.execute("ALTER TABLE discovery_pool ADD COLUMN deezer_album_id TEXT")
                cursor.execute("ALTER TABLE discovery_pool ADD COLUMN deezer_artist_id TEXT")
                logger.info("Added Deezer columns to discovery_pool table")

            # Migration: Add iTunes ID to similar_artists for dual-source discovery
            cursor.execute("PRAGMA table_info(similar_artists)")
            similar_artists_columns = [column[1] for column in cursor.fetchall()]

            if 'similar_artist_itunes_id' not in similar_artists_columns:
                cursor.execute("ALTER TABLE similar_artists ADD COLUMN similar_artist_itunes_id TEXT")
                logger.info("Added similar_artist_itunes_id column to similar_artists table")

            if 'similar_artist_deezer_id' not in similar_artists_columns:
                cursor.execute("ALTER TABLE similar_artists ADD COLUMN similar_artist_deezer_id TEXT")
                logger.info("Added similar_artist_deezer_id column to similar_artists table")

            # Migration: Add iTunes columns to recent_releases for dual-source discovery
            cursor.execute("PRAGMA table_info(recent_releases)")
            recent_releases_columns = [column[1] for column in cursor.fetchall()]

            if 'source' not in recent_releases_columns:
                cursor.execute("ALTER TABLE recent_releases ADD COLUMN source TEXT DEFAULT 'spotify'")
                logger.info("Added source column to recent_releases table")

            if 'album_itunes_id' not in recent_releases_columns:
                cursor.execute("ALTER TABLE recent_releases ADD COLUMN album_itunes_id TEXT")
                logger.info("Added iTunes columns to recent_releases table for dual-source discovery")

            # Migration: Add Deezer column to recent_releases for tri-source discovery
            if 'album_deezer_id' not in recent_releases_columns:
                cursor.execute("ALTER TABLE recent_releases ADD COLUMN album_deezer_id TEXT")
                logger.info("Added album_deezer_id column to recent_releases table")

            # Migration: Add iTunes columns to discovery_recent_albums for dual-source discovery
            cursor.execute("PRAGMA table_info(discovery_recent_albums)")
            discovery_recent_albums_columns = [column[1] for column in cursor.fetchall()]

            if 'source' not in discovery_recent_albums_columns:
                cursor.execute("ALTER TABLE discovery_recent_albums ADD COLUMN source TEXT DEFAULT 'spotify'")
                logger.info("Added source column to discovery_recent_albums table")

            if 'album_itunes_id' not in discovery_recent_albums_columns:
                cursor.execute("ALTER TABLE discovery_recent_albums ADD COLUMN album_itunes_id TEXT")
                cursor.execute("ALTER TABLE discovery_recent_albums ADD COLUMN artist_itunes_id TEXT")
                logger.info("Added iTunes columns to discovery_recent_albums table for dual-source discovery")

            # Migration: Add Deezer columns to discovery_recent_albums for tri-source discovery
            if 'album_deezer_id' not in discovery_recent_albums_columns:
                cursor.execute("ALTER TABLE discovery_recent_albums ADD COLUMN album_deezer_id TEXT")
                cursor.execute("ALTER TABLE discovery_recent_albums ADD COLUMN artist_deezer_id TEXT")
                logger.info("Added Deezer columns to discovery_recent_albums table")

            # Migration: Fix NOT NULL constraint on album_spotify_id (required for iTunes-only albums)
            # Check if album_spotify_id has NOT NULL constraint by checking table schema
            cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='discovery_recent_albums'")
            table_schema = cursor.fetchone()
            if table_schema and 'album_spotify_id TEXT NOT NULL' in (table_schema[0] or ''):
                logger.info("Migrating discovery_recent_albums to allow NULL album_spotify_id for iTunes support...")
                # SQLite doesn't support ALTER COLUMN, so recreate table
                cursor.execute("PRAGMA table_info(discovery_recent_albums)")
                old_cols_info = cursor.fetchall()
                old_col_names = [c[1] for c in old_cols_info]
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS discovery_recent_albums_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        album_spotify_id TEXT,
                        album_itunes_id TEXT,
                        album_deezer_id TEXT,
                        artist_spotify_id TEXT,
                        artist_itunes_id TEXT,
                        artist_deezer_id TEXT,
                        source TEXT NOT NULL DEFAULT 'spotify',
                        album_name TEXT NOT NULL,
                        artist_name TEXT NOT NULL,
                        album_cover_url TEXT,
                        release_date TEXT NOT NULL,
                        album_type TEXT DEFAULT 'album',
                        cached_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(album_spotify_id, album_itunes_id, album_deezer_id, source)
                    )
                """)
                new_cols = ['id', 'album_spotify_id', 'album_itunes_id', 'album_deezer_id',
                            'artist_spotify_id', 'artist_itunes_id', 'artist_deezer_id',
                            'source', 'album_name', 'artist_name', 'album_cover_url',
                            'release_date', 'album_type', 'cached_date']
                shared_cols = [c for c in new_cols if c in old_col_names]
                cols_str = ', '.join(shared_cols)
                cursor.execute(f"INSERT OR IGNORE INTO discovery_recent_albums_new ({cols_str}) SELECT {cols_str} FROM discovery_recent_albums")
                cursor.execute("DROP TABLE discovery_recent_albums")
                cursor.execute("ALTER TABLE discovery_recent_albums_new RENAME TO discovery_recent_albums")
                conn.commit()
                logger.info("Successfully migrated discovery_recent_albums table for iTunes support")

            # Migration: Add UNIQUE constraint to similar_artists table
            # Skip if table already has profile-scoped UNIQUE constraint (from v3 migration)
            cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='similar_artists'")
            sa_create_sql = cursor.fetchone()
            has_profile_unique = sa_create_sql and 'UNIQUE(profile_id' in (sa_create_sql[0] or '')

            if not has_profile_unique:
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
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_discovery_pool_deezer_track ON discovery_pool (deezer_track_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_discovery_pool_deezer_artist ON discovery_pool (deezer_artist_id)")
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

            # Sync match cache — caches server track ID for discovered Spotify tracks
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sync_match_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    spotify_track_id TEXT NOT NULL,
                    normalized_title TEXT NOT NULL,
                    normalized_artist TEXT NOT NULL,
                    server_source TEXT NOT NULL,
                    server_track_id INTEGER NOT NULL,
                    server_track_title TEXT,
                    confidence REAL NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    use_count INTEGER DEFAULT 1,
                    UNIQUE(spotify_track_id, server_source)
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_sync_cache_lookup ON sync_match_cache (spotify_track_id, server_source)")

            # Download blacklist — tracks users have rejected as wrong matches
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS download_blacklist (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    track_title TEXT,
                    track_artist TEXT,
                    blocked_filename TEXT,
                    blocked_username TEXT,
                    reason TEXT DEFAULT 'user_rejected',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(blocked_username, blocked_filename)
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_blacklist_user_file ON download_blacklist (blocked_username, blocked_filename)")

            # Track download provenance — where each library track came from
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS track_downloads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    track_id TEXT,
                    file_path TEXT,
                    source_service TEXT NOT NULL,
                    source_username TEXT,
                    source_filename TEXT,
                    source_size INTEGER,
                    audio_quality TEXT,
                    track_title TEXT,
                    track_artist TEXT,
                    track_album TEXT,
                    status TEXT DEFAULT 'completed',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_td_track_id ON track_downloads (track_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_td_file_path ON track_downloads (file_path)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_td_source ON track_downloads (source_username, source_filename)")

            # Migration: Add audio detail columns to track_downloads
            cursor.execute("PRAGMA table_info(track_downloads)")
            td_columns = [c[1] for c in cursor.fetchall()]
            if 'bit_depth' not in td_columns:
                cursor.execute("ALTER TABLE track_downloads ADD COLUMN bit_depth INTEGER")
                cursor.execute("ALTER TABLE track_downloads ADD COLUMN sample_rate INTEGER")
                cursor.execute("ALTER TABLE track_downloads ADD COLUMN bitrate INTEGER")
                logger.info("Added audio detail columns (bit_depth, sample_rate, bitrate) to track_downloads")

            # Discovery artist blacklist — artists users never want to see in discovery
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS discovery_artist_blacklist (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    artist_name TEXT NOT NULL COLLATE NOCASE,
                    spotify_artist_id TEXT,
                    itunes_artist_id TEXT,
                    deezer_artist_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(artist_name)
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_dab_name ON discovery_artist_blacklist (artist_name COLLATE NOCASE)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_dab_spotify ON discovery_artist_blacklist (spotify_artist_id)")

            # Liked artists pool — aggregated followed/liked artists from connected services
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS liked_artists_pool (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    artist_name TEXT NOT NULL,
                    normalized_name TEXT NOT NULL,
                    spotify_artist_id TEXT,
                    itunes_artist_id TEXT,
                    deezer_artist_id TEXT,
                    discogs_artist_id TEXT,
                    image_url TEXT,
                    genres TEXT,
                    source_services TEXT DEFAULT '[]',
                    active_source_id TEXT,
                    active_source TEXT,
                    match_status TEXT DEFAULT 'pending',
                    on_watchlist INTEGER DEFAULT 0,
                    profile_id INTEGER DEFAULT 1,
                    last_fetched_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(profile_id, normalized_name)
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_lap_profile ON liked_artists_pool (profile_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_lap_status ON liked_artists_pool (profile_id, match_status)")

            # Liked albums pool — aggregated saved/liked albums from connected services
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS liked_albums_pool (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    album_name TEXT NOT NULL,
                    artist_name TEXT NOT NULL,
                    normalized_key TEXT NOT NULL,
                    spotify_album_id TEXT,
                    tidal_album_id TEXT,
                    deezer_album_id TEXT,
                    image_url TEXT,
                    release_date TEXT,
                    total_tracks INTEGER DEFAULT 0,
                    source_services TEXT DEFAULT '[]',
                    profile_id INTEGER DEFAULT 1,
                    last_fetched_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(profile_id, normalized_key)
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_lalp_profile ON liked_albums_pool (profile_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_lalp_spotify ON liked_albums_pool (spotify_album_id)")

            logger.info("Discovery tables added/verified successfully")

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
                'include_compilations': ('INTEGER', '0'),   # 0 = False (exclude compilations by default)
                'include_instrumentals': ('INTEGER', '0')   # 0 = False (exclude instrumentals by default)
            }

            for column_name, (column_type, default_value) in columns_to_add.items():
                if column_name not in columns:
                    cursor.execute(f"ALTER TABLE watchlist_artists ADD COLUMN {column_name} {column_type} DEFAULT {default_value}")
                    logger.info(f"Added {column_name} column to watchlist_artists table")

        except Exception as e:
            logger.error(f"Error adding content type filter columns to watchlist_artists: {e}")
            # Don't raise - this is a migration, database can still function

    def _add_watchlist_lookback_days_column(self, cursor):
        """Add per-artist lookback_days column to watchlist_artists table"""
        try:
            cursor.execute("PRAGMA table_info(watchlist_artists)")
            columns = [column[1] for column in cursor.fetchall()]
            if 'lookback_days' not in columns:
                cursor.execute("ALTER TABLE watchlist_artists ADD COLUMN lookback_days INTEGER DEFAULT NULL")
                logger.info("Added lookback_days column to watchlist_artists table")
        except Exception as e:
            logger.error(f"Error adding lookback_days column to watchlist_artists: {e}")

    def _add_watchlist_itunes_id_column(self, cursor):
        """Add iTunes artist ID column to watchlist_artists table for cross-provider support"""
        try:
            cursor.execute("PRAGMA table_info(watchlist_artists)")
            columns = [column[1] for column in cursor.fetchall()]

            if 'itunes_artist_id' not in columns:
                cursor.execute("ALTER TABLE watchlist_artists ADD COLUMN itunes_artist_id TEXT")
                logger.info("Added itunes_artist_id column to watchlist_artists table for cross-provider support")

            if 'deezer_artist_id' not in columns:
                cursor.execute("ALTER TABLE watchlist_artists ADD COLUMN deezer_artist_id TEXT")
                logger.info("Added deezer_artist_id column to watchlist_artists table for cross-provider support")

            if 'discogs_artist_id' not in columns:
                cursor.execute("ALTER TABLE watchlist_artists ADD COLUMN discogs_artist_id TEXT")
                logger.info("Added discogs_artist_id column to watchlist_artists table for cross-provider support")

        except Exception as e:
            logger.error(f"Error adding itunes_artist_id column to watchlist_artists: {e}")
            # Don't raise - this is a migration, database can still function

    def _add_similar_artists_last_featured_column(self, cursor):
        """Add last_featured column to similar_artists for hero slider cycling"""
        try:
            cursor.execute("PRAGMA table_info(similar_artists)")
            columns = [column[1] for column in cursor.fetchall()]

            if 'last_featured' not in columns:
                cursor.execute("ALTER TABLE similar_artists ADD COLUMN last_featured TIMESTAMP")
                logger.info("Added last_featured column to similar_artists table for hero cycling")

            # Migration: Add cached metadata columns to avoid API calls on every page load
            if 'image_url' not in columns:
                cursor.execute("ALTER TABLE similar_artists ADD COLUMN image_url TEXT")
                cursor.execute("ALTER TABLE similar_artists ADD COLUMN genres TEXT")
                cursor.execute("ALTER TABLE similar_artists ADD COLUMN popularity INTEGER DEFAULT 0")
                cursor.execute("ALTER TABLE similar_artists ADD COLUMN metadata_updated_at TIMESTAMP")
                logger.info("Added image_url, genres, popularity, metadata_updated_at columns to similar_artists for hero caching")

        except Exception as e:
            logger.error(f"Error adding columns to similar_artists: {e}")
            # Don't raise - this is a migration, database can still function

    def _fix_watchlist_spotify_id_nullable(self, cursor):
        """
        Make spotify_artist_id nullable in watchlist_artists table.
        This allows adding iTunes-only artists without Spotify IDs.

        Since SQLite doesn't support modifying column constraints directly,
        we need to recreate the table if the constraint needs to be changed.
        """
        try:
            # Check if spotify_artist_id is currently NOT NULL using PRAGMA
            # (more reliable than string-matching the CREATE TABLE SQL)
            cursor.execute("PRAGMA table_info(watchlist_artists)")
            columns = {col[1]: col for col in cursor.fetchall()}
            spotify_col = columns.get('spotify_artist_id')

            # notnull flag is index 3 in PRAGMA table_info
            has_not_null = spotify_col and spotify_col[3] == 1

            if has_not_null:
                logger.info("Migrating watchlist_artists table to make spotify_artist_id nullable...")

                # Check if old table already has profile_id (from profile migration)
                old_has_profile = 'profile_id' in columns

                # Drop leftover temp table from any previous failed migration
                cursor.execute("DROP TABLE IF EXISTS watchlist_artists_new")

                # Create new table with nullable spotify_artist_id
                # Include profile_id + composite UNIQUE if old table had profile support
                if old_has_profile:
                    cursor.execute("""
                        CREATE TABLE watchlist_artists_new (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            spotify_artist_id TEXT,
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
                            include_instrumentals INTEGER DEFAULT 0,
                            lookback_days INTEGER DEFAULT NULL,
                            itunes_artist_id TEXT,
                            deezer_artist_id TEXT,
                            discogs_artist_id TEXT,
                            profile_id INTEGER DEFAULT 1,
                            UNIQUE(profile_id, spotify_artist_id),
                            UNIQUE(profile_id, itunes_artist_id)
                        )
                    """)
                else:
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
                            include_instrumentals INTEGER DEFAULT 0,
                            lookback_days INTEGER DEFAULT NULL,
                            itunes_artist_id TEXT,
                            deezer_artist_id TEXT,
                            discogs_artist_id TEXT
                        )
                    """)

                # Copy data from old table (only columns that exist in both)
                cursor.execute("PRAGMA table_info(watchlist_artists)")
                old_cols = [col[1] for col in cursor.fetchall()]
                new_cols = ['id', 'spotify_artist_id', 'artist_name', 'date_added',
                            'last_scan_timestamp', 'created_at', 'updated_at', 'image_url',
                            'include_albums', 'include_eps', 'include_singles', 'include_live',
                            'include_remixes', 'include_acoustic', 'include_compilations',
                            'include_instrumentals', 'lookback_days',
                            'itunes_artist_id', 'deezer_artist_id', 'discogs_artist_id', 'profile_id']
                shared_cols = [c for c in new_cols if c in old_cols]
                cols_str = ', '.join(shared_cols)
                cursor.execute(f"INSERT INTO watchlist_artists_new ({cols_str}) SELECT {cols_str} FROM watchlist_artists")

                # Drop old table
                cursor.execute("DROP TABLE watchlist_artists")

                # Rename new table
                cursor.execute("ALTER TABLE watchlist_artists_new RENAME TO watchlist_artists")

                # Recreate indexes
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_watchlist_spotify_id ON watchlist_artists (spotify_artist_id)")
                if old_has_profile:
                    cursor.execute("CREATE INDEX IF NOT EXISTS idx_watchlist_profile ON watchlist_artists (profile_id)")
                
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
            # --- Artists ---
            cursor.execute("PRAGMA table_info(artists)")
            artists_columns = [column[1] for column in cursor.fetchall()]

            if 'musicbrainz_id' not in artists_columns:
                cursor.execute("ALTER TABLE artists ADD COLUMN musicbrainz_id TEXT")
                columns_added = True
            if 'musicbrainz_last_attempted' not in artists_columns:
                cursor.execute("ALTER TABLE artists ADD COLUMN musicbrainz_last_attempted TIMESTAMP")
                columns_added = True
            if 'musicbrainz_match_status' not in artists_columns:
                cursor.execute("ALTER TABLE artists ADD COLUMN musicbrainz_match_status TEXT")
                columns_added = True
            if columns_added:
                logger.info("Added MusicBrainz columns to artists table")

            # --- Albums ---
            cursor.execute("PRAGMA table_info(albums)")
            albums_columns = [column[1] for column in cursor.fetchall()]

            added_albums = False
            if 'musicbrainz_release_id' not in albums_columns:
                cursor.execute("ALTER TABLE albums ADD COLUMN musicbrainz_release_id TEXT")
                added_albums = True
            if 'musicbrainz_last_attempted' not in albums_columns:
                cursor.execute("ALTER TABLE albums ADD COLUMN musicbrainz_last_attempted TIMESTAMP")
                added_albums = True
            if 'musicbrainz_match_status' not in albums_columns:
                cursor.execute("ALTER TABLE albums ADD COLUMN musicbrainz_match_status TEXT")
                added_albums = True
            if added_albums:
                columns_added = True
                logger.info("Added MusicBrainz columns to albums table")

            # --- Tracks ---
            cursor.execute("PRAGMA table_info(tracks)")
            tracks_columns = [column[1] for column in cursor.fetchall()]

            added_tracks = False
            if 'musicbrainz_recording_id' not in tracks_columns:
                cursor.execute("ALTER TABLE tracks ADD COLUMN musicbrainz_recording_id TEXT")
                added_tracks = True
            if 'musicbrainz_last_attempted' not in tracks_columns:
                cursor.execute("ALTER TABLE tracks ADD COLUMN musicbrainz_last_attempted TIMESTAMP")
                added_tracks = True
            if 'musicbrainz_match_status' not in tracks_columns:
                cursor.execute("ALTER TABLE tracks ADD COLUMN musicbrainz_match_status TEXT")
                added_tracks = True
            if added_tracks:
                columns_added = True
                logger.info("Added MusicBrainz columns to tracks table")
            
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
            
            # Create indexes (safe even if columns were already present)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_artists_mbid ON artists (musicbrainz_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_artists_mb_status ON artists (musicbrainz_match_status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_albums_mbid ON albums (musicbrainz_release_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_albums_mb_status ON albums (musicbrainz_match_status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tracks_mbid ON tracks (musicbrainz_recording_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tracks_mb_status ON tracks (musicbrainz_match_status)")
            
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_mb_cache_entity ON musicbrainz_cache (entity_type, entity_name)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_mb_cache_mbid ON musicbrainz_cache (musicbrainz_id)")
            # Partial index for failed lookups — speeds up the management modal queries
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_mb_cache_failed ON musicbrainz_cache (entity_type, last_updated) WHERE musicbrainz_id IS NULL")
            
            if columns_added:
                logger.info("MusicBrainz migration completed successfully")
            
        except Exception as e:
            logger.error(f"Error in MusicBrainz migration: {e}")
            # Don't raise - this is a migration, database can still function

    def _add_external_id_columns(self, cursor):
        """Add Spotify/iTunes external ID columns to library tables for enrichment"""
        try:
            # --- Artists ---
            cursor.execute("PRAGMA table_info(artists)")
            artists_columns = [column[1] for column in cursor.fetchall()]

            if 'spotify_artist_id' not in artists_columns:
                cursor.execute("ALTER TABLE artists ADD COLUMN spotify_artist_id TEXT")
            if 'itunes_artist_id' not in artists_columns:
                cursor.execute("ALTER TABLE artists ADD COLUMN itunes_artist_id TEXT")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_artists_spotify_id ON artists (spotify_artist_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_artists_itunes_id ON artists (itunes_artist_id)")

            # --- Albums ---
            cursor.execute("PRAGMA table_info(albums)")
            albums_columns = [column[1] for column in cursor.fetchall()]

            if 'spotify_album_id' not in albums_columns:
                cursor.execute("ALTER TABLE albums ADD COLUMN spotify_album_id TEXT")
            if 'itunes_album_id' not in albums_columns:
                cursor.execute("ALTER TABLE albums ADD COLUMN itunes_album_id TEXT")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_albums_spotify_id ON albums (spotify_album_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_albums_itunes_id ON albums (itunes_album_id)")

            # --- Tracks ---
            cursor.execute("PRAGMA table_info(tracks)")
            tracks_columns = [column[1] for column in cursor.fetchall()]

            if 'spotify_track_id' not in tracks_columns:
                cursor.execute("ALTER TABLE tracks ADD COLUMN spotify_track_id TEXT")
            if 'itunes_track_id' not in tracks_columns:
                cursor.execute("ALTER TABLE tracks ADD COLUMN itunes_track_id TEXT")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tracks_spotify_id ON tracks (spotify_track_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tracks_itunes_id ON tracks (itunes_track_id)")

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

    def _add_discogs_columns(self, cursor):
        """Add Discogs enrichment columns to artists and albums tables."""
        try:
            # --- Artists ---
            cursor.execute("PRAGMA table_info(artists)")
            artists_columns = [column[1] for column in cursor.fetchall()]

            for col in ['discogs_id', 'discogs_match_status', 'discogs_bio', 'discogs_members', 'discogs_urls']:
                if col not in artists_columns:
                    col_type = 'TIMESTAMP' if col.endswith('_attempted') else 'TEXT'
                    cursor.execute(f"ALTER TABLE artists ADD COLUMN {col} {col_type}")
            if 'discogs_last_attempted' not in artists_columns:
                cursor.execute("ALTER TABLE artists ADD COLUMN discogs_last_attempted TIMESTAMP")

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_artists_discogs_id ON artists (discogs_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_artists_discogs_status ON artists (discogs_match_status)")

            # --- Albums ---
            cursor.execute("PRAGMA table_info(albums)")
            albums_columns = [column[1] for column in cursor.fetchall()]

            for col in ['discogs_id', 'discogs_match_status', 'discogs_genres', 'discogs_styles',
                         'discogs_label', 'discogs_catno', 'discogs_country']:
                if col not in albums_columns:
                    cursor.execute(f"ALTER TABLE albums ADD COLUMN {col} TEXT")
            if 'discogs_last_attempted' not in albums_columns:
                cursor.execute("ALTER TABLE albums ADD COLUMN discogs_last_attempted TIMESTAMP")
            if 'discogs_rating' not in albums_columns:
                cursor.execute("ALTER TABLE albums ADD COLUMN discogs_rating REAL")
            if 'discogs_rating_count' not in albums_columns:
                cursor.execute("ALTER TABLE albums ADD COLUMN discogs_rating_count INTEGER")

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_albums_discogs_id ON albums (discogs_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_albums_discogs_status ON albums (discogs_match_status)")

            logger.info("Discogs enrichment columns added/verified successfully")

        except Exception as e:
            logger.error(f"Error adding Discogs columns: {e}")

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

    def _add_lastfm_genius_columns(self, cursor):
        """Add Last.fm and Genius enrichment tracking + metadata columns to artists, albums, tracks"""
        try:
            # --- Artists ---
            cursor.execute("PRAGMA table_info(artists)")
            artists_columns = [column[1] for column in cursor.fetchall()]

            # Last.fm columns
            if 'lastfm_match_status' not in artists_columns:
                cursor.execute("ALTER TABLE artists ADD COLUMN lastfm_match_status TEXT")
            if 'lastfm_last_attempted' not in artists_columns:
                cursor.execute("ALTER TABLE artists ADD COLUMN lastfm_last_attempted TIMESTAMP")
            if 'lastfm_listeners' not in artists_columns:
                cursor.execute("ALTER TABLE artists ADD COLUMN lastfm_listeners INTEGER")
            if 'lastfm_playcount' not in artists_columns:
                cursor.execute("ALTER TABLE artists ADD COLUMN lastfm_playcount INTEGER")
            if 'lastfm_tags' not in artists_columns:
                cursor.execute("ALTER TABLE artists ADD COLUMN lastfm_tags TEXT")
            if 'lastfm_similar' not in artists_columns:
                cursor.execute("ALTER TABLE artists ADD COLUMN lastfm_similar TEXT")
            if 'lastfm_bio' not in artists_columns:
                cursor.execute("ALTER TABLE artists ADD COLUMN lastfm_bio TEXT")
            if 'lastfm_url' not in artists_columns:
                cursor.execute("ALTER TABLE artists ADD COLUMN lastfm_url TEXT")

            # Genius columns
            if 'genius_id' not in artists_columns:
                cursor.execute("ALTER TABLE artists ADD COLUMN genius_id TEXT")
            if 'genius_match_status' not in artists_columns:
                cursor.execute("ALTER TABLE artists ADD COLUMN genius_match_status TEXT")
            if 'genius_last_attempted' not in artists_columns:
                cursor.execute("ALTER TABLE artists ADD COLUMN genius_last_attempted TIMESTAMP")
            if 'genius_description' not in artists_columns:
                cursor.execute("ALTER TABLE artists ADD COLUMN genius_description TEXT")
            if 'genius_alt_names' not in artists_columns:
                cursor.execute("ALTER TABLE artists ADD COLUMN genius_alt_names TEXT")
            if 'genius_url' not in artists_columns:
                cursor.execute("ALTER TABLE artists ADD COLUMN genius_url TEXT")

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_artists_lastfm_status ON artists (lastfm_match_status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_artists_genius_id ON artists (genius_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_artists_genius_status ON artists (genius_match_status)")

            # --- Albums ---
            cursor.execute("PRAGMA table_info(albums)")
            albums_columns = [column[1] for column in cursor.fetchall()]

            # Last.fm columns
            if 'lastfm_match_status' not in albums_columns:
                cursor.execute("ALTER TABLE albums ADD COLUMN lastfm_match_status TEXT")
            if 'lastfm_last_attempted' not in albums_columns:
                cursor.execute("ALTER TABLE albums ADD COLUMN lastfm_last_attempted TIMESTAMP")
            if 'lastfm_listeners' not in albums_columns:
                cursor.execute("ALTER TABLE albums ADD COLUMN lastfm_listeners INTEGER")
            if 'lastfm_playcount' not in albums_columns:
                cursor.execute("ALTER TABLE albums ADD COLUMN lastfm_playcount INTEGER")
            if 'lastfm_tags' not in albums_columns:
                cursor.execute("ALTER TABLE albums ADD COLUMN lastfm_tags TEXT")
            if 'lastfm_wiki' not in albums_columns:
                cursor.execute("ALTER TABLE albums ADD COLUMN lastfm_wiki TEXT")
            if 'lastfm_url' not in albums_columns:
                cursor.execute("ALTER TABLE albums ADD COLUMN lastfm_url TEXT")

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_albums_lastfm_status ON albums (lastfm_match_status)")

            # --- Tracks ---
            cursor.execute("PRAGMA table_info(tracks)")
            tracks_columns = [column[1] for column in cursor.fetchall()]

            # Last.fm columns
            if 'lastfm_match_status' not in tracks_columns:
                cursor.execute("ALTER TABLE tracks ADD COLUMN lastfm_match_status TEXT")
            if 'lastfm_last_attempted' not in tracks_columns:
                cursor.execute("ALTER TABLE tracks ADD COLUMN lastfm_last_attempted TIMESTAMP")
            if 'lastfm_listeners' not in tracks_columns:
                cursor.execute("ALTER TABLE tracks ADD COLUMN lastfm_listeners INTEGER")
            if 'lastfm_playcount' not in tracks_columns:
                cursor.execute("ALTER TABLE tracks ADD COLUMN lastfm_playcount INTEGER")
            if 'lastfm_tags' not in tracks_columns:
                cursor.execute("ALTER TABLE tracks ADD COLUMN lastfm_tags TEXT")
            if 'lastfm_url' not in tracks_columns:
                cursor.execute("ALTER TABLE tracks ADD COLUMN lastfm_url TEXT")

            # Genius columns
            if 'genius_id' not in tracks_columns:
                cursor.execute("ALTER TABLE tracks ADD COLUMN genius_id TEXT")
            if 'genius_match_status' not in tracks_columns:
                cursor.execute("ALTER TABLE tracks ADD COLUMN genius_match_status TEXT")
            if 'genius_last_attempted' not in tracks_columns:
                cursor.execute("ALTER TABLE tracks ADD COLUMN genius_last_attempted TIMESTAMP")
            if 'genius_lyrics' not in tracks_columns:
                cursor.execute("ALTER TABLE tracks ADD COLUMN genius_lyrics TEXT")
            if 'genius_description' not in tracks_columns:
                cursor.execute("ALTER TABLE tracks ADD COLUMN genius_description TEXT")
            if 'genius_url' not in tracks_columns:
                cursor.execute("ALTER TABLE tracks ADD COLUMN genius_url TEXT")

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tracks_lastfm_status ON tracks (lastfm_match_status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tracks_genius_id ON tracks (genius_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tracks_genius_status ON tracks (genius_match_status)")

            # One-time reset: clear all Genius matches due to blind-fallback bug in search
            # The old search_artist/search_song returned the first result with no name validation,
            # causing wrong matches. This reset lets the fixed worker re-enrich everything.
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='_genius_search_fix_applied'")
            if not cursor.fetchone():
                logger.info("Applying one-time Genius search fix: resetting all artist and track matches for re-enrichment")
                cursor.execute("""
                    UPDATE artists SET
                        genius_id = NULL, genius_match_status = NULL, genius_last_attempted = NULL,
                        genius_description = NULL, genius_alt_names = NULL, genius_url = NULL
                    WHERE genius_match_status IS NOT NULL
                """)
                artist_count = cursor.rowcount
                cursor.execute("""
                    UPDATE tracks SET
                        genius_id = NULL, genius_match_status = NULL, genius_last_attempted = NULL,
                        genius_lyrics = NULL, genius_description = NULL, genius_url = NULL
                    WHERE genius_match_status IS NOT NULL
                """)
                track_count = cursor.rowcount
                cursor.execute("CREATE TABLE _genius_search_fix_applied (applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
                logger.info(f"Genius search fix applied: reset {artist_count} artists and {track_count} tracks")

        except Exception as e:
            logger.error(f"Error adding Last.fm/Genius enrichment columns: {e}")
            # Don't raise - this is a migration, database can still function

    def _add_tidal_qobuz_enrichment_columns(self, cursor):
        """Add Tidal and Qobuz enrichment tracking columns to artists, albums, tracks"""
        try:
            # --- Artists ---
            cursor.execute("PRAGMA table_info(artists)")
            artists_columns = [column[1] for column in cursor.fetchall()]

            if 'tidal_id' not in artists_columns:
                cursor.execute("ALTER TABLE artists ADD COLUMN tidal_id TEXT")
            if 'tidal_match_status' not in artists_columns:
                cursor.execute("ALTER TABLE artists ADD COLUMN tidal_match_status TEXT")
            if 'tidal_last_attempted' not in artists_columns:
                cursor.execute("ALTER TABLE artists ADD COLUMN tidal_last_attempted TIMESTAMP")
            if 'qobuz_id' not in artists_columns:
                cursor.execute("ALTER TABLE artists ADD COLUMN qobuz_id TEXT")
            if 'qobuz_match_status' not in artists_columns:
                cursor.execute("ALTER TABLE artists ADD COLUMN qobuz_match_status TEXT")
            if 'qobuz_last_attempted' not in artists_columns:
                cursor.execute("ALTER TABLE artists ADD COLUMN qobuz_last_attempted TIMESTAMP")

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_artists_tidal_id ON artists (tidal_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_artists_tidal_status ON artists (tidal_match_status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_artists_qobuz_id ON artists (qobuz_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_artists_qobuz_status ON artists (qobuz_match_status)")

            # --- Albums ---
            cursor.execute("PRAGMA table_info(albums)")
            albums_columns = [column[1] for column in cursor.fetchall()]

            if 'tidal_id' not in albums_columns:
                cursor.execute("ALTER TABLE albums ADD COLUMN tidal_id TEXT")
            if 'tidal_match_status' not in albums_columns:
                cursor.execute("ALTER TABLE albums ADD COLUMN tidal_match_status TEXT")
            if 'tidal_last_attempted' not in albums_columns:
                cursor.execute("ALTER TABLE albums ADD COLUMN tidal_last_attempted TIMESTAMP")
            if 'qobuz_id' not in albums_columns:
                cursor.execute("ALTER TABLE albums ADD COLUMN qobuz_id TEXT")
            if 'qobuz_match_status' not in albums_columns:
                cursor.execute("ALTER TABLE albums ADD COLUMN qobuz_match_status TEXT")
            if 'qobuz_last_attempted' not in albums_columns:
                cursor.execute("ALTER TABLE albums ADD COLUMN qobuz_last_attempted TIMESTAMP")

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_albums_tidal_id ON albums (tidal_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_albums_tidal_status ON albums (tidal_match_status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_albums_qobuz_id ON albums (qobuz_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_albums_qobuz_status ON albums (qobuz_match_status)")

            # --- Albums (extra metadata columns) ---
            if 'upc' not in albums_columns:
                cursor.execute("ALTER TABLE albums ADD COLUMN upc TEXT")
            if 'copyright' not in albums_columns:
                cursor.execute("ALTER TABLE albums ADD COLUMN copyright TEXT")

            # --- Tracks ---
            cursor.execute("PRAGMA table_info(tracks)")
            tracks_columns = [column[1] for column in cursor.fetchall()]

            if 'tidal_id' not in tracks_columns:
                cursor.execute("ALTER TABLE tracks ADD COLUMN tidal_id TEXT")
            if 'tidal_match_status' not in tracks_columns:
                cursor.execute("ALTER TABLE tracks ADD COLUMN tidal_match_status TEXT")
            if 'tidal_last_attempted' not in tracks_columns:
                cursor.execute("ALTER TABLE tracks ADD COLUMN tidal_last_attempted TIMESTAMP")
            if 'qobuz_id' not in tracks_columns:
                cursor.execute("ALTER TABLE tracks ADD COLUMN qobuz_id TEXT")
            if 'qobuz_match_status' not in tracks_columns:
                cursor.execute("ALTER TABLE tracks ADD COLUMN qobuz_match_status TEXT")
            if 'qobuz_last_attempted' not in tracks_columns:
                cursor.execute("ALTER TABLE tracks ADD COLUMN qobuz_last_attempted TIMESTAMP")
            if 'isrc' not in tracks_columns:
                cursor.execute("ALTER TABLE tracks ADD COLUMN isrc TEXT")
            if 'copyright' not in tracks_columns:
                cursor.execute("ALTER TABLE tracks ADD COLUMN copyright TEXT")

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tracks_tidal_id ON tracks (tidal_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tracks_tidal_status ON tracks (tidal_match_status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tracks_qobuz_id ON tracks (qobuz_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tracks_qobuz_status ON tracks (qobuz_match_status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tracks_isrc ON tracks (isrc)")

        except Exception as e:
            logger.error(f"Error adding Tidal/Qobuz enrichment columns: {e}")
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

    def _add_profile_support(self, cursor):
        """Add multi-profile support: profiles table + profile_id on per-profile tables"""
        try:
            # Check if migration already applied
            cursor.execute("SELECT value FROM metadata WHERE key = 'profiles_migration_v1' LIMIT 1")
            already_migrated = cursor.fetchone() is not None

            # Even if already migrated, ensure profile_id columns exist on all tables
            # (another migration may have rebuilt a table without profile_id)
            tables_needing_profile_id = [
                'watchlist_artists', 'wishlist_tracks', 'similar_artists',
                'discovery_pool', 'discovery_recent_albums', 'discovery_curated_playlists',
                'bubble_snapshots', 'recent_releases'
            ]
            for table in tables_needing_profile_id:
                try:
                    cursor.execute(f"PRAGMA table_info({table})")
                    columns = [col[1] for col in cursor.fetchall()]
                    if 'profile_id' not in columns:
                        cursor.execute(f"ALTER TABLE {table} ADD COLUMN profile_id INTEGER DEFAULT 1")
                        logger.info(f"Repaired missing profile_id column on {table}")
                except Exception:
                    pass

            if already_migrated:
                return  # Rest of migration already done

            logger.info("Adding multi-profile support...")

            # 1. Create profiles table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    avatar_color TEXT DEFAULT '#6366f1',
                    pin_hash TEXT,
                    is_admin INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 2. Insert default admin profile
            cursor.execute("""
                INSERT OR IGNORE INTO profiles (id, name, is_admin)
                VALUES (1, 'Admin', 1)
            """)

            # 3. profile_id columns already ensured above (before early-return guard)

            # 4. Rebuild watchlist_artists to change UNIQUE constraint
            #    Old: UNIQUE(spotify_artist_id)
            #    New: UNIQUE(profile_id, spotify_artist_id)
            try:
                cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='watchlist_artists'")
                create_sql = cursor.fetchone()
                if create_sql and 'UNIQUE(profile_id' not in create_sql[0]:
                    # Get current columns for the table
                    cursor.execute("PRAGMA table_info(watchlist_artists)")
                    cols_info = cursor.fetchall()
                    col_names = [c[1] for c in cols_info]

                    # Drop leftover temp table from any previous failed migration
                    cursor.execute("DROP TABLE IF EXISTS watchlist_artists_new")

                    cursor.execute("""
                        CREATE TABLE watchlist_artists_new (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            spotify_artist_id TEXT,
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
                            include_instrumentals INTEGER DEFAULT 0,
                            lookback_days INTEGER DEFAULT NULL,
                            itunes_artist_id TEXT,
                            deezer_artist_id TEXT,
                            discogs_artist_id TEXT,
                            profile_id INTEGER DEFAULT 1,
                            UNIQUE(profile_id, spotify_artist_id),
                            UNIQUE(profile_id, itunes_artist_id)
                        )
                    """)

                    # Build column list for INSERT (only columns that exist in both)
                    new_cols = ['id', 'spotify_artist_id', 'artist_name', 'date_added',
                                'last_scan_timestamp', 'created_at', 'updated_at', 'image_url',
                                'include_albums', 'include_eps', 'include_singles', 'include_live',
                                'include_remixes', 'include_acoustic', 'include_compilations',
                                'include_instrumentals', 'lookback_days',
                                'itunes_artist_id', 'deezer_artist_id', 'discogs_artist_id', 'profile_id']
                    shared_cols = [c for c in new_cols if c in col_names]
                    cols_str = ', '.join(shared_cols)

                    cursor.execute(f"INSERT INTO watchlist_artists_new ({cols_str}) SELECT {cols_str} FROM watchlist_artists")
                    cursor.execute("DROP TABLE watchlist_artists")
                    cursor.execute("ALTER TABLE watchlist_artists_new RENAME TO watchlist_artists")
                    cursor.execute("CREATE INDEX IF NOT EXISTS idx_watchlist_spotify_id ON watchlist_artists (spotify_artist_id)")
                    cursor.execute("CREATE INDEX IF NOT EXISTS idx_watchlist_profile ON watchlist_artists (profile_id)")
                    logger.info("Rebuilt watchlist_artists with profile-scoped UNIQUE constraints")
            except Exception as e:
                logger.error(f"Error rebuilding watchlist_artists for profiles: {e}")

            # 5. Rebuild wishlist_tracks for profile-scoped uniqueness
            try:
                cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='wishlist_tracks'")
                create_sql = cursor.fetchone()
                if create_sql and 'UNIQUE(profile_id' not in create_sql[0]:
                    cursor.execute("DROP TABLE IF EXISTS wishlist_tracks_new")
                    cursor.execute("""
                        CREATE TABLE wishlist_tracks_new (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            spotify_track_id TEXT NOT NULL,
                            spotify_data TEXT NOT NULL,
                            failure_reason TEXT,
                            retry_count INTEGER DEFAULT 0,
                            last_attempted TIMESTAMP,
                            date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            source_type TEXT DEFAULT 'unknown',
                            source_info TEXT,
                            profile_id INTEGER DEFAULT 1,
                            UNIQUE(profile_id, spotify_track_id)
                        )
                    """)

                    cursor.execute("PRAGMA table_info(wishlist_tracks)")
                    old_cols = [c[1] for c in cursor.fetchall()]
                    new_cols = ['id', 'spotify_track_id', 'spotify_data', 'failure_reason',
                                'retry_count', 'last_attempted', 'date_added', 'source_type',
                                'source_info', 'profile_id']
                    shared_cols = [c for c in new_cols if c in old_cols]
                    cols_str = ', '.join(shared_cols)

                    cursor.execute(f"INSERT INTO wishlist_tracks_new ({cols_str}) SELECT {cols_str} FROM wishlist_tracks")
                    cursor.execute("DROP TABLE wishlist_tracks")
                    cursor.execute("ALTER TABLE wishlist_tracks_new RENAME TO wishlist_tracks")
                    cursor.execute("CREATE INDEX IF NOT EXISTS idx_wishlist_spotify_id ON wishlist_tracks (spotify_track_id)")
                    cursor.execute("CREATE INDEX IF NOT EXISTS idx_wishlist_profile ON wishlist_tracks (profile_id)")
                    logger.info("Rebuilt wishlist_tracks with profile-scoped UNIQUE constraints")
            except Exception as e:
                logger.error(f"Error rebuilding wishlist_tracks for profiles: {e}")

            # 6. Rebuild bubble_snapshots for profile-scoped PRIMARY KEY
            try:
                cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='bubble_snapshots'")
                create_sql = cursor.fetchone()
                if create_sql and 'profile_id' in [c[1] for c in (cursor.execute("PRAGMA table_info(bubble_snapshots)").fetchall())]:
                    cursor.execute("DROP TABLE IF EXISTS bubble_snapshots_new")
                    cursor.execute("""
                        CREATE TABLE bubble_snapshots_new (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            type TEXT NOT NULL,
                            data TEXT NOT NULL,
                            timestamp TEXT NOT NULL,
                            snapshot_id TEXT NOT NULL,
                            profile_id INTEGER DEFAULT 1,
                            UNIQUE(profile_id, type)
                        )
                    """)

                    cursor.execute("""
                        INSERT INTO bubble_snapshots_new (type, data, timestamp, snapshot_id, profile_id)
                        SELECT type, data, timestamp, snapshot_id, profile_id FROM bubble_snapshots
                    """)
                    cursor.execute("DROP TABLE bubble_snapshots")
                    cursor.execute("ALTER TABLE bubble_snapshots_new RENAME TO bubble_snapshots")
                    logger.info("Rebuilt bubble_snapshots with profile-scoped UNIQUE constraints")
            except Exception as e:
                logger.error(f"Error rebuilding bubble_snapshots for profiles: {e}")

            # 7. Rebuild discovery_curated_playlists for profile-scoped uniqueness
            try:
                cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='discovery_curated_playlists'")
                create_sql = cursor.fetchone()
                if create_sql and 'UNIQUE(profile_id' not in create_sql[0]:
                    cursor.execute("DROP TABLE IF EXISTS discovery_curated_playlists_new")
                    cursor.execute("""
                        CREATE TABLE discovery_curated_playlists_new (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            playlist_type TEXT NOT NULL,
                            track_ids_json TEXT NOT NULL,
                            curated_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            profile_id INTEGER DEFAULT 1,
                            UNIQUE(profile_id, playlist_type)
                        )
                    """)

                    cursor.execute("PRAGMA table_info(discovery_curated_playlists)")
                    old_cols = [c[1] for c in cursor.fetchall()]
                    new_cols = ['id', 'playlist_type', 'track_ids_json', 'curated_date', 'profile_id']
                    shared_cols = [c for c in new_cols if c in old_cols]
                    cols_str = ', '.join(shared_cols)

                    cursor.execute(f"INSERT INTO discovery_curated_playlists_new ({cols_str}) SELECT {cols_str} FROM discovery_curated_playlists")
                    cursor.execute("DROP TABLE discovery_curated_playlists")
                    cursor.execute("ALTER TABLE discovery_curated_playlists_new RENAME TO discovery_curated_playlists")
                    logger.info("Rebuilt discovery_curated_playlists with profile-scoped UNIQUE constraints")
            except Exception as e:
                logger.error(f"Error rebuilding discovery_curated_playlists for profiles: {e}")

            # 8. Add indexes for profile_id on remaining tables
            index_pairs = [
                ('idx_similar_artists_profile', 'similar_artists'),
                ('idx_discovery_pool_profile', 'discovery_pool'),
                ('idx_discovery_recent_albums_profile', 'discovery_recent_albums'),
                ('idx_recent_releases_profile', 'recent_releases'),
            ]
            for idx_name, table in index_pairs:
                try:
                    cursor.execute(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table} (profile_id)")
                except Exception:
                    pass

            # Set migration marker
            cursor.execute("""
                INSERT OR REPLACE INTO metadata (key, value, updated_at)
                VALUES ('profiles_migration_v1', 'true', CURRENT_TIMESTAMP)
            """)

            logger.info("Multi-profile support migration completed successfully")

        except Exception as e:
            logger.error(f"Error adding profile support: {e}")
            # Don't raise - this is a migration, database can still function

    def _add_profile_support_v2(self, cursor):
        """Fix missing profile-scoped UNIQUE constraints on 3 tables (v2 migration)"""
        try:
            cursor.execute("SELECT value FROM metadata WHERE key = 'profiles_migration_v2' LIMIT 1")
            if cursor.fetchone():
                return  # Already migrated

            logger.info("Applying profile support v2 migration...")

            # Rebuild discovery_pool: UNIQUE(profile_id, spotify_track_id, itunes_track_id, source)
            try:
                cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='discovery_pool'")
                create_sql = cursor.fetchone()
                if create_sql and 'UNIQUE(profile_id' not in create_sql[0]:
                    cursor.execute("PRAGMA table_info(discovery_pool)")
                    old_cols = [c[1] for c in cursor.fetchall()]

                    cursor.execute("DROP TABLE IF EXISTS discovery_pool_new")
                    cursor.execute("""
                        CREATE TABLE discovery_pool_new (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            spotify_track_id TEXT,
                            spotify_album_id TEXT,
                            spotify_artist_id TEXT,
                            itunes_track_id TEXT,
                            itunes_album_id TEXT,
                            itunes_artist_id TEXT,
                            deezer_track_id TEXT,
                            deezer_album_id TEXT,
                            deezer_artist_id TEXT,
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
                            artist_genres TEXT,
                            added_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            profile_id INTEGER DEFAULT 1,
                            UNIQUE(profile_id, spotify_track_id, itunes_track_id, source)
                        )
                    """)

                    new_cols = ['id', 'spotify_track_id', 'spotify_album_id', 'spotify_artist_id',
                                'itunes_track_id', 'itunes_album_id', 'itunes_artist_id',
                                'deezer_track_id', 'deezer_album_id', 'deezer_artist_id',
                                'source', 'track_name', 'artist_name', 'album_name', 'album_cover_url',
                                'duration_ms', 'popularity', 'release_date', 'is_new_release',
                                'track_data_json', 'artist_genres', 'added_date', 'profile_id']
                    shared_cols = [c for c in new_cols if c in old_cols]
                    cols_str = ', '.join(shared_cols)

                    cursor.execute(f"INSERT INTO discovery_pool_new ({cols_str}) SELECT {cols_str} FROM discovery_pool")
                    cursor.execute("DROP TABLE discovery_pool")
                    cursor.execute("ALTER TABLE discovery_pool_new RENAME TO discovery_pool")
                    cursor.execute("CREATE INDEX IF NOT EXISTS idx_discovery_pool_profile ON discovery_pool (profile_id)")
                    logger.info("Rebuilt discovery_pool with profile-scoped UNIQUE constraint")
            except Exception as e:
                logger.error(f"Error rebuilding discovery_pool for profiles v2: {e}")

            # Rebuild discovery_recent_albums: UNIQUE(profile_id, album_spotify_id, album_itunes_id, source)
            try:
                cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='discovery_recent_albums'")
                create_sql = cursor.fetchone()
                if create_sql and 'UNIQUE(profile_id' not in create_sql[0]:
                    cursor.execute("PRAGMA table_info(discovery_recent_albums)")
                    old_cols = [c[1] for c in cursor.fetchall()]

                    cursor.execute("DROP TABLE IF EXISTS discovery_recent_albums_new")
                    cursor.execute("""
                        CREATE TABLE discovery_recent_albums_new (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            album_spotify_id TEXT,
                            album_itunes_id TEXT,
                            album_deezer_id TEXT,
                            artist_spotify_id TEXT,
                            artist_itunes_id TEXT,
                            artist_deezer_id TEXT,
                            source TEXT NOT NULL DEFAULT 'spotify',
                            album_name TEXT NOT NULL,
                            artist_name TEXT NOT NULL,
                            album_cover_url TEXT,
                            release_date TEXT NOT NULL,
                            album_type TEXT DEFAULT 'album',
                            cached_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            profile_id INTEGER DEFAULT 1,
                            UNIQUE(profile_id, album_spotify_id, album_itunes_id, album_deezer_id, source)
                        )
                    """)

                    new_cols = ['id', 'album_spotify_id', 'album_itunes_id', 'album_deezer_id',
                                'artist_spotify_id', 'artist_itunes_id', 'artist_deezer_id',
                                'source', 'album_name', 'artist_name',
                                'album_cover_url', 'release_date', 'album_type', 'cached_date', 'profile_id']
                    shared_cols = [c for c in new_cols if c in old_cols]
                    cols_str = ', '.join(shared_cols)

                    cursor.execute(f"INSERT INTO discovery_recent_albums_new ({cols_str}) SELECT {cols_str} FROM discovery_recent_albums")
                    cursor.execute("DROP TABLE discovery_recent_albums")
                    cursor.execute("ALTER TABLE discovery_recent_albums_new RENAME TO discovery_recent_albums")
                    cursor.execute("CREATE INDEX IF NOT EXISTS idx_discovery_recent_albums_profile ON discovery_recent_albums (profile_id)")
                    logger.info("Rebuilt discovery_recent_albums with profile-scoped UNIQUE constraint")
            except Exception as e:
                logger.error(f"Error rebuilding discovery_recent_albums for profiles v2: {e}")

            # Rebuild recent_releases: UNIQUE(profile_id, watchlist_artist_id, album_spotify_id, album_itunes_id)
            try:
                cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='recent_releases'")
                create_sql = cursor.fetchone()
                if create_sql and 'UNIQUE(profile_id' not in create_sql[0]:
                    cursor.execute("PRAGMA table_info(recent_releases)")
                    old_cols = [c[1] for c in cursor.fetchall()]

                    cursor.execute("DROP TABLE IF EXISTS recent_releases_new")
                    cursor.execute("""
                        CREATE TABLE recent_releases_new (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            watchlist_artist_id INTEGER NOT NULL,
                            album_spotify_id TEXT,
                            album_itunes_id TEXT,
                            album_deezer_id TEXT,
                            source TEXT NOT NULL DEFAULT 'spotify',
                            album_name TEXT NOT NULL,
                            release_date TEXT NOT NULL,
                            album_cover_url TEXT,
                            track_count INTEGER DEFAULT 0,
                            added_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            profile_id INTEGER DEFAULT 1,
                            UNIQUE(profile_id, watchlist_artist_id, album_spotify_id, album_itunes_id)
                        )
                    """)

                    new_cols = ['id', 'watchlist_artist_id', 'album_spotify_id', 'album_itunes_id',
                                'album_deezer_id', 'source', 'album_name', 'release_date',
                                'album_cover_url', 'track_count', 'added_date', 'profile_id']
                    shared_cols = [c for c in new_cols if c in old_cols]
                    cols_str = ', '.join(shared_cols)

                    cursor.execute(f"INSERT INTO recent_releases_new ({cols_str}) SELECT {cols_str} FROM recent_releases")
                    cursor.execute("DROP TABLE recent_releases")
                    cursor.execute("ALTER TABLE recent_releases_new RENAME TO recent_releases")
                    cursor.execute("CREATE INDEX IF NOT EXISTS idx_recent_releases_profile ON recent_releases (profile_id)")
                    logger.info("Rebuilt recent_releases with profile-scoped UNIQUE constraint")
            except Exception as e:
                logger.error(f"Error rebuilding recent_releases for profiles v2: {e}")

            # Set migration marker
            cursor.execute("""
                INSERT OR REPLACE INTO metadata (key, value, updated_at)
                VALUES ('profiles_migration_v2', 'true', CURRENT_TIMESTAMP)
            """)

            logger.info("Profile support v2 migration completed successfully")

        except Exception as e:
            logger.error(f"Error in profile support v2 migration: {e}")

    def _add_profile_support_v3(self, cursor):
        """Fix similar_artists UNIQUE constraint and make discovery_pool_metadata per-profile (v3 migration)"""
        try:
            cursor.execute("SELECT value FROM metadata WHERE key = 'profiles_migration_v3' LIMIT 1")
            already_migrated = cursor.fetchone() is not None

            # Always check if similar_artists actually has profile_id column
            # (an older bug could strip it even after v3 migration ran)
            cursor.execute("PRAGMA table_info(similar_artists)")
            sa_cols = [c[1] for c in cursor.fetchall()]
            needs_repair = 'profile_id' not in sa_cols

            if already_migrated and not needs_repair:
                return  # Already migrated and table is intact

            if needs_repair:
                logger.info("Repairing similar_artists table — profile_id column missing, rebuilding...")
            else:
                logger.info("Applying profile support v3 migration...")

            # Rebuild similar_artists: UNIQUE(profile_id, source_artist_id, similar_artist_name)
            try:
                cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='similar_artists'")
                create_sql = cursor.fetchone()
                if create_sql and ('UNIQUE(profile_id' not in create_sql[0] or needs_repair):
                    cursor.execute("PRAGMA table_info(similar_artists)")
                    old_cols = [c[1] for c in cursor.fetchall()]

                    cursor.execute("DROP TABLE IF EXISTS similar_artists_new")
                    cursor.execute("""
                        CREATE TABLE similar_artists_new (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            source_artist_id TEXT NOT NULL,
                            similar_artist_spotify_id TEXT,
                            similar_artist_itunes_id TEXT,
                            similar_artist_deezer_id TEXT,
                            similar_artist_name TEXT NOT NULL,
                            similarity_rank INTEGER DEFAULT 1,
                            occurrence_count INTEGER DEFAULT 1,
                            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            image_url TEXT,
                            genres TEXT,
                            popularity INTEGER DEFAULT 0,
                            metadata_updated_at TIMESTAMP,
                            last_featured TIMESTAMP,
                            profile_id INTEGER DEFAULT 1,
                            UNIQUE(profile_id, source_artist_id, similar_artist_name)
                        )
                    """)

                    new_cols = ['id', 'source_artist_id', 'similar_artist_spotify_id',
                                'similar_artist_itunes_id', 'similar_artist_deezer_id',
                                'similar_artist_name', 'similarity_rank', 'occurrence_count',
                                'last_updated', 'image_url', 'genres', 'popularity',
                                'metadata_updated_at', 'last_featured', 'profile_id']
                    shared_cols = [c for c in new_cols if c in old_cols]
                    cols_str = ', '.join(shared_cols)

                    cursor.execute(f"INSERT INTO similar_artists_new ({cols_str}) SELECT {cols_str} FROM similar_artists")
                    cursor.execute("DROP TABLE similar_artists")
                    cursor.execute("ALTER TABLE similar_artists_new RENAME TO similar_artists")
                    cursor.execute("CREATE INDEX IF NOT EXISTS idx_similar_artists_profile ON similar_artists (profile_id)")
                    logger.info("Rebuilt similar_artists with profile-scoped UNIQUE constraint")
            except Exception as e:
                logger.error(f"Error rebuilding similar_artists for profiles v3: {e}")

            # Make discovery_pool_metadata per-profile: change CHECK(id=1) to use profile_id as key
            try:
                cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='discovery_pool_metadata'")
                create_sql = cursor.fetchone()
                if create_sql and 'profile_id' not in create_sql[0]:
                    cursor.execute("DROP TABLE IF EXISTS discovery_pool_metadata_new")
                    cursor.execute("""
                        CREATE TABLE discovery_pool_metadata_new (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            profile_id INTEGER NOT NULL DEFAULT 1 UNIQUE,
                            last_populated_timestamp TIMESTAMP NOT NULL,
                            track_count INTEGER DEFAULT 0,
                            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        )
                    """)
                    # Migrate existing row (profile 1)
                    cursor.execute("""
                        INSERT OR IGNORE INTO discovery_pool_metadata_new
                        (profile_id, last_populated_timestamp, track_count, updated_at)
                        SELECT 1, last_populated_timestamp, track_count, updated_at
                        FROM discovery_pool_metadata WHERE id = 1
                    """)
                    cursor.execute("DROP TABLE discovery_pool_metadata")
                    cursor.execute("ALTER TABLE discovery_pool_metadata_new RENAME TO discovery_pool_metadata")
                    logger.info("Rebuilt discovery_pool_metadata with per-profile support")
            except Exception as e:
                logger.error(f"Error rebuilding discovery_pool_metadata for profiles v3: {e}")

            # Set migration marker
            cursor.execute("""
                INSERT OR REPLACE INTO metadata (key, value, updated_at)
                VALUES ('profiles_migration_v3', 'true', CURRENT_TIMESTAMP)
            """)

            logger.info("Profile support v3 migration completed successfully")

        except Exception as e:
            logger.error(f"Error in profile support v3 migration: {e}")

    def _add_profile_support_v4(self, cursor):
        """Add avatar_url column to profiles table (v4 migration)"""
        try:
            cursor.execute("SELECT value FROM metadata WHERE key = 'profiles_migration_v4' LIMIT 1")
            if cursor.fetchone():
                return  # Already migrated

            logger.info("Applying profile support v4 migration...")

            # Add avatar_url column
            try:
                cursor.execute("ALTER TABLE profiles ADD COLUMN avatar_url TEXT DEFAULT NULL")
            except sqlite3.OperationalError:
                pass  # Column already exists

            cursor.execute("""
                INSERT OR REPLACE INTO metadata (key, value) VALUES ('profiles_migration_v4', '1')
            """)

            logger.info("Profile support v4 migration completed successfully")

        except Exception as e:
            logger.error(f"Error in profile support v4 migration: {e}")

    def _add_profile_settings(self, cursor):
        """Add home_page, allowed_pages, can_download columns to profiles table"""
        try:
            cursor.execute("SELECT value FROM metadata WHERE key = 'profiles_migration_settings' LIMIT 1")
            if cursor.fetchone():
                return  # Already migrated

            logger.info("Applying profile settings migration...")

            for col_sql in [
                "ALTER TABLE profiles ADD COLUMN home_page TEXT DEFAULT NULL",
                "ALTER TABLE profiles ADD COLUMN allowed_pages TEXT DEFAULT NULL",
                "ALTER TABLE profiles ADD COLUMN can_download INTEGER DEFAULT 1",
            ]:
                try:
                    cursor.execute(col_sql)
                except sqlite3.OperationalError:
                    pass  # Column already exists

            cursor.execute("""
                INSERT OR REPLACE INTO metadata (key, value) VALUES ('profiles_migration_settings', '1')
            """)

            logger.info("Profile settings migration completed successfully")

        except Exception as e:
            logger.error(f"Error in profile settings migration: {e}")

    def _add_profile_listenbrainz_support(self, cursor):
        """Add per-profile ListenBrainz credentials and scope playlist cache by profile"""
        try:
            cursor.execute("SELECT value FROM metadata WHERE key = 'profiles_listenbrainz_v1' LIMIT 1")
            if cursor.fetchone():
                return  # Already migrated

            logger.info("Applying per-profile ListenBrainz migration...")

            # Per-profile LB credentials on profiles table
            for col_sql in [
                "ALTER TABLE profiles ADD COLUMN listenbrainz_token TEXT DEFAULT NULL",
                "ALTER TABLE profiles ADD COLUMN listenbrainz_base_url TEXT DEFAULT NULL",
                "ALTER TABLE profiles ADD COLUMN listenbrainz_username TEXT DEFAULT NULL",
            ]:
                try:
                    cursor.execute(col_sql)
                except sqlite3.OperationalError:
                    pass  # Column already exists

            # Recreate listenbrainz_playlists with profile_id and compound unique constraint
            # (SQLite can't ALTER constraints, so we must recreate the table)
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='listenbrainz_playlists'")
            if cursor.fetchone():
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS listenbrainz_playlists_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        playlist_mbid TEXT NOT NULL,
                        title TEXT NOT NULL,
                        creator TEXT,
                        playlist_type TEXT NOT NULL,
                        track_count INTEGER DEFAULT 0,
                        annotation_data TEXT,
                        profile_id INTEGER DEFAULT 1,
                        last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        cached_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(playlist_mbid, profile_id)
                    )
                """)
                cursor.execute("""
                    INSERT OR IGNORE INTO listenbrainz_playlists_new
                    (id, playlist_mbid, title, creator, playlist_type, track_count, annotation_data, profile_id, last_updated, cached_date)
                    SELECT id, playlist_mbid, title, creator, playlist_type, track_count, annotation_data, 1, last_updated, cached_date
                    FROM listenbrainz_playlists
                """)
                cursor.execute("DROP TABLE listenbrainz_playlists")
                cursor.execute("ALTER TABLE listenbrainz_playlists_new RENAME TO listenbrainz_playlists")

                # Clean up playlists that lost their tracks during table recreation
                # (track playlist_id foreign keys may reference stale IDs).
                # This forces a fresh re-fetch from ListenBrainz on next page load.
                cursor.execute("""
                    DELETE FROM listenbrainz_playlists
                    WHERE id NOT IN (SELECT DISTINCT playlist_id FROM listenbrainz_tracks)
                """)
                cleaned = cursor.rowcount
                if cleaned:
                    logger.info(f"Cleaned up {cleaned} stale playlists (will re-fetch from ListenBrainz)")

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_lb_playlists_profile ON listenbrainz_playlists (profile_id)")

            cursor.execute("""
                INSERT OR REPLACE INTO metadata (key, value) VALUES ('profiles_listenbrainz_v1', '1')
            """)

            logger.info("Per-profile ListenBrainz migration completed successfully")

        except Exception as e:
            logger.error(f"Error in per-profile ListenBrainz migration: {e}")

    def set_profile_listenbrainz(self, profile_id: int, token: str, base_url: str = '', username: str = '') -> bool:
        """Save encrypted ListenBrainz credentials for a profile"""
        try:
            from config.settings import config_manager
            encrypted_token = config_manager._encrypt_value(token) if token else None
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE profiles
                    SET listenbrainz_token = ?, listenbrainz_base_url = ?, listenbrainz_username = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (encrypted_token, base_url or None, username or None, profile_id))
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error setting ListenBrainz credentials for profile {profile_id}: {e}")
            return False

    def get_profile_listenbrainz(self, profile_id: int) -> Dict[str, Any]:
        """Get decrypted ListenBrainz credentials for a profile"""
        try:
            from config.settings import config_manager
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT listenbrainz_token, listenbrainz_base_url, listenbrainz_username
                    FROM profiles WHERE id = ?
                """, (profile_id,))
                row = cursor.fetchone()
                if not row:
                    return {'token': None, 'base_url': None, 'username': None}
                token_raw = row[0]
                token = config_manager._decrypt_value(token_raw) if token_raw else None
                return {
                    'token': token,
                    'base_url': row[1] or '',
                    'username': row[2] or '',
                }
        except Exception as e:
            logger.error(f"Error getting ListenBrainz credentials for profile {profile_id}: {e}")
            return {'token': None, 'base_url': None, 'username': None}

    def clear_profile_listenbrainz(self, profile_id: int) -> bool:
        """Clear ListenBrainz credentials for a profile"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE profiles
                    SET listenbrainz_token = NULL, listenbrainz_base_url = NULL, listenbrainz_username = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (profile_id,))
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error clearing ListenBrainz credentials for profile {profile_id}: {e}")
            return False

    def get_profiles_with_listenbrainz(self) -> List[Dict[str, Any]]:
        """Get all profiles that have ListenBrainz tokens configured"""
        try:
            from config.settings import config_manager
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, listenbrainz_token, listenbrainz_base_url
                    FROM profiles WHERE listenbrainz_token IS NOT NULL
                """)
                results = []
                for row in cursor.fetchall():
                    token = config_manager._decrypt_value(row[1]) if row[1] else None
                    if token:
                        results.append({
                            'id': row[0],
                            'token': token,
                            'base_url': row[2] or '',
                        })
                return results
        except Exception as e:
            logger.error(f"Error getting profiles with ListenBrainz tokens: {e}")
            return []

    # ── Per-profile service credentials (Spotify, Tidal, server library) ──

    def _add_profile_service_credentials(self, cursor):
        """Add per-profile Spotify, Tidal, and media server library columns to profiles table."""
        try:
            cursor.execute("SELECT value FROM metadata WHERE key = 'profiles_services_v1' LIMIT 1")
            if cursor.fetchone():
                return  # Already migrated

            logger.info("Applying per-profile service credentials migration...")

            columns = [
                # Spotify per-profile
                "ALTER TABLE profiles ADD COLUMN spotify_client_id TEXT DEFAULT NULL",
                "ALTER TABLE profiles ADD COLUMN spotify_client_secret TEXT DEFAULT NULL",
                "ALTER TABLE profiles ADD COLUMN spotify_redirect_uri TEXT DEFAULT NULL",
                "ALTER TABLE profiles ADD COLUMN spotify_access_token TEXT DEFAULT NULL",
                "ALTER TABLE profiles ADD COLUMN spotify_refresh_token TEXT DEFAULT NULL",
                # Tidal per-profile
                "ALTER TABLE profiles ADD COLUMN tidal_access_token TEXT DEFAULT NULL",
                "ALTER TABLE profiles ADD COLUMN tidal_refresh_token TEXT DEFAULT NULL",
                # Media server library selection per-profile
                "ALTER TABLE profiles ADD COLUMN plex_library_id TEXT DEFAULT NULL",
                "ALTER TABLE profiles ADD COLUMN jellyfin_user_id TEXT DEFAULT NULL",
                "ALTER TABLE profiles ADD COLUMN jellyfin_library_id TEXT DEFAULT NULL",
                "ALTER TABLE profiles ADD COLUMN navidrome_library_id TEXT DEFAULT NULL",
            ]

            for sql in columns:
                try:
                    cursor.execute(sql)
                except sqlite3.OperationalError:
                    pass  # Column already exists

            cursor.execute("""
                INSERT OR REPLACE INTO metadata (key, value) VALUES ('profiles_services_v1', '1')
            """)

            logger.info("Per-profile service credentials migration completed")

        except Exception as e:
            logger.error(f"Error in per-profile service credentials migration: {e}")

    def _add_soul_id_columns(self, cursor):
        """Add soul_id columns to artists, albums, and tracks tables."""
        try:
            # Artists: soul_id
            cursor.execute("PRAGMA table_info(artists)")
            artist_cols = [c[1] for c in cursor.fetchall()]
            if 'soul_id' not in artist_cols:
                cursor.execute("ALTER TABLE artists ADD COLUMN soul_id TEXT DEFAULT NULL")
                logger.info("Added soul_id column to artists table")

            # Albums: soul_id
            cursor.execute("PRAGMA table_info(albums)")
            album_cols = [c[1] for c in cursor.fetchall()]
            if 'soul_id' not in album_cols:
                cursor.execute("ALTER TABLE albums ADD COLUMN soul_id TEXT DEFAULT NULL")
                logger.info("Added soul_id column to albums table")

            # Tracks: soul_id (song-level) + album_soul_id (release-specific)
            cursor.execute("PRAGMA table_info(tracks)")
            track_cols = [c[1] for c in cursor.fetchall()]
            if 'soul_id' not in track_cols:
                cursor.execute("ALTER TABLE tracks ADD COLUMN soul_id TEXT DEFAULT NULL")
                logger.info("Added soul_id column to tracks table")
            if 'album_soul_id' not in track_cols:
                cursor.execute("ALTER TABLE tracks ADD COLUMN album_soul_id TEXT DEFAULT NULL")
                logger.info("Added album_soul_id column to tracks table")

            # Indexes for lookups
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_artists_soul_id ON artists (soul_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_albums_soul_id ON albums (soul_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tracks_soul_id ON tracks (soul_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tracks_album_soul_id ON tracks (album_soul_id)")

            # v2.1 migration: regenerate artist soul_ids with new canonical ID algorithm
            # (was name+debut_year, now name+max(deezer_id,itunes_id) via track-verified lookup)
            cursor.execute("SELECT value FROM metadata WHERE key = 'soulid_v2_migration'")
            if not cursor.fetchone():
                cursor.execute("UPDATE artists SET soul_id = NULL")
                cleared = cursor.rowcount
                cursor.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('soulid_v2_migration', '1')")
                if cleared > 0:
                    logger.info(f"SoulID v2 migration: cleared {cleared} artist soul_ids for regeneration")

        except Exception as e:
            logger.error(f"Error adding soul_id columns: {e}")

    def _add_listening_history_table(self, cursor):
        """Create listening_history table and add play_count/last_played to tracks."""
        try:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS listening_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    track_id TEXT,
                    title TEXT NOT NULL,
                    artist TEXT,
                    album TEXT,
                    played_at TIMESTAMP NOT NULL,
                    duration_ms INTEGER DEFAULT 0,
                    server_source TEXT,
                    db_track_id INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_listening_played_at ON listening_history (played_at)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_listening_artist ON listening_history (artist)")
            cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_listening_dedup ON listening_history (track_id, played_at, server_source)")

            # Add play_count and last_played to tracks table
            cursor.execute("PRAGMA table_info(tracks)")
            track_cols = [c[1] for c in cursor.fetchall()]
            if 'play_count' not in track_cols:
                cursor.execute("ALTER TABLE tracks ADD COLUMN play_count INTEGER DEFAULT 0")
                logger.info("Added play_count column to tracks table")
            if 'last_played' not in track_cols:
                cursor.execute("ALTER TABLE tracks ADD COLUMN last_played TIMESTAMP")
                logger.info("Added last_played column to tracks table")

            # Add scrobble tracking columns to listening_history
            cursor.execute("PRAGMA table_info(listening_history)")
            lh_cols = [c[1] for c in cursor.fetchall()]
            if 'scrobbled_lastfm' not in lh_cols:
                cursor.execute("ALTER TABLE listening_history ADD COLUMN scrobbled_lastfm INTEGER DEFAULT 0")
                logger.info("Added scrobbled_lastfm column to listening_history")
            if 'scrobbled_listenbrainz' not in lh_cols:
                cursor.execute("ALTER TABLE listening_history ADD COLUMN scrobbled_listenbrainz INTEGER DEFAULT 0")
                logger.info("Added scrobbled_listenbrainz column to listening_history")

        except Exception as e:
            logger.error(f"Error creating listening_history table: {e}")

    def insert_listening_events(self, events):
        """Bulk insert listening events, skipping duplicates."""
        if not events:
            return 0
        conn = None
        inserted = 0
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            for event in events:
                try:
                    cursor.execute("""
                        INSERT OR IGNORE INTO listening_history
                            (track_id, title, artist, album, played_at, duration_ms, server_source, db_track_id)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        event.get('track_id'),
                        event.get('title', ''),
                        event.get('artist', ''),
                        event.get('album', ''),
                        event.get('played_at'),
                        event.get('duration_ms', 0),
                        event.get('server_source', ''),
                        event.get('db_track_id'),
                    ))
                    if cursor.rowcount > 0:
                        inserted += 1
                except Exception:
                    pass
            conn.commit()
            return inserted
        except Exception as e:
            logger.error(f"Error inserting listening events: {e}")
            return 0
        finally:
            if conn:
                conn.close()

    def update_track_play_counts(self, counts):
        """Update play_count and last_played on the tracks table.

        Args:
            counts: list of dicts with {db_track_id, play_count, last_played}
        """
        if not counts:
            return
        conn = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            for item in counts:
                cursor.execute("""
                    UPDATE tracks SET play_count = ?, last_played = ?
                    WHERE id = ?
                """, (item.get('play_count', 0), item.get('last_played'), item.get('db_track_id')))
            conn.commit()
        except Exception as e:
            logger.error(f"Error updating track play counts: {e}")
        finally:
            if conn:
                conn.close()

    def get_listening_stats(self, time_range='all'):
        """Get aggregate listening stats for a time range.

        Args:
            time_range: '7d', '30d', '12m', or 'all'

        Returns:
            Dict with total_plays, total_time_ms, unique_artists, unique_albums, unique_tracks
        """
        conn = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            where = self._listening_time_filter(time_range)

            cursor.execute(f"""
                SELECT
                    COUNT(*) as total_plays,
                    COALESCE(SUM(duration_ms), 0) as total_time_ms,
                    COUNT(DISTINCT artist) as unique_artists,
                    COUNT(DISTINCT album) as unique_albums,
                    COUNT(DISTINCT title || '|||' || COALESCE(artist, '')) as unique_tracks
                FROM listening_history
                {where}
            """)
            row = cursor.fetchone()
            return {
                'total_plays': row[0] or 0,
                'total_time_ms': row[1] or 0,
                'unique_artists': row[2] or 0,
                'unique_albums': row[3] or 0,
                'unique_tracks': row[4] or 0,
            }
        except Exception as e:
            logger.error(f"Error getting listening stats: {e}")
            return {'total_plays': 0, 'total_time_ms': 0, 'unique_artists': 0, 'unique_albums': 0, 'unique_tracks': 0}
        finally:
            if conn:
                conn.close()

    def get_top_artists(self, time_range='all', limit=10):
        """Get top artists by play count."""
        conn = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            where = self._listening_time_filter(time_range)

            cursor.execute(f"""
                SELECT artist, COUNT(*) as play_count
                FROM listening_history
                {where}
                AND artist IS NOT NULL AND artist != ''
                GROUP BY LOWER(artist)
                ORDER BY play_count DESC
                LIMIT ?
            """, (limit,))
            return [{'name': row[0], 'play_count': row[1]} for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Error getting top artists: {e}")
            return []
        finally:
            if conn:
                conn.close()

    def get_top_albums(self, time_range='all', limit=10):
        """Get top albums by play count."""
        conn = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            where = self._listening_time_filter(time_range)

            cursor.execute(f"""
                SELECT album, artist, COUNT(*) as play_count
                FROM listening_history
                {where}
                AND album IS NOT NULL AND album != ''
                GROUP BY LOWER(album), LOWER(artist)
                ORDER BY play_count DESC
                LIMIT ?
            """, (limit,))
            return [{'name': row[0], 'artist': row[1], 'play_count': row[2]} for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Error getting top albums: {e}")
            return []
        finally:
            if conn:
                conn.close()

    def get_top_tracks(self, time_range='all', limit=10):
        """Get top tracks by play count."""
        conn = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            where = self._listening_time_filter(time_range)

            cursor.execute(f"""
                SELECT title, artist, album, COUNT(*) as play_count
                FROM listening_history
                {where}
                AND title IS NOT NULL AND title != ''
                GROUP BY LOWER(title), LOWER(artist)
                ORDER BY play_count DESC
                LIMIT ?
            """, (limit,))
            return [{'name': row[0], 'artist': row[1], 'album': row[2], 'play_count': row[3]} for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Error getting top tracks: {e}")
            return []
        finally:
            if conn:
                conn.close()

    def get_listening_timeline(self, time_range='30d', granularity='day'):
        """Get play count per time period for chart rendering."""
        conn = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            where = self._listening_time_filter(time_range)

            if granularity == 'month':
                date_fmt = '%Y-%m'
            elif granularity == 'week':
                date_fmt = '%Y-W%W'
            else:
                date_fmt = '%Y-%m-%d'

            cursor.execute(f"""
                SELECT strftime('{date_fmt}', played_at) as period, COUNT(*) as plays
                FROM listening_history
                {where}
                GROUP BY period
                ORDER BY period ASC
            """)
            return [{'date': row[0], 'plays': row[1]} for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Error getting listening timeline: {e}")
            return []
        finally:
            if conn:
                conn.close()

    def get_genre_breakdown(self, time_range='all'):
        """Get genre distribution by play count (joins listening_history to tracks/artists)."""
        conn = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            where = self._listening_time_filter(time_range, alias='lh')

            cursor.execute(f"""
                SELECT a.genres, COUNT(*) as play_count
                FROM listening_history lh
                JOIN tracks t ON t.id = lh.db_track_id
                JOIN artists a ON a.id = t.artist_id
                {where}
                AND a.genres IS NOT NULL AND a.genres != ''
                GROUP BY a.genres
                ORDER BY play_count DESC
                LIMIT 50
            """)
            # Parse genre JSON and aggregate
            genre_counts = {}
            for row in cursor.fetchall():
                genres_str = row[0]
                count = row[1]
                try:
                    import json
                    genres = json.loads(genres_str)
                    if isinstance(genres, list):
                        for g in genres:
                            genre_counts[g] = genre_counts.get(g, 0) + count
                    else:
                        genre_counts[str(genres)] = genre_counts.get(str(genres), 0) + count
                except (ValueError, TypeError):
                    for g in genres_str.split(','):
                        g = g.strip()
                        if g:
                            genre_counts[g] = genre_counts.get(g, 0) + count

            total = sum(genre_counts.values()) or 1
            result = sorted(
                [{'genre': g, 'play_count': c, 'percentage': round(c / total * 100, 1)} for g, c in genre_counts.items()],
                key=lambda x: x['play_count'], reverse=True
            )[:15]
            return result
        except Exception as e:
            logger.error(f"Error getting genre breakdown: {e}")
            return []
        finally:
            if conn:
                conn.close()

    def get_library_health(self):
        """Get library health metrics."""
        conn = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # Total tracks
            cursor.execute("SELECT COUNT(*) FROM tracks WHERE id IS NOT NULL")
            total_tracks = (cursor.fetchone() or [0])[0]

            # Unplayed
            cursor.execute("SELECT COUNT(*) FROM tracks WHERE (play_count IS NULL OR play_count = 0) AND id IS NOT NULL")
            unplayed = (cursor.fetchone() or [0])[0]

            # Format breakdown
            cursor.execute("""
                SELECT
                    CASE
                        WHEN LOWER(file_path) LIKE '%.flac' THEN 'FLAC'
                        WHEN LOWER(file_path) LIKE '%.mp3' THEN 'MP3'
                        WHEN LOWER(file_path) LIKE '%.opus' THEN 'Opus'
                        WHEN LOWER(file_path) LIKE '%.m4a' THEN 'AAC'
                        WHEN LOWER(file_path) LIKE '%.ogg' THEN 'OGG'
                        WHEN LOWER(file_path) LIKE '%.wav' THEN 'WAV'
                        ELSE 'Other'
                    END as format,
                    COUNT(*) as count
                FROM tracks
                WHERE file_path IS NOT NULL AND file_path != ''
                GROUP BY format
                ORDER BY count DESC
            """)
            format_breakdown = {row[0]: row[1] for row in cursor.fetchall()}

            # Total duration
            cursor.execute("SELECT COALESCE(SUM(duration), 0) FROM tracks WHERE id IS NOT NULL")
            total_duration_ms = (cursor.fetchone() or [0])[0]

            # Enrichment coverage
            enrichment = {}
            for service, col in [('spotify', 'spotify_artist_id'), ('musicbrainz', 'musicbrainz_id'),
                                 ('deezer', 'deezer_id'), ('lastfm', 'lastfm_url'),
                                 ('itunes', 'itunes_artist_id'), ('audiodb', 'audiodb_id'),
                                 ('genius', 'genius_id'), ('tidal', 'tidal_id'),
                                 ('qobuz', 'qobuz_id')]:
                try:
                    cursor.execute(f"SELECT COUNT(*) FROM artists WHERE {col} IS NOT NULL AND {col} != ''")
                    matched = (cursor.fetchone() or [0])[0]
                    cursor.execute("SELECT COUNT(*) FROM artists WHERE id IS NOT NULL")
                    total_artists = (cursor.fetchone() or [0])[0]
                    enrichment[service] = round(matched / total_artists * 100, 1) if total_artists else 0
                except Exception:
                    enrichment[service] = 0

            return {
                'total_tracks': total_tracks,
                'unplayed_count': unplayed,
                'unplayed_percentage': round(unplayed / total_tracks * 100, 1) if total_tracks else 0,
                'format_breakdown': format_breakdown,
                'total_duration_ms': total_duration_ms,
                'enrichment_coverage': enrichment,
            }
        except Exception as e:
            logger.error(f"Error getting library health: {e}")
            return {}
        finally:
            if conn:
                conn.close()

    def get_db_storage_stats(self):
        """Get database storage breakdown by table."""
        import os
        conn = None
        try:
            # Total file size
            total_size = 0
            try:
                total_size = os.path.getsize(str(self.database_path))
            except Exception:
                pass

            conn = self._get_connection()
            cursor = conn.cursor()

            # Try dbstat first (real byte sizes)
            tables = []
            method = 'row_count'
            try:
                cursor.execute("""
                    SELECT name, SUM(pgsize) as size
                    FROM dbstat
                    WHERE name IN (SELECT name FROM sqlite_master WHERE type='table')
                    GROUP BY name
                    ORDER BY size DESC
                """)
                tables = [{'name': r[0], 'size': r[1]} for r in cursor.fetchall()]
                method = 'dbstat'
            except Exception:
                # Fallback: row counts per table
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
                for row in cursor.fetchall():
                    tbl = row[0]
                    try:
                        cursor.execute(f"SELECT COUNT(*) FROM [{tbl}]")
                        count = cursor.fetchone()[0]
                        tables.append({'name': tbl, 'size': count})
                    except Exception:
                        pass
                tables.sort(key=lambda x: x['size'], reverse=True)

            return {
                'tables': tables,
                'total_file_size': total_size,
                'method': method,
            }
        except Exception as e:
            logger.error(f"Error getting db storage stats: {e}")
            return {'tables': [], 'total_file_size': 0, 'method': 'error'}
        finally:
            if conn:
                conn.close()

    @staticmethod
    def _listening_time_filter(time_range, alias=''):
        """Build a WHERE clause for time-range filtering."""
        prefix = f"{alias}." if alias else ""
        if time_range == '7d':
            return f"WHERE {prefix}played_at >= datetime('now', '-7 days')"
        elif time_range == '30d':
            return f"WHERE {prefix}played_at >= datetime('now', '-30 days')"
        elif time_range == '12m':
            return f"WHERE {prefix}played_at >= datetime('now', '-12 months')"
        else:
            return "WHERE 1=1"

    def set_profile_spotify(self, profile_id: int, client_id: str, client_secret: str,
                            redirect_uri: str = '') -> bool:
        """Save Spotify API credentials for a profile (encrypted)."""
        try:
            from config.settings import config_manager
            enc_id = config_manager._encrypt_value(client_id) if client_id else None
            enc_secret = config_manager._encrypt_value(client_secret) if client_secret else None
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE profiles
                    SET spotify_client_id = ?, spotify_client_secret = ?, spotify_redirect_uri = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (enc_id, enc_secret, redirect_uri or None, profile_id))
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error setting Spotify credentials for profile {profile_id}: {e}")
            return False

    def get_profile_spotify(self, profile_id: int) -> Dict[str, Any]:
        """Get decrypted Spotify credentials for a profile."""
        try:
            from config.settings import config_manager
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT spotify_client_id, spotify_client_secret, spotify_redirect_uri,
                           spotify_access_token, spotify_refresh_token
                    FROM profiles WHERE id = ?
                """, (profile_id,))
                row = cursor.fetchone()
                if not row or not row[0]:
                    return {}
                return {
                    'client_id': config_manager._decrypt_value(row[0]) if row[0] else '',
                    'client_secret': config_manager._decrypt_value(row[1]) if row[1] else '',
                    'redirect_uri': row[2] or '',
                    'access_token': config_manager._decrypt_value(row[3]) if row[3] else '',
                    'refresh_token': config_manager._decrypt_value(row[4]) if row[4] else '',
                }
        except Exception as e:
            logger.error(f"Error getting Spotify credentials for profile {profile_id}: {e}")
            return {}

    def set_profile_spotify_tokens(self, profile_id: int, access_token: str, refresh_token: str) -> bool:
        """Save Spotify OAuth tokens for a profile (from auth callback)."""
        try:
            from config.settings import config_manager
            enc_access = config_manager._encrypt_value(access_token) if access_token else None
            enc_refresh = config_manager._encrypt_value(refresh_token) if refresh_token else None
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE profiles
                    SET spotify_access_token = ?, spotify_refresh_token = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (enc_access, enc_refresh, profile_id))
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error setting Spotify tokens for profile {profile_id}: {e}")
            return False

    def set_profile_server_library(self, profile_id: int, server_type: str,
                                    library_id: str = None, user_id: str = None) -> bool:
        """Save media server library/user selection for a profile."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                if server_type == 'plex':
                    cursor.execute("UPDATE profiles SET plex_library_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                                   (library_id, profile_id))
                elif server_type == 'jellyfin':
                    cursor.execute("UPDATE profiles SET jellyfin_user_id = ?, jellyfin_library_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                                   (user_id, library_id, profile_id))
                elif server_type == 'navidrome':
                    cursor.execute("UPDATE profiles SET navidrome_library_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                                   (library_id, profile_id))
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error setting server library for profile {profile_id}: {e}")
            return False

    def get_profile_server_library(self, profile_id: int) -> Dict[str, Any]:
        """Get media server library/user selection for a profile."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT plex_library_id, jellyfin_user_id, jellyfin_library_id, navidrome_library_id
                    FROM profiles WHERE id = ?
                """, (profile_id,))
                row = cursor.fetchone()
                if not row:
                    return {}
                return {
                    'plex_library_id': row[0],
                    'jellyfin_user_id': row[1],
                    'jellyfin_library_id': row[2],
                    'navidrome_library_id': row[3],
                }
        except Exception as e:
            logger.error(f"Error getting server library for profile {profile_id}: {e}")
            return {}

    def _add_spotify_library_cache_table(self, cursor):
        """Create spotify_library_cache table for caching user's saved Spotify albums"""
        try:
            cursor.execute("SELECT value FROM metadata WHERE key = 'spotify_library_cache_v1' LIMIT 1")
            if cursor.fetchone():
                return  # Already migrated

            logger.info("Creating spotify_library_cache table...")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS spotify_library_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    spotify_album_id TEXT NOT NULL,
                    album_name TEXT NOT NULL,
                    artist_name TEXT NOT NULL,
                    artist_id TEXT,
                    release_date TEXT,
                    total_tracks INTEGER DEFAULT 0,
                    album_type TEXT DEFAULT 'album',
                    image_url TEXT,
                    date_saved TEXT,
                    cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    profile_id INTEGER DEFAULT 1,
                    UNIQUE(spotify_album_id, profile_id)
                )
            """)

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_spotify_library_album_id ON spotify_library_cache (spotify_album_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_spotify_library_profile ON spotify_library_cache (profile_id)")

            cursor.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('spotify_library_cache_v1', '1')")

            logger.info("spotify_library_cache table created successfully")

        except Exception as e:
            logger.error(f"Error creating spotify_library_cache table: {e}")

    def _add_metadata_cache_tables(self, cursor):
        """Create metadata_cache_entities and metadata_cache_searches tables for universal API response caching"""
        try:
            cursor.execute("SELECT value FROM metadata WHERE key = 'metadata_cache_v1' LIMIT 1")
            if cursor.fetchone():
                return  # Already migrated

            logger.info("Creating metadata cache tables...")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS metadata_cache_entities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    image_url TEXT,
                    external_urls TEXT,
                    genres TEXT,
                    popularity INTEGER,
                    followers INTEGER,
                    artist_name TEXT,
                    artist_id TEXT,
                    release_date TEXT,
                    total_tracks INTEGER,
                    album_type TEXT,
                    label TEXT,
                    album_name TEXT,
                    album_id TEXT,
                    duration_ms INTEGER,
                    track_number INTEGER,
                    disc_number INTEGER,
                    explicit INTEGER,
                    isrc TEXT,
                    preview_url TEXT,
                    raw_json TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_accessed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    access_count INTEGER DEFAULT 1,
                    ttl_days INTEGER DEFAULT 30,
                    UNIQUE(source, entity_type, entity_id)
                )
            """)

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_mce_lookup ON metadata_cache_entities (source, entity_type, entity_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_mce_name ON metadata_cache_entities (entity_type, name COLLATE NOCASE)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_mce_artist ON metadata_cache_entities (artist_name COLLATE NOCASE)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_mce_accessed ON metadata_cache_entities (last_accessed_at)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_mce_source ON metadata_cache_entities (source)")
            # Composite indexes for browse queries (entity_type + sort column)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_mce_browse ON metadata_cache_entities (entity_type, source, last_accessed_at DESC)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_mce_browse_name ON metadata_cache_entities (entity_type, source, name COLLATE NOCASE)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_mce_browse_pop ON metadata_cache_entities (entity_type, source, popularity DESC)")
            # Stats query index (covers GROUP BY entity_type, source with count)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_mce_stats ON metadata_cache_entities (entity_type, source, access_count)")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS metadata_cache_searches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    search_type TEXT NOT NULL,
                    query_normalized TEXT NOT NULL,
                    query_original TEXT NOT NULL,
                    result_ids TEXT NOT NULL,
                    result_count INTEGER NOT NULL,
                    search_limit INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_accessed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    access_count INTEGER DEFAULT 1,
                    ttl_days INTEGER DEFAULT 7,
                    UNIQUE(source, search_type, query_normalized, search_limit)
                )
            """)

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_mcs_lookup ON metadata_cache_searches (source, search_type, query_normalized)")

            cursor.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('metadata_cache_v1', '1')")

            logger.info("Metadata cache tables created successfully")

        except Exception as e:
            logger.error(f"Error creating metadata cache tables: {e}")

    def _add_repair_worker_tables(self, cursor):
        """Create repair_findings and repair_job_runs tables for the multi-job repair worker."""
        try:
            cursor.execute("SELECT value FROM metadata WHERE key = 'repair_worker_v2' LIMIT 1")
            if cursor.fetchone():
                return  # Already migrated

            logger.info("Creating repair worker v2 tables...")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS repair_findings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    finding_type TEXT NOT NULL,
                    severity TEXT NOT NULL DEFAULT 'info',
                    status TEXT NOT NULL DEFAULT 'pending',
                    entity_type TEXT,
                    entity_id TEXT,
                    file_path TEXT,
                    title TEXT NOT NULL,
                    description TEXT,
                    details_json TEXT DEFAULT '{}',
                    user_action TEXT,
                    resolved_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_rf_job ON repair_findings (job_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_rf_status ON repair_findings (status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_rf_type ON repair_findings (finding_type)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_rf_created ON repair_findings (created_at)")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS repair_job_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    started_at TIMESTAMP NOT NULL,
                    finished_at TIMESTAMP,
                    duration_seconds REAL,
                    items_scanned INTEGER DEFAULT 0,
                    findings_created INTEGER DEFAULT 0,
                    auto_fixed INTEGER DEFAULT 0,
                    errors INTEGER DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'running'
                )
            """)

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_rjr_job ON repair_job_runs (job_id)")

            cursor.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('repair_worker_v2', '1')")

            logger.info("Repair worker v2 tables created successfully")

        except Exception as e:
            logger.error(f"Error creating repair worker v2 tables: {e}")

    # ── Profile CRUD ──────────────────────────────────────────────────

    def get_all_profiles(self) -> List[Dict[str, Any]]:
        """Get all profiles"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='profiles'")
                if not cursor.fetchone():
                    return [{'id': 1, 'name': 'Admin', 'avatar_color': '#6366f1', 'avatar_url': None, 'is_admin': True, 'has_pin': False}]
                cursor.execute("SELECT * FROM profiles ORDER BY id")
                rows = cursor.fetchall()
                columns = [desc[0] for desc in cursor.description]
                results = []
                for row in rows:
                    ap_raw = row['allowed_pages'] if 'allowed_pages' in columns else None
                    results.append({
                        'id': row['id'],
                        'name': row['name'],
                        'avatar_color': row['avatar_color'],
                        'avatar_url': row['avatar_url'] if 'avatar_url' in columns else None,
                        'is_admin': bool(row['is_admin']),
                        'has_pin': row['pin_hash'] is not None,
                        'home_page': row['home_page'] if 'home_page' in columns else None,
                        'allowed_pages': json.loads(ap_raw) if ap_raw else None,
                        'can_download': bool(row['can_download']) if 'can_download' in columns else True,
                        'has_listenbrainz': row['listenbrainz_token'] is not None if 'listenbrainz_token' in columns else False,
                        'listenbrainz_username': row['listenbrainz_username'] if 'listenbrainz_username' in columns else None,
                        'created_at': row['created_at'],
                        'updated_at': row['updated_at'],
                    })
                return results
        except Exception as e:
            logger.error(f"Error getting profiles: {e}")
            return [{'id': 1, 'name': 'Admin', 'avatar_color': '#6366f1', 'avatar_url': None, 'is_admin': True, 'has_pin': False}]

    def get_profile(self, profile_id: int) -> Optional[Dict[str, Any]]:
        """Get a single profile by ID"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM profiles WHERE id = ?", (profile_id,))
                row = cursor.fetchone()
                if row:
                    columns = [desc[0] for desc in cursor.description]
                    ap_raw = row['allowed_pages'] if 'allowed_pages' in columns else None
                    return {
                        'id': row['id'],
                        'name': row['name'],
                        'avatar_color': row['avatar_color'],
                        'avatar_url': row['avatar_url'] if 'avatar_url' in columns else None,
                        'is_admin': bool(row['is_admin']),
                        'has_pin': row['pin_hash'] is not None,
                        'home_page': row['home_page'] if 'home_page' in columns else None,
                        'allowed_pages': json.loads(ap_raw) if ap_raw else None,
                        'can_download': bool(row['can_download']) if 'can_download' in columns else True,
                        'has_listenbrainz': row['listenbrainz_token'] is not None if 'listenbrainz_token' in columns else False,
                        'listenbrainz_username': row['listenbrainz_username'] if 'listenbrainz_username' in columns else None,
                        'created_at': row['created_at'],
                        'updated_at': row['updated_at'],
                    }
                return None
        except Exception as e:
            logger.error(f"Error getting profile {profile_id}: {e}")
            return None

    def create_profile(self, name: str, avatar_color: str = '#6366f1',
                       pin_hash: Optional[str] = None, is_admin: bool = False,
                       avatar_url: Optional[str] = None, home_page: Optional[str] = None,
                       allowed_pages: Optional[list] = None, can_download: bool = True) -> Optional[int]:
        """Create a new profile. Returns new profile ID or None on error."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                ap_json = json.dumps(allowed_pages) if allowed_pages is not None else None
                cursor.execute("""
                    INSERT INTO profiles (name, avatar_color, pin_hash, is_admin, avatar_url, home_page, allowed_pages, can_download)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (name, avatar_color, pin_hash, int(is_admin), avatar_url, home_page, ap_json, int(can_download)))
                conn.commit()
                return cursor.lastrowid
        except sqlite3.IntegrityError:
            logger.warning(f"Profile name '{name}' already exists")
            return None
        except Exception as e:
            logger.error(f"Error creating profile: {e}")
            return None

    def update_profile(self, profile_id: int, **kwargs) -> bool:
        """Update profile fields. Accepts: name, avatar_color, avatar_url, pin_hash, is_admin, home_page, allowed_pages, can_download."""
        allowed = {'name', 'avatar_color', 'avatar_url', 'pin_hash', 'is_admin', 'home_page', 'allowed_pages', 'can_download'}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        # Serialize allowed_pages list to JSON string for storage
        if 'allowed_pages' in updates:
            v = updates['allowed_pages']
            updates['allowed_pages'] = json.dumps(v) if v is not None else None
        if not updates:
            return False
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                set_clause = ', '.join(f"{k} = ?" for k in updates)
                values = list(updates.values())
                values.append(profile_id)
                cursor.execute(
                    f"UPDATE profiles SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    values
                )
                conn.commit()
                return cursor.rowcount > 0
        except sqlite3.IntegrityError:
            logger.warning(f"Profile update failed (duplicate name?)")
            return False
        except Exception as e:
            logger.error(f"Error updating profile {profile_id}: {e}")
            return False

    def delete_profile(self, profile_id: int) -> bool:
        """Delete a profile and all its per-profile data."""
        if profile_id == 1:
            return False  # Cannot delete the default admin profile
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                # Delete per-profile data from all tables
                for table in ['watchlist_artists', 'wishlist_tracks', 'similar_artists',
                              'discovery_pool', 'discovery_recent_albums', 'discovery_curated_playlists',
                              'bubble_snapshots', 'recent_releases']:
                    try:
                        cursor.execute(f"DELETE FROM {table} WHERE profile_id = ?", (profile_id,))
                    except Exception:
                        pass
                cursor.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error deleting profile {profile_id}: {e}")
            return False

    def verify_profile_pin(self, profile_id: int, pin: str) -> bool:
        """Verify a profile's PIN"""
        try:
            from werkzeug.security import check_password_hash
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT pin_hash FROM profiles WHERE id = ?", (profile_id,))
                row = cursor.fetchone()
                if not row or not row['pin_hash']:
                    return True  # No PIN set = always valid
                return check_password_hash(row['pin_hash'], pin)
        except Exception as e:
            logger.error(f"Error verifying PIN for profile {profile_id}: {e}")
            return False

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
                    logger.info(f"Removed {orphaned_artists_count} orphaned artists")
                
                # Delete orphaned albums  
                if orphaned_albums_count > 0:
                    cursor.execute("""
                        DELETE FROM albums 
                        WHERE id NOT IN (SELECT DISTINCT album_id FROM tracks WHERE album_id IS NOT NULL)
                    """)
                    logger.info(f"Removed {orphaned_albums_count} orphaned albums")
                
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
                    logger.debug("No duplicate artists found")
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

                    logger.info(f"Merging duplicate artist '{artist_name}' ({server_source}): IDs {ids}")

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
                    logger.info(f"Duplicate merge complete: {total_merged} duplicates merged, {total_albums_migrated} albums migrated")

                return {'artists_merged': total_merged, 'albums_migrated': total_albums_migrated}

        except Exception as e:
            logger.error(f"Error merging duplicate artists: {e}")
            return {'artists_merged': 0, 'albums_migrated': 0}

    # --- Removal detection helpers ---

    def get_all_artist_ids_for_server(self, server_source: str) -> set:
        """Get all artist IDs stored in the database for a specific server."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id FROM artists WHERE server_source = ?", (server_source,))
                return {row[0] for row in cursor.fetchall()}
        except Exception as e:
            logger.error(f"Error getting artist IDs for {server_source}: {e}")
            return set()

    def get_all_album_ids_for_server(self, server_source: str) -> set:
        """Get all album IDs stored in the database for a specific server."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id FROM albums WHERE server_source = ?", (server_source,))
                return {row[0] for row in cursor.fetchall()}
        except Exception as e:
            logger.error(f"Error getting album IDs for {server_source}: {e}")
            return set()

    def get_all_track_ids_for_server(self, server_source: str) -> set:
        """Get all track IDs stored in the database for a specific server."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id FROM tracks WHERE server_source = ?", (server_source,))
                return {row[0] for row in cursor.fetchall()}
        except Exception as e:
            logger.error(f"Error getting track IDs for {server_source}: {e}")
            return set()

    def delete_stale_tracks(self, stale_track_ids: set, server_source: str) -> int:
        """Delete tracks by ID+server_source that no longer exist on the media server.
        Processes in batches of 500 for database safety."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                batch_size = 500
                tracks_removed = 0

                track_list = list(stale_track_ids)
                for i in range(0, len(track_list), batch_size):
                    batch = track_list[i:i + batch_size]
                    placeholders = ','.join('?' * len(batch))
                    params = batch + [server_source]

                    cursor.execute(
                        f"DELETE FROM tracks WHERE id IN ({placeholders}) AND server_source = ?",
                        params)
                    tracks_removed += cursor.rowcount

                conn.commit()

                if tracks_removed > 0:
                    logger.info(f"Deep scan stale removal for {server_source}: "
                                f"{tracks_removed} tracks removed")

                return tracks_removed

        except Exception as e:
            logger.error(f"Error deleting stale tracks for {server_source}: {e}")
            return 0

    def delete_removed_content(self, removed_artist_ids: set, removed_album_ids: set,
                               server_source: str):
        """Delete artists and albums that were removed from the media server.
        Manually cascades deletes (tracks -> albums -> artists) to match existing patterns."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                batch_size = 500

                artists_removed = 0
                albums_removed = 0
                tracks_removed = 0

                # Remove artists and their children
                if removed_artist_ids:
                    artist_list = list(removed_artist_ids)
                    for i in range(0, len(artist_list), batch_size):
                        batch = artist_list[i:i + batch_size]
                        placeholders = ','.join('?' * len(batch))
                        params = batch + [server_source]

                        # Delete tracks belonging to these artists
                        cursor.execute(
                            f"SELECT COUNT(*) FROM tracks WHERE artist_id IN ({placeholders}) AND server_source = ?",
                            params)
                        tracks_removed += cursor.fetchone()[0]
                        cursor.execute(
                            f"DELETE FROM tracks WHERE artist_id IN ({placeholders}) AND server_source = ?",
                            params)

                        # Delete albums belonging to these artists
                        cursor.execute(
                            f"SELECT COUNT(*) FROM albums WHERE artist_id IN ({placeholders}) AND server_source = ?",
                            params)
                        albums_removed += cursor.fetchone()[0]
                        cursor.execute(
                            f"DELETE FROM albums WHERE artist_id IN ({placeholders}) AND server_source = ?",
                            params)

                        # Delete the artists themselves
                        cursor.execute(
                            f"DELETE FROM artists WHERE id IN ({placeholders}) AND server_source = ?",
                            params)
                        artists_removed += cursor.rowcount

                # Remove albums (not already handled by artist cascade above)
                if removed_album_ids:
                    album_list = list(removed_album_ids)
                    for i in range(0, len(album_list), batch_size):
                        batch = album_list[i:i + batch_size]
                        placeholders = ','.join('?' * len(batch))
                        params = batch + [server_source]

                        # Delete tracks belonging to these albums
                        cursor.execute(
                            f"SELECT COUNT(*) FROM tracks WHERE album_id IN ({placeholders}) AND server_source = ?",
                            params)
                        tracks_removed += cursor.fetchone()[0]
                        cursor.execute(
                            f"DELETE FROM tracks WHERE album_id IN ({placeholders}) AND server_source = ?",
                            params)

                        # Delete the albums themselves
                        cursor.execute(
                            f"DELETE FROM albums WHERE id IN ({placeholders}) AND server_source = ?",
                            params)
                        albums_removed += cursor.rowcount

                conn.commit()

                if artists_removed > 0 or albums_removed > 0:
                    logger.info(f"Removal cleanup for {server_source}: "
                                f"{artists_removed} artists, {albums_removed} albums, "
                                f"{tracks_removed} tracks removed")

                return {
                    'artists_removed': artists_removed,
                    'albums_removed': albums_removed,
                    'tracks_removed': tracks_removed
                }

        except Exception as e:
            logger.error(f"Error deleting removed content for {server_source}: {e}")
            return {'artists_removed': 0, 'albums_removed': 0, 'tracks_removed': 0}

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
                        logger.info(f"Artist ratingKey migrated: '{name}' ({old_id} → {artist_id})")

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
                    logger.info(f"Album ratingKey migrated: '{title}' ({old_id} → {album_id})")

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

                # Extract per-track artist for compilations/DJ mixes.
                # Only stored when it differs from the album artist.
                track_artist = None
                # Plex: originalTitle holds the per-track artist on compilation albums
                plex_original = getattr(track_obj, 'originalTitle', None)
                if plex_original and plex_original.strip():
                    track_artist = plex_original.strip()
                # Jellyfin/Emby: ArtistItems[0] is the track artist, may differ from album artist
                if not track_artist and hasattr(track_obj, '_data'):
                    raw = getattr(track_obj, '_data', {}) or {}
                    artist_items = raw.get('ArtistItems', [])
                    if artist_items:
                        jf_track_artist = artist_items[0].get('Name', '')
                        album_artists = raw.get('AlbumArtists', [])
                        jf_album_artist = album_artists[0].get('Name', '') if album_artists else ''
                        if jf_track_artist and jf_track_artist != jf_album_artist:
                            track_artist = jf_track_artist
                # Navidrome/Subsonic: artist attribute is per-track
                if not track_artist and hasattr(track_obj, 'artist') and isinstance(getattr(track_obj, 'artist', None), str):
                    nav_artist = getattr(track_obj, 'artist', '').strip()
                    # Compare against album artist name to only store when different
                    try:
                        artist_row = cursor.execute("SELECT name FROM artists WHERE id = ?", (artist_id,)).fetchone()
                        album_artist_name = artist_row[0] if artist_row else ''
                        if nav_artist and nav_artist.lower() != album_artist_name.lower():
                            track_artist = nav_artist
                    except Exception:
                        pass

                # Extract MusicBrainz recording ID from server if available (Navidrome provides this)
                mbid = getattr(track_obj, 'musicBrainzId', None) or None

                # Check if track already exists — UPDATE to preserve enrichment columns,
                # INSERT only for genuinely new tracks
                cursor.execute("SELECT 1 FROM tracks WHERE id = ? LIMIT 1", (track_id,))
                is_new_track = cursor.fetchone() is None

                if is_new_track:
                    cursor.execute("""
                        INSERT INTO tracks
                        (id, album_id, artist_id, title, track_number, duration, file_path, bitrate, server_source, track_artist, musicbrainz_recording_id, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """, (track_id, album_id, artist_id, title, track_number, duration, file_path, bitrate, server_source, track_artist, mbid))
                else:
                    # Update server-provided fields only — preserves spotify_track_id, deezer_id,
                    # isrc, bpm, and all other enrichment data
                    cursor.execute("""
                        UPDATE tracks
                        SET album_id = ?, artist_id = ?, title = ?, track_number = ?,
                            duration = ?, file_path = ?, bitrate = ?, server_source = ?,
                            track_artist = COALESCE(?, track_artist),
                            musicbrainz_recording_id = COALESCE(?, musicbrainz_recording_id),
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                    """, (album_id, artist_id, title, track_number, duration, file_path, bitrate, server_source, track_artist, mbid, track_id))

                conn.commit()

                # Log new imports to library history
                if is_new_track:
                    try:
                        cursor.execute("SELECT name FROM artists WHERE id = ?", (artist_id,))
                        artist_row = cursor.fetchone()
                        cursor.execute("SELECT title, thumb_url FROM albums WHERE id = ?", (album_id,))
                        album_row = cursor.fetchone()
                        self.add_library_history_entry(
                            event_type='import',
                            title=title,
                            artist_name=artist_row[0] if artist_row else None,
                            album_name=album_row[0] if album_row else None,
                            server_source=server_source,
                            file_path=file_path,
                            thumb_url=album_row[1] if album_row and len(album_row) > 1 else None
                        )
                    except Exception:
                        pass  # Non-critical history logging

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
    
    def search_artists(self, query: str, limit: int = 50, server_source: str = None) -> List[DatabaseArtist]:
        """Search artists by name, optionally filtered by server source.
        Uses diacritic-insensitive matching so 'Tiesto' finds 'Tiësto'."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            norm_query = f"%{self._normalize_for_comparison(query)}%"

            if server_source:
                cursor.execute("""
                    SELECT * FROM artists
                    WHERE unidecode_lower(name) LIKE ? AND server_source = ?
                    ORDER BY name
                    LIMIT ?
                """, (norm_query, server_source, limit))
            else:
                cursor.execute("""
                    SELECT * FROM artists
                    WHERE unidecode_lower(name) LIKE ?
                    ORDER BY name
                    LIMIT ?
                """, (norm_query, limit))
            
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
                logger.debug(f"Basic search found {len(basic_results)} results")
                return basic_results

            # STRATEGY 2: Broader fuzzy search - splits into individual words with OR matching
            fuzzy_results = self._search_tracks_fuzzy_fallback(cursor, title, artist, limit, server_source)
            if fuzzy_results:
                logger.debug(f"Fuzzy fallback search found {len(fuzzy_results)} results")
            
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
            norm_artist = f"%{self._normalize_for_comparison(artist)}%"
            where_conditions.append("(unidecode_lower(artists.name) LIKE ? OR unidecode_lower(COALESCE(tracks.track_artist, '')) LIKE ?)")
            params.append(norm_artist)
            params.append(norm_artist)

        # Add server filter if specified
        if server_source:
            where_conditions.append("tracks.server_source = ?")
            params.append(server_source)
        
        if not where_conditions:
            return []
        
        where_clause = " AND ".join(where_conditions)
        params.append(limit)
        
        cursor.execute(f"""
            SELECT tracks.*, artists.name as artist_name, albums.title as album_title, albums.thumb_url as album_thumb_url
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
            like_conditions.append("(unidecode_lower(tracks.title) LIKE ? OR unidecode_lower(artists.name) LIKE ? OR unidecode_lower(COALESCE(tracks.track_artist, '')) LIKE ?)")
            params.extend([f"%{term}%", f"%{term}%", f"%{term}%"])
        
        if not like_conditions:
            return []
        
        # Build WHERE clause with optional server filter
        where_parts = [f"({' OR '.join(like_conditions)})"]
        if server_source:
            where_parts.append("tracks.server_source = ?")
            params.append(server_source)  # Append after LIKE params, before LIMIT
        
        where_clause = " AND ".join(where_parts)
        params.append(limit * 3)  # Get more results for scoring
        
        cursor.execute(f"""
            SELECT tracks.*, artists.name as artist_name, albums.title as album_title, albums.thumb_url as album_thumb_url
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
            track.album_thumb_url = row['album_thumb_url'] if 'album_thumb_url' in row.keys() else ''
            track.server_source = row['server_source'] if 'server_source' in row.keys() else ''
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

    
    def check_track_exists(self, title: str, artist: str, confidence_threshold: float = 0.8, server_source: str = None, album: str = None) -> Tuple[Optional[DatabaseTrack], float]:
        """
        Check if a track exists in the database with enhanced fuzzy matching and confidence scoring.

        Args:
            album: Optional album name — enables album-aware matching for multi-artist albums

        Returns (track, confidence) tuple where confidence is 0.0-1.0
        """
        try:
            # Generate title variations for better matching (similar to album approach)
            title_variations = self._generate_track_title_variations(title)
            
            logger.debug(f"Enhanced track matching for '{title}' by '{artist}': trying {len(title_variations)} variations")
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
                
                logger.debug(f"Found {len(potential_matches)} tracks for variation '{title_variation}'")
                
                # Score each potential match
                for track in potential_matches:
                    confidence = self._calculate_track_confidence(title, artist, track)
                    logger.debug(f"  '{track.title}' confidence: {confidence:.3f}")
                    
                    if confidence > best_confidence:
                        best_confidence = confidence
                        best_match = track
            
            # Return match only if it meets threshold
            if best_match and best_confidence >= confidence_threshold:
                logger.debug(f"Enhanced track match found: '{title}' -> '{best_match.title}' (confidence: {best_confidence:.3f})")
                return best_match, best_confidence

            # Album-aware fallback: find album by title (any artist), check tracks on it
            # Handles multi-artist albums filed under a different artist in the library
            if album and best_confidence < confidence_threshold:
                logger.debug(f"Artist-specific search failed, trying album-aware fallback: '{title}' on '{album}'")
                try:
                    album_candidates = self.search_albums(title=album, artist="", limit=10, server_source=server_source)
                    for album_candidate in album_candidates:
                        album_title_sim = max(
                            self._string_similarity(self._normalize_for_comparison(album), self._normalize_for_comparison(album_candidate.title)),
                            self._string_similarity(self._clean_album_title_for_comparison(album), self._clean_album_title_for_comparison(album_candidate.title))
                        )
                        if album_title_sim < 0.8:
                            continue

                        conn = self._get_connection()
                        cursor = conn.cursor()
                        source_filter = "AND t.server_source = ?" if server_source else ""
                        params = [album_candidate.id] + ([server_source] if server_source else [])
                        cursor.execute(f"""
                            SELECT t.*, a.name as artist_name, al.title as album_title
                            FROM tracks t
                            JOIN artists a ON a.id = t.artist_id
                            JOIN albums al ON al.id = t.album_id
                            WHERE t.album_id = ? {source_filter}
                        """, params)

                        for row in cursor.fetchall():
                            db_track = DatabaseTrack(
                                id=row['id'], title=row['title'], artist_name=row['artist_name'],
                                album_title=row['album_title'], album_id=row['album_id'],
                                track_number=row['track_number'], duration=row['duration'],
                                file_path=row['file_path'], bitrate=row['bitrate'],
                                artist_id=row['artist_id'], server_source=row['server_source']
                            )
                            title_sim = max(
                                self._string_similarity(self._normalize_for_comparison(title), self._normalize_for_comparison(db_track.title)),
                                self._string_similarity(self._clean_track_title_for_comparison(title), self._clean_track_title_for_comparison(db_track.title))
                            )
                            if title_sim > best_confidence and title_sim >= 0.7:
                                best_confidence = title_sim
                                best_match = db_track

                        if best_match and best_confidence >= 0.7:
                            logger.debug(f"Album-aware fallback matched: '{title}' on '{album}' -> '{best_match.title}' by '{best_match.artist_name}' (title_sim: {best_confidence:.3f})")
                            return best_match, best_confidence
                except Exception as album_fallback_err:
                    logger.debug(f"Album-aware fallback error: {album_fallback_err}")

            logger.debug(f"No confident track match for '{title}' (best: {best_confidence:.3f}, threshold: {confidence_threshold})")
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

        # Censored title detection: Apple Music returns "B*****t" for "Bullshit"
        # Asterisks replace middle characters — word count matches, non-censored words match,
        # censored words share first char and non-asterisk trailing chars
        if '*' in s1 or '*' in s2:
            censored, uncensored = (s1, s2) if '*' in s1 else (s2, s1)
            c_words = censored.lower().split()
            u_words = uncensored.lower().split()
            if len(c_words) == len(u_words):
                all_match = True
                for cw, uw in zip(c_words, u_words):
                    if '*' in cw:
                        # Strip asterisks to get the visible prefix/suffix
                        # "b*****t" → prefix "b", suffix "t"
                        # "f**k" → prefix "f", suffix "k"
                        prefix = cw.split('*')[0]
                        suffix = cw.rstrip('*').split('*')[-1] if not cw.endswith('*') else ''
                        if not uw.startswith(prefix):
                            all_match = False
                            break
                        if suffix and not uw.endswith(suffix):
                            all_match = False
                            break
                    else:
                        if cw != uw:
                            all_match = False
                            break
                if all_match:
                    return 1.0

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
            # Count DISTINCT titles to deduplicate across split/duplicate album entries
            # (e.g., 3 "GNX" albums with 12+1+2 tracks = 15 rows but only 12 unique songs)
            placeholders = ','.join('?' for _ in sibling_ids)
            cursor.execute(f"""
                SELECT COUNT(*) FROM (
                    SELECT DISTINCT LOWER(title), track_number FROM tracks
                    WHERE album_id IN ({placeholders}) AND file_path IS NOT NULL AND file_path != ''
                )
            """, sibling_ids)
            owned_tracks = cursor.fetchone()[0]

            # Get the max track_count from sibling albums (not SUM — avoids inflating from duplicates)
            cursor.execute(f"SELECT MAX(track_count) FROM albums WHERE id IN ({placeholders})", sibling_ids)
            result = cursor.fetchone()
            stored_track_count = result[0] if result and result[0] else 0

            # Use provided expected count if available, otherwise use stored count.
            # However, if the album is complete by its own stored metadata, prefer the stored
            # count so edition differences don't make a complete album appear incomplete.
            # e.g. user has standard edition (12 tracks, all present) but Spotify returns
            # deluxe edition count (20) — should show as complete, not 12/20.
            if (expected_track_count is not None and stored_track_count > 0
                    and owned_tracks >= stored_track_count
                    and stored_track_count >= expected_track_count * 0.6):
                # Album is complete by its own metadata — standard vs deluxe edition difference
                expected_tracks = stored_track_count
            elif expected_track_count is not None:
                expected_tracks = expected_track_count
            else:
                expected_tracks = stored_track_count

            # Determine completeness with refined thresholds
            if expected_tracks and expected_tracks > 0:
                # Exact match — complete only when owned == expected
                is_complete = owned_tracks >= expected_tracks
            else:
                # No expected count known — complete if we have any tracks
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
            
            logger.debug(f"Edition matching for '{title}' by '{artist}': trying {len(title_variations)} variations")
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
                    logger.debug(f"Found {len(albums)} albums for variation '{variation}'")
                
                if not albums:
                    continue
                
                # Score each potential match with Smart Edition Matching
                for album in albums:
                    confidence = self._calculate_album_confidence(title, artist, album, expected_track_count)
                    logger.debug(f"  '{album.title}' confidence: {confidence:.3f}")
                    
                    if confidence > best_confidence:
                        best_confidence = confidence
                        best_match = album
            
            # Return match only if it meets threshold
            if best_match and best_confidence >= confidence_threshold:
                logger.debug(f"Edition match found: '{title}' -> '{best_match.title}' (confidence: {best_confidence:.3f})")
                return best_match, best_confidence
            
            # Fallback: Check ALL albums by this artist (resolves SQL accent sensitivity issues #101)
            # If we haven't found a match yet, fetch broader list from artist and double check
            if best_confidence < confidence_threshold:
                logger.debug(f"specific title search failed, trying broad artist search fallback for '{artist}'")
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
                            logger.debug(f"  Fallback match: '{album.title}' confidence: {confidence:.3f}")
                except Exception as fallback_error:
                     logger.warning(f"Fallback artist search failed: {fallback_error}")

            if best_match and best_confidence >= confidence_threshold:
                 logger.debug(f"Fallback match succeeded: '{title}' -> '{best_match.title}' (confidence: {best_confidence:.3f})")
                 return best_match, best_confidence

            # Multi-artist fallback: search by title only (any artist)
            # Handles collaborative albums filed under a different artist in the library
            if best_confidence < confidence_threshold:
                logger.debug(f"Artist-specific search failed, trying title-only fallback for '{title}'")
                try:
                    title_only_albums = self.search_albums(title=title, artist="", limit=20, server_source=server_source)
                    for album in title_only_albums:
                        confidence = self._calculate_album_confidence(title, artist, album, expected_track_count)
                        # Slightly penalize cross-artist matches to prefer same-artist when possible
                        if confidence > best_confidence:
                            best_confidence = confidence
                            best_match = album
                            logger.debug(f"  Title-only match: '{album.title}' (confidence: {confidence:.3f})")
                except Exception as title_error:
                    logger.warning(f"Title-only fallback search failed: {title_error}")

            if best_match and best_confidence >= confidence_threshold:
                logger.debug(f"Title-only match succeeded: '{title}' -> '{best_match.title}' (confidence: {best_confidence:.3f})")
                return best_match, best_confidence

            logger.debug(f"No confident edition match for '{title}' (best: {best_confidence:.3f}, threshold: {confidence_threshold})")
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
        # Specific patterns first, generic catch-alls last (first match wins due to break)
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
            r'\s+collector\'?s?\s*edition?$': ['collectors', 'collectors edition'],
            r'\s*\(collector\'?s?\s*edition?\)': ['collectors', 'collectors edition'],
            # Generic catch-alls for any edition in parens/brackets (e.g. Silver Edition, MMXI Special Edition)
            r'\s*\([^)]*\bedition\b[^)]*\)': ['edition'],
            r'\s*\[[^\]]*\bedition\b[^\]]*\]': ['edition'],
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
                'anniversary edition',
                "collector's edition",
                'collectors edition',
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
            common_editions = ['Deluxe Edition', 'Deluxe', 'Platinum Edition', 'Special Edition', "Collector's Edition", 'Collectors Edition']
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
                logger.debug(f"  Diacritic normalization improved match: '{search_title}' -> '{db_album.title}' (normalized: {normalized_title_similarity:.3f} vs raw: {title_similarity:.3f})")

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
                    logger.debug(f"  Edition upgrade bonus: +{edition_bonus:.3f} ({db_album.track_count} >= {expected_track_count} tracks)")
                elif db_album.track_count < expected_track_count * 0.8:
                    # Found significantly smaller edition, apply penalty
                    edition_penalty = 0.1
                    confidence -= edition_penalty
                    logger.debug(f"  Edition downgrade penalty: -{edition_penalty:.3f} ({db_album.track_count} << {expected_track_count} tracks)")
            
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
                logger.debug(f"Unicode normalization:")
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

            # Length ratio penalty: if the DB title is significantly longer/shorter than the
            # search title, it's likely a different track (e.g. "Believe" vs "Believe In Me").
            # SequenceMatcher gives high scores when the shorter string is fully contained
            # in the longer one, which causes false positives for prefix/suffix matches.
            len_search = len(clean_search_title) if clean_search_title else len(search_title_norm)
            len_db = len(clean_db_title) if clean_db_title else len(db_title_norm)
            if len_search > 0 and len_db > 0:
                len_ratio = min(len_search, len_db) / max(len_search, len_db)
                if len_ratio < 0.7:
                    # Titles differ in length by more than 30% — penalize heavily
                    best_title_similarity *= len_ratio

            # Require minimum title similarity to prevent a perfect artist match from
            # carrying a bad title match over the threshold (e.g. "Time" vs "Time Flies")
            if best_title_similarity < 0.6:
                return best_title_similarity * 0.5  # Can never exceed 0.3, well below any threshold

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

        # PRE-STEP: Handle "(with Artist)" featuring BEFORE bracket removal.
        # This catches "with" only when used as featuring syntax inside brackets,
        # NOT when "with" is part of the song title like "Stay With Me".
        # e.g. "Levitating (with DaBaby)" → "Levitating"
        #      "Stay (with Justin Bieber)" → "Stay"
        #      "Stay With Me" → unchanged (no brackets around "with")
        cleaned = re.sub(r'\s*\(with\s+[^)]*\)', '', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'\s*\[with\s+[^\]]*\]', '', cleaned, flags=re.IGNORECASE)

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

            # Remasters (same recording, different mastering)
            r'\s*\d{4}\s*remaster.*',  # Remove "2015 remaster"
            r'\s*remaster.*',       # Remove "remaster/remastered"
            r'\s*remastered.*',     # Remove "remastered"

            # NOTE: Edit versions (radio edit, single edit, album edit) are NOT
            # removed here — they are treated as different versions by
            # matching_engine.similarity_score() which applies a 0.30 penalty.
            # Removing them here would override that penalty via max() and
            # cause incorrect matches (e.g. radio edit matched to full version).

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
        # - radio edit, single edit, album edit (different cuts)
        # These are handled by matching_engine.similarity_score() which applies penalties

        for pattern in patterns_to_remove:
            cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE).strip()

        # STEP 3: Clean up extra spaces
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()

        return cleaned
    
    def _clean_album_title_for_comparison(self, title: str) -> str:
        """Clean album title by removing edition markers for comparison"""
        cleaned = title.lower()

        # Remove common edition patterns (specific first, then generic catch-alls)
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
            # Generic catch-alls: any parenthesized/bracketed text containing "edition"
            # Handles "Silver Edition", "MMXI Special Edition", "Limited Edition", etc.
            r'\s*\([^)]*\bedition\b[^)]*\)',
            r'\s*\[[^\]]*\bedition\b[^\]]*\]',
            r'\s*-\s+\w+\s+edition\s*$',
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

    def save_bubble_snapshot(self, snapshot_type: str, data_dict: dict, profile_id: int = 1):
        """Save a bubble snapshot (upserts by type + profile).

        Args:
            snapshot_type: One of 'artist_bubbles', 'search_bubbles', 'discover_downloads'
            data_dict: The bubbles/downloads dict to persist
            profile_id: Profile to save for
        """
        from datetime import datetime
        now = datetime.now()
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                # Check if profile_id column exists
                cursor.execute("PRAGMA table_info(bubble_snapshots)")
                cols = {c[1] for c in cursor.fetchall()}
                if 'profile_id' in cols:
                    # Delete existing entry for this profile+type, then insert
                    cursor.execute("DELETE FROM bubble_snapshots WHERE type = ? AND profile_id = ?",
                                   (snapshot_type, profile_id))
                    cursor.execute(
                        "INSERT INTO bubble_snapshots (type, data, timestamp, snapshot_id, profile_id) VALUES (?, ?, ?, ?, ?)",
                        (snapshot_type, json.dumps(data_dict), now.isoformat(), now.strftime('%Y%m%d_%H%M%S'), profile_id)
                    )
                else:
                    cursor.execute(
                        "INSERT OR REPLACE INTO bubble_snapshots (type, data, timestamp, snapshot_id) VALUES (?, ?, ?, ?)",
                        (snapshot_type, json.dumps(data_dict), now.isoformat(), now.strftime('%Y%m%d_%H%M%S'))
                    )
                conn.commit()
        except Exception as e:
            logger.error(f"Error saving bubble snapshot '{snapshot_type}': {e}")
            raise

    def get_bubble_snapshot(self, snapshot_type: str, profile_id: int = 1) -> Optional[Dict[str, Any]]:
        """Load a bubble snapshot for the given profile.

        Returns:
            {'data': dict, 'timestamp': str} or None if not found
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("PRAGMA table_info(bubble_snapshots)")
                cols = {c[1] for c in cursor.fetchall()}
                if 'profile_id' in cols:
                    cursor.execute("SELECT data, timestamp FROM bubble_snapshots WHERE type = ? AND profile_id = ?",
                                   (snapshot_type, profile_id))
                else:
                    cursor.execute("SELECT data, timestamp FROM bubble_snapshots WHERE type = ?", (snapshot_type,))
                row = cursor.fetchone()
                if row:
                    return {'data': json.loads(row['data']), 'timestamp': row['timestamp']}
                return None
        except Exception as e:
            logger.error(f"Error getting bubble snapshot '{snapshot_type}': {e}")
            return None

    def delete_bubble_snapshot(self, snapshot_type: str, profile_id: int = 1):
        """Delete a bubble snapshot for the given profile."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("PRAGMA table_info(bubble_snapshots)")
                cols = {c[1] for c in cursor.fetchall()}
                if 'profile_id' in cols:
                    cursor.execute("DELETE FROM bubble_snapshots WHERE type = ? AND profile_id = ?",
                                   (snapshot_type, profile_id))
                else:
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
                       source_type: str = "unknown", source_info: Dict[str, Any] = None,
                       profile_id: int = 1) -> bool:
        """Add a failed track to the wishlist for retry"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # Use track ID as unique identifier. Field name stays legacy-compatible.
                track_id = spotify_track_data.get('id')
                if not track_id:
                    logger.error("Cannot add track to wishlist: missing track ID")
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

                # Ensure album is a proper dict — repair if needed so display doesn't break
                album = spotify_track_data.get('album')
                if not album or not isinstance(album, dict):
                    spotify_track_data['album'] = {'name': track_name, 'images': []}
                    logger.info(f"Wishlist add: no album info for '{track_name}', using track name as fallback")
                elif not album.get('name') or album.get('name') in ('Unknown Album', ''):
                    album['name'] = track_name
                    logger.info(f"Wishlist add: missing album name for '{track_name}', using track name as fallback")

                # Check for duplicates by track name + artist (not just Spotify ID)
                # When allow_duplicates is True (default), same song from different albums can coexist
                from config.settings import config_manager
                allow_duplicates = config_manager.get('wishlist.allow_duplicate_tracks', True)

                if not allow_duplicates:
                    cursor.execute("""
                        SELECT id, spotify_track_id, spotify_data FROM wishlist_tracks
                        WHERE profile_id = ?
                    """, (profile_id,))

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
                                # Enhance mode: upsert existing entry with enhance bypass context
                                if source_type == 'enhance':
                                    source_json = json.dumps(source_info or {})
                                    cursor.execute("""
                                        UPDATE wishlist_tracks
                                        SET source_type = ?, source_info = ?, failure_reason = ?,
                                            spotify_data = ?, spotify_track_id = ?
                                        WHERE id = ?
                                    """, (source_type, source_json, failure_reason,
                                          json.dumps(spotify_track_data), track_id, existing['id']))
                                    conn.commit()
                                    logger.info(f"Upserted wishlist entry to enhance mode: '{track_name}' by {artist_name}")
                                    return True
                                logger.info(f"Skipping duplicate wishlist entry: '{track_name}' by {artist_name} (already exists as ID: {existing['id']})")
                                return False  # Already exists, don't add duplicate
                        except Exception as parse_error:
                            logger.warning(f"Error parsing existing wishlist track data: {parse_error}")
                            continue

                # Convert data to JSON strings
                spotify_json = json.dumps(spotify_track_data)
                source_json = json.dumps(source_info or {})

                # When allow_duplicates is on, make the key unique per album so the same
                # track from different albums can coexist in the wishlist
                insert_track_id = track_id
                if allow_duplicates:
                    album_obj = spotify_track_data.get('album', {})
                    album_id = album_obj.get('id', '') if isinstance(album_obj, dict) else ''
                    if album_id:
                        # Check if this exact track+album combo already exists
                        composite_id = f"{track_id}::{album_id}"
                        cursor.execute("SELECT id FROM wishlist_tracks WHERE spotify_track_id = ? AND profile_id = ?",
                                       (composite_id, profile_id))
                        if cursor.fetchone():
                            logger.debug(f"Skipping wishlist entry — same track+album already in wishlist: '{track_name}' on '{album_obj.get('name', '')}'")
                            return False
                        # Check if base track_id exists (from a different album)
                        cursor.execute("SELECT id FROM wishlist_tracks WHERE spotify_track_id = ? AND profile_id = ?",
                                       (track_id, profile_id))
                        if cursor.fetchone():
                            # Same track exists from different album — use composite ID
                            insert_track_id = composite_id

                # Insert the track
                cursor.execute("""
                    INSERT OR REPLACE INTO wishlist_tracks
                    (spotify_track_id, spotify_data, failure_reason, source_type, source_info, date_added, profile_id)
                    VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?)
                """, (insert_track_id, spotify_json, failure_reason, source_type, source_json, profile_id))

                conn.commit()

                logger.info(f"Added track to wishlist: '{track_name}' by {artist_name}")
                return True

        except Exception as e:
            logger.error(f"Error adding track to wishlist: {e}")
            return False
    
    def remove_from_wishlist(self, spotify_track_id: str, profile_id: int = 1) -> bool:
        """Remove a track from the wishlist (typically after successful download)"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM wishlist_tracks WHERE spotify_track_id = ? AND profile_id = ?",
                               (spotify_track_id, profile_id))
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
    
    def get_wishlist_tracks(self, limit: Optional[int] = None, profile_id: int = 1) -> List[Dict[str, Any]]:
        """Get all tracks in the wishlist for the given profile, ordered by date added (oldest first for retry priority)"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                query = """
                    SELECT id, spotify_track_id, spotify_data, failure_reason, retry_count,
                           last_attempted, date_added, source_type, source_info
                    FROM wishlist_tracks
                    WHERE profile_id = ?
                    ORDER BY date_added
                """

                params = [profile_id]
                if limit:
                    query += f" LIMIT {limit}"

                cursor.execute(query, params)
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
    
    def update_wishlist_retry(self, spotify_track_id: str, success: bool, error_message: str = None, profile_id: int = 1) -> bool:
        """Update retry count and status for a wishlist track"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                if success:
                    # Remove from ALL profiles' wishlists — track is now in shared library
                    cursor.execute("DELETE FROM wishlist_tracks WHERE spotify_track_id = ?", (spotify_track_id,))
                else:
                    # Increment retry count and update failure reason
                    cursor.execute("""
                        UPDATE wishlist_tracks
                        SET retry_count = retry_count + 1,
                            last_attempted = CURRENT_TIMESTAMP,
                            failure_reason = COALESCE(?, failure_reason)
                        WHERE spotify_track_id = ? AND profile_id = ?
                    """, (error_message, spotify_track_id, profile_id))
                
                conn.commit()
                return cursor.rowcount > 0
                
        except Exception as e:
            logger.error(f"Error updating wishlist retry status: {e}")
            return False
    
    def get_wishlist_count(self, profile_id: int = 1) -> int:
        """Get the total number of tracks in the wishlist for the given profile"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM wishlist_tracks WHERE profile_id = ?", (profile_id,))
                result = cursor.fetchone()
                return result[0] if result else 0
        except Exception as e:
            logger.error(f"Error getting wishlist count: {e}")
            return 0
    
    def clear_wishlist(self, profile_id: int = 1) -> bool:
        """Clear all tracks from the wishlist for the given profile"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM wishlist_tracks WHERE profile_id = ?", (profile_id,))
                cleared_count = cursor.rowcount
                conn.commit()
                logger.info(f"Cleared {cleared_count} tracks from wishlist (profile: {profile_id})")
                return True
        except Exception as e:
            logger.error(f"Error clearing wishlist: {e}")
            return False

    def remove_wishlist_duplicates(self, profile_id: int = 1) -> int:
        """Remove duplicate tracks from wishlist.
        When allow_duplicate_tracks is True, only removes exact duplicates
        (same name + artist + album). When False, removes any track with the
        same name + artist regardless of album.
        Keeps the oldest entry (by date_added) for each duplicate set.
        Returns the number of duplicates removed."""
        try:
            from config.settings import config_manager
            allow_duplicates = config_manager.get('wishlist.allow_duplicate_tracks', True)

            with self._get_connection() as conn:
                cursor = conn.cursor()

                # Get all wishlist tracks for this profile
                cursor.execute("""
                    SELECT id, spotify_track_id, spotify_data, date_added
                    FROM wishlist_tracks
                    WHERE profile_id = ?
                    ORDER BY date_added ASC
                """, (profile_id,))
                all_tracks = cursor.fetchall()

                # Track seen tracks and duplicates to remove
                seen_tracks = {}  # Value: track row id to keep
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

                        if allow_duplicates:
                            # Include album in the key so same song from different albums survives
                            album = track_data.get('album', {})
                            album_name = (album.get('name', '') if isinstance(album, dict) else str(album)).lower()
                            key = (track_name, artist_name, album_name)
                        else:
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
                if removed_count > 0:
                    logger.info(f"Removed {removed_count} duplicate tracks from wishlist (allow_duplicates={allow_duplicates})")
                return removed_count

        except Exception as e:
            logger.error(f"Error removing wishlist duplicates: {e}")
            return 0

    # Watchlist operations
    def add_artist_to_watchlist(self, artist_id: str, artist_name: str, profile_id: int = 1, source: str = None) -> bool:
        """Add an artist to the watchlist for monitoring new releases.

        Automatically detects if artist_id is a Spotify ID (alphanumeric) or iTunes/Deezer ID (numeric).
        If the artist already exists (by name match), updates the existing row with the new source ID.
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # Check if artist already exists by name (case-insensitive) for this profile
                cursor.execute("""
                    SELECT id, spotify_artist_id, itunes_artist_id, deezer_artist_id, discogs_artist_id
                    FROM watchlist_artists
                    WHERE LOWER(artist_name) = LOWER(?) AND profile_id = ?
                    LIMIT 1
                """, (artist_name, profile_id))
                existing = cursor.fetchone()

                # Detect source: explicit source param, or infer from ID format
                if not source:
                    source = 'itunes' if artist_id.isdigit() else 'spotify'

                if existing:
                    # Artist already on watchlist — update with new source ID if missing
                    col_map = {'spotify': 'spotify_artist_id', 'itunes': 'itunes_artist_id', 'deezer': 'deezer_artist_id', 'discogs': 'discogs_artist_id'}
                    col = col_map.get(source)
                    if col and not existing[col]:
                        cursor.execute(f"""
                            UPDATE watchlist_artists
                            SET {col} = ?, updated_at = CURRENT_TIMESTAMP
                            WHERE id = ?
                        """, (artist_id, existing['id']))
                        conn.commit()
                        logger.info(f"Updated existing watchlist artist '{artist_name}' with {source} ID: {artist_id}")
                    else:
                        logger.info(f"Artist '{artist_name}' already on watchlist (profile: {profile_id})")
                    return True

                # New artist — insert with the appropriate ID column
                if source == 'deezer':
                    cursor.execute("""
                        INSERT INTO watchlist_artists
                        (deezer_artist_id, artist_name, date_added, updated_at, profile_id)
                        VALUES (?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?)
                    """, (artist_id, artist_name, profile_id))
                    logger.info(f"Added artist '{artist_name}' to watchlist (Deezer ID: {artist_id}, profile: {profile_id})")
                elif source == 'itunes':
                    cursor.execute("""
                        INSERT INTO watchlist_artists
                        (itunes_artist_id, artist_name, date_added, updated_at, profile_id)
                        VALUES (?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?)
                    """, (artist_id, artist_name, profile_id))
                    logger.info(f"Added artist '{artist_name}' to watchlist (iTunes ID: {artist_id}, profile: {profile_id})")
                elif source == 'discogs':
                    cursor.execute("""
                        INSERT INTO watchlist_artists
                        (discogs_artist_id, artist_name, date_added, updated_at, profile_id)
                        VALUES (?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?)
                    """, (artist_id, artist_name, profile_id))
                    logger.info(f"Added artist '{artist_name}' to watchlist (Discogs ID: {artist_id}, profile: {profile_id})")
                else:
                    cursor.execute("""
                        INSERT INTO watchlist_artists
                        (spotify_artist_id, artist_name, date_added, updated_at, profile_id)
                        VALUES (?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?)
                    """, (artist_id, artist_name, profile_id))
                    logger.info(f"Added artist '{artist_name}' to watchlist (Spotify ID: {artist_id}, profile: {profile_id})")

                conn.commit()
                return True

        except Exception as e:
            logger.error(f"Error adding artist '{artist_name}' to watchlist: {e}")
            return False

    def remove_artist_from_watchlist(self, artist_id: str, profile_id: int = 1) -> bool:
        """Remove an artist from the watchlist (checks Spotify, iTunes, Deezer, and Discogs IDs)"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # Get artist name for logging (check all ID columns)
                cursor.execute("""
                    SELECT artist_name FROM watchlist_artists
                    WHERE (spotify_artist_id = ? OR itunes_artist_id = ? OR deezer_artist_id = ? OR discogs_artist_id = ?) AND profile_id = ?
                """, (artist_id, artist_id, artist_id, artist_id, profile_id))
                result = cursor.fetchone()
                artist_name = result['artist_name'] if result else "Unknown"

                cursor.execute("""
                    DELETE FROM watchlist_artists
                    WHERE (spotify_artist_id = ? OR itunes_artist_id = ? OR deezer_artist_id = ? OR discogs_artist_id = ?) AND profile_id = ?
                """, (artist_id, artist_id, artist_id, artist_id, profile_id))

                if cursor.rowcount > 0:
                    conn.commit()
                    logger.info(f"Removed artist '{artist_name}' from watchlist (ID: {artist_id}, profile: {profile_id})")
                    return True
                else:
                    logger.warning(f"Artist with ID {artist_id} not found in watchlist for profile {profile_id}")
                    return False

        except Exception as e:
            logger.error(f"Error removing artist from watchlist (ID: {artist_id}): {e}")
            return False

    def is_artist_in_watchlist(self, artist_id: str, profile_id: int = 1, artist_name: str = None) -> bool:
        """Check if an artist is currently in the watchlist (checks Spotify, iTunes, Deezer, Discogs IDs and name)"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # Check all ID columns and optionally artist name
                if artist_name:
                    cursor.execute("""
                        SELECT 1 FROM watchlist_artists
                        WHERE (spotify_artist_id = ? OR itunes_artist_id = ? OR deezer_artist_id = ? OR discogs_artist_id = ? OR LOWER(artist_name) = LOWER(?)) AND profile_id = ?
                        LIMIT 1
                    """, (artist_id, artist_id, artist_id, artist_id, artist_name, profile_id))
                else:
                    cursor.execute("""
                        SELECT 1 FROM watchlist_artists
                        WHERE (spotify_artist_id = ? OR itunes_artist_id = ? OR deezer_artist_id = ? OR discogs_artist_id = ?) AND profile_id = ?
                        LIMIT 1
                    """, (artist_id, artist_id, artist_id, artist_id, profile_id))
                result = cursor.fetchone()

                return result is not None

        except Exception as e:
            logger.error(f"Error checking if artist is in watchlist (ID: {artist_id}): {e}")
            return False

    def get_watchlist_artists(self, profile_id: int = 1) -> List[WatchlistArtist]:
        """Get all artists in the watchlist for the given profile"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # Check which columns exist (for migration compatibility)
                cursor.execute("PRAGMA table_info(watchlist_artists)")
                existing_columns = {column[1] for column in cursor.fetchall()}

                # Build SELECT query based on existing columns
                base_columns = ['id', 'spotify_artist_id', 'artist_name', 'date_added',
                               'last_scan_timestamp', 'created_at', 'updated_at']
                optional_columns = ['image_url', 'itunes_artist_id', 'deezer_artist_id', 'discogs_artist_id', 'include_albums', 'include_eps', 'include_singles',
                                   'include_live', 'include_remixes', 'include_acoustic', 'include_compilations',
                                   'include_instrumentals', 'lookback_days']

                columns_to_select = base_columns + [col for col in optional_columns if col in existing_columns]

                if 'profile_id' in existing_columns:
                    cursor.execute(f"""
                        SELECT {', '.join(columns_to_select)}
                        FROM watchlist_artists
                        WHERE profile_id = ?
                        ORDER BY date_added DESC
                    """, (profile_id,))
                else:
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
                    deezer_artist_id = row['deezer_artist_id'] if 'deezer_artist_id' in existing_columns else None
                    discogs_artist_id = row['discogs_artist_id'] if 'discogs_artist_id' in existing_columns else None
                    include_albums = bool(row['include_albums']) if 'include_albums' in existing_columns else True
                    include_eps = bool(row['include_eps']) if 'include_eps' in existing_columns else True
                    include_singles = bool(row['include_singles']) if 'include_singles' in existing_columns else True
                    include_live = bool(row['include_live']) if 'include_live' in existing_columns else False
                    include_remixes = bool(row['include_remixes']) if 'include_remixes' in existing_columns else False
                    include_acoustic = bool(row['include_acoustic']) if 'include_acoustic' in existing_columns else False
                    include_compilations = bool(row['include_compilations']) if 'include_compilations' in existing_columns else False
                    include_instrumentals = bool(row['include_instrumentals']) if 'include_instrumentals' in existing_columns else False
                    lookback_days = row['lookback_days'] if 'lookback_days' in existing_columns else None

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
                        deezer_artist_id=deezer_artist_id,
                        discogs_artist_id=discogs_artist_id,
                        include_albums=include_albums,
                        include_eps=include_eps,
                        include_singles=include_singles,
                        include_live=include_live,
                        include_remixes=include_remixes,
                        include_acoustic=include_acoustic,
                        include_compilations=include_compilations,
                        include_instrumentals=include_instrumentals,
                        lookback_days=lookback_days,
                        profile_id=profile_id
                    ))

                return watchlist_artists

        except Exception as e:
            logger.error(f"Error getting watchlist artists: {e}")
            return []

    # ── Spotify Library Cache ──────────────────────────────────────────

    def upsert_spotify_library_albums(self, albums: list, profile_id: int = 1):
        """Bulk upsert saved Spotify albums into cache table"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                for album in albums:
                    cursor.execute("""
                        INSERT OR REPLACE INTO spotify_library_cache
                        (spotify_album_id, album_name, artist_name, artist_id,
                         release_date, total_tracks, album_type, image_url,
                         date_saved, cached_at, profile_id)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?)
                    """, (
                        album['spotify_album_id'],
                        album['album_name'],
                        album['artist_name'],
                        album.get('artist_id'),
                        album.get('release_date'),
                        album.get('total_tracks', 0),
                        album.get('album_type', 'album'),
                        album.get('image_url'),
                        album.get('date_saved'),
                        profile_id,
                    ))
                conn.commit()
                logger.info(f"Upserted {len(albums)} albums into spotify_library_cache")
        except Exception as e:
            logger.error(f"Error upserting spotify library albums: {e}")

    def get_spotify_library_albums(self, offset=0, limit=50, search='', sort='date_saved',
                                    sort_dir='desc', profile_id=1):
        """Get cached Spotify library albums with pagination, search, and sorting.
        Returns (albums_list, total_count)."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                where_clauses = ['profile_id = ?']
                params = [profile_id]

                if search:
                    where_clauses.append('(album_name LIKE ? OR artist_name LIKE ?)')
                    params.extend([f'%{search}%', f'%{search}%'])

                where_sql = ' AND '.join(where_clauses)

                # Count total
                cursor.execute(f"SELECT COUNT(*) as count FROM spotify_library_cache WHERE {where_sql}", params)
                total = cursor.fetchone()['count']

                # Validate sort column
                valid_sorts = {'date_saved', 'artist_name', 'album_name', 'release_date'}
                if sort not in valid_sorts:
                    sort = 'date_saved'
                sort_direction = 'ASC' if sort_dir == 'asc' else 'DESC'

                cursor.execute(f"""
                    SELECT * FROM spotify_library_cache
                    WHERE {where_sql}
                    ORDER BY {sort} {sort_direction}
                    LIMIT ? OFFSET ?
                """, params + [limit, offset])

                albums = []
                for row in cursor.fetchall():
                    albums.append({
                        'id': row['id'],
                        'spotify_album_id': row['spotify_album_id'],
                        'album_name': row['album_name'],
                        'artist_name': row['artist_name'],
                        'artist_id': row['artist_id'],
                        'release_date': row['release_date'],
                        'total_tracks': row['total_tracks'],
                        'album_type': row['album_type'],
                        'image_url': row['image_url'],
                        'date_saved': row['date_saved'],
                    })

                return albums, total

        except Exception as e:
            logger.error(f"Error getting spotify library albums: {e}")
            return [], 0

    def get_spotify_library_album_ids(self, profile_id=1):
        """Get all cached spotify album IDs as a set"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT spotify_album_id FROM spotify_library_cache WHERE profile_id = ?", (profile_id,))
                return {row['spotify_album_id'] for row in cursor.fetchall()}
        except Exception as e:
            logger.error(f"Error getting spotify library album IDs: {e}")
            return set()

    def remove_spotify_library_albums_not_in(self, keep_ids: set, profile_id=1):
        """Remove cached albums that are no longer in the user's Spotify library"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                if not keep_ids:
                    cursor.execute("DELETE FROM spotify_library_cache WHERE profile_id = ?", (profile_id,))
                else:
                    placeholders = ','.join('?' * len(keep_ids))
                    cursor.execute(f"""
                        DELETE FROM spotify_library_cache
                        WHERE profile_id = ? AND spotify_album_id NOT IN ({placeholders})
                    """, [profile_id] + list(keep_ids))
                removed = cursor.rowcount
                conn.commit()
                if removed > 0:
                    logger.info(f"Removed {removed} un-saved albums from spotify_library_cache")
                return removed
        except Exception as e:
            logger.error(f"Error removing spotify library albums: {e}")
            return 0

    def get_library_spotify_album_ids(self, profile_id=1):
        """Get all spotify_album_id values from the local music library albums table"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT DISTINCT spotify_album_id FROM albums
                    WHERE spotify_album_id IS NOT NULL AND spotify_album_id != ''
                """)
                return {row['spotify_album_id'] for row in cursor.fetchall()}
        except Exception as e:
            logger.error(f"Error getting library spotify album IDs: {e}")
            return set()

    def get_library_album_names(self):
        """Get normalized (artist, album) pairs from library for fuzzy ownership matching"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT LOWER(a.title) as album, LOWER(ar.name) as artist
                    FROM albums a
                    JOIN artists ar ON a.artist_id = ar.id
                """)
                return {(row['artist'], row['album']) for row in cursor.fetchall()}
        except Exception as e:
            logger.error(f"Error getting library album names: {e}")
            return set()

    def get_watchlist_count(self, profile_id: int = 1) -> int:
        """Get the number of artists in the watchlist for the given profile"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                cursor.execute("SELECT COUNT(*) as count FROM watchlist_artists WHERE profile_id = ?", (profile_id,))
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
                    WHERE spotify_artist_id = ? OR itunes_artist_id = ? OR deezer_artist_id = ? OR discogs_artist_id = ?
                """, (image_url, artist_id, artist_id, artist_id, artist_id))

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

    def update_watchlist_deezer_id(self, watchlist_id: int, deezer_id: str) -> bool:
        """Update the Deezer artist ID for a watchlist artist (cross-provider support)"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                cursor.execute("""
                    UPDATE watchlist_artists
                    SET deezer_artist_id = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (deezer_id, watchlist_id))

                conn.commit()
                logger.info(f"Updated Deezer ID for watchlist artist {watchlist_id}: {deezer_id}")
                return cursor.rowcount > 0

        except Exception as e:
            logger.error(f"Error updating watchlist Deezer ID: {e}")
            return False

    def update_watchlist_discogs_id(self, watchlist_id: int, discogs_id: str) -> bool:
        """Update the Discogs artist ID for a watchlist artist (cross-provider support)"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE watchlist_artists
                    SET discogs_artist_id = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (discogs_id, watchlist_id))
                conn.commit()
                logger.info(f"Updated Discogs ID for watchlist artist {watchlist_id}: {discogs_id}")
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error updating watchlist Discogs ID: {e}")
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

    def update_watchlist_artist_deezer_id(self, spotify_artist_id: str, deezer_id: str) -> bool:
        """Update the Deezer artist ID for a watchlist artist by Spotify ID (for cross-provider caching)"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                cursor.execute("""
                    UPDATE watchlist_artists
                    SET deezer_artist_id = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE spotify_artist_id = ?
                """, (deezer_id, spotify_artist_id))

                conn.commit()
                if cursor.rowcount > 0:
                    logger.info(f"Cached Deezer ID {deezer_id} for Spotify artist {spotify_artist_id}")
                return cursor.rowcount > 0

        except Exception as e:
            logger.error(f"Error caching watchlist Deezer ID: {e}")
            return False

    # === Discovery Feature Methods ===

    def add_or_update_similar_artist(self, source_artist_id: str, similar_artist_name: str,
                                      similar_artist_spotify_id: Optional[str] = None,
                                      similar_artist_itunes_id: Optional[str] = None,
                                      similarity_rank: int = 1,
                                      profile_id: int = 1,
                                      image_url: Optional[str] = None,
                                      genres: Optional[list] = None,
                                      popularity: int = 0,
                                      similar_artist_deezer_id: Optional[str] = None) -> bool:
        """Add or update a similar artist recommendation (supports Spotify, iTunes, and Deezer IDs)"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                genres_json = json.dumps(genres) if genres else None

                # Use artist name as the unique key (allows storing both IDs for same artist)
                cursor.execute("""
                    INSERT INTO similar_artists
                    (source_artist_id, similar_artist_spotify_id, similar_artist_itunes_id, similar_artist_deezer_id, similar_artist_name,
                     similarity_rank, occurrence_count, last_updated, profile_id,
                     image_url, genres, popularity, metadata_updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(profile_id, source_artist_id, similar_artist_name)
                    DO UPDATE SET
                        similar_artist_spotify_id = COALESCE(excluded.similar_artist_spotify_id, similar_artist_spotify_id),
                        similar_artist_itunes_id = COALESCE(excluded.similar_artist_itunes_id, similar_artist_itunes_id),
                        similar_artist_deezer_id = COALESCE(excluded.similar_artist_deezer_id, similar_artist_deezer_id),
                        similarity_rank = excluded.similarity_rank,
                        occurrence_count = occurrence_count + 1,
                        last_updated = CURRENT_TIMESTAMP,
                        image_url = COALESCE(excluded.image_url, image_url),
                        genres = COALESCE(excluded.genres, genres),
                        popularity = CASE WHEN excluded.popularity > 0 THEN excluded.popularity ELSE popularity END,
                        metadata_updated_at = CASE WHEN excluded.image_url IS NOT NULL THEN CURRENT_TIMESTAMP ELSE metadata_updated_at END
                """, (source_artist_id, similar_artist_spotify_id, similar_artist_itunes_id, similar_artist_deezer_id, similar_artist_name,
                      similarity_rank, profile_id, image_url, genres_json, popularity))

                conn.commit()
                return True

        except Exception as e:
            logger.error(f"Error adding similar artist: {e}")
            return False

    def get_similar_artists_for_source(self, source_artist_id: str, profile_id: int = 1) -> List[SimilarArtist]:
        """Get all similar artists for a given source artist"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                cursor.execute("""
                    SELECT * FROM similar_artists
                    WHERE source_artist_id = ? AND profile_id = ?
                    ORDER BY similarity_rank ASC
                """, (source_artist_id, profile_id))

                rows = cursor.fetchall()
                return [SimilarArtist(
                    id=row['id'],
                    source_artist_id=row['source_artist_id'],
                    similar_artist_spotify_id=row['similar_artist_spotify_id'],
                    similar_artist_itunes_id=row['similar_artist_itunes_id'] if 'similar_artist_itunes_id' in row.keys() else None,
                    similar_artist_name=row['similar_artist_name'],
                    similarity_rank=row['similarity_rank'],
                    occurrence_count=row['occurrence_count'],
                    last_updated=datetime.fromisoformat(row['last_updated']),
                    similar_artist_deezer_id=row['similar_artist_deezer_id'] if 'similar_artist_deezer_id' in row.keys() else None,
                ) for row in rows]

        except Exception as e:
            logger.error(f"Error getting similar artists: {e}")
            return []

    def get_similar_artists_missing_itunes_ids(self, source_artist_id: str, profile_id: int = 1) -> List[SimilarArtist]:
        """Get similar artists for a source that are missing iTunes IDs (for backfill)"""
        return self.get_similar_artists_missing_fallback_ids(source_artist_id, 'itunes', profile_id)

    def get_similar_artists_missing_fallback_ids(self, source_artist_id: str, fallback_source: str = 'itunes', profile_id: int = 1) -> List[SimilarArtist]:
        """Get similar artists missing IDs for the given fallback source (for backfill)"""
        try:
            col = 'similar_artist_deezer_id' if fallback_source == 'deezer' else 'similar_artist_itunes_id'
            with self._get_connection() as conn:
                cursor = conn.cursor()

                cursor.execute(f"""
                    SELECT * FROM similar_artists
                    WHERE source_artist_id = ? AND profile_id = ?
                    AND ({col} IS NULL OR {col} = '')
                    ORDER BY occurrence_count DESC
                    LIMIT 50
                """, (source_artist_id, profile_id))

                rows = cursor.fetchall()
                return [SimilarArtist(
                    id=row['id'],
                    source_artist_id=row['source_artist_id'],
                    similar_artist_spotify_id=row['similar_artist_spotify_id'],
                    similar_artist_itunes_id=row['similar_artist_itunes_id'] if 'similar_artist_itunes_id' in row.keys() else None,
                    similar_artist_name=row['similar_artist_name'],
                    similarity_rank=row['similarity_rank'],
                    occurrence_count=row['occurrence_count'],
                    last_updated=datetime.fromisoformat(row['last_updated']),
                    similar_artist_deezer_id=row['similar_artist_deezer_id'] if 'similar_artist_deezer_id' in row.keys() else None,
                ) for row in rows]

        except Exception as e:
            logger.error(f"Error getting similar artists missing {fallback_source} IDs: {e}")
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

    def update_similar_artist_deezer_id(self, similar_artist_id: int, deezer_id: str) -> bool:
        """Update a similar artist's Deezer ID (for backfill)"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                cursor.execute("""
                    UPDATE similar_artists
                    SET similar_artist_deezer_id = ?
                    WHERE id = ?
                """, (deezer_id, similar_artist_id))

                conn.commit()
                return cursor.rowcount > 0

        except Exception as e:
            logger.error(f"Error updating similar artist Deezer ID: {e}")
            return False

    def update_similar_artist_metadata(self, similar_artist_id: int, image_url: str = None,
                                        genres: list = None, popularity: int = None) -> bool:
        """Cache artist metadata (image, genres, popularity) to avoid repeated API calls"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                genres_json = json.dumps(genres) if genres else None
                cursor.execute("""
                    UPDATE similar_artists
                    SET image_url = ?, genres = ?, popularity = ?, metadata_updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (image_url, genres_json, popularity or 0, similar_artist_id))
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error updating similar artist metadata: {e}")
            return False

    def update_similar_artist_metadata_by_external_id(self, external_id: str, source: str = 'spotify',
                                                       image_url: str = None, genres: list = None,
                                                       popularity: int = None) -> bool:
        """Cache artist metadata by Spotify or iTunes ID (updates all rows for that artist)"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                genres_json = json.dumps(genres) if genres else None
                if source == 'spotify':
                    where_clause = "similar_artist_spotify_id = ?"
                elif source == 'deezer':
                    where_clause = "similar_artist_deezer_id = ?"
                else:
                    where_clause = "similar_artist_itunes_id = ?"
                cursor.execute(f"""
                    UPDATE similar_artists
                    SET image_url = ?, genres = ?, popularity = ?, metadata_updated_at = CURRENT_TIMESTAMP
                    WHERE {where_clause}
                """, (image_url, genres_json, popularity or 0, external_id))
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error updating similar artist metadata by external ID: {e}")
            return False

    def has_fresh_similar_artists(self, source_artist_id: str, days_threshold: int = 30, require_itunes: bool = True, require_spotify: bool = False, profile_id: int = 1) -> bool:
        """
        Check if we have cached similar artists that are still fresh (<days_threshold old).
        Also checks that similar artists have the required provider IDs.

        Args:
            source_artist_id: The source artist ID to check
            days_threshold: Maximum age in days to consider fresh
            require_itunes: If True, also requires iTunes IDs to be present (for seamless provider switching)
            require_spotify: If True, also requires Spotify IDs to be present (for Spotify discovery)
            profile_id: Profile to check freshness for

        Returns True if we have recent data with required IDs, False if data is stale, missing, or incomplete.
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                cursor.execute("""
                    SELECT COUNT(*) as count, MAX(last_updated) as last_updated
                    FROM similar_artists
                    WHERE source_artist_id = ? AND profile_id = ?
                """, (source_artist_id, profile_id))

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
                        WHERE source_artist_id = ? AND profile_id = ?
                    """, (source_artist_id, profile_id))
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
                        WHERE source_artist_id = ? AND profile_id = ?
                    """, (source_artist_id, profile_id))
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

    def get_top_similar_artists(self, limit: int = 50, profile_id: int = 1, require_source: str = None) -> List[SimilarArtist]:
        """Get top similar artists excluding watchlist artists, with cycling support.
        require_source: if set ('spotify','itunes','deezer'), only returns artists with that source ID."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # Build source filter
                source_filter = ''
                if require_source == 'spotify':
                    source_filter = "AND sa.similar_artist_spotify_id IS NOT NULL AND sa.similar_artist_spotify_id != ''"
                elif require_source == 'itunes':
                    source_filter = "AND sa.similar_artist_itunes_id IS NOT NULL AND sa.similar_artist_itunes_id != ''"
                elif require_source == 'deezer':
                    source_filter = "AND sa.similar_artist_deezer_id IS NOT NULL AND sa.similar_artist_deezer_id != ''"

                cursor.execute(f"""
                    SELECT
                        MAX(sa.id) as id,
                        MAX(sa.source_artist_id) as source_artist_id,
                        MAX(sa.similar_artist_spotify_id) as similar_artist_spotify_id,
                        MAX(sa.similar_artist_itunes_id) as similar_artist_itunes_id,
                        MAX(sa.similar_artist_deezer_id) as similar_artist_deezer_id,
                        sa.similar_artist_name,
                        AVG(sa.similarity_rank) as similarity_rank,
                        SUM(sa.occurrence_count) as occurrence_count,
                        MAX(sa.last_updated) as last_updated,
                        MAX(sa.image_url) as image_url,
                        MAX(sa.genres) as genres,
                        MAX(sa.popularity) as popularity
                    FROM similar_artists sa
                    LEFT JOIN watchlist_artists wa ON (
                        (sa.similar_artist_spotify_id IS NOT NULL AND sa.similar_artist_spotify_id = wa.spotify_artist_id)
                        OR (sa.similar_artist_itunes_id IS NOT NULL AND sa.similar_artist_itunes_id = wa.itunes_artist_id)
                        OR (sa.similar_artist_deezer_id IS NOT NULL AND sa.similar_artist_deezer_id = wa.deezer_artist_id)
                        OR LOWER(sa.similar_artist_name) = LOWER(wa.artist_name)
                    ) AND wa.profile_id = ?
                    WHERE wa.id IS NULL AND sa.profile_id = ? {source_filter}
                    GROUP BY sa.similar_artist_name
                    ORDER BY
                        CASE WHEN MAX(sa.last_featured) IS NULL THEN 0 ELSE 1 END,
                        MAX(sa.last_featured) ASC,
                        occurrence_count DESC,
                        similarity_rank ASC
                    LIMIT ?
                """, (profile_id, profile_id, limit))

                rows = cursor.fetchall()
                results = []
                for row in rows:
                    genres_raw = row['genres'] if 'genres' in row.keys() else None
                    try:
                        genres_list = json.loads(genres_raw) if genres_raw else None
                    except (json.JSONDecodeError, TypeError):
                        genres_list = None
                    results.append(SimilarArtist(
                        id=row['id'],
                        source_artist_id=row['source_artist_id'],
                        similar_artist_spotify_id=row['similar_artist_spotify_id'],
                        similar_artist_itunes_id=row['similar_artist_itunes_id'] if 'similar_artist_itunes_id' in row.keys() else None,
                        similar_artist_deezer_id=row['similar_artist_deezer_id'] if 'similar_artist_deezer_id' in row.keys() else None,
                        similar_artist_name=row['similar_artist_name'],
                        similarity_rank=int(row['similarity_rank']),
                        occurrence_count=row['occurrence_count'],
                        last_updated=datetime.fromisoformat(row['last_updated']),
                        image_url=row['image_url'] if 'image_url' in row.keys() else None,
                        genres=genres_list,
                        popularity=row['popularity'] if 'popularity' in row.keys() else 0,
                    ))
                return results

        except Exception as e:
            logger.error(f"Error getting top similar artists: {e}")
            return []

    def mark_artists_featured(self, artist_names: List[str]):
        """Update last_featured timestamp for artists shown in the hero slider"""
        if not artist_names:
            return
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                placeholders = ','.join('?' for _ in artist_names)
                cursor.execute(f"""
                    UPDATE similar_artists
                    SET last_featured = CURRENT_TIMESTAMP
                    WHERE similar_artist_name IN ({placeholders})
                """, artist_names)
                conn.commit()
        except Exception as e:
            logger.error(f"Error marking artists as featured: {e}")

    def add_to_discovery_pool(self, track_data: Dict[str, Any], source: str = 'spotify', profile_id: int = 1) -> bool:
        """Add a track to the discovery pool (supports both Spotify and iTunes sources)"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # Check if track already exists based on source (scoped to profile)
                if source == 'spotify' and track_data.get('spotify_track_id'):
                    cursor.execute("SELECT COUNT(*) as count FROM discovery_pool WHERE spotify_track_id = ? AND source = 'spotify' AND profile_id = ?",
                                  (track_data['spotify_track_id'], profile_id))
                elif source == 'itunes' and track_data.get('itunes_track_id'):
                    cursor.execute("SELECT COUNT(*) as count FROM discovery_pool WHERE itunes_track_id = ? AND source = 'itunes' AND profile_id = ?",
                                  (track_data['itunes_track_id'], profile_id))
                elif source == 'deezer' and track_data.get('deezer_track_id'):
                    cursor.execute("SELECT COUNT(*) as count FROM discovery_pool WHERE deezer_track_id = ? AND source = 'deezer' AND profile_id = ?",
                                  (track_data['deezer_track_id'], profile_id))
                else:
                    # Fallback check by track name and artist
                    cursor.execute("SELECT COUNT(*) as count FROM discovery_pool WHERE track_name = ? AND artist_name = ? AND source = ? AND profile_id = ?",
                                  (track_data['track_name'], track_data['artist_name'], source, profile_id))

                if cursor.fetchone()['count'] > 0:
                    return True  # Already in pool

                # Get artist genres if available
                artist_genres = track_data.get('artist_genres')
                artist_genres_json = json.dumps(artist_genres) if artist_genres else None

                cursor.execute("""
                    INSERT INTO discovery_pool
                    (spotify_track_id, spotify_album_id, spotify_artist_id,
                     itunes_track_id, itunes_album_id, itunes_artist_id,
                     deezer_track_id, deezer_album_id, deezer_artist_id,
                     source, track_name, artist_name, album_name, album_cover_url,
                     duration_ms, popularity, release_date, is_new_release, track_data_json, artist_genres, added_date, profile_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?)
                """, (
                    track_data.get('spotify_track_id'),
                    track_data.get('spotify_album_id'),
                    track_data.get('spotify_artist_id'),
                    track_data.get('itunes_track_id'),
                    track_data.get('itunes_album_id'),
                    track_data.get('itunes_artist_id'),
                    track_data.get('deezer_track_id'),
                    track_data.get('deezer_album_id'),
                    track_data.get('deezer_artist_id'),
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
                    artist_genres_json,
                    profile_id
                ))

                conn.commit()
                return True

        except Exception as e:
            logger.error(f"Error adding to discovery pool: {e}")
            return False

    def rotate_discovery_pool(self, max_tracks: int = 2000, remove_count: int = 500, profile_id: int = 1):
        """Remove oldest tracks from discovery pool if it exceeds max_tracks"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # Check current count for this profile
                cursor.execute("SELECT COUNT(*) as count FROM discovery_pool WHERE profile_id = ?", (profile_id,))
                current_count = cursor.fetchone()['count']

                if current_count > max_tracks:
                    # Remove oldest tracks for this profile
                    cursor.execute("""
                        DELETE FROM discovery_pool
                        WHERE id IN (
                            SELECT id FROM discovery_pool
                            WHERE profile_id = ?
                            ORDER BY added_date ASC
                            LIMIT ?
                        )
                    """, (profile_id, remove_count))

                    conn.commit()
                    logger.info(f"Removed {remove_count} oldest tracks from discovery pool")

        except Exception as e:
            logger.error(f"Error rotating discovery pool: {e}")

    def get_discovery_pool_tracks(self, limit: int = 100, new_releases_only: bool = False, source: Optional[str] = None, profile_id: int = 1) -> List[DiscoveryTrack]:
        """Get tracks from discovery pool, optionally filtered by source ('spotify' or 'itunes')"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # Build query with optional source filter
                where_clauses = ["profile_id = ?"]
                params = [profile_id]

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
                    deezer_track_id=row['deezer_track_id'] if 'deezer_track_id' in row_keys else None,
                    deezer_album_id=row['deezer_album_id'] if 'deezer_album_id' in row_keys else None,
                    deezer_artist_id=row['deezer_artist_id'] if 'deezer_artist_id' in row_keys else None,
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

    def cache_discovery_recent_album(self, album_data: Dict[str, Any], source: str = 'spotify', profile_id: int = 1) -> bool:
        """Cache a recent album for the discover page (supports both Spotify and iTunes sources)"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                cursor.execute("""
                    INSERT OR REPLACE INTO discovery_recent_albums
                    (album_spotify_id, album_itunes_id, album_deezer_id,
                     artist_spotify_id, artist_itunes_id, artist_deezer_id, source,
                     album_name, artist_name, album_cover_url, release_date, album_type, cached_date, profile_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?)
                """, (
                    album_data.get('album_spotify_id'),
                    album_data.get('album_itunes_id'),
                    album_data.get('album_deezer_id'),
                    album_data.get('artist_spotify_id'),
                    album_data.get('artist_itunes_id'),
                    album_data.get('artist_deezer_id'),
                    source,
                    album_data['album_name'],
                    album_data['artist_name'],
                    album_data.get('album_cover_url'),
                    album_data['release_date'],
                    album_data.get('album_type', 'album'),
                    profile_id
                ))

                conn.commit()
                return True

        except Exception as e:
            logger.error(f"Error caching discovery recent album: {e}")
            return False

    def get_discovery_recent_albums(self, limit: int = 10, source: Optional[str] = None, profile_id: int = 1) -> List[Dict[str, Any]]:
        """Get cached recent albums for discover page, optionally filtered by source"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                if source:
                    cursor.execute("""
                        SELECT * FROM discovery_recent_albums
                        WHERE source = ? AND profile_id = ?
                        ORDER BY release_date DESC
                        LIMIT ?
                    """, (source, profile_id, limit))
                else:
                    cursor.execute("""
                        SELECT * FROM discovery_recent_albums
                        WHERE profile_id = ?
                        ORDER BY release_date DESC
                        LIMIT ?
                    """, (profile_id, limit))

                rows = cursor.fetchall()
                row_keys = rows[0].keys() if rows else []

                return [{
                    'album_spotify_id': row['album_spotify_id'],
                    'album_itunes_id': row['album_itunes_id'] if 'album_itunes_id' in row_keys else None,
                    'album_deezer_id': row['album_deezer_id'] if 'album_deezer_id' in row_keys else None,
                    'album_name': row['album_name'],
                    'artist_name': row['artist_name'],
                    'artist_spotify_id': row['artist_spotify_id'],
                    'artist_itunes_id': row['artist_itunes_id'] if 'artist_itunes_id' in row_keys else None,
                    'artist_deezer_id': row['artist_deezer_id'] if 'artist_deezer_id' in row_keys else None,
                    'album_cover_url': row['album_cover_url'],
                    'release_date': row['release_date'],
                    'album_type': row['album_type'],
                    'source': row['source'] if 'source' in row_keys else 'spotify'
                } for row in rows]

        except Exception as e:
            logger.error(f"Error getting discovery recent albums: {e}")
            return []

    def update_discovery_recent_album_cover(self, album_id: str, cover_url: str) -> bool:
        """Backfill a missing cover URL on a recent album entry."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE discovery_recent_albums SET album_cover_url = ?
                    WHERE album_spotify_id = ? OR album_itunes_id = ? OR album_deezer_id = ?
                """, (cover_url, album_id, album_id, album_id))
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.debug(f"Error updating recent album cover: {e}")
            return False

    def clear_discovery_recent_albums(self, profile_id: int = 1) -> bool:
        """Clear cached recent albums for a profile"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM discovery_recent_albums WHERE profile_id = ?", (profile_id,))
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Error clearing discovery recent albums: {e}")
            return False

    def save_curated_playlist(self, playlist_type: str, track_ids: List[str], profile_id: int = 1) -> bool:
        """Save a curated playlist selection (stays same until next discovery pool update)"""
        try:
            import json
            with self._get_connection() as conn:
                cursor = conn.cursor()
                # Delete existing for this profile+type, then insert
                cursor.execute("DELETE FROM discovery_curated_playlists WHERE playlist_type = ? AND profile_id = ?",
                               (playlist_type, profile_id))
                cursor.execute("""
                    INSERT INTO discovery_curated_playlists
                    (playlist_type, track_ids_json, curated_date, profile_id)
                    VALUES (?, ?, CURRENT_TIMESTAMP, ?)
                """, (playlist_type, json.dumps(track_ids), profile_id))
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Error saving curated playlist {playlist_type}: {e}")
            return False

    def get_curated_playlist(self, playlist_type: str, profile_id: int = 1) -> Optional[List[str]]:
        """Get saved curated playlist track IDs for the given profile"""
        try:
            import json
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT track_ids_json FROM discovery_curated_playlists
                    WHERE playlist_type = ? AND profile_id = ?
                """, (playlist_type, profile_id))
                row = cursor.fetchone()
                if row:
                    return json.loads(row['track_ids_json'])
                return None
        except Exception as e:
            logger.error(f"Error getting curated playlist {playlist_type}: {e}")
            return None

    def should_populate_discovery_pool(self, hours_threshold: int = 24, profile_id: int = 1) -> bool:
        """Check if discovery pool should be populated (hasn't been updated in X hours)"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT last_populated_timestamp
                    FROM discovery_pool_metadata
                    WHERE profile_id = ?
                """, (profile_id,))
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

    def update_discovery_pool_timestamp(self, track_count: int, profile_id: int = 1) -> bool:
        """Update the last populated timestamp and track count"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO discovery_pool_metadata
                    (profile_id, last_populated_timestamp, track_count, updated_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(profile_id) DO UPDATE SET
                        last_populated_timestamp = excluded.last_populated_timestamp,
                        track_count = excluded.track_count,
                        updated_at = CURRENT_TIMESTAMP
                """, (profile_id, datetime.now().isoformat(), track_count))
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

    def add_recent_release(self, watchlist_artist_id: int, album_data: Dict[str, Any], profile_id: int = 1) -> bool:
        """Add a recent release to the recent_releases table"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                cursor.execute("""
                    INSERT OR IGNORE INTO recent_releases
                    (watchlist_artist_id, album_spotify_id, album_name, release_date, album_cover_url, track_count, added_date, profile_id)
                    VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?)
                """, (
                    watchlist_artist_id,
                    album_data['album_spotify_id'],
                    album_data['album_name'],
                    album_data['release_date'],
                    album_data.get('album_cover_url'),
                    album_data.get('track_count', 0),
                    profile_id
                ))

                conn.commit()
                return True

        except Exception as e:
            logger.error(f"Error adding recent release: {e}")
            return False

    def get_recent_releases(self, limit: int = 50, profile_id: int = 1) -> List[RecentRelease]:
        """Get recent releases from watchlist artists for the given profile"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                cursor.execute("""
                    SELECT * FROM recent_releases
                    WHERE profile_id = ?
                    ORDER BY release_date DESC, added_date DESC
                    LIMIT ?
                """, (profile_id, limit))

                rows = cursor.fetchall()
                return [RecentRelease(
                    id=row['id'],
                    watchlist_artist_id=row['watchlist_artist_id'],
                    album_spotify_id=row['album_spotify_id'],
                    album_itunes_id=row['album_itunes_id'] if 'album_itunes_id' in row.keys() else None,
                    album_deezer_id=row['album_deezer_id'] if 'album_deezer_id' in row.keys() else None,
                    source=row['source'] if 'source' in row.keys() else 'spotify',
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

    def get_library_artists(self, search_query: str = "", letter: str = "", page: int = 1, limit: int = 50, watchlist_filter: str = "all", profile_id: int = 1, source_filter: str = "") -> Dict[str, Any]:
        """
        Get artists for the library page with search, filtering, and pagination

        Args:
            search_query: Search term to filter artists by name
            letter: Filter by first letter (a-z, #, or "" for all)
            page: Page number (1-based)
            limit: Number of results per page
            watchlist_filter: Filter by watchlist status ("all", "watched", "unwatched")
            source_filter: Filter by metadata source match (e.g. "spotify", "!spotify" for unmatched)

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

                # Metadata source filter — match or exclude by enrichment source
                if source_filter:
                    _source_columns = {
                        'spotify': 'a.spotify_artist_id',
                        'musicbrainz': 'a.musicbrainz_id',
                        'deezer': 'a.deezer_id',
                        'discogs': 'a.discogs_id',
                        'audiodb': 'a.audiodb_id',
                        'itunes': 'a.itunes_artist_id',
                        'lastfm': 'a.lastfm_url',
                        'genius': 'a.genius_url',
                        'tidal': 'a.tidal_id',
                        'qobuz': 'a.qobuz_id',
                    }
                    negate = source_filter.startswith('!')
                    key = source_filter.lstrip('!')
                    col = _source_columns.get(key)
                    if col:
                        if negate:
                            where_conditions.append(f"({col} IS NULL OR {col} = '')")
                        else:
                            where_conditions.append(f"({col} IS NOT NULL AND {col} != '')")

                # Get active server for filtering
                from config.settings import config_manager
                active_server = config_manager.get_active_media_server()

                # Add active server filter to where conditions
                where_conditions.append("a.server_source = ?")
                params.append(active_server)

                where_clause = " AND ".join(where_conditions) if where_conditions else "1=1"

                # Pre-fetch watchlist data for this profile (small table, single fast query)
                cursor.execute("SELECT spotify_artist_id, itunes_artist_id, LOWER(artist_name) as name_lower FROM watchlist_artists WHERE profile_id = ?", (profile_id,))
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

                # Step 1: Fast count query — no joins, just filter canonical artists
                count_query = f"""
                    SELECT COUNT(*) as total_count
                    FROM artists a
                    WHERE {where_clause}
                        AND a.id = (SELECT MIN(a2.id) FROM artists a2
                                    WHERE a2.name = a.name AND a2.server_source = a.server_source)
                """
                cursor.execute(count_query, params)
                total_count = cursor.fetchone()['total_count']

                # Step 2: Get paginated artist rows (no album/track joins — fast)
                offset = (page - 1) * limit
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
                        a.discogs_id,
                        a.lastfm_url,
                        a.genius_url,
                        a.tidal_id,
                        a.qobuz_id,
                        a.soul_id,
                        a.server_source
                    FROM artists a
                    WHERE {where_clause}
                        AND a.id = (SELECT MIN(a2.id) FROM artists a2
                                    WHERE a2.name = a.name AND a2.server_source = a.server_source)
                    ORDER BY a.name COLLATE NOCASE
                    LIMIT ? OFFSET ?
                """
                query_params = params + [limit, offset]
                cursor.execute(artists_query, query_params)
                artist_rows = cursor.fetchall()

                # Step 3: Batch-fetch album/track counts only for the 75 artists on this page
                artist_ids_on_page = [row['id'] for row in artist_rows]
                counts_map = {}
                if artist_ids_on_page:
                    # Get all artist IDs that share names with the page artists (for dedup merging)
                    name_pairs = [(row['name'], row['server_source']) for row in artist_rows]
                    # Build counts query using artist IDs directly
                    # Get all artist IDs sharing names with page artists
                    id_placeholders = ','.join(['?'] * len(artist_ids_on_page))
                    cursor.execute(f"""
                        SELECT id, name, server_source FROM artists
                        WHERE id IN ({id_placeholders})
                    """, artist_ids_on_page)
                    page_info = cursor.fetchall()

                    # Find all related artist IDs (same name+server) for count merging
                    or_clauses = []
                    or_params = []
                    for pi in page_info:
                        or_clauses.append("(ar.name = ? AND ar.server_source = ?)")
                        or_params.extend([pi['name'], pi['server_source']])

                    cursor.execute(f"""
                        SELECT
                            ar.name as artist_name, ar.server_source as artist_source,
                            COUNT(DISTINCT al.id) as album_count,
                            COUNT(DISTINCT t.id) as track_count
                        FROM artists ar
                        LEFT JOIN albums al ON al.artist_id = ar.id
                        LEFT JOIN tracks t ON t.album_id = al.id
                        WHERE {' OR '.join(or_clauses)}
                        GROUP BY ar.name, ar.server_source
                    """, or_params)
                    # Map back to canonical IDs
                    name_to_canonical = {(pi['name'], pi['server_source']): pi['id'] for pi in page_info}
                    for crow in cursor.fetchall():
                        cid = name_to_canonical.get((crow['artist_name'], crow['artist_source']))
                        if cid:
                            counts_map[cid] = (crow['album_count'], crow['track_count'])

                rows = artist_rows

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
                        'spotify_artist_id': row['spotify_artist_id'],
                        'itunes_artist_id': row['itunes_artist_id'],
                        'deezer_id': row['deezer_id'],
                        'audiodb_id': row['audiodb_id'],
                        'discogs_id': row['discogs_id'],
                        'lastfm_url': row['lastfm_url'],
                        'genius_url': row['genius_url'],
                        'tidal_id': row['tidal_id'],
                        'qobuz_id': row['qobuz_id'],
                        'soul_id': row['soul_id'],
                        'album_count': counts_map.get(row['id'], (0, 0))[0],
                        'track_count': counts_map.get(row['id'], (0, 0))[1],
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
                        id, name, thumb_url, genres, server_source,
                        musicbrainz_id, deezer_id, audiodb_id, discogs_id,
                        spotify_artist_id, itunes_artist_id, lastfm_url, genius_url,
                        tidal_id, qobuz_id, soul_id,
                        lastfm_listeners, lastfm_playcount, lastfm_tags, lastfm_bio
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
                # Group by artist_id+title+year to merge Navidrome split albums (same artist,
                # same album split into multiple DB entries) WITHOUT merging across different artists
                cursor.execute("""
                    SELECT
                        MIN(a.id) as id,
                        a.title,
                        a.year,
                        SUM(a.track_count) as track_count,
                        MAX(a.thumb_url) as thumb_url,
                        MAX(a.musicbrainz_release_id) as musicbrainz_release_id,
                        (SELECT COUNT(*) FROM tracks t WHERE t.album_id IN (
                            SELECT a2.id FROM albums a2
                            WHERE a2.artist_id = a.artist_id
                            AND a2.title = a.title
                            AND COALESCE(a2.year, '') = COALESCE(a.year, '')
                        )) as owned_tracks
                    FROM albums a
                    WHERE a.artist_id IN (
                        SELECT id FROM artists
                        WHERE name = (SELECT name FROM artists WHERE id = ?)
                        AND server_source = (SELECT server_source FROM artists WHERE id = ?)
                    )
                    GROUP BY a.artist_id, a.title, a.year
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
                        COUNT(*) as album_count,
                        (SELECT COUNT(*) FROM tracks WHERE album_id IN (
                            SELECT id FROM albums WHERE artist_id IN (
                                SELECT id FROM artists
                                WHERE name = (SELECT name FROM artists WHERE id = ?)
                                AND server_source = (SELECT server_source FROM artists WHERE id = ?)
                            )
                        )) as track_count
                    FROM albums
                    WHERE artist_id IN (
                        SELECT id FROM artists
                        WHERE name = (SELECT name FROM artists WHERE id = ?)
                        AND server_source = (SELECT server_source FROM artists WHERE id = ?)
                    )
                """, (artist_id, artist_id, artist_id, artist_id))

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
                        'discogs_id': artist_row['discogs_id'],
                        'spotify_artist_id': artist_row['spotify_artist_id'],
                        'itunes_artist_id': artist_row['itunes_artist_id'],
                        'lastfm_url': artist_row['lastfm_url'],
                        'genius_url': artist_row['genius_url'],
                        'tidal_id': artist_row['tidal_id'],
                        'qobuz_id': artist_row['qobuz_id'],
                        'soul_id': artist_row['soul_id'],
                        'lastfm_listeners': artist_row['lastfm_listeners'],
                        'lastfm_playcount': artist_row['lastfm_playcount'],
                        'lastfm_tags': artist_row['lastfm_tags'],
                        'lastfm_bio': artist_row['lastfm_bio'],
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

    # ==================== Enhanced Library Management Methods ====================

    # Field whitelists for safe updates
    ARTIST_EDITABLE_FIELDS = {'name', 'genres', 'summary', 'style', 'mood', 'label'}
    ALBUM_EDITABLE_FIELDS = {'title', 'year', 'genres', 'style', 'mood', 'label', 'explicit', 'record_type', 'track_count'}
    TRACK_EDITABLE_FIELDS = {'title', 'track_number', 'bpm', 'explicit', 'style', 'mood'}

    def get_artist_full_detail(self, artist_id) -> Dict[str, Any]:
        """
        Get complete artist information with ALL columns, all albums with ALL columns,
        and all tracks per album with ALL columns. For the enhanced library management view.
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # Get artist with all columns
                cursor.execute("SELECT * FROM artists WHERE id = ?", (artist_id,))
                artist_row = cursor.fetchone()
                if not artist_row:
                    return {'success': False, 'error': f'Artist with ID {artist_id} not found'}

                artist_name = artist_row['name']
                server_source = artist_row['server_source']

                # Parse artist data
                artist_data = dict(artist_row)
                # Parse genres JSON
                if artist_data.get('genres'):
                    try:
                        parsed = json.loads(artist_data['genres'])
                        artist_data['genres'] = parsed if isinstance(parsed, list) else [str(parsed)]
                    except (json.JSONDecodeError, ValueError):
                        artist_data['genres'] = [g.strip() for g in artist_data['genres'].split(',') if g.strip()]
                else:
                    artist_data['genres'] = []

                # Get all album IDs for this artist (including same-name artists on same server)
                cursor.execute("""
                    SELECT id FROM artists
                    WHERE name = ? AND server_source = ?
                """, (artist_name, server_source))
                artist_ids = [row['id'] for row in cursor.fetchall()]

                # Get all albums with all columns
                placeholders = ','.join('?' * len(artist_ids))
                cursor.execute(f"""
                    SELECT * FROM albums
                    WHERE artist_id IN ({placeholders})
                    ORDER BY year DESC, title
                """, artist_ids)
                album_rows = cursor.fetchall()

                albums = []
                for album_row in album_rows:
                    album_data = dict(album_row)
                    # Parse album genres
                    if album_data.get('genres'):
                        try:
                            parsed = json.loads(album_data['genres'])
                            album_data['genres'] = parsed if isinstance(parsed, list) else [str(parsed)]
                        except (json.JSONDecodeError, ValueError):
                            album_data['genres'] = [g.strip() for g in album_data['genres'].split(',') if g.strip()]
                    else:
                        album_data['genres'] = []

                    # Get all tracks for this album with all columns
                    cursor.execute("""
                        SELECT * FROM tracks
                        WHERE album_id = ?
                        ORDER BY track_number, title
                    """, (album_data['id'],))
                    track_rows = cursor.fetchall()
                    album_data['tracks'] = [dict(tr) for tr in track_rows]

                    # Determine record type from data if not set
                    if not album_data.get('record_type'):
                        track_count = len(album_data['tracks']) or album_data.get('track_count') or 0
                        title_lower = (album_data.get('title') or '').lower()
                        if any(ind in title_lower for ind in ['single', ' - single', '(single)']) and track_count <= 3:
                            album_data['record_type'] = 'single'
                        elif any(ind in title_lower for ind in ['ep', ' - ep', '(ep)', 'extended play']) or (4 <= track_count <= 7):
                            album_data['record_type'] = 'ep'
                        else:
                            album_data['record_type'] = 'album'

                    albums.append(album_data)

                return {
                    'success': True,
                    'artist': artist_data,
                    'albums': albums
                }

        except Exception as e:
            logger.error(f"Error getting artist full detail for ID {artist_id}: {e}")
            return {'success': False, 'error': str(e)}

    def update_artist_fields(self, artist_id, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Update artist metadata fields. Only whitelisted fields are accepted."""
        valid_updates = {k: v for k, v in updates.items() if k in self.ARTIST_EDITABLE_FIELDS}
        if not valid_updates:
            return {'success': False, 'error': 'No valid fields to update'}

        # Serialize genres to JSON if present
        if 'genres' in valid_updates:
            if isinstance(valid_updates['genres'], list):
                valid_updates['genres'] = json.dumps(valid_updates['genres'])

        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                set_clause = ', '.join(f'{k} = ?' for k in valid_updates)
                values = list(valid_updates.values()) + [artist_id]
                cursor.execute(f"UPDATE artists SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE id = ?", values)
                conn.commit()
                if cursor.rowcount == 0:
                    return {'success': False, 'error': f'Artist {artist_id} not found'}
                return {'success': True, 'updated_fields': list(valid_updates.keys())}
        except Exception as e:
            logger.error(f"Error updating artist {artist_id}: {e}")
            return {'success': False, 'error': str(e)}

    def update_album_fields(self, album_id, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Update album metadata fields. Only whitelisted fields are accepted."""
        valid_updates = {k: v for k, v in updates.items() if k in self.ALBUM_EDITABLE_FIELDS}
        if not valid_updates:
            return {'success': False, 'error': 'No valid fields to update'}

        if 'genres' in valid_updates:
            if isinstance(valid_updates['genres'], list):
                valid_updates['genres'] = json.dumps(valid_updates['genres'])

        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                set_clause = ', '.join(f'{k} = ?' for k in valid_updates)
                values = list(valid_updates.values()) + [album_id]
                cursor.execute(f"UPDATE albums SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE id = ?", values)
                conn.commit()
                if cursor.rowcount == 0:
                    return {'success': False, 'error': f'Album {album_id} not found'}
                return {'success': True, 'updated_fields': list(valid_updates.keys())}
        except Exception as e:
            logger.error(f"Error updating album {album_id}: {e}")
            return {'success': False, 'error': str(e)}

    def update_track_fields(self, track_id, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Update track metadata fields. Only whitelisted fields are accepted."""
        valid_updates = {k: v for k, v in updates.items() if k in self.TRACK_EDITABLE_FIELDS}
        if not valid_updates:
            return {'success': False, 'error': 'No valid fields to update'}

        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                set_clause = ', '.join(f'{k} = ?' for k in valid_updates)
                values = list(valid_updates.values()) + [track_id]
                cursor.execute(f"UPDATE tracks SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE id = ?", values)
                conn.commit()
                if cursor.rowcount == 0:
                    return {'success': False, 'error': f'Track {track_id} not found'}
                return {'success': True, 'updated_fields': list(valid_updates.keys())}
        except Exception as e:
            logger.error(f"Error updating track {track_id}: {e}")
            return {'success': False, 'error': str(e)}

    def batch_update_tracks(self, track_ids: List[str], updates: Dict[str, Any]) -> Dict[str, Any]:
        """Batch update multiple tracks with the same field values."""
        valid_updates = {k: v for k, v in updates.items() if k in self.TRACK_EDITABLE_FIELDS}
        if not valid_updates:
            return {'success': False, 'error': 'No valid fields to update'}
        if not track_ids:
            return {'success': False, 'error': 'No track IDs provided'}

        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                set_clause = ', '.join(f'{k} = ?' for k in valid_updates)
                placeholders = ','.join('?' * len(track_ids))
                values = list(valid_updates.values()) + list(track_ids)
                cursor.execute(
                    f"UPDATE tracks SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE id IN ({placeholders})",
                    values
                )
                conn.commit()
                return {'success': True, 'updated_count': cursor.rowcount, 'updated_fields': list(valid_updates.keys())}
        except Exception as e:
            logger.error(f"Error batch updating tracks: {e}")
            return {'success': False, 'error': str(e)}

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

    # ==================== Sync Match Cache ====================

    def read_sync_match_cache(self, spotify_track_id: str, server_source: str) -> Optional[Dict]:
        """Read a cached sync match. Returns {server_track_id, server_track_title, confidence} or None.
        Also bumps last_used_at and use_count on hit."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT server_track_id, server_track_title, confidence FROM sync_match_cache
                WHERE spotify_track_id = ? AND server_source = ?
            """, (spotify_track_id, server_source))
            row = cursor.fetchone()
            if row:
                cursor.execute("""
                    UPDATE sync_match_cache
                    SET last_used_at = CURRENT_TIMESTAMP, use_count = use_count + 1
                    WHERE spotify_track_id = ? AND server_source = ?
                """, (spotify_track_id, server_source))
                conn.commit()
                return {
                    'server_track_id': row['server_track_id'],
                    'server_track_title': row['server_track_title'],
                    'confidence': row['confidence'],
                }
            return None
        except Exception as e:
            logger.error(f"Error reading sync match cache: {e}")
            return None

    def save_sync_match_cache(self, spotify_track_id: str, normalized_title: str,
                               normalized_artist: str, server_source: str,
                               server_track_id, server_track_title: str,
                               confidence: float) -> bool:
        """Save a sync match to cache. Uses INSERT OR REPLACE for upsert."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO sync_match_cache
                (spotify_track_id, normalized_title, normalized_artist, server_source,
                 server_track_id, server_track_title, confidence,
                 created_at, last_used_at, use_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 1)
            """, (spotify_track_id, normalized_title, normalized_artist, server_source,
                  server_track_id, server_track_title, confidence))
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error saving sync match cache: {e}")
            return False

    def invalidate_sync_match_cache(self, server_source: str = None) -> int:
        """Clear sync match cache entries. If server_source given, only clear that server's entries."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            if server_source:
                cursor.execute("DELETE FROM sync_match_cache WHERE server_source = ?", (server_source,))
            else:
                cursor.execute("DELETE FROM sync_match_cache")
            conn.commit()
            return cursor.rowcount
        except Exception as e:
            logger.error(f"Error invalidating sync match cache: {e}")
            return 0

    # ==================== Download Blacklist Methods ====================

    def add_to_blacklist(self, track_title: str, track_artist: str, blocked_filename: str, blocked_username: str, reason: str = 'user_rejected') -> bool:
        """Add a download source to the blacklist so it won't be used again."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR IGNORE INTO download_blacklist
                (track_title, track_artist, blocked_filename, blocked_username, reason)
                VALUES (?, ?, ?, ?, ?)
            """, (track_title, track_artist, blocked_filename, blocked_username, reason))
            conn.commit()
            return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error adding to blacklist: {e}")
            return False

    def is_blacklisted(self, username: str, filename: str) -> bool:
        """Check if a download source is blacklisted."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT 1 FROM download_blacklist
                WHERE blocked_username = ? AND blocked_filename = ?
                LIMIT 1
            """, (username, filename))
            return cursor.fetchone() is not None
        except Exception:
            return False

    def get_blacklist(self, limit: int = 100, offset: int = 0) -> list:
        """Get blacklist entries."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, track_title, track_artist, blocked_filename, blocked_username, reason, created_at
                FROM download_blacklist
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
            """, (limit, offset))
            return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Error getting blacklist: {e}")
            return []

    def remove_from_blacklist(self, blacklist_id: int) -> bool:
        """Remove an entry from the blacklist."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM download_blacklist WHERE id = ?", (blacklist_id,))
            conn.commit()
            return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error removing from blacklist: {e}")
            return False

    # ==================== Discovery Artist Blacklist Methods ====================

    def add_to_discovery_blacklist(self, artist_name: str, spotify_id: str = None,
                                   itunes_id: str = None, deezer_id: str = None) -> bool:
        """Block an artist from appearing in discovery results."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO discovery_artist_blacklist
                (artist_name, spotify_artist_id, itunes_artist_id, deezer_artist_id)
                VALUES (?, ?, ?, ?)
            """, (artist_name.strip(), spotify_id, itunes_id, deezer_id))
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error adding to discovery blacklist: {e}")
            return False

    def remove_from_discovery_blacklist(self, blacklist_id: int) -> bool:
        """Remove an artist from the discovery blacklist."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM discovery_artist_blacklist WHERE id = ?", (blacklist_id,))
            conn.commit()
            return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error removing from discovery blacklist: {e}")
            return False

    def get_discovery_blacklist(self) -> list:
        """Get all blacklisted discovery artists."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, artist_name, spotify_artist_id, itunes_artist_id, deezer_artist_id, created_at
                FROM discovery_artist_blacklist ORDER BY created_at DESC
            """)
            return [dict(r) for r in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Error getting discovery blacklist: {e}")
            return []

    def get_discovery_blacklist_names(self) -> set:
        """Get set of blacklisted artist names (lowercased) for fast filtering."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT LOWER(artist_name) FROM discovery_artist_blacklist")
            return {r[0] for r in cursor.fetchall()}
        except Exception as e:
            logger.error(f"Error getting discovery blacklist names: {e}")
            return set()

    # ==================== Liked Artists Pool Methods ====================

    @staticmethod
    def _normalize_artist_name_for_dedup(name: str) -> str:
        """Normalize artist name for deduplication. Lowercases, strips diacritics,
        removes 'the ' prefix, collapses whitespace."""
        import unicodedata
        if not name:
            return ''
        n = unicodedata.normalize('NFKD', name)
        n = ''.join(c for c in n if not unicodedata.combining(c))
        n = n.lower().strip()
        if n.startswith('the '):
            n = n[4:]
        # Handle "Artist, The" format (Last.fm)
        if n.endswith(', the'):
            n = n[:-5]
        n = ' '.join(n.split())  # collapse whitespace
        return n

    # Known placeholder/default images that should be treated as "no image"
    _PLACEHOLDER_IMAGES = {
        '2a96cbd8b46e442fc41c2b86b821562f',  # Last.fm default star
    }

    @classmethod
    def _is_placeholder_image(cls, url: str) -> bool:
        """Check if an image URL is a known service placeholder."""
        if not url:
            return True
        return any(ph in url for ph in cls._PLACEHOLDER_IMAGES)

    def upsert_liked_artist(self, artist_name: str, source_service: str,
                            source_id: str = None, source_id_type: str = None,
                            image_url: str = None, genres: list = None,
                            profile_id: int = 1) -> bool:
        """Insert or merge a liked artist into the pool. Deduplicates by normalized name."""
        try:
            import json
            # Reject known placeholder images
            if self._is_placeholder_image(image_url):
                image_url = None
            normalized = self._normalize_artist_name_for_dedup(artist_name)
            if not normalized:
                return False

            conn = self._get_connection()
            cursor = conn.cursor()

            # Check if exists to merge source_services
            cursor.execute(
                "SELECT id, source_services FROM liked_artists_pool WHERE profile_id = ? AND normalized_name = ?",
                (profile_id, normalized)
            )
            existing = cursor.fetchone()

            if existing:
                # Merge source into existing entry
                current_sources = json.loads(existing['source_services'] or '[]')
                if source_service not in current_sources:
                    current_sources.append(source_service)

                # Build SET clause with COALESCE for IDs and image
                set_parts = [
                    "source_services = ?",
                    "updated_at = CURRENT_TIMESTAMP",
                    "last_fetched_at = CURRENT_TIMESTAMP",
                ]
                params = [json.dumps(current_sources)]

                if source_id and source_id_type:
                    col = {'spotify': 'spotify_artist_id', 'itunes': 'itunes_artist_id',
                           'deezer': 'deezer_artist_id', 'discogs': 'discogs_artist_id'}.get(source_id_type)
                    if col:
                        set_parts.append(f"{col} = COALESCE({col}, ?)")
                        params.append(source_id)
                if image_url:
                    set_parts.append("image_url = COALESCE(image_url, ?)")
                    params.append(image_url)
                if genres:
                    set_parts.append("genres = COALESCE(genres, ?)")
                    params.append(json.dumps(genres))

                params.extend([profile_id, normalized])
                cursor.execute(
                    f"UPDATE liked_artists_pool SET {', '.join(set_parts)} WHERE profile_id = ? AND normalized_name = ?",
                    params
                )
            else:
                # New entry
                sources_json = json.dumps([source_service])
                id_cols = {'spotify': 'spotify_artist_id', 'itunes': 'itunes_artist_id',
                           'deezer': 'deezer_artist_id', 'discogs': 'discogs_artist_id'}
                col_values = {v: None for v in id_cols.values()}
                if source_id and source_id_type and source_id_type in id_cols:
                    col_values[id_cols[source_id_type]] = source_id

                cursor.execute("""
                    INSERT INTO liked_artists_pool
                    (artist_name, normalized_name, spotify_artist_id, itunes_artist_id,
                     deezer_artist_id, discogs_artist_id, image_url, genres,
                     source_services, profile_id, last_fetched_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (
                    artist_name, normalized, col_values['spotify_artist_id'],
                    col_values['itunes_artist_id'], col_values['deezer_artist_id'],
                    col_values['discogs_artist_id'], image_url,
                    json.dumps(genres) if genres else None, sources_json, profile_id
                ))

            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error upserting liked artist '{artist_name}': {e}")
            return False

    def get_liked_artists(self, profile_id: int = 1, limit: int = None,
                          random: bool = False, matched_only: bool = True,
                          page: int = 1, per_page: int = 50,
                          search: str = None, source_filter: str = None,
                          sort: str = 'name',
                          require_source_id: str = None,
                          require_image: bool = False) -> dict:
        """Get liked artists from the pool. Returns {artists: [...], total: N}.
        require_source_id: column name like 'spotify_artist_id' — only return artists with this ID set.
        require_image: if True, only return artists with a non-empty image_url."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            where = ["profile_id = ?"]
            params = [profile_id]
            if matched_only:
                where.append("match_status = 'matched'")
            if require_source_id:
                where.append(f"{require_source_id} IS NOT NULL AND {require_source_id} != ''")
            if require_image:
                where.append("image_url IS NOT NULL AND image_url != ''")
            if search:
                where.append("artist_name LIKE ? COLLATE NOCASE")
                params.append(f"%{search}%")
            if source_filter:
                where.append("source_services LIKE ?")
                params.append(f'%"{source_filter}"%')

            where_clause = " AND ".join(where)

            cursor.execute(f"SELECT COUNT(*) FROM liked_artists_pool WHERE {where_clause}", params)
            total = cursor.fetchone()[0]

            order = "RANDOM()" if random else {
                'name': 'artist_name COLLATE NOCASE',
                'recent': 'created_at DESC',
                'source': 'source_services, artist_name COLLATE NOCASE'
            }.get(sort, 'artist_name COLLATE NOCASE')

            query_limit = limit if limit else per_page
            offset = (page - 1) * per_page if not limit else 0

            cursor.execute(f"""
                SELECT * FROM liked_artists_pool
                WHERE {where_clause}
                ORDER BY {order}
                LIMIT ? OFFSET ?
            """, params + [query_limit, offset])

            import json
            artists = []
            for r in cursor.fetchall():
                d = dict(r)
                d['source_services'] = json.loads(d['source_services'] or '[]')
                d['genres'] = json.loads(d['genres']) if d['genres'] else []
                artists.append(d)

            return {'artists': artists, 'total': total}
        except Exception as e:
            logger.error(f"Error getting liked artists: {e}")
            return {'artists': [], 'total': 0}

    def get_liked_artists_last_fetch(self, profile_id: int = 1):
        """Get the most recent fetch timestamp."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT MAX(last_fetched_at) FROM liked_artists_pool WHERE profile_id = ?",
                (profile_id,)
            )
            row = cursor.fetchone()
            return row[0] if row and row[0] else None
        except Exception:
            return None

    def update_liked_artist_match(self, pool_id: int, active_source: str = None,
                                  active_source_id: str = None, image_url: str = None,
                                  all_ids: dict = None) -> bool:
        """Mark a liked artist as matched. Stores all discovered source IDs, not just active.
        all_ids: optional dict like {'spotify_artist_id': '...', 'itunes_artist_id': '...'}"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            set_parts = ["match_status = 'matched'", "updated_at = CURRENT_TIMESTAMP"]
            params = []

            if active_source and active_source_id:
                set_parts.append("active_source = ?")
                set_parts.append("active_source_id = ?")
                params.extend([active_source, active_source_id])

            # Store all discovered source IDs (COALESCE preserves existing values)
            if all_ids:
                for col in ('spotify_artist_id', 'itunes_artist_id', 'deezer_artist_id', 'discogs_artist_id'):
                    val = all_ids.get(col)
                    if val:
                        set_parts.append(f"{col} = COALESCE({col}, ?)")
                        params.append(str(val))

            # Update image — replace if current is NULL or empty string
            if image_url:
                set_parts.append("image_url = CASE WHEN image_url IS NULL OR image_url = '' THEN ? ELSE image_url END")
                params.append(image_url)

            params.append(pool_id)
            cursor.execute(f"UPDATE liked_artists_pool SET {', '.join(set_parts)} WHERE id = ?", params)
            conn.commit()
            return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error updating liked artist match: {e}")
            return False

    def sync_liked_artists_watchlist_flags(self, profile_id: int = 1) -> int:
        """Batch-update on_watchlist flags by checking against watchlist_artists.
        Uses case-insensitive artist_name comparison (not normalized_name) to avoid
        normalization mismatches like 'The Beatles' vs 'beatles'."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            # Reset all, then set matches
            cursor.execute(
                "UPDATE liked_artists_pool SET on_watchlist = 0 WHERE profile_id = ?",
                (profile_id,)
            )
            cursor.execute("""
                UPDATE liked_artists_pool SET on_watchlist = 1
                WHERE profile_id = ? AND EXISTS (
                    SELECT 1 FROM watchlist_artists wa
                    WHERE wa.profile_id = liked_artists_pool.profile_id
                      AND wa.artist_name = liked_artists_pool.artist_name COLLATE NOCASE
                )
            """, (profile_id,))
            conn.commit()
            return cursor.rowcount
        except Exception as e:
            logger.error(f"Error syncing liked artists watchlist flags: {e}")
            return 0

    def get_liked_artists_pending_match(self, profile_id: int = 1, limit: int = 50) -> list:
        """Get artists that haven't been matched to the active source yet."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM liked_artists_pool
                WHERE profile_id = ? AND match_status = 'pending'
                ORDER BY created_at
                LIMIT ?
            """, (profile_id, limit))
            import json
            return [dict(r) for r in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Error getting pending liked artists: {e}")
            return []

    def clear_liked_artists(self, profile_id: int = 1) -> int:
        """Clear all liked artists for a profile."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM liked_artists_pool WHERE profile_id = ?", (profile_id,))
            conn.commit()
            return cursor.rowcount
        except Exception as e:
            logger.error(f"Error clearing liked artists: {e}")
            return 0

    # ==================== Liked Albums Pool Methods ====================

    @staticmethod
    def _normalize_album_key(artist_name: str, album_name: str) -> str:
        """Normalize artist+album into a dedup key."""
        import unicodedata
        def _norm(s):
            if not s:
                return ''
            n = unicodedata.normalize('NFKD', s)
            n = ''.join(c for c in n if not unicodedata.combining(c))
            n = n.lower().strip()
            if n.startswith('the '):
                n = n[4:]
            return ' '.join(n.split())
        return f"{_norm(artist_name)}::{_norm(album_name)}"

    def upsert_liked_album(self, album_name: str, artist_name: str, source_service: str,
                           source_id: str = None, source_id_type: str = None,
                           image_url: str = None, release_date: str = None,
                           total_tracks: int = 0, profile_id: int = 1) -> bool:
        """Insert or merge a liked album into the pool. Deduplicates by normalized artist+album key."""
        try:
            import json
            if self._is_placeholder_image(image_url):
                image_url = None
            normalized = self._normalize_album_key(artist_name, album_name)
            if not normalized or '::' not in normalized:
                return False

            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute(
                "SELECT id, source_services FROM liked_albums_pool WHERE profile_id = ? AND normalized_key = ?",
                (profile_id, normalized)
            )
            existing = cursor.fetchone()

            if existing:
                current_sources = json.loads(existing['source_services'] or '[]')
                if source_service not in current_sources:
                    current_sources.append(source_service)

                set_parts = [
                    "source_services = ?",
                    "updated_at = CURRENT_TIMESTAMP",
                    "last_fetched_at = CURRENT_TIMESTAMP",
                ]
                params = [json.dumps(current_sources)]

                if source_id and source_id_type:
                    col = {'spotify': 'spotify_album_id', 'tidal': 'tidal_album_id',
                           'deezer': 'deezer_album_id'}.get(source_id_type)
                    if col:
                        set_parts.append(f"{col} = COALESCE({col}, ?)")
                        params.append(source_id)
                if image_url:
                    set_parts.append("image_url = COALESCE(image_url, ?)")
                    params.append(image_url)
                if release_date:
                    set_parts.append("release_date = COALESCE(release_date, ?)")
                    params.append(release_date)
                if total_tracks:
                    set_parts.append("total_tracks = COALESCE(NULLIF(total_tracks, 0), ?)")
                    params.append(total_tracks)

                params.extend([profile_id, normalized])
                cursor.execute(
                    f"UPDATE liked_albums_pool SET {', '.join(set_parts)} WHERE profile_id = ? AND normalized_key = ?",
                    params
                )
            else:
                sources_json = json.dumps([source_service])
                id_cols = {'spotify': 'spotify_album_id', 'tidal': 'tidal_album_id',
                           'deezer': 'deezer_album_id'}
                col_values = {v: None for v in id_cols.values()}
                if source_id and source_id_type and source_id_type in id_cols:
                    col_values[id_cols[source_id_type]] = source_id

                cursor.execute("""
                    INSERT INTO liked_albums_pool
                    (album_name, artist_name, normalized_key, spotify_album_id, tidal_album_id,
                     deezer_album_id, image_url, release_date, total_tracks, source_services,
                     profile_id, last_fetched_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (
                    album_name, artist_name, normalized,
                    col_values['spotify_album_id'], col_values['tidal_album_id'],
                    col_values['deezer_album_id'],
                    image_url, release_date, total_tracks or 0,
                    sources_json, profile_id
                ))

            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error upserting liked album '{album_name}' by '{artist_name}': {e}")
            return False

    def get_liked_albums(self, profile_id: int = 1, page: int = 1, per_page: int = 50,
                         search: str = None, source_filter: str = None,
                         sort: str = 'artist_name') -> dict:
        """Get liked albums from the pool. Returns {albums: [...], total: N}."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            where = ["profile_id = ?"]
            params = [profile_id]
            if search:
                where.append("(album_name LIKE ? COLLATE NOCASE OR artist_name LIKE ? COLLATE NOCASE)")
                params.extend([f"%{search}%", f"%{search}%"])
            if source_filter:
                where.append("source_services LIKE ?")
                params.append(f'%"{source_filter}"%')

            where_clause = " AND ".join(where)

            cursor.execute(f"SELECT COUNT(*) FROM liked_albums_pool WHERE {where_clause}", params)
            total = cursor.fetchone()[0]

            order = {
                'artist_name': 'artist_name COLLATE NOCASE, album_name COLLATE NOCASE',
                'album_name': 'album_name COLLATE NOCASE',
                'recent': 'created_at DESC',
                'release_date': 'release_date DESC',
            }.get(sort, 'artist_name COLLATE NOCASE')

            offset = (page - 1) * per_page
            cursor.execute(f"""
                SELECT * FROM liked_albums_pool
                WHERE {where_clause}
                ORDER BY {order}
                LIMIT ? OFFSET ?
            """, params + [per_page, offset])

            import json
            albums = []
            for r in cursor.fetchall():
                d = dict(r)
                d['source_services'] = json.loads(d['source_services'] or '[]')
                albums.append(d)

            return {'albums': albums, 'total': total}
        except Exception as e:
            logger.error(f"Error getting liked albums: {e}")
            return {'albums': [], 'total': 0}

    def get_liked_albums_last_fetch(self, profile_id: int = 1):
        """Get the most recent fetch timestamp."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT MAX(last_fetched_at) FROM liked_albums_pool WHERE profile_id = ?",
                (profile_id,)
            )
            row = cursor.fetchone()
            return row[0] if row and row[0] else None
        except Exception:
            return None

    def clear_liked_albums(self, profile_id: int = 1) -> int:
        """Clear all liked albums for a profile."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM liked_albums_pool WHERE profile_id = ?", (profile_id,))
            conn.commit()
            return cursor.rowcount
        except Exception as e:
            logger.error(f"Error clearing liked albums: {e}")
            return 0

    # ==================== Track Download Provenance Methods ====================

    def record_track_download(self, file_path: str, source_service: str, source_username: str,
                               source_filename: str, source_size: int = 0, audio_quality: str = '',
                               track_title: str = '', track_artist: str = '', track_album: str = '',
                               status: str = 'completed', track_id: str = None,
                               bit_depth: int = None, sample_rate: int = None, bitrate: int = None) -> Optional[int]:
        """Record a download with full source provenance. Returns the record ID."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # Try to link to existing library track by file path if track_id not given
            if not track_id and file_path:
                cursor.execute("SELECT id FROM tracks WHERE file_path = ? LIMIT 1", (file_path,))
                row = cursor.fetchone()
                if not row:
                    # Fallback: match by filename suffix (handles server path vs local path differences)
                    import os as _os
                    fname = _os.path.basename(file_path.replace('\\', '/'))
                    if fname:
                        cursor.execute(
                            "SELECT id FROM tracks WHERE file_path LIKE ? OR file_path LIKE ? LIMIT 1",
                            (f'%/{fname}', f'%\\{fname}')
                        )
                        row = cursor.fetchone()
                if row:
                    track_id = str(row[0])

            cursor.execute("""
                INSERT INTO track_downloads
                (track_id, file_path, source_service, source_username, source_filename,
                 source_size, audio_quality, track_title, track_artist, track_album, status,
                 bit_depth, sample_rate, bitrate)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (track_id, file_path, source_service, source_username, source_filename,
                  source_size, audio_quality, track_title, track_artist, track_album, status,
                  bit_depth, sample_rate, bitrate))
            conn.commit()
            return cursor.lastrowid
        except Exception as e:
            logger.error(f"Error recording track download: {e}")
            return None

    def get_track_downloads(self, track_id: str) -> list:
        """Get all download records for a library track."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM track_downloads
                WHERE track_id = ?
                ORDER BY created_at DESC
            """, (str(track_id),))
            return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Error getting track downloads: {e}")
            return []

    def update_provenance_file_path(self, old_path: str, new_path: str) -> bool:
        """Update file_path in provenance records when a file is transcoded/moved."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE track_downloads SET file_path = ? WHERE file_path = ?
            """, (new_path, old_path))
            conn.commit()
            return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error updating provenance file path: {e}")
            return False

    def get_download_by_file_path(self, file_path: str) -> Optional[dict]:
        """Find the most recent download record for a file path."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM track_downloads
                WHERE file_path = ?
                ORDER BY created_at DESC
                LIMIT 1
            """, (file_path,))
            row = cursor.fetchone()
            return dict(row) if row else None
        except Exception as e:
            logger.error(f"Error getting download by file path: {e}")
            return None

    def get_download_by_filename(self, filename: str, link_track_id: str = None) -> Optional[dict]:
        """Find a download record by filename suffix (handles server vs local path mismatches).
        Optionally back-links the track_id on the found record for future fast lookups."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            # Match using both separator styles to handle Windows vs Unix paths
            cursor.execute("""
                SELECT * FROM track_downloads
                WHERE file_path LIKE ? OR file_path LIKE ?
                ORDER BY created_at DESC
                LIMIT 1
            """, (f'%/{filename}', f'%\\{filename}'))
            row = cursor.fetchone()
            if row and link_track_id:
                # Back-link this record so future track_id lookups work directly
                cursor.execute(
                    "UPDATE track_downloads SET track_id = ? WHERE id = ? AND track_id IS NULL",
                    (str(link_track_id), row['id'])
                )
                conn.commit()
            conn.close()
            return dict(row) if row else None
        except Exception as e:
            logger.error(f"Error getting download by filename: {e}")
            return None

    # ==================== Discovery Pool Methods ====================

    def get_discovery_pool_matched(self, limit: int = 500) -> list:
        """Get all cached discovery matches, ordered by most recently used."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, original_title, original_artist, normalized_title, normalized_artist,
                       provider, match_confidence, matched_data_json, use_count, last_used_at, created_at
                FROM discovery_match_cache
                ORDER BY last_used_at DESC
                LIMIT ?
            """, (limit,))
            results = []
            for row in cursor.fetchall():
                try:
                    matched_data = json.loads(row['matched_data_json'])
                except (json.JSONDecodeError, TypeError):
                    matched_data = {}
                results.append({
                    'id': row['id'],
                    'original_title': row['original_title'] or row['normalized_title'],
                    'original_artist': row['original_artist'] or row['normalized_artist'],
                    'provider': row['provider'],
                    'confidence': row['match_confidence'],
                    'matched_data': matched_data,
                    'use_count': row['use_count'],
                    'last_used_at': row['last_used_at'],
                    'created_at': row['created_at'],
                })
            return results
        except Exception as e:
            logger.error(f"Error getting discovery pool matched: {e}")
            return []

    def get_discovery_pool_failed(self, profile_id: int = None, playlist_id: int = None) -> list:
        """Get all tracks where discovery was attempted but failed."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            query = """
                SELECT mpt.id, mpt.track_name, mpt.artist_name, mpt.album_name,
                       mpt.playlist_id, mp.name as playlist_name
                FROM mirrored_playlist_tracks mpt
                JOIN mirrored_playlists mp ON mpt.playlist_id = mp.id
                WHERE mpt.extra_data LIKE '%"discovery_attempted": true%'
                  AND mpt.extra_data NOT LIKE '%"discovered": true%'
            """
            params = []
            if playlist_id:
                query += " AND mpt.playlist_id = ?"
                params.append(playlist_id)
            elif profile_id:
                query += " AND mp.profile_id = ?"
                params.append(profile_id)
            query += " ORDER BY mp.name, mpt.track_name"
            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Error getting discovery pool failed: {e}")
            return []

    def delete_discovery_cache_entry(self, entry_id: int) -> bool:
        """Delete a single entry from the discovery match cache."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM discovery_match_cache WHERE id = ?", (entry_id,))
            conn.commit()
            return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error deleting discovery cache entry: {e}")
            return False

    def get_discovery_pool_stats(self, profile_id: int = None) -> dict:
        """Get counts for matched and failed discovery tracks."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) as cnt FROM discovery_match_cache")
            matched = cursor.fetchone()['cnt']

            query = """
                SELECT COUNT(*) as cnt FROM mirrored_playlist_tracks mpt
                JOIN mirrored_playlists mp ON mpt.playlist_id = mp.id
                WHERE mpt.extra_data LIKE '%"discovery_attempted": true%'
                  AND mpt.extra_data NOT LIKE '%"discovered": true%'
            """
            params = []
            if profile_id:
                query += " AND mp.profile_id = ?"
                params.append(profile_id)
            cursor.execute(query, params)
            failed = cursor.fetchone()['cnt']
            return {'matched': matched, 'failed': failed}
        except Exception as e:
            logger.error(f"Error getting discovery pool stats: {e}")
            return {'matched': 0, 'failed': 0}

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

    def delete_all_retag_groups(self) -> int:
        """Delete all retag groups and tracks. Returns count deleted."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM retag_groups")
            count = cursor.fetchone()[0]
            cursor.execute("DELETE FROM retag_tracks")
            cursor.execute("DELETE FROM retag_groups")
            conn.commit()
            return count
        except Exception as e:
            logger.error(f"Error clearing all retag groups: {e}")
            return 0

    # ── Full-row API query methods (return dicts, not dataclasses) ────────

    def api_get_artist(self, artist_id: int) -> Optional[Dict[str, Any]]:
        """Get artist by ID with ALL columns as a dict."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM artists WHERE id = ?", (artist_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
        except Exception as e:
            logger.error(f"API: Error getting artist {artist_id}: {e}")
            return None

    def api_get_album(self, album_id: int) -> Optional[Dict[str, Any]]:
        """Get album by ID with ALL columns as a dict."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM albums WHERE id = ?", (album_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
        except Exception as e:
            logger.error(f"API: Error getting album {album_id}: {e}")
            return None

    def api_get_track(self, track_id: int) -> Optional[Dict[str, Any]]:
        """Get track by ID with ALL columns as a dict, plus artist_name and album_title."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT t.*, a.name as artist_name, al.title as album_title
                FROM tracks t
                LEFT JOIN artists a ON t.artist_id = a.id
                LEFT JOIN albums al ON t.album_id = al.id
                WHERE t.id = ?
            """, (track_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
        except Exception as e:
            logger.error(f"API: Error getting track {track_id}: {e}")
            return None

    def api_get_albums_by_artist(self, artist_id: int) -> List[Dict[str, Any]]:
        """Get all albums for an artist with ALL columns."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM albums WHERE artist_id = ? ORDER BY year, title",
                (artist_id,),
            )
            return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"API: Error getting albums for artist {artist_id}: {e}")
            return []

    def api_get_tracks_by_album(self, album_id: int) -> List[Dict[str, Any]]:
        """Get all tracks for an album with ALL columns, plus artist_name."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT t.*, a.name as artist_name
                FROM tracks t
                LEFT JOIN artists a ON t.artist_id = a.id
                WHERE t.album_id = ?
                ORDER BY t.track_number, t.title
            """, (album_id,))
            return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"API: Error getting tracks for album {album_id}: {e}")
            return []

    def api_get_tracks_by_ids(self, track_ids: List[int]) -> List[Dict[str, Any]]:
        """Get multiple tracks by ID with ALL columns, plus artist_name and album_title."""
        if not track_ids:
            return []
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            placeholders = ",".join("?" * len(track_ids))
            cursor.execute(f"""
                SELECT t.*, a.name as artist_name, al.title as album_title
                FROM tracks t
                LEFT JOIN artists a ON t.artist_id = a.id
                LEFT JOIN albums al ON t.album_id = al.id
                WHERE t.id IN ({placeholders})
            """, track_ids)
            return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"API: Error getting tracks by IDs: {e}")
            return []

    def api_lookup_by_external_id(self, table: str, provider: str, external_id: str) -> Optional[Dict[str, Any]]:
        """Look up an entity by external provider ID.

        Args:
            table: 'artists', 'albums', or 'tracks'
            provider: 'spotify', 'musicbrainz', 'itunes', 'deezer', 'audiodb',
                      'tidal', 'qobuz', 'genius' (genius: artists/tracks only)
        """
        column_map = {
            "artists": {
                "spotify": "spotify_artist_id",
                "musicbrainz": "musicbrainz_id",
                "itunes": "itunes_artist_id",
                "deezer": "deezer_id",
                "audiodb": "audiodb_id",
                "tidal": "tidal_id",
                "qobuz": "qobuz_id",
                "genius": "genius_id",
            },
            "albums": {
                "spotify": "spotify_album_id",
                "musicbrainz": "musicbrainz_release_id",
                "itunes": "itunes_album_id",
                "deezer": "deezer_id",
                "audiodb": "audiodb_id",
                "tidal": "tidal_id",
                "qobuz": "qobuz_id",
            },
            "tracks": {
                "spotify": "spotify_track_id",
                "musicbrainz": "musicbrainz_recording_id",
                "itunes": "itunes_track_id",
                "deezer": "deezer_id",
                "audiodb": "audiodb_id",
                "tidal": "tidal_id",
                "qobuz": "qobuz_id",
                "genius": "genius_id",
            },
        }
        if table not in column_map or provider not in column_map[table]:
            return None
        column = column_map[table][provider]
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(f"SELECT * FROM {table} WHERE {column} = ?", (external_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
        except Exception as e:
            logger.error(f"API: External lookup {table}.{column}={external_id}: {e}")
            return None

    def api_get_genres(self, table: str = "artists") -> List[Dict[str, Any]]:
        """Get all unique genres with counts from the given table."""
        if table not in ("artists", "albums"):
            return []
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(f"SELECT genres FROM {table}")
            genre_counts: Dict[str, int] = {}
            for row in cursor.fetchall():
                raw = row["genres"]
                if raw:
                    try:
                        genres = json.loads(raw) if isinstance(raw, str) else raw
                        if isinstance(genres, list):
                            for g in genres:
                                g = g.strip() if isinstance(g, str) else str(g)
                                if g:
                                    genre_counts[g] = genre_counts.get(g, 0) + 1
                    except (json.JSONDecodeError, TypeError):
                        pass
            return sorted(
                [{"name": k, "count": v} for k, v in genre_counts.items()],
                key=lambda x: x["count"],
                reverse=True,
            )
        except Exception as e:
            logger.error(f"API: Error getting genres from {table}: {e}")
            return []

    # ── Library History ─────────────────────────────────────────────────

    def add_library_history_entry(self, event_type, title, artist_name=None, album_name=None,
                                  quality=None, server_source=None, file_path=None, thumb_url=None,
                                  download_source=None, source_track_id=None, source_track_title=None,
                                  source_filename=None, acoustid_result=None, source_artist=None):
        """Record a download or import event to the library history table."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO library_history (event_type, title, artist_name, album_name,
                                             quality, server_source, file_path, thumb_url, download_source,
                                             source_track_id, source_track_title, source_filename,
                                             acoustid_result, source_artist)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (event_type, title, artist_name, album_name, quality, server_source, file_path, thumb_url,
                  download_source, source_track_id, source_track_title, source_filename,
                  acoustid_result, source_artist))
            conn.commit()
            return True
        except Exception as e:
            logger.debug(f"Error adding library history entry: {e}")
            return False

    def get_library_history(self, event_type=None, page=1, limit=50):
        """Query library history with optional type filter and pagination.

        Returns (entries_list, total_count).
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            where = "WHERE event_type = ?" if event_type else ""
            params = [event_type] if event_type else []

            cursor.execute(f"SELECT COUNT(*) as cnt FROM library_history {where}", params)
            total = cursor.fetchone()['cnt']

            offset = (page - 1) * limit
            cursor.execute(f"""
                SELECT * FROM library_history {where}
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
            """, params + [limit, offset])
            entries = [dict(row) for row in cursor.fetchall()]

            return entries, total
        except Exception as e:
            logger.error(f"Error querying library history: {e}")
            return [], 0

    def get_library_history_stats(self):
        """Return counts per event_type and per download_source."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT event_type, COUNT(*) as cnt FROM library_history GROUP BY event_type")
            stats = {'downloads': 0, 'imports': 0}
            for row in cursor.fetchall():
                if row['event_type'] == 'download':
                    stats['downloads'] = row['cnt']
                elif row['event_type'] == 'import':
                    stats['imports'] = row['cnt']

            # Per-source breakdown for downloads
            source_counts = {}
            try:
                cursor.execute("""
                    SELECT download_source, COUNT(*) as cnt FROM library_history
                    WHERE event_type = 'download' AND download_source IS NOT NULL AND download_source != ''
                    GROUP BY download_source ORDER BY cnt DESC
                """)
                for row in cursor.fetchall():
                    source_counts[row['download_source']] = row['cnt']
            except Exception:
                pass
            stats['source_counts'] = source_counts

            return stats
        except Exception as e:
            logger.debug(f"Error getting library history stats: {e}")
            return {'downloads': 0, 'imports': 0, 'source_counts': {}}

    # ── Sync History ──────────────────────────────────────────────

    def add_sync_history_entry(self, batch_id, playlist_id, playlist_name, source, sync_type,
                               tracks_json, artist_context=None, album_context=None,
                               thumb_url=None, total_tracks=0, is_album_download=False,
                               playlist_folder_mode=False):
        """Record a new sync operation to sync_history."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO sync_history (batch_id, playlist_id, playlist_name, source, sync_type,
                    tracks_json, artist_context, album_context, thumb_url, total_tracks,
                    is_album_download, playlist_folder_mode)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (batch_id, playlist_id, playlist_name, source, sync_type,
                  tracks_json, artist_context, album_context, thumb_url, total_tracks,
                  int(is_album_download), int(playlist_folder_mode)))
            conn.commit()
            # Cap at 100 entries
            cursor.execute("""
                DELETE FROM sync_history WHERE id NOT IN (
                    SELECT id FROM sync_history ORDER BY started_at DESC LIMIT 100
                )
            """)
            conn.commit()
            return True
        except Exception as e:
            logger.debug(f"Error adding sync history entry: {e}")
            return False

    def update_sync_history_completion(self, batch_id, tracks_found=0, tracks_downloaded=0, tracks_failed=0):
        """Update a sync_history entry with completion stats."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE sync_history SET tracks_found = ?, tracks_downloaded = ?,
                    tracks_failed = ?, completed_at = CURRENT_TIMESTAMP
                WHERE batch_id = ?
            """, (tracks_found, tracks_downloaded, tracks_failed, batch_id))
            conn.commit()
            return cursor.rowcount > 0
        except Exception as e:
            logger.debug(f"Error updating sync history completion: {e}")
            return False

    def update_sync_history_track_results(self, batch_id, track_results_json):
        """Store per-track match/download results on a sync_history entry."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE sync_history SET track_results = ? WHERE batch_id = ?
            """, (track_results_json, batch_id))
            conn.commit()
            return cursor.rowcount > 0
        except Exception as e:
            logger.debug(f"Error updating sync history track results: {e}")
            return False

    def refresh_sync_history_entry(self, entry_id, tracks_found=0, tracks_downloaded=0, tracks_failed=0):
        """Update an existing sync_history entry with new stats and reset timestamps to move it to the top."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE sync_history SET tracks_found = ?, tracks_downloaded = ?,
                    tracks_failed = ?, started_at = CURRENT_TIMESTAMP,
                    completed_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (tracks_found, tracks_downloaded, tracks_failed, entry_id))
            conn.commit()
            return cursor.rowcount > 0
        except Exception as e:
            logger.debug(f"Error refreshing sync history entry: {e}")
            return False

    def get_sync_history(self, source=None, page=1, limit=20):
        """Return (entries, total) for sync_history, newest first. Full tracks_json excluded from list."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            where = "WHERE source = ?" if source else ""
            params = [source] if source else []

            cursor.execute(f"SELECT COUNT(*) as cnt FROM sync_history {where}", params)
            total = cursor.fetchone()['cnt']

            offset = (page - 1) * limit
            cursor.execute(f"""
                SELECT id, batch_id, playlist_id, playlist_name, source, sync_type,
                       artist_context, album_context, thumb_url, total_tracks,
                       tracks_found, tracks_downloaded, tracks_failed,
                       is_album_download, playlist_folder_mode, started_at, completed_at
                FROM sync_history {where}
                ORDER BY started_at DESC
                LIMIT ? OFFSET ?
            """, params + [limit, offset])
            entries = [dict(row) for row in cursor.fetchall()]
            return entries, total
        except Exception as e:
            logger.error(f"Error querying sync history: {e}")
            return [], 0

    def get_latest_sync_history_by_playlist(self, playlist_id):
        """Return the most recent sync_history row for a given playlist_id."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM sync_history
                WHERE playlist_id = ?
                ORDER BY started_at DESC LIMIT 1
            """, (playlist_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
        except Exception as e:
            logger.debug(f"Error getting latest sync history by playlist: {e}")
            return None

    def get_sync_history_entry(self, entry_id):
        """Return a single sync_history row with full tracks_json (for re-trigger)."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM sync_history WHERE id = ?", (entry_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
        except Exception as e:
            logger.error(f"Error getting sync history entry: {e}")
            return None

    def delete_sync_history_entry(self, entry_id):
        """Delete a single sync_history entry."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM sync_history WHERE id = ?", (entry_id,))
            conn.commit()
            return cursor.rowcount > 0
        except Exception as e:
            logger.debug(f"Error deleting sync history entry: {e}")
            return False

    def get_sync_history_playlist_names(self):
        """Return distinct playlist names ever synced (for server playlist filtering)."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT playlist_name FROM sync_history WHERE playlist_name != ''")
            return [row[0] for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Error getting sync history playlist names: {e}")
            return []

    def get_sync_history_stats(self):
        """Return counts grouped by source."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT source, COUNT(*) as cnt FROM sync_history GROUP BY source")
            return {row['source']: row['cnt'] for row in cursor.fetchall()}
        except Exception as e:
            logger.debug(f"Error getting sync history stats: {e}")
            return {}

    def api_get_recently_added(self, entity_type: str = "albums", limit: int = 50) -> List[Dict[str, Any]]:
        """Get recently added entities, ordered by created_at DESC."""
        table = {"artists": "artists", "albums": "albums", "tracks": "tracks"}.get(entity_type)
        if not table:
            return []
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(f"SELECT * FROM {table} ORDER BY created_at DESC LIMIT ?", (limit,))
            return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"API: Error getting recently added {entity_type}: {e}")
            return []

    def api_list_albums(self, search: str = "", artist_id: int = None,
                        year: int = None, page: int = 1, limit: int = 50) -> Dict[str, Any]:
        """List/search albums with pagination, returning full rows."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            where_parts = []
            params: list = []

            if search:
                where_parts.append("LOWER(al.title) LIKE LOWER(?)")
                params.append(f"%{search}%")
            if artist_id is not None:
                where_parts.append("al.artist_id = ?")
                params.append(artist_id)
            if year is not None:
                where_parts.append("al.year = ?")
                params.append(year)

            where_clause = " AND ".join(where_parts) if where_parts else "1=1"

            # Count
            cursor.execute(f"SELECT COUNT(*) as cnt FROM albums al WHERE {where_clause}", params)
            total = cursor.fetchone()["cnt"]

            # Fetch page
            offset = (page - 1) * limit
            cursor.execute(
                f"""SELECT al.*, a.name as artist_name
                    FROM albums al
                    LEFT JOIN artists a ON al.artist_id = a.id
                    WHERE {where_clause}
                    ORDER BY al.title COLLATE NOCASE
                    LIMIT ? OFFSET ?""",
                params + [limit, offset],
            )
            albums = [dict(row) for row in cursor.fetchall()]

            return {"albums": albums, "total": total}
        except Exception as e:
            logger.error(f"API: Error listing albums: {e}")
            return {"albums": [], "total": 0}

    # ── Mirrored Playlists ───────────────────────────────────────────────

    def mirror_playlist(self, source: str, source_playlist_id: str, name: str,
                        tracks: List[Dict], profile_id: int = 1, **kwargs) -> Optional[int]:
        """Upsert a mirrored playlist and replace all its tracks."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                # Upsert the playlist row
                cursor.execute("""
                    INSERT INTO mirrored_playlists
                        (source, source_playlist_id, name, description, owner, image_url, track_count, profile_id, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(source, source_playlist_id, profile_id) DO UPDATE SET
                        name = excluded.name,
                        description = excluded.description,
                        owner = excluded.owner,
                        image_url = excluded.image_url,
                        track_count = excluded.track_count,
                        updated_at = CURRENT_TIMESTAMP
                """, (
                    source, source_playlist_id, name,
                    kwargs.get('description'), kwargs.get('owner'),
                    kwargs.get('image_url'), len(tracks), profile_id
                ))
                playlist_id = cursor.execute(
                    "SELECT id FROM mirrored_playlists WHERE source=? AND source_playlist_id=? AND profile_id=?",
                    (source, source_playlist_id, profile_id)
                ).fetchone()['id']

                # Preserve existing extra_data (discovery results) before replacing tracks
                old_extra_map = {}
                try:
                    cursor.execute("""
                        SELECT source_track_id, extra_data FROM mirrored_playlist_tracks
                        WHERE playlist_id = ? AND source_track_id IS NOT NULL AND extra_data IS NOT NULL
                    """, (playlist_id,))
                    old_extra_map = {row['source_track_id']: row['extra_data'] for row in cursor.fetchall()}
                except Exception:
                    pass

                # Replace all tracks
                cursor.execute("DELETE FROM mirrored_playlist_tracks WHERE playlist_id=?", (playlist_id,))
                for i, t in enumerate(tracks):
                    extra = t.get('extra_data')
                    if extra and not isinstance(extra, str):
                        extra = json.dumps(extra)
                    # Restore preserved discovery data if the incoming track doesn't have its own
                    if not extra:
                        sid = t.get('source_track_id')
                        if sid and sid in old_extra_map:
                            extra = old_extra_map[sid]
                    cursor.execute("""
                        INSERT INTO mirrored_playlist_tracks
                            (playlist_id, position, track_name, artist_name, album_name, duration_ms, image_url, source_track_id, extra_data)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        playlist_id, i + 1,
                        t.get('track_name', ''), t.get('artist_name', ''),
                        t.get('album_name', ''), t.get('duration_ms', 0),
                        t.get('image_url'), t.get('source_track_id'), extra
                    ))
                conn.commit()
                logger.info(f"Mirrored playlist '{name}' ({source}) with {len(tracks)} tracks")
                return playlist_id
        except Exception as e:
            logger.error(f"Error mirroring playlist: {e}")
            return None

    def get_mirrored_playlists(self, profile_id: int = 1) -> List[Dict]:
        """Return all mirrored playlists for a profile, newest first."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM mirrored_playlists
                    WHERE profile_id = ?
                    ORDER BY updated_at DESC
                """, (profile_id,))
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Error getting mirrored playlists: {e}")
            return []

    def mark_mirrored_playlist_explored(self, playlist_id: int) -> bool:
        """Set explored_at to now for a mirrored playlist."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE mirrored_playlists SET explored_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (playlist_id,)
                )
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error marking playlist {playlist_id} as explored: {e}")
            return False

    def get_mirrored_playlist(self, playlist_id: int) -> Optional[Dict]:
        """Return a single mirrored playlist by id."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM mirrored_playlists WHERE id = ?", (playlist_id,))
                row = cursor.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"Error getting mirrored playlist: {e}")
            return None

    def get_mirrored_playlist_tracks(self, playlist_id: int) -> List[Dict]:
        """Return all tracks for a mirrored playlist ordered by position."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM mirrored_playlist_tracks
                    WHERE playlist_id = ?
                    ORDER BY position
                """, (playlist_id,))
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Error getting mirrored playlist tracks: {e}")
            return []

    def update_mirrored_track_extra_data(self, track_id: int, extra_data_dict: dict) -> bool:
        """Merge new data into a mirrored track's extra_data JSON field."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT extra_data FROM mirrored_playlist_tracks WHERE id = ?",
                    (track_id,)
                )
                row = cursor.fetchone()
                if not row:
                    return False
                existing = {}
                if row['extra_data']:
                    try:
                        existing = json.loads(row['extra_data'])
                    except (json.JSONDecodeError, TypeError):
                        pass
                existing.update(extra_data_dict)
                cursor.execute(
                    "UPDATE mirrored_playlist_tracks SET extra_data = ? WHERE id = ?",
                    (json.dumps(existing), track_id)
                )
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error updating mirrored track extra_data: {e}")
            return False

    def get_mirrored_tracks_extra_data_map(self, playlist_id: int) -> dict:
        """Return {source_track_id: extra_data_json_string} for a playlist.
        Used to preserve discovery data across refreshes."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT source_track_id, extra_data FROM mirrored_playlist_tracks
                    WHERE playlist_id = ? AND source_track_id IS NOT NULL AND extra_data IS NOT NULL
                """, (playlist_id,))
                return {row['source_track_id']: row['extra_data'] for row in cursor.fetchall()}
        except Exception as e:
            logger.error(f"Error getting extra_data map: {e}")
            return {}

    def clear_mirrored_playlist_discovery(self, playlist_id: int) -> int:
        """Clear extra_data for all tracks in a mirrored playlist (resets discovery)."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE mirrored_playlist_tracks SET extra_data = NULL WHERE playlist_id = ?",
                    (playlist_id,)
                )
                conn.commit()
                return cursor.rowcount
        except Exception as e:
            logger.error(f"Error clearing mirrored playlist discovery: {e}")
            return 0

    def get_mirrored_playlist_discovery_counts(self, playlist_id: int) -> tuple:
        """Return (discovered_count, total_count) for a mirrored playlist."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT COUNT(*) as total FROM mirrored_playlist_tracks WHERE playlist_id = ?",
                    (playlist_id,)
                )
                total = cursor.fetchone()['total']
                cursor.execute(
                    "SELECT COUNT(*) as discovered FROM mirrored_playlist_tracks WHERE playlist_id = ? AND extra_data LIKE '%\"discovered\": true%'",
                    (playlist_id,)
                )
                discovered = cursor.fetchone()['discovered']
                return (discovered, total)
        except Exception as e:
            logger.error(f"Error getting mirrored playlist discovery counts: {e}")
            return (0, 0)

    def get_mirrored_playlist_status_counts(self, playlist_id: int) -> dict:
        """Return discovery, wishlisted, and downloaded counts for a mirrored playlist.
        Discovery counts are critical (same as old method). Library/wishlist counts are
        best-effort extras that won't break discovery detection if they fail."""
        result = {'total': 0, 'discovered': 0, 'wishlisted': 0, 'in_library': 0}
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # Core counts — same reliable queries as get_mirrored_playlist_discovery_counts
                cursor.execute(
                    "SELECT COUNT(*) as total FROM mirrored_playlist_tracks WHERE playlist_id = ?",
                    (playlist_id,)
                )
                result['total'] = cursor.fetchone()['total']
                cursor.execute(
                    "SELECT COUNT(*) as discovered FROM mirrored_playlist_tracks WHERE playlist_id = ? AND extra_data LIKE '%\"discovered\": true%'",
                    (playlist_id,)
                )
                result['discovered'] = cursor.fetchone()['discovered']

                # Best-effort extras — won't break if tracks table has issues
                try:
                    cursor.execute("""
                        SELECT
                            SUM(CASE WHEN mpt.source_track_id IS NOT NULL AND mpt.source_track_id != ''
                                 AND EXISTS (SELECT 1 FROM wishlist_tracks wt
                                             WHERE wt.spotify_track_id = mpt.source_track_id)
                                 THEN 1 ELSE 0 END) as wishlisted,
                            SUM(CASE WHEN EXISTS (SELECT 1 FROM tracks t
                                                  WHERE t.title = mpt.track_name COLLATE NOCASE
                                                    AND t.artist = mpt.artist_name COLLATE NOCASE)
                                 THEN 1 ELSE 0 END) as in_library
                        FROM mirrored_playlist_tracks mpt
                        WHERE mpt.playlist_id = ?
                    """, (playlist_id,))
                    row = cursor.fetchone()
                    result['wishlisted'] = row['wishlisted'] or 0
                    result['in_library'] = row['in_library'] or 0
                except Exception as extra_err:
                    logger.debug(f"Optional status counts failed for playlist {playlist_id}: {extra_err}")

        except Exception as e:
            logger.error(f"Error getting mirrored playlist status counts: {e}")
        return result

    def delete_mirrored_playlist(self, playlist_id: int) -> bool:
        """Delete a mirrored playlist and its tracks (CASCADE)."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM mirrored_playlists WHERE id = ?", (playlist_id,))
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error deleting mirrored playlist: {e}")
            return False

    # ===========================
    # AUTOMATIONS CRUD
    # ===========================

    def create_automation(self, name: str, trigger_type: str, trigger_config: str,
                          action_type: str, action_config: str, profile_id: int = 1,
                          notify_type: str = None, notify_config: str = '{}',
                          then_actions: str = '[]', group_name: str = None):
        """Create a new automation. Returns the new automation ID or None."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO automations (name, trigger_type, trigger_config, action_type, action_config, profile_id, notify_type, notify_config, then_actions, group_name)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (name, trigger_type, trigger_config, action_type, action_config, profile_id, notify_type, notify_config, then_actions, group_name))
                conn.commit()
                return cursor.lastrowid
        except Exception as e:
            logger.error(f"Error creating automation: {e}")
            return None

    def get_automations(self, profile_id: int = 1):
        """Get all automations for a profile (includes system automations regardless of profile)."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM automations WHERE profile_id = ? OR is_system = 1 ORDER BY is_system DESC, created_at DESC
                """, (profile_id,))
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Error getting automations: {e}")
            return []

    def get_system_automation_by_action(self, action_type: str):
        """Get a system automation by its action_type. Returns dict or None."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM automations WHERE is_system = 1 AND action_type = ?", (action_type,))
                row = cursor.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"Error getting system automation for {action_type}: {e}")
            return None

    def get_automation(self, automation_id: int):
        """Get a single automation by ID."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM automations WHERE id = ?", (automation_id,))
                row = cursor.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"Error getting automation {automation_id}: {e}")
            return None

    def update_automation(self, automation_id: int, **kwargs) -> bool:
        """Update automation fields."""
        allowed = {'name', 'enabled', 'trigger_type', 'trigger_config', 'action_type', 'action_config', 'next_run', 'notify_type', 'notify_config', 'last_result', 'is_system', 'then_actions', 'group_name'}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                set_clause = ', '.join(f"{k} = ?" for k in updates)
                values = list(updates.values()) + [automation_id]
                cursor.execute(
                    f"UPDATE automations SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    values
                )
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error updating automation {automation_id}: {e}")
            return False

    def delete_automation(self, automation_id: int) -> bool:
        """Delete an automation. System automations cannot be deleted."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT is_system FROM automations WHERE id = ?", (automation_id,))
                row = cursor.fetchone()
                if row and row['is_system']:
                    logger.warning(f"Attempted to delete system automation {automation_id}")
                    return False
                cursor.execute("DELETE FROM automations WHERE id = ?", (automation_id,))
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error deleting automation {automation_id}: {e}")
            return False

    def toggle_automation(self, automation_id: int) -> bool:
        """Toggle the enabled state of an automation. Returns True on success."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE automations SET enabled = CASE WHEN enabled = 1 THEN 0 ELSE 1 END, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (automation_id,)
                )
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error toggling automation {automation_id}: {e}")
            return False

    def update_automation_run(self, automation_id: int, next_run=None, error=None, last_result=None) -> bool:
        """Record a run: set last_run=now, increment run_count, optionally set next_run, last_error, last_result."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE automations
                    SET last_run = CURRENT_TIMESTAMP,
                        run_count = run_count + 1,
                        next_run = ?,
                        last_error = ?,
                        last_result = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (next_run, error, last_result, automation_id))
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error updating automation run {automation_id}: {e}")
            return False

    def insert_automation_run_history(self, automation_id, started_at, finished_at,
                                       duration_seconds, status, summary=None,
                                       result_json=None, log_lines=None):
        """Insert a run history entry and enforce 100-row retention cap per automation."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO automation_run_history
                    (automation_id, started_at, finished_at, duration_seconds, status, summary, result_json, log_lines)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (automation_id, started_at, finished_at, duration_seconds,
                      status, summary, result_json, log_lines))
                # Retention: keep only the newest 100 rows per automation
                cursor.execute("""
                    DELETE FROM automation_run_history
                    WHERE automation_id = ? AND id NOT IN (
                        SELECT id FROM automation_run_history
                        WHERE automation_id = ?
                        ORDER BY id DESC LIMIT 100
                    )
                """, (automation_id, automation_id))
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Error inserting automation run history for {automation_id}: {e}")
            return False

    def get_automation_run_history(self, automation_id, limit=50, offset=0):
        """Get run history for an automation, newest first."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT COUNT(*) FROM automation_run_history WHERE automation_id = ?",
                    (automation_id,))
                total = cursor.fetchone()[0]
                cursor.execute("""
                    SELECT id, automation_id, started_at, finished_at, duration_seconds,
                           status, summary, result_json, log_lines
                    FROM automation_run_history
                    WHERE automation_id = ?
                    ORDER BY id DESC
                    LIMIT ? OFFSET ?
                """, (automation_id, limit, offset))
                cols = [d[0] for d in cursor.description]
                rows = [dict(zip(cols, row)) for row in cursor.fetchall()]
                return {'history': rows, 'total': total}
        except Exception as e:
            logger.error(f"Error getting automation run history for {automation_id}: {e}")
            return {'history': [], 'total': 0}

    def clear_automation_run_history(self, automation_id=None):
        """Clear run history for a specific automation or all automations."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                if automation_id:
                    cursor.execute("DELETE FROM automation_run_history WHERE automation_id = ?",
                                   (automation_id,))
                else:
                    cursor.execute("DELETE FROM automation_run_history")
                conn.commit()
                return cursor.rowcount
        except Exception as e:
            logger.error(f"Error clearing automation run history: {e}")
            return 0

    def get_radio_tracks(self, track_id, limit=20, exclude_ids=None) -> Dict[str, Any]:
        """Find similar tracks for radio mode auto-play queue.

        Strategy (each tier capped to ensure diversity):
          1. Same artist, different albums (max 30% of limit)
          2. Same genre — from album genres + artist genres (other artists)
          3. Same mood / style — from album + artist metadata
          4. Random library tracks (fallback)

        Args:
            track_id: The seed track ID.
            limit: Maximum number of tracks to return.
            exclude_ids: Optional list of track IDs to exclude.

        Returns:
            dict with ``success``, ``tracks`` list.
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # Resolve the seed track and its album / artist
                cursor.execute("""
                    SELECT t.id, t.artist_id, t.album_id,
                           al.genres  AS album_genres,
                           al.mood    AS album_mood,
                           al.style   AS album_style,
                           ar.name    AS artist_name,
                           ar.genres  AS artist_genres,
                           ar.mood    AS artist_mood,
                           ar.style   AS artist_style
                    FROM tracks t
                    JOIN albums al ON al.id = t.album_id
                    JOIN artists ar ON ar.id = t.artist_id
                    WHERE t.id = ?
                """, (track_id,))
                seed = cursor.fetchone()
                if not seed:
                    return {'success': False, 'error': f'Track {track_id} not found'}

                seed = dict(seed)
                artist_name = seed['artist_name']

                # Build the set of IDs to exclude (seed + caller-supplied)
                excluded = {str(track_id)}
                if exclude_ids:
                    excluded.update(str(eid) for eid in exclude_ids)

                collected: list[dict] = []
                seen_ids: set[str] = set(excluded)

                def _exclude_placeholders():
                    return ','.join('?' * len(seen_ids))

                def _exclude_values():
                    return list(seen_ids)

                _track_select = """
                    SELECT t.id, t.title, t.track_number, t.duration,
                           t.file_path, t.bitrate,
                           t.album_id, t.artist_id,
                           al.title   AS album,
                           COALESCE(al.thumb_url, ar.thumb_url) AS image_url,
                           ar.name    AS artist
                    FROM tracks t
                    JOIN albums al ON al.id = t.album_id
                    JOIN artists ar ON ar.id = t.artist_id
                """
                # Only return tracks that have actual files on disk
                _file_filter = "t.file_path IS NOT NULL AND t.file_path != ''"

                def _collect(rows, cap=None):
                    """Append rows to collected. Stop at cap or limit."""
                    target = min(limit, (len(collected) + cap)) if cap else limit
                    for row in rows:
                        r = dict(row)
                        rid = str(r['id'])
                        if rid not in seen_ids:
                            seen_ids.add(rid)
                            collected.append(r)
                            if len(collected) >= target:
                                return True
                    return len(collected) >= limit

                def _parse_tags(raw_val):
                    """Parse a JSON array or comma-separated string into a list."""
                    if not raw_val:
                        return []
                    try:
                        parsed = json.loads(raw_val)
                        return parsed if isinstance(parsed, list) else [str(parsed)]
                    except (json.JSONDecodeError, ValueError):
                        return [t.strip() for t in raw_val.split(',') if t.strip()]

                # --- 1. Same artist, different albums (capped at 30% of limit) ---
                same_artist_cap = max(5, limit * 3 // 10)
                cursor.execute(f"""
                    {_track_select}
                    WHERE {_file_filter} AND ar.name = ? AND t.album_id != ? AND t.id NOT IN ({_exclude_placeholders()})
                    ORDER BY RANDOM()
                    LIMIT ?
                """, [artist_name, seed['album_id']] + _exclude_values() + [same_artist_cap])
                _collect(cursor.fetchall(), cap=same_artist_cap)

                if len(collected) >= limit:
                    return {'success': True, 'tracks': collected}

                # --- 2. Same genre (album genres + artist genres, other artists) ---
                genre_list = _parse_tags(seed.get('album_genres'))
                artist_genre_list = _parse_tags(seed.get('artist_genres'))
                all_genres = list(dict.fromkeys(genre_list + artist_genre_list))  # dedupe, preserve order

                if all_genres:
                    genre_conditions = ' OR '.join(
                        ['al.genres LIKE ?' for _ in all_genres] +
                        ['ar.genres LIKE ?' for _ in all_genres]
                    )
                    genre_params = [f'%{g}%' for g in all_genres] * 2
                    cursor.execute(f"""
                        {_track_select}
                        WHERE {_file_filter} AND ({genre_conditions})
                          AND ar.name != ?
                          AND t.id NOT IN ({_exclude_placeholders()})
                        ORDER BY RANDOM()
                        LIMIT ?
                    """, genre_params + [artist_name] + _exclude_values() + [limit - len(collected)])
                    if _collect(cursor.fetchall()):
                        return {'success': True, 'tracks': collected}

                # --- 3. Same mood / style (album + artist level) ---
                for field_name in ('mood', 'style'):
                    album_tags = _parse_tags(seed.get(f'album_{field_name}'))
                    artist_tags = _parse_tags(seed.get(f'artist_{field_name}'))
                    all_tags = list(dict.fromkeys(album_tags + artist_tags))

                    if all_tags:
                        tag_conditions = ' OR '.join(
                            [f'al.{field_name} LIKE ?' for _ in all_tags] +
                            [f'ar.{field_name} LIKE ?' for _ in all_tags]
                        )
                        tag_params = [f'%{t}%' for t in all_tags] * 2
                        cursor.execute(f"""
                            {_track_select}
                            WHERE {_file_filter} AND ({tag_conditions})
                              AND ar.name != ?
                              AND t.id NOT IN ({_exclude_placeholders()})
                            ORDER BY RANDOM()
                            LIMIT ?
                        """, tag_params + [artist_name] + _exclude_values() + [limit - len(collected)])
                        if _collect(cursor.fetchall()):
                            return {'success': True, 'tracks': collected}

                # --- 4. Random library tracks ---
                if len(collected) < limit:
                    cursor.execute(f"""
                        {_track_select}
                        WHERE {_file_filter} AND t.id NOT IN ({_exclude_placeholders()})
                        ORDER BY RANDOM()
                        LIMIT ?
                    """, _exclude_values() + [limit - len(collected)])
                    _collect(cursor.fetchall())

                return {'success': True, 'tracks': collected}

        except Exception as e:
            logger.error(f"Error getting radio tracks for track {track_id}: {e}")
            return {'success': False, 'error': str(e)}

    # ── Library Issues CRUD ──

    def create_issue(self, profile_id: int, entity_type: str, entity_id: str,
                     category: str, title: str, description: str = '',
                     snapshot_data: Dict = None, priority: str = 'normal') -> Dict[str, Any]:
        """Create a new library issue report."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO library_issues
                    (profile_id, entity_type, entity_id, category, title, description,
                     snapshot_data, priority)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (profile_id, entity_type, entity_id, category, title, description,
                      json.dumps(snapshot_data or {}), priority))
                conn.commit()
                return {'success': True, 'id': cursor.lastrowid}
        except Exception as e:
            logger.error(f"Error creating issue: {e}")
            return {'success': False, 'error': str(e)}

    def get_issues(self, profile_id: int = None, status: str = None,
                   category: str = None, entity_type: str = None,
                   limit: int = 100, offset: int = 0,
                   is_admin: bool = False) -> Dict[str, Any]:
        """Get issues with optional filters. Non-admin only sees own issues."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                conditions = []
                params = []

                if not is_admin and profile_id:
                    conditions.append("i.profile_id = ?")
                    params.append(profile_id)
                if status:
                    conditions.append("i.status = ?")
                    params.append(status)
                if category:
                    conditions.append("i.category = ?")
                    params.append(category)
                if entity_type:
                    conditions.append("i.entity_type = ?")
                    params.append(entity_type)

                where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

                # Count total
                cursor.execute(f"SELECT COUNT(*) FROM library_issues i {where}", params)
                total = cursor.fetchone()[0]

                # Fetch issues with reporter profile info
                cursor.execute(f"""
                    SELECT i.*, p.name as reporter_name, p.avatar_color as reporter_color,
                           p.avatar_url as reporter_avatar
                    FROM library_issues i
                    LEFT JOIN profiles p ON i.profile_id = p.id
                    {where}
                    ORDER BY
                        CASE i.status WHEN 'open' THEN 0 WHEN 'in_progress' THEN 1 ELSE 2 END,
                        CASE i.priority WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END,
                        i.created_at DESC
                    LIMIT ? OFFSET ?
                """, params + [limit, offset])

                issues = []
                for row in cursor.fetchall():
                    issue = dict(row)
                    try:
                        issue['snapshot_data'] = json.loads(issue.get('snapshot_data', '{}'))
                    except (json.JSONDecodeError, TypeError):
                        issue['snapshot_data'] = {}
                    issues.append(issue)

                return {'success': True, 'issues': issues, 'total': total}
        except Exception as e:
            logger.error(f"Error getting issues: {e}")
            return {'success': False, 'error': str(e), 'issues': [], 'total': 0}

    def get_issue(self, issue_id: int) -> Optional[Dict[str, Any]]:
        """Get a single issue by ID with reporter info."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT i.*, p.name as reporter_name, p.avatar_color as reporter_color,
                           p.avatar_url as reporter_avatar
                    FROM library_issues i
                    LEFT JOIN profiles p ON i.profile_id = p.id
                    WHERE i.id = ?
                """, (issue_id,))
                row = cursor.fetchone()
                if not row:
                    return None
                issue = dict(row)
                try:
                    issue['snapshot_data'] = json.loads(issue.get('snapshot_data', '{}'))
                except (json.JSONDecodeError, TypeError):
                    issue['snapshot_data'] = {}
                return issue
        except Exception as e:
            logger.error(f"Error getting issue {issue_id}: {e}")
            return None

    def update_issue(self, issue_id: int, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Update an issue (admin response, status change, etc.)."""
        allowed_fields = {'status', 'priority', 'admin_response', 'resolved_by', 'resolved_at',
                          'title', 'description', 'category'}
        valid = {k: v for k, v in updates.items() if k in allowed_fields}
        if not valid:
            return {'success': False, 'error': 'No valid fields to update'}
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                set_clause = ', '.join(f'{k} = ?' for k in valid)
                values = list(valid.values()) + [issue_id]
                cursor.execute(
                    f"UPDATE library_issues SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    values
                )
                conn.commit()
                if cursor.rowcount == 0:
                    return {'success': False, 'error': 'Issue not found'}
                return {'success': True}
        except Exception as e:
            logger.error(f"Error updating issue {issue_id}: {e}")
            return {'success': False, 'error': str(e)}

    def delete_issue(self, issue_id: int) -> Dict[str, Any]:
        """Delete an issue (admin only)."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM library_issues WHERE id = ?", (issue_id,))
                conn.commit()
                if cursor.rowcount == 0:
                    return {'success': False, 'error': 'Issue not found'}
                return {'success': True}
        except Exception as e:
            logger.error(f"Error deleting issue {issue_id}: {e}")
            return {'success': False, 'error': str(e)}

    def get_issue_counts(self, is_admin: bool = False, profile_id: int = None) -> Dict[str, int]:
        """Get issue counts by status for badge display."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                profile_filter = ""
                params = []
                if not is_admin and profile_id:
                    profile_filter = "WHERE profile_id = ?"
                    params = [profile_id]
                cursor.execute(f"""
                    SELECT status, COUNT(*) as count
                    FROM library_issues
                    {profile_filter}
                    GROUP BY status
                """, params)
                counts = {'open': 0, 'in_progress': 0, 'resolved': 0, 'dismissed': 0, 'total': 0}
                for row in cursor.fetchall():
                    counts[row['status']] = row['count']
                    counts['total'] += row['count']
                return counts
        except Exception as e:
            logger.error(f"Error getting issue counts: {e}")
            return {'open': 0, 'in_progress': 0, 'resolved': 0, 'dismissed': 0, 'total': 0}

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
