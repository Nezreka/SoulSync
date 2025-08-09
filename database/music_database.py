#!/usr/bin/env python3

import sqlite3
import json
import logging
import os
import re
import threading
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
            
            # Create indexes for performance
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_albums_artist_id ON albums (artist_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tracks_album_id ON tracks (album_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tracks_artist_id ON tracks (artist_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_wishlist_spotify_id ON wishlist_tracks (spotify_track_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_wishlist_date_added ON wishlist_tracks (date_added)")
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
    
    def track_exists(self, track_id: int) -> bool:
        """Check if a track exists in the database by Plex ID"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cursor.execute("SELECT 1 FROM tracks WHERE id = ? LIMIT 1", (track_id,))
            result = cursor.fetchone()
            
            return result is not None
            
        except Exception as e:
            logger.error(f"Error checking if track {track_id} exists: {e}")
            return False
    
    def get_track_by_id(self, track_id: int) -> Optional[DatabaseTrackWithMetadata]:
        """Get a track with artist and album names by Plex ID"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT t.id, t.album_id, t.artist_id, t.title, t.track_number, 
                       t.duration, t.created_at, t.updated_at,
                       a.name as artist_name, al.title as album_title
                FROM tracks t
                JOIN artists a ON t.artist_id = a.id
                JOIN albums al ON t.album_id = al.id
                WHERE t.id = ?
            """, (track_id,))
            
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
    
    def search_tracks(self, title: str = "", artist: str = "", limit: int = 50) -> List[DatabaseTrack]:
        """Search tracks by title and/or artist name with Unicode-aware fuzzy matching"""
        try:
            if not title and not artist:
                return []
            
            conn = self._get_connection()
            cursor = conn.cursor()
            
            # STRATEGY 1: Try basic SQL LIKE search first (fastest)
            basic_results = self._search_tracks_basic(cursor, title, artist, limit)
            
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
                normalized_results = self._search_tracks_unicode_fallback(cursor, title, artist, limit)
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
    
    def _search_tracks_basic(self, cursor, title: str, artist: str, limit: int) -> List[DatabaseTrack]:
        """Basic SQL LIKE search - fastest method"""
        where_conditions = []
        params = []
        
        if title:
            where_conditions.append("tracks.title LIKE ?")
            params.append(f"%{title}%")
        
        if artist:
            where_conditions.append("artists.name LIKE ?")
            params.append(f"%{artist}%")
        
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
    
    def _search_tracks_unicode_fallback(self, cursor, title: str, artist: str, limit: int) -> List[DatabaseTrack]:
        """Unicode-aware fallback search - tries normalized versions"""
        from unidecode import unidecode
        
        # Normalize search terms
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
    
    def search_albums(self, title: str = "", artist: str = "", limit: int = 50) -> List[DatabaseAlbum]:
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
    
    def check_track_exists(self, title: str, artist: str, confidence_threshold: float = 0.8) -> Tuple[Optional[DatabaseTrack], float]:
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
                potential_matches = self.search_tracks(title=title_variation, artist=artist, limit=20)
                
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
    
    def check_album_exists_with_completeness(self, title: str, artist: str, expected_track_count: Optional[int] = None, confidence_threshold: float = 0.8) -> Tuple[Optional[DatabaseAlbum], float, int, int, bool]:
        """
        Check if an album exists in the database with completeness information.
        Enhanced to handle edition matching (standard <-> deluxe variants).
        Returns (album, confidence, owned_tracks, expected_tracks, is_complete)
        """
        try:
            # Try enhanced edition-aware matching first with expected track count for Smart Edition Matching
            album, confidence = self.check_album_exists_with_editions(title, artist, confidence_threshold, expected_track_count)
            
            if not album:
                return None, 0.0, 0, 0, False
            
            # Now check completeness
            owned_tracks, expected_tracks, is_complete = self.check_album_completeness(album.id, expected_track_count)
            
            return album, confidence, owned_tracks, expected_tracks, is_complete
            
        except Exception as e:
            logger.error(f"Error checking album existence with completeness for '{title}' by '{artist}': {e}")
            return None, 0.0, 0, 0, False
    
    def check_album_exists_with_editions(self, title: str, artist: str, confidence_threshold: float = 0.8, expected_track_count: Optional[int] = None) -> Tuple[Optional[DatabaseAlbum], float]:
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
                albums = self.search_albums(title=variation, artist=artist, limit=10)
                
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
            else:
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
                
                # Convert data to JSON strings
                spotify_json = json.dumps(spotify_track_data)
                source_json = json.dumps(source_info or {})
                
                cursor.execute("""
                    INSERT OR REPLACE INTO wishlist_tracks 
                    (spotify_track_id, spotify_data, failure_reason, source_type, source_info, date_added)
                    VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (track_id, spotify_json, failure_reason, source_type, source_json))
                
                conn.commit()
                
                track_name = spotify_track_data.get('name', 'Unknown Track')
                artist_name = spotify_track_data.get('artists', [{}])[0].get('name', 'Unknown Artist')
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
                    ORDER BY RANDOM()
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