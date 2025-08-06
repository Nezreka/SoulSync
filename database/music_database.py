#!/usr/bin/env python3

import sqlite3
import json
import os
import threading
from datetime import datetime
from typing import List, Optional, Dict, Any, Tuple
from dataclasses import dataclass
from pathlib import Path
from utils.logging_config import get_logger

logger = get_logger("music_database")

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

class MusicDatabase:
    """SQLite database manager for SoulSync music library data"""
    
    def __init__(self, database_path: str = "database/music_library.db"):
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
            
            # Create indexes for performance
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_albums_artist_id ON albums (artist_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tracks_album_id ON tracks (album_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tracks_artist_id ON tracks (artist_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_artists_name ON artists (name)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_albums_title ON albums (title)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tracks_title ON tracks (title)")
            
            conn.commit()
            logger.info("Database initialized successfully")
            
        except Exception as e:
            logger.error(f"Error initializing database: {e}")
            raise
    
    def close(self):
        """Close database connection (no-op since we create connections per operation)"""
        # Each operation creates and closes its own connection, so nothing to do here
        pass
    
    def get_statistics(self) -> Dict[str, int]:
        """Get database statistics"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
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
            logger.error(f"Error getting database statistics: {e}")
            return {'artists': 0, 'albums': 0, 'tracks': 0}
    
    def clear_all_data(self):
        """Clear all data from database (for full refresh)"""
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
    
    # Artist operations
    def insert_or_update_artist(self, plex_artist) -> bool:
        """Insert or update artist from Plex artist object"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                artist_id = int(plex_artist.ratingKey)
                name = plex_artist.title
                thumb_url = getattr(plex_artist, 'thumb', None)
                summary = getattr(plex_artist, 'summary', None)
                
                # Get genres
                genres = []
                if hasattr(plex_artist, 'genres') and plex_artist.genres:
                    genres = [genre.tag if hasattr(genre, 'tag') else str(genre) 
                             for genre in plex_artist.genres]
                
                genres_json = json.dumps(genres) if genres else None
                
                # Check if artist exists
                cursor.execute("SELECT id FROM artists WHERE id = ?", (artist_id,))
                exists = cursor.fetchone()
                
                if exists:
                    # Update existing artist
                    cursor.execute("""
                        UPDATE artists 
                        SET name = ?, thumb_url = ?, genres = ?, summary = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                    """, (name, thumb_url, genres_json, summary, artist_id))
                else:
                    # Insert new artist
                    cursor.execute("""
                        INSERT INTO artists (id, name, thumb_url, genres, summary)
                        VALUES (?, ?, ?, ?, ?)
                    """, (artist_id, name, thumb_url, genres_json, summary))
                
                conn.commit()
                return True
                
        except Exception as e:
            logger.error(f"Error inserting/updating artist {getattr(plex_artist, 'title', 'Unknown')}: {e}")
            return False
    
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
        """Insert or update album from Plex album object"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            album_id = int(plex_album.ratingKey)
            title = plex_album.title
            year = getattr(plex_album, 'year', None)
            thumb_url = getattr(plex_album, 'thumb', None)
            
            # Get track count and duration
            track_count = getattr(plex_album, 'leafCount', None)
            duration = getattr(plex_album, 'duration', None)
            
            # Get genres
            genres = []
            if hasattr(plex_album, 'genres') and plex_album.genres:
                genres = [genre.tag if hasattr(genre, 'tag') else str(genre) 
                         for genre in plex_album.genres]
            
            genres_json = json.dumps(genres) if genres else None
            
            # Check if album exists
            cursor.execute("SELECT id FROM albums WHERE id = ?", (album_id,))
            exists = cursor.fetchone()
            
            if exists:
                # Update existing album
                cursor.execute("""
                    UPDATE albums 
                    SET artist_id = ?, title = ?, year = ?, thumb_url = ?, genres = ?, 
                        track_count = ?, duration = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (artist_id, title, year, thumb_url, genres_json, track_count, duration, album_id))
            else:
                # Insert new album
                cursor.execute("""
                    INSERT INTO albums (id, artist_id, title, year, thumb_url, genres, track_count, duration)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (album_id, artist_id, title, year, thumb_url, genres_json, track_count, duration))
            
            conn.commit()
            return True
            
        except Exception as e:
            logger.error(f"Error inserting/updating album {getattr(plex_album, 'title', 'Unknown')}: {e}")
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
        """Insert or update track from Plex track object"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            track_id = int(plex_track.ratingKey)
            title = plex_track.title
            track_number = getattr(plex_track, 'trackNumber', None)
            duration = getattr(plex_track, 'duration', None)
            
            # Get file path and media info
            file_path = None
            bitrate = None
            if hasattr(plex_track, 'media') and plex_track.media:
                media = plex_track.media[0] if plex_track.media else None
                if media:
                    if hasattr(media, 'parts') and media.parts:
                        part = media.parts[0]
                        file_path = getattr(part, 'file', None)
                    bitrate = getattr(media, 'bitrate', None)
            
            # Check if track exists
            cursor.execute("SELECT id FROM tracks WHERE id = ?", (track_id,))
            exists = cursor.fetchone()
            
            if exists:
                # Update existing track
                cursor.execute("""
                    UPDATE tracks 
                    SET album_id = ?, artist_id = ?, title = ?, track_number = ?, 
                        duration = ?, file_path = ?, bitrate = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (album_id, artist_id, title, track_number, duration, file_path, bitrate, track_id))
            else:
                # Insert new track
                cursor.execute("""
                    INSERT INTO tracks (id, album_id, artist_id, title, track_number, duration, file_path, bitrate)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (track_id, album_id, artist_id, title, track_number, duration, file_path, bitrate))
            
            conn.commit()
            return True
            
        except Exception as e:
            logger.error(f"Error inserting/updating track {getattr(plex_track, 'title', 'Unknown')}: {e}")
            return False
    
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
    
    def get_database_info(self) -> Dict[str, Any]:
        """Get comprehensive database information"""
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
            
            return {
                **stats,
                'database_size_mb': round(db_size_mb, 2),
                'database_path': str(self.database_path),
                'last_update': last_update
            }
            
        except Exception as e:
            logger.error(f"Error getting database info: {e}")
            return {
                'artists': 0,
                'albums': 0,
                'tracks': 0,
                'database_size_mb': 0.0,
                'database_path': str(self.database_path),
                'last_update': None
            }

# Thread-safe singleton pattern for database access
_database_instances: Dict[int, MusicDatabase] = {}  # Thread ID -> Database instance
_database_lock = threading.Lock()

def get_database(database_path: str = "database/music_library.db") -> MusicDatabase:
    """Get thread-local database instance"""
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