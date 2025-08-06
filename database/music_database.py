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
    
    def search_tracks(self, title: str = "", artist: str = "", limit: int = 50) -> List[DatabaseTrack]:
        """Search tracks by title and/or artist name with fuzzy matching"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            # Build dynamic query based on provided parameters
            where_conditions = []
            params = []
            
            if title:
                where_conditions.append("tracks.title LIKE ?")
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
                SELECT tracks.*, artists.name as artist_name, albums.title as album_title
                FROM tracks
                JOIN artists ON tracks.artist_id = artists.id
                JOIN albums ON tracks.album_id = albums.id
                WHERE {where_clause}
                ORDER BY tracks.title, artists.name
                LIMIT ?
            """, params)
            
            rows = cursor.fetchall()
            
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
            
        except Exception as e:
            logger.error(f"Error searching tracks with title='{title}', artist='{artist}': {e}")
            return []
    
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
        Check if a track exists in the database with fuzzy matching and confidence scoring.
        Returns (track, confidence) tuple where confidence is 0.0-1.0
        """
        try:
            # Search for potential matches
            potential_matches = self.search_tracks(title=title, artist=artist, limit=20)
            
            if not potential_matches:
                return None, 0.0
            
            # Simple confidence scoring based on string similarity
            def calculate_confidence(db_track: DatabaseTrack) -> float:
                title_similarity = self._string_similarity(title.lower().strip(), db_track.title.lower().strip())
                artist_similarity = self._string_similarity(artist.lower().strip(), db_track.artist_name.lower().strip())
                
                # Weight title slightly more than artist
                return (title_similarity * 0.6) + (artist_similarity * 0.4)
            
            # Find best match
            best_match = None
            best_confidence = 0.0
            
            for track in potential_matches:
                confidence = calculate_confidence(track)
                if confidence > best_confidence:
                    best_confidence = confidence
                    best_match = track
            
            # Return match only if it meets threshold
            if best_confidence >= confidence_threshold:
                return best_match, best_confidence
            else:
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
        Calculate simple string similarity using Levenshtein distance.
        Returns value between 0.0 (no similarity) and 1.0 (identical)
        """
        if s1 == s2:
            return 1.0
        
        if not s1 or not s2:
            return 0.0
        
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
        Returns (album, confidence, owned_tracks, expected_tracks, is_complete)
        """
        try:
            # First find the album match
            album, confidence = self.check_album_exists(title, artist, confidence_threshold)
            
            if not album:
                return None, 0.0, 0, 0, False
            
            # Now check completeness
            owned_tracks, expected_tracks, is_complete = self.check_album_completeness(album.id, expected_track_count)
            
            return album, confidence, owned_tracks, expected_tracks, is_complete
            
        except Exception as e:
            logger.error(f"Error checking album existence with completeness for '{title}' by '{artist}': {e}")
            return None, 0.0, 0, 0, False
    
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