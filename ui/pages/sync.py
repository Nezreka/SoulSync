from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
                           QFrame, QPushButton, QListWidget, QListWidgetItem,
                           QProgressBar, QTextEdit, QCheckBox, QComboBox,
                           QScrollArea, QSizePolicy, QMessageBox, QDialog,
                           QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QLineEdit, QTabWidget)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QPropertyAnimation, QEasingCurve, QRunnable, QThreadPool, QObject
from PyQt6.QtGui import QFont, QBrush, QColor
import os
import json
from datetime import datetime
from dataclasses import dataclass
from typing import List, Optional
from core.soulseek_client import TrackResult
import re
import asyncio
from core.matching_engine import MusicMatchingEngine
from core.wishlist_service import get_wishlist_service
from ui.components.toast_manager import ToastType
from database.music_database import get_database
from core.plex_scan_manager import PlexScanManager
from utils.logging_config import get_logger
import yt_dlp
from core.spotify_client import Track, Playlist

logger = get_logger("sync")

# Define constants for storage
STORAGE_DIR = "storage"
STATUS_FILE = os.path.join(STORAGE_DIR, "sync_status.json")

class EllipsisLabel(QLabel):
    """A label that shows ellipsis for long text and tooltip on hover"""
    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.full_text = text
        self.setText(text)
        
    def setText(self, text):
        self.full_text = text
        # Set elided text with ellipsis
        fm = self.fontMetrics()
        elided_text = fm.elidedText(text, Qt.TextElideMode.ElideRight, self.width() - 10)
        super().setText(elided_text)
        
        # Set tooltip to show full text if it's elided
        if elided_text != text:
            self.setToolTip(text)
        else:
            self.setToolTip("")  # Clear tooltip if text fits
    
    def resizeEvent(self, event):
        """Handle resize events to recalculate ellipsis"""
        super().resizeEvent(event)
        # Re-elide text with new width
        if self.full_text:
            fm = self.fontMetrics()
            elided_text = fm.elidedText(self.full_text, Qt.TextElideMode.ElideRight, self.width() - 10)
            super().setText(elided_text)
            
            # Update tooltip
            if elided_text != self.full_text:
                self.setToolTip(self.full_text)
            else:
                self.setToolTip("")

def load_sync_status():
    """Loads the sync status from the JSON file."""
    if not os.path.exists(STATUS_FILE):
        return {}
    try:
        with open(STATUS_FILE, 'r') as f:
            # Return empty dict if file is empty
            content = f.read()
            if not content:
                return {}
            return json.loads(content)
    except (json.JSONDecodeError, FileNotFoundError):
        # If file is corrupted or not found, return an empty dict
        print(f"Warning: Could not read or parse {STATUS_FILE}. Starting with a clean slate.")
        return {}

def save_sync_status(data):
    """Saves the sync status to the JSON file."""
    try:
        os.makedirs(STORAGE_DIR, exist_ok=True)
        with open(STATUS_FILE, 'w') as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        print(f"Error saving sync status to {STATUS_FILE}: {e}")

def clean_track_name_for_search(track_name):
    """
    Intelligently cleans a track name for searching by removing noise while preserving important version information.
    Removes: (feat. Artist), (Explicit), (Clean), etc.
    Keeps: (Extended Version), (Live), (Acoustic), (Remix), etc.
    """
    if not track_name or not isinstance(track_name, str):
        return track_name

    cleaned_name = track_name
    
    # Define patterns to REMOVE (noise that doesn't affect track identity)
    remove_patterns = [
        r'\s*\(explicit\)',           # (Explicit)
        r'\s*\(clean\)',              # (Clean) 
        r'\s*\(radio\s*edit\)',       # (Radio Edit)
        r'\s*\(radio\s*version\)',    # (Radio Version)
        r'\s*\(feat\.?\s*[^)]+\)',    # (feat. Artist) or (ft. Artist)
        r'\s*\(ft\.?\s*[^)]+\)',      # (ft Artist)
        r'\s*\(featuring\s*[^)]+\)',  # (featuring Artist)
        r'\s*\(with\s*[^)]+\)',       # (with Artist)
        r'\s*\[[^\]]*explicit[^\]]*\]', # [Explicit] in brackets
        r'\s*\[[^\]]*clean[^\]]*\]',    # [Clean] in brackets
    ]
    
    # Apply removal patterns
    for pattern in remove_patterns:
        cleaned_name = re.sub(pattern, '', cleaned_name, flags=re.IGNORECASE).strip()
    
    # PRESERVE important version information (do NOT remove these)
    # These patterns are intentionally NOT in the remove list:
    # - (Extended Version), (Extended), (Long Version)
    # - (Live), (Live Version), (Concert)
    # - (Acoustic), (Acoustic Version)  
    # - (Remix), (Club Mix), (Dance Mix)
    # - (Remastered), (Remaster)
    # - (Demo), (Studio Version)
    # - (Instrumental)
    # - Album/year info like (2023), (Deluxe Edition)
    
    # If cleaning results in an empty string, return the original track name
    if not cleaned_name.strip():
        return track_name
        
    # Log cleaning if significant changes were made
    if cleaned_name != track_name:
        print(f"🧹 Intelligent track cleaning: '{track_name}' -> '{cleaned_name}'")
    
    return cleaned_name

def clean_youtube_track_title(title, artist_name=None):
    """
    Aggressively clean YouTube track titles by removing video noise and extracting clean track names
    
    Examples:
    'No Way Jose (Official Music Video)' → 'No Way Jose'
    'bbno$ - mary poppins (official music video)' → 'mary poppins'
    'Beyond (From "Moana 2") (Official Video) ft. Rachel House' → 'Beyond'
    'Temporary (feat. Skylar Grey) [Official Music Video]' → 'Temporary'
    'ALL MY LOVE (Directors\' Cut)' → 'ALL MY LOVE'
    'Espresso Macchiato | Estonia 🇪🇪 | Official Music Video | #Eurovision2025' → 'Espresso Macchiato'
    """
    import re
    
    if not title:
        return title
    
    original_title = title
    
    # FIRST: Remove artist name if it appears at the start with a dash
    # Handle formats like "LITTLE BIG - MOUSTACHE" → "MOUSTACHE"
    if artist_name:
        # Create a regex pattern to match artist name at the beginning followed by dash
        # Use word boundaries and case-insensitive matching for better accuracy
        artist_pattern = r'^' + re.escape(artist_name.strip()) + r'\s*[-–—]\s*'
        cleaned_title = re.sub(artist_pattern, '', title, flags=re.IGNORECASE).strip()
        
        # Debug logging for artist removal
        if cleaned_title != title:
            print(f"🎯 Removed artist from title: '{title}' -> '{cleaned_title}' (artist: '{artist_name}')")
        
        title = cleaned_title
    
    # Remove content in brackets/braces of any type SECOND (before general dash removal)
    title = re.sub(r'【[^】]*】', '', title)  # Japanese brackets
    title = re.sub(r'\s*\([^)]*\)', '', title)   # Parentheses - removes everything after first (
    title = re.sub(r'\s*\(.*$', '', title)      # Remove everything after lone ( (unmatched parentheses)
    title = re.sub(r'\[[^\]]*\]', '', title)  # Square brackets
    title = re.sub(r'\{[^}]*\}', '', title)   # Curly braces
    title = re.sub(r'<[^>]*>', '', title)     # Angle brackets
    
    # Remove everything after a dash (often album or extra info)
    title = re.sub(r'\s*-\s*.*$', '', title)
    
    # Remove everything after pipes (|) - often used for additional context
    title = re.split(r'\s*\|\s*', title)[0].strip()
    
    # Remove common video/platform noise
    noise_patterns = [
        r'\bapple\s+music\b',
        r'\bfull\s+video\b', 
        r'\bmusic\s+video\b',
        r'\bofficial\s+video\b',
        r'\bofficial\s+music\s+video\b',
        r'\bofficial\b',
        r'\bcensored\s+version\b',
        r'\buncensored\s+version\b',
        r'\bexplicit\s+version\b',
        r'\blive\s+version\b',
        r'\bversion\b',
        r'\btopic\b',
        r'\baudio\b',
        r'\blyrics?\b',
        r'\blyric\s+video\b',
        r'\bwith\s+lyrics?\b',
        r'\bvisuali[sz]er\b',
        r'\bmv\b',
        r'\bdirectors?\s+cut\b',
        r'\bremaster(ed)?\b',
        r'\bremix\b'
    ]
    
    for pattern in noise_patterns:
        title = re.sub(pattern, '', title, flags=re.IGNORECASE)
    
    # Remove artist name from title if present
    if artist_name:
        # Try removing exact artist name
        title = re.sub(rf'\b{re.escape(artist_name)}\b', '', title, flags=re.IGNORECASE)
        # Try removing artist name with common separators
        title = re.sub(rf'\b{re.escape(artist_name)}\s*[-–—:]\s*', '', title, flags=re.IGNORECASE)
        title = re.sub(rf'^{re.escape(artist_name)}\s*[-–—:]\s*', '', title, flags=re.IGNORECASE)
    
    # Remove all quotes and other punctuation
    title = re.sub(r'["\'''""„‚‛‹›«»]', '', title)
    
    # Remove featured artist patterns (after removing parentheses)
    feat_patterns = [
        r'\s+feat\.?\s+.+$',     # " feat Artist" at end
        r'\s+ft\.?\s+.+$',       # " ft Artist" at end  
        r'\s+featuring\s+.+$',   # " featuring Artist" at end
        r'\s+with\s+.+$',        # " with Artist" at end
    ]
    
    for pattern in feat_patterns:
        title = re.sub(pattern, '', title, flags=re.IGNORECASE).strip()
    
    # Clean up whitespace and punctuation
    title = re.sub(r'\s+', ' ', title).strip()
    title = re.sub(r'^[-–—:,.\s]+|[-–—:,.\s]+$', '', title).strip()
    
    # If we cleaned too much, return original
    if not title.strip() or len(title.strip()) < 2:
        title = original_title
    
    if title != original_title:
        print(f"🧹 YouTube title cleaned: '{original_title}' → '{title}'")
    
    return title

def clean_youtube_artist(artist_string):
    """
    Clean YouTube artist strings to get primary artist name
    
    Examples:
    'Yung Gravy, bbno$ (BABY GRAVY)' → 'Yung Gravy'
    'Y2K, bbno$' → 'Y2K'
    'LITTLE BIG' → 'LITTLE BIG'
    'Artist "Nickname" Name' → 'Artist Nickname Name'
    'ArtistVEVO' → 'Artist'
    """
    import re
    
    if not artist_string:
        return artist_string
    
    original_artist = artist_string
    
    # Remove all quotes - they're usually not part of artist names
    artist_string = artist_string.replace('"', '').replace("'", '').replace(''', '').replace(''', '').replace('"', '').replace('"', '')
    
    # Remove anything in parentheses (often group/label names)
    artist_string = re.sub(r'\s*\([^)]*\)', '', artist_string).strip()
    
    # Remove anything in brackets (often additional info)
    artist_string = re.sub(r'\s*\[[^\]]*\]', '', artist_string).strip()
    
    # Remove common YouTube channel suffixes
    channel_suffixes = [
        r'\s*VEVO\s*$',
        r'\s*Music\s*$',
        r'\s*Official\s*$',
        r'\s*Records\s*$',
        r'\s*Entertainment\s*$',
        r'\s*TV\s*$',
        r'\s*Channel\s*$'
    ]
    
    for suffix in channel_suffixes:
        artist_string = re.sub(suffix, '', artist_string, flags=re.IGNORECASE).strip()
    
    # Split on common separators and take the first artist
    separators = [',', '&', ' and ', ' x ', ' X ', ' feat.', ' ft.', ' featuring', ' with', ' vs ', ' vs.']
    
    for sep in separators:
        if sep in artist_string:
            parts = artist_string.split(sep)
            artist_string = parts[0].strip()
            break
    
    # Clean up extra whitespace and punctuation
    artist_string = re.sub(r'\s+', ' ', artist_string).strip()
    artist_string = re.sub(r'^\-\s*|\s*\-$', '', artist_string).strip()  # Remove leading/trailing dashes
    artist_string = re.sub(r'^,\s*|\s*,$', '', artist_string).strip()    # Remove leading/trailing commas
    
    # If we cleaned too much, return original
    if not artist_string.strip():
        artist_string = original_artist
    
    if artist_string != original_artist:
        print(f"🧹 YouTube artist cleaned: '{original_artist}' → '{artist_string}'")
    
    return artist_string

def parse_youtube_playlist(url):
    """
    Parse a YouTube Music playlist URL and extract track information using yt-dlp
    Uses flat playlist extraction to avoid rate limits and get all tracks
    Returns a list of track dictionaries compatible with our Track structure
    """
    try:
        # Configure yt-dlp options for flat playlist extraction (avoids rate limits)
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': True,  # Only extract basic info, no individual video metadata
            'flat_playlist': True,  # Extract all playlist entries without hitting API for each video
            'skip_download': True,  # Don't download, just extract IDs and basic info
            # Remove all limits to get complete playlist
        }
        
        tracks = []
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Extract playlist info
            playlist_info = ydl.extract_info(url, download=False)
            
            if not playlist_info:
                raise Exception("Could not extract playlist information")
            
            # Get playlist entries
            entries = playlist_info.get('entries', [])
            
            if not entries:
                raise Exception("No tracks found in playlist")
            
            # Extract playlist title
            playlist_title = playlist_info.get('title', 'YouTube Playlist')
            print(f"🎵 Found {len(entries)} tracks in YouTube playlist: '{playlist_title}'")
            print(f"📊 Playlist info keys: {list(playlist_info.keys())}")
            if 'playlist_count' in playlist_info:
                print(f"📊 Reported playlist count: {playlist_info['playlist_count']}")
            if 'n_entries' in playlist_info:
                print(f"📊 Reported n_entries: {playlist_info['n_entries']}")
            print(f"📊 Actual entries length: {len(entries)}")
            
            # Convert each entry to our Track format
            for i, entry in enumerate(entries):
                if not entry:  # Skip None entries
                    continue
                    
                try:
                    # Extract title and uploader
                    raw_title = entry.get('title', f'Unknown Track {i+1}')
                    raw_uploader = entry.get('uploader', 'Unknown Artist')
                    duration = entry.get('duration', 0)
                    
                    # Start with uploader as default artist
                    artists = [clean_youtube_artist(raw_uploader)]
                    track_name = raw_title
                    
                    # Try to extract artist and track from title patterns
                    # Pattern 1: "LITTLE BIG – HARDCORE AMERICAN COWBOY" (artist in title)
                    if ' – ' in raw_title or ' - ' in raw_title:
                        separator = ' – ' if ' – ' in raw_title else ' - '
                        parts = raw_title.split(separator, 1)
                        if len(parts) == 2:
                            potential_artist = clean_youtube_artist(parts[0].strip())
                            potential_track = clean_youtube_track_title(parts[1].strip(), potential_artist)
                            
                            # Use the artist from title if it looks valid
                            if potential_artist and len(potential_artist) > 1:
                                artists = [potential_artist]
                                track_name = potential_track
                            else:
                                track_name = clean_youtube_track_title(raw_title)
                        else:
                            track_name = clean_youtube_track_title(raw_title)
                    
                    # Pattern 2: "Track by Artist" or "Track : Artist"
                    elif ' by ' in raw_title.lower():
                        parts = raw_title.lower().split(' by ', 1)
                        if len(parts) == 2:
                            track_name = clean_youtube_track_title(raw_title[:len(parts[0])].strip())
                            potential_artist = clean_youtube_artist(raw_title[len(parts[0]) + 4:].strip())
                            if potential_artist and len(potential_artist) > 1:
                                artists = [potential_artist]
                    
                    elif ': ' in raw_title and len(raw_title.split(': ')) == 2:
                        parts = raw_title.split(': ', 1)
                        potential_artist = clean_youtube_artist(parts[0].strip())
                        potential_track = clean_youtube_track_title(parts[1].strip(), potential_artist)
                        
                        if potential_artist and len(potential_artist) > 1:
                            artists = [potential_artist]
                            track_name = potential_track
                        else:
                            track_name = clean_youtube_track_title(raw_title)
                    
                    else:
                        # No clear pattern, just clean the title
                        track_name = clean_youtube_track_title(raw_title)
                    
                    # Final cleanup
                    track_name = track_name.strip()
                    artists = [artist.strip() for artist in artists if artist.strip()]
                    if not artists or not artists[0]:
                        artists = ['Unknown Artist']
                    
                    # Create track dict compatible with our Track structure
                    track_data = {
                        'id': entry.get('id', f'youtube_{i}'),
                        'name': track_name,
                        'artists': artists,
                        'album': 'YouTube Music',  # Default album name
                        'duration_ms': duration * 1000 if duration else 0,
                        'popularity': 0,  # YouTube doesn't provide popularity
                        'preview_url': None,
                        'external_urls': {'youtube': entry.get('webpage_url', '')},
                        # Store original uncleaned data for fallback searches
                        'raw_title': raw_title,
                        'raw_uploader': raw_uploader
                    }
                    
                    tracks.append(track_data)
                    
                    # Log the parsing result for debugging
                    if track_name != raw_title or artists[0] != raw_uploader:
                        print(f"🎯 Parsed: '{raw_title}' by '{raw_uploader}' → '{track_name}' by '{artists[0]}'")
                    
                except Exception as e:
                    print(f"⚠️ Error processing track {i}: {e}")
                    continue
        
        print(f"✅ Successfully processed {len(tracks)} tracks out of {len(entries)} entries")
        if len(tracks) != len(entries):
            skipped = len(entries) - len(tracks)
            print(f"⚠️ Skipped {skipped} tracks due to processing errors")
        
        return tracks, playlist_title
        
    except Exception as e:
        print(f"❌ Error parsing YouTube playlist: {e}")
        raise e

def create_youtube_playlist_object(tracks_data, playlist_url, playlist_title=None):
    """
    Create a Playlist object from YouTube tracks data that's compatible 
    with the existing DownloadMissingTracksModal
    """
    try:
        # Convert track dictionaries to Track objects
        tracks = []
        for track_data in tracks_data:
            track = Track(
                id=track_data['id'],
                name=track_data['name'],
                artists=track_data['artists'],
                album=track_data['album'],
                duration_ms=track_data['duration_ms'],
                popularity=track_data['popularity'],
                preview_url=track_data['preview_url'],
                external_urls=track_data['external_urls']
            )
            
            # Add raw uncleaned data for fallback searches
            if 'raw_title' in track_data and 'raw_uploader' in track_data:
                track.raw_title = track_data['raw_title']
                track.raw_uploader = track_data['raw_uploader']
            
            tracks.append(track)
        
        # Create playlist object
        # Use provided playlist title or fall back to generic name
        if playlist_title:
            playlist_name = playlist_title
        else:
            playlist_name = f"YouTube Playlist ({len(tracks)} tracks)"
        
        playlist = Playlist(
            id=f"youtube_{hash(playlist_url)}",  # Generate unique ID from URL
            name=playlist_name,
            description=f"Imported from YouTube Music: {playlist_url}",
            owner="YouTube Music",
            public=True,
            collaborative=False,
            tracks=tracks,
            total_tracks=len(tracks)
        )
        
        return playlist
        
    except Exception as e:
        print(f"❌ Error creating YouTube playlist object: {e}")
        raise e

@dataclass
class TrackAnalysisResult:
    """Result of analyzing a track for Plex existence"""
    spotify_track: object  # Spotify track object
    exists_in_plex: bool
    plex_match: Optional[object] = None  # Plex track if found
    confidence: float = 0.0
    error_message: Optional[str] = None

class PlaylistTrackAnalysisWorkerSignals(QObject):
    """Signals for playlist track analysis worker"""
    analysis_started = pyqtSignal(int)  # total_tracks
    track_analyzed = pyqtSignal(int, object)  # track_index, TrackAnalysisResult
    analysis_completed = pyqtSignal(list)  # List[TrackAnalysisResult]
    analysis_failed = pyqtSignal(str)  # error_message

class PlaylistTrackAnalysisWorker(QRunnable):
    """Background worker to analyze playlist tracks against media library"""
    
    def __init__(self, playlist_tracks, media_client, server_type="plex"):
        super().__init__()
        self.playlist_tracks = playlist_tracks
        self.media_client = media_client  # Can be plex_client or jellyfin_client
        self.server_type = server_type
        self.signals = PlaylistTrackAnalysisWorkerSignals()
        self._cancelled = False
        # Instantiate the matching engine once per worker for efficiency
        self.matching_engine = MusicMatchingEngine()
    
    def cancel(self):
        """Cancel the analysis operation"""
        self._cancelled = True
    
    def run(self):
        """Analyze each track in the playlist"""
        try:
            if self._cancelled:
                return
                
            self.signals.analysis_started.emit(len(self.playlist_tracks))
            results = []
            
            # Check if media server is connected
            server_connected = False
            try:
                if self.media_client:
                    server_connected = self.media_client.is_connected()
            except Exception as e:
                print(f"{self.server_type.title()} connection check failed: {e}")
                server_connected = False
            
            for i, track in enumerate(self.playlist_tracks):
                if self._cancelled:
                    return
                
                result = TrackAnalysisResult(
                    spotify_track=track,
                    exists_in_plex=False
                )
                
                if server_connected:
                    # Check if track exists in media server
                    try:
                        match, confidence = self._check_track_in_library(track)
                        # Use the 0.8 confidence threshold
                        if match and confidence >= 0.8:
                            result.exists_in_plex = True  # Keep existing field name for compatibility
                            result.plex_match = match      # Keep existing field name for compatibility
                            result.confidence = confidence
                    except Exception as e:
                        result.error_message = f"{self.server_type.title()} check failed: {str(e)}"
                
                results.append(result)
                self.signals.track_analyzed.emit(i + 1, result)
            
            if not self._cancelled:
                self.signals.analysis_completed.emit(results)
                
        except Exception as e:
            if not self._cancelled:
                import traceback
                traceback.print_exc()
                self.signals.analysis_failed.emit(str(e))
    
    def _check_track_in_library(self, spotify_track):
        """
        Check if a Spotify track exists in the database by searching for each artist and
        stopping as soon as a confident match is found.
        Now uses local database instead of media server API for much faster performance.
        """
        try:
            original_title = spotify_track.name
            
            # Get database instance
            db = get_database()
            
            # --- Generate conservative title variations (preserve meaningful differences) ---
            title_variations = [original_title]
            
            # Only add cleaned version if it removes clear noise (not meaningful content like remixes)
            cleaned_for_search = clean_track_name_for_search(original_title)
            if cleaned_for_search.lower() != original_title.lower():
                title_variations.append(cleaned_for_search)

            # Use matching engine's conservative clean_title (no longer strips remixes/versions)
            base_title = self.matching_engine.clean_title(original_title)
            if base_title.lower() not in [t.lower() for t in title_variations]:
                title_variations.append(base_title)
            
            # DO NOT strip content after dashes - this removes important remix/version info

            unique_title_variations = list(dict.fromkeys(title_variations))
            
            # --- Search for each artist with each title variation ---
            artists_to_search = spotify_track.artists if spotify_track.artists else [""]
            for artist_name in artists_to_search:
                if self._cancelled: return None, 0.0
                
                for query_title in unique_title_variations:
                    if self._cancelled: return None, 0.0

                    # Use database check_track_exists method with consistent thresholds and active server filter
                    from config.settings import config_manager
                    active_server = config_manager.get_active_media_server()
                    db_track, confidence = db.check_track_exists(query_title, artist_name, confidence_threshold=0.7, server_source=active_server)
                    
                    if db_track and confidence >= 0.7:
                        print(f"✔️ Database match found for '{original_title}' by '{artist_name}': '{db_track.title}' with confidence {confidence:.2f}")
                        
                        # Convert database track to format compatible with existing code
                        # Create a mock Plex track object for compatibility
                        class MockPlexTrack:
                            def __init__(self, db_track):
                                self.id = str(db_track.id)
                                self.title = db_track.title
                                self.artist_name = db_track.artist_name
                                self.album_title = db_track.album_title
                                self.track_number = db_track.track_number
                                self.duration = db_track.duration
                                self.file_path = db_track.file_path
                        
                        mock_track = MockPlexTrack(db_track)
                        return mock_track, confidence
            
            print(f"❌ No database match found for '{original_title}' by any of the artists {artists_to_search}")
            return None, 0.0
            
        except Exception as e:
            import traceback
            print(f"Error checking track in database: {e}")
            traceback.print_exc()
            return None, 0.0


class TrackDownloadWorkerSignals(QObject):
    """Signals for track download worker"""
    download_started = pyqtSignal(int, int, str)  # download_index, track_index, download_id
    download_failed = pyqtSignal(int, int, str)  # download_index, track_index, error_message

class TrackDownloadWorker(QRunnable):
    """Background worker to download individual tracks via Soulseek"""
    
    def __init__(self, spotify_track, soulseek_client, download_index, track_index, quality_preference=None):
        super().__init__()
        self.spotify_track = spotify_track
        self.soulseek_client = soulseek_client
        self.download_index = download_index
        self.track_index = track_index
        self.quality_preference = quality_preference or 'flac'
        self.signals = TrackDownloadWorkerSignals()
        self._cancelled = False
    
    def cancel(self):
        """Cancel the download operation"""
        self._cancelled = True
    
    def run(self):
        """Download the track via Soulseek"""
        try:
            if self._cancelled or not self.soulseek_client:
                return
            
            # Create search queries - prioritize artist + track for better accuracy
            track_name = self.spotify_track.name
            artist_name = self.spotify_track.artists[0] if self.spotify_track.artists else ""
            
            search_queries = []
            # Try artist + track first (more specific, less false matches)
            if artist_name:
                search_queries.append(f"{artist_name} {track_name}")
            # Fallback to track name only if artist search fails
            search_queries.append(track_name)
            
            download_id = None
            
            # Try each search query until we find a download
            for query in search_queries:
                if self._cancelled:
                    return
                    
                print(f"🔍 Searching Soulseek: {query}")
                
                # Use the async method (need to run in sync context)
                import asyncio
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                
                try:
                    download_id = loop.run_until_complete(
                        self.soulseek_client.search_and_download_best(query, self.quality_preference)
                    )
                    if download_id:
                        break  # Success - stop trying other queries
                finally:
                    loop.close()
            
            if download_id:
                self.signals.download_started.emit(self.download_index, self.track_index, download_id)
            else:
                self.signals.download_failed.emit(self.download_index, self.track_index, "No search results found")
                
        except Exception as e:
            self.signals.download_failed.emit(self.download_index, self.track_index, str(e))

class SyncStatusProcessingWorkerSignals(QObject):
    """Defines the signals available from the SyncStatusProcessingWorker."""
    completed = pyqtSignal(list)
    error = pyqtSignal(str)

class SyncStatusProcessingWorker(QRunnable):
    """
    Runs download status processing in a background thread for the sync modal.
    It checks the slskd API to provide a reliable status, with fallbacks.
    This implementation is based on the working logic from downloads.py to restore live updates.
    """
    def __init__(self, soulseek_client, download_items_data):
        super().__init__()
        self.signals = SyncStatusProcessingWorkerSignals()
        self.soulseek_client = soulseek_client
        self.download_items_data = download_items_data
        # This worker no longer performs filesystem checks, so it doesn't need transfers_directory.

    def run(self):
        """The main logic of the background worker."""
        try:
            import asyncio
            import os
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            transfers_data = loop.run_until_complete(
                self.soulseek_client._make_request('GET', 'transfers/downloads')
            )
            loop.close()

            results = []
            if not transfers_data:
                transfers_data = []

            # --- FIX: More robustly parse the transfers data ---
            # Errored/finished downloads might not be nested inside 'directories'.
            # This checks for a 'files' list at both the user and directory levels.
            all_transfers = []
            for user_data in transfers_data:
                # Check for files directly under the user object
                if 'files' in user_data and isinstance(user_data['files'], list):
                    all_transfers.extend(user_data['files'])
                # Also check for files nested inside directories
                if 'directories' in user_data and isinstance(user_data['directories'], list):
                    for directory in user_data['directories']:
                        if 'files' in directory and isinstance(directory['files'], list):
                            all_transfers.extend(directory['files'])

            transfers_by_id = {t['id']: t for t in all_transfers}
            
            for item_data in self.download_items_data:
                matching_transfer = None
                
                # Step 1: Try to match by the original download ID.
                if item_data.get('download_id'):
                    matching_transfer = transfers_by_id.get(item_data['download_id'])

                # Step 2: If no match by ID, fall back to an exact filename match.
                if not matching_transfer:
                    expected_basename = os.path.basename(item_data['file_path']).lower()
                    for t in all_transfers:
                        api_basename = os.path.basename(t.get('filename', '')).lower()
                        if api_basename == expected_basename:
                            matching_transfer = t
                            print(f"ℹ️ Found download for '{expected_basename}' by exact filename match.")
                            break

                if matching_transfer:
                    state = matching_transfer.get('state', 'Unknown')
                    progress = matching_transfer.get('percentComplete', 0)
                    
                    # Determine status with correct priority (Errored/Cancelled before Completed)
                    if 'Cancelled' in state or 'Canceled' in state:
                        new_status = 'cancelled'
                    elif 'Failed' in state or 'Errored' in state:
                        new_status = 'failed'
                    elif 'Completed' in state or 'Succeeded' in state:
                        new_status = 'completed'
                    elif 'InProgress' in state:
                        new_status = 'downloading'
                    else:
                        new_status = 'queued'

                    payload = {
                        'widget_id': item_data['widget_id'],
                        'status': new_status,
                        'progress': int(progress),
                        'transfer_id': matching_transfer.get('id'),
                        'username': matching_transfer.get('username')
                    }
                    results.append(payload)
                else:
                    # If not found in the API, it might have failed or been cancelled.
                    # Use a grace period before marking as failed.
                    item_data['api_missing_count'] = item_data.get('api_missing_count', 0) + 1
                    if item_data['api_missing_count'] >= 3:
                        expected_filename = os.path.basename(item_data['file_path'])
                        print(f"❌ Download failed (missing from API after 3 checks): {expected_filename}")
                        payload = {'widget_id': item_data['widget_id'], 'status': 'failed'}
                        results.append(payload)

            self.signals.completed.emit(results)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.signals.error.emit(str(e))

class PlaylistLoaderThread(QThread):
    playlist_loaded = pyqtSignal(object)  # Single playlist
    loading_finished = pyqtSignal(int)  # Total count
    loading_failed = pyqtSignal(str)  # Error message
    progress_updated = pyqtSignal(str)  # Progress text
    
    def __init__(self, spotify_client):
        super().__init__()
        self.spotify_client = spotify_client
        
    def run(self):
        try:
            self.progress_updated.emit("Connecting to Spotify...")
            if not self.spotify_client or not self.spotify_client.is_authenticated():
                self.loading_failed.emit("Spotify not authenticated")
                return
            
            self.progress_updated.emit("Fetching playlists...")
            playlists = self.spotify_client.get_user_playlists_metadata_only()
            
            for i, playlist in enumerate(playlists):
                self.progress_updated.emit(f"Loading playlist {i+1}/{len(playlists)}: {playlist.name}")
                self.playlist_loaded.emit(playlist)
                self.msleep(20)  # Reduced delay for faster but visible progressive loading
            
            self.loading_finished.emit(len(playlists))
            
        except Exception as e:
            self.loading_failed.emit(str(e))

class TrackLoadingWorkerSignals(QObject):
    """Signals for async track loading worker"""
    tracks_loaded = pyqtSignal(str, list)  # playlist_id, tracks
    loading_failed = pyqtSignal(str, str)  # playlist_id, error_message
    loading_started = pyqtSignal(str)  # playlist_id

class TrackLoadingWorker(QRunnable):
    """Async worker for loading playlist tracks (following downloads.py pattern)"""
    
    def __init__(self, spotify_client, playlist_id, playlist_name):
        super().__init__()
        self.spotify_client = spotify_client
        self.playlist_id = playlist_id
        self.playlist_name = playlist_name
        self.signals = TrackLoadingWorkerSignals()
        self._cancelled = False
    
    def cancel(self):
        """Cancel the worker operation"""
        self._cancelled = True
    
    def run(self):
        """Load tracks in background thread"""
        try:
            if self._cancelled:
                return
                
            self.signals.loading_started.emit(self.playlist_id)
            
            if self._cancelled:
                return
            
            # Fetch tracks from Spotify API
            tracks = self.spotify_client._get_playlist_tracks(self.playlist_id)
            
            if self._cancelled:
                return
            
            # Emit success signal
            self.signals.tracks_loaded.emit(self.playlist_id, tracks)
            
        except Exception as e:
            if not self._cancelled:
                # Emit error signal only if not cancelled
                self.signals.loading_failed.emit(self.playlist_id, str(e))

class SyncWorkerSignals(QObject):
    """Signals for sync worker"""
    progress = pyqtSignal(object)  # SyncProgress
    finished = pyqtSignal(object, object)  # SyncResult, snapshot_id (can be None)
    error = pyqtSignal(str)

class SyncWorker(QRunnable):
    """Background worker for playlist synchronization with real-time progress callbacks"""
    
    def __init__(self, playlist, sync_service, progress_callback=None):
        super().__init__()
        self.playlist = playlist
        self.sync_service = sync_service
        self.progress_callback = progress_callback
        self.signals = SyncWorkerSignals()
        self._cancelled = False
        
        # Connect progress callback
        if progress_callback:
            self.signals.progress.connect(progress_callback)
    
    def cancel(self):
        """Cancel the sync operation"""
        self._cancelled = True
        if hasattr(self.sync_service, 'cancel_sync'):
            self.sync_service.cancel_sync()
        
        # Clear the progress callback to stop further progress updates
        if hasattr(self.sync_service, 'clear_progress_callback'):
            self.sync_service.clear_progress_callback(self.playlist.name)
        
        # Log the cancellation request
        print(f"DEBUG: SyncWorker.cancel() called for playlist {getattr(self.playlist, 'name', 'unknown')}")
    
    def run(self):
        """Execute the sync operation"""
        snapshot_id = None # Define snapshot_id in the outer scope
        try:
            if self._cancelled:
                return
            
            # Set up progress callback for sync service
            def on_progress(progress):
                print(f"⚡ SyncWorker progress callback called! total={progress.total_tracks}, matched={progress.matched_tracks}")
                if not self._cancelled:
                    print(f"⚡ Emitting progress signal to parent page")
                    self.signals.progress.emit(progress)
                else:
                    print(f"⚡ Sync was cancelled, not emitting signal")
            
            print(f"⚡ Setting up progress callback for playlist: '{self.playlist.name}'")
            self.sync_service.set_progress_callback(on_progress, self.playlist.name)
            
            # Create new event loop for this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            try:
                # Run sync with playlist object
                result = loop.run_until_complete(
                    self.sync_service.sync_playlist(self.playlist, download_missing=False)
                )
                
                # --- THE FIX ---
                # After sync, fetch the new snapshot_id directly from Spotify
                # to ensure we have the most up-to-date value.
                try:
                    if hasattr(self.sync_service, 'spotify_client') and self.sync_service.spotify_client:
                        # Assuming a synchronous method to get a single playlist's metadata
                        updated_playlist = self.sync_service.spotify_client.get_playlist(self.playlist.id)
                        if updated_playlist:
                            snapshot_id = updated_playlist.snapshot_id
                            print(f"DEBUG: Successfully fetched new snapshot_id: {snapshot_id}")
                        else:
                            print(f"WARNING: get_playlist returned None for {self.playlist.name}")
                    else:
                        print("WARNING: Could not get snapshot_id, spotify_client not found on sync_service.")
                except Exception as e:
                    print(f"WARNING: Could not fetch updated snapshot_id for {self.playlist.name}: {e}")
                
                if not self._cancelled:
                    # Emit the result and the (potentially new) snapshot_id
                    self.signals.finished.emit(result, snapshot_id)
                    
            finally:
                loop.close()
                
        except Exception as e:
            if not self._cancelled:
                self.signals.error.emit(str(e))

class PlaylistDetailsModal(QDialog):
    def __init__(self, playlist, parent=None):
        super().__init__(parent)
        self.playlist = playlist
        self.parent_page = parent
        self.spotify_client = parent.spotify_client if parent else None
        
        # Thread management
        self.active_workers = []
        self.fallback_pools = []
        self.is_closing = False
        
        # Sync state tracking
        self.is_syncing = False
        self.sync_worker = None
        self.sync_status_widget = None
        self.sync_button = None
        
        # Clear existing tracks BEFORE setup_ui to prevent synchronous population
        if self.spotify_client:
            self.playlist.tracks = []
        
        self.setup_ui()
        
        # Restore sync state if playlist is currently syncing
        self.restore_sync_state()
        
        # Load tracks asynchronously if not already loaded
        if not self.playlist.tracks and self.spotify_client:
            # Check cache first
            if hasattr(parent, 'track_cache') and playlist.id in parent.track_cache:
                self.playlist.tracks = parent.track_cache[playlist.id]
                self.refresh_track_table()
            else:
                self.load_tracks_async()
    
    def closeEvent(self, event):
        """Clean up threads and resources when modal is closed"""
        self.is_closing = True
        self.cleanup_workers()
        super().closeEvent(event)
    
    def cleanup_workers(self):
        """Clean up all active workers and thread pools (except sync workers)"""
        # Cancel active workers first, but skip sync workers to allow background sync
        for worker in self.active_workers:
            try:
                # Don't cancel sync workers - they should continue in background
                if hasattr(worker, 'cancel') and not isinstance(worker, SyncWorker):
                    worker.cancel()
            except (RuntimeError, AttributeError):
                pass
        
        # Disconnect signals from active workers to prevent race conditions (except sync workers)
        for worker in self.active_workers:
            try:
                # Don't disconnect sync worker signals - they need to continue updating playlist items
                if hasattr(worker, 'signals') and not isinstance(worker, SyncWorker):
                    # Disconnect track loading worker signals
                    try:
                        worker.signals.tracks_loaded.disconnect(self.on_tracks_loaded)
                    except (RuntimeError, TypeError):
                        pass
                    try:
                        worker.signals.loading_failed.disconnect(self.on_tracks_loading_failed)
                    except (RuntimeError, TypeError):
                        pass
                    
                    # Disconnect playlist analysis worker signals
                    try:
                        worker.signals.analysis_started.disconnect(self.on_analysis_started)
                    except (RuntimeError, TypeError):
                        pass
                    try:
                        worker.signals.track_analyzed.disconnect(self.on_track_analyzed)
                    except (RuntimeError, TypeError):
                        pass
                    try:
                        worker.signals.analysis_completed.disconnect(self.on_analysis_completed)
                    except (RuntimeError, TypeError):
                        pass
                    try:
                        worker.signals.analysis_failed.disconnect(self.on_analysis_failed)
                    except (RuntimeError, TypeError):
                        pass
            except (RuntimeError, AttributeError):
                # Signal may already be disconnected or worker deleted
                pass
        
        # Clean up fallback thread pools with timeout
        for pool in self.fallback_pools:
            try:
                pool.clear()  # Cancel pending workers
                if not pool.waitForDone(2000):  # Wait 2 seconds max
                    # Force termination if workers don't finish gracefully
                    pool.clear()
            except (RuntimeError, AttributeError):
                pass
        
        # Clear tracking lists
        self.active_workers.clear()
        self.fallback_pools.clear()
    
    def setup_ui(self):
        self.setWindowTitle(f"Playlist Details - {self.playlist.name}")
        self.setFixedSize(1200, 800)
        self.setStyleSheet("""
            QDialog {
                background: #191414;
                color: #ffffff;
            }
        """)
        
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(32, 32, 32, 32)
        main_layout.setSpacing(24)
        
        # Header section
        header = self.create_header()
        main_layout.addWidget(header)
        
        # Track list section
        track_list = self.create_track_list()
        main_layout.addWidget(track_list)
        
        # Button section
        button_widget = QWidget()
        button_layout = self.create_buttons()
        button_widget.setLayout(button_layout)
        main_layout.addWidget(button_widget)
    
    def create_header(self):
        header = QFrame()
        header.setFixedHeight(120)
        header.setStyleSheet("""
            QFrame {
                background: #282828;
                border-radius: 16px;
                border: 1px solid #404040;
            }
        """)
        
        layout = QVBoxLayout(header)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(12)
        
        # Playlist name - larger, more prominent
        name_label = QLabel(self.playlist.name)
        name_label.setFont(QFont("SF Pro Display", 24, QFont.Weight.Bold))
        name_label.setStyleSheet("color: #ffffff; border: none; background: transparent;")
        
        # Playlist info in a more compact horizontal layout
        info_layout = QHBoxLayout()
        info_layout.setSpacing(24)
        
        # Track count with icon-like styling
        track_count = QLabel(f"{self.playlist.total_tracks} tracks")
        track_count.setFont(QFont("SF Pro Text", 14, QFont.Weight.Medium))
        track_count.setStyleSheet("color: #b3b3b3; border: none; background: transparent;")
        
        # Owner with subtle separator
        owner = QLabel(f"by {self.playlist.owner}")
        owner.setFont(QFont("SF Pro Text", 14))
        owner.setStyleSheet("color: #b3b3b3; border: none; background: transparent;")
        
        # Status with accent color
        visibility = "Public" if self.playlist.public else "Private"
        if self.playlist.collaborative:
            visibility = "Collaborative"
        status = QLabel(visibility)
        status.setFont(QFont("SF Pro Text", 14, QFont.Weight.Medium))
        status.setStyleSheet("""
            color: #1db954; 
            border: none; 
            background: rgba(29, 185, 84, 0.1);
            padding: 4px 12px;
            border-radius: 12px;
        """)
        
        info_layout.addWidget(track_count)
        info_layout.addWidget(owner)
        info_layout.addWidget(status)
        info_layout.addStretch()
        
        # Sync status display (hidden by default)
        self.sync_status_widget = self.create_sync_status_display()
        info_layout.addWidget(self.sync_status_widget)
        
        layout.addWidget(name_label)
        layout.addLayout(info_layout)
        
        return header
    
    def create_sync_status_display(self):
        """Create sync status display widget (hidden by default)"""
        sync_status = QFrame()
        sync_status.setStyleSheet("""
            QFrame {
                background: rgba(29, 185, 84, 0.1);
                border: 1px solid rgba(29, 185, 84, 0.3);
                border-radius: 12px;
            }
        """)
        sync_status.setMinimumHeight(36)  # Ensure adequate height
        sync_status.hide()  # Hidden by default
        
        layout = QHBoxLayout(sync_status)
        layout.setContentsMargins(12, 8, 12, 8)  # Increased margins for better text visibility
        layout.setSpacing(12)
        
        # Total tracks
        self.total_tracks_label = QLabel("♪ 0")
        self.total_tracks_label.setFont(QFont("SF Pro Text", 12, QFont.Weight.Medium))
        self.total_tracks_label.setStyleSheet("color: #ffa500; background: transparent; border: none;")
        
        # Matched tracks
        self.matched_tracks_label = QLabel("✓ 0")
        self.matched_tracks_label.setFont(QFont("SF Pro Text", 12, QFont.Weight.Medium))
        self.matched_tracks_label.setStyleSheet("color: #1db954; background: transparent; border: none;")
        
        # Failed tracks
        self.failed_tracks_label = QLabel("✗ 0")
        self.failed_tracks_label.setFont(QFont("SF Pro Text", 12, QFont.Weight.Medium))
        self.failed_tracks_label.setStyleSheet("color: #e22134; background: transparent; border: none;")
        
        # Percentage
        self.percentage_label = QLabel("0%")
        self.percentage_label.setFont(QFont("SF Pro Text", 12, QFont.Weight.Bold))
        self.percentage_label.setStyleSheet("color: #1db954; background: transparent; border: none;")
        
        layout.addWidget(self.total_tracks_label)
        
        # Separator 1
        sep1 = QLabel("/")
        sep1.setFont(QFont("SF Pro Text", 12, QFont.Weight.Medium))
        sep1.setStyleSheet("color: #666666; background: transparent; border: none;")
        layout.addWidget(sep1)
        
        layout.addWidget(self.matched_tracks_label)
        
        # Separator 2
        sep2 = QLabel("/")
        sep2.setFont(QFont("SF Pro Text", 12, QFont.Weight.Medium))
        sep2.setStyleSheet("color: #666666; background: transparent; border: none;")
        layout.addWidget(sep2)
        
        layout.addWidget(self.failed_tracks_label)
        
        # Separator 3
        sep3 = QLabel("/")
        sep3.setFont(QFont("SF Pro Text", 12, QFont.Weight.Medium))
        sep3.setStyleSheet("color: #666666; background: transparent; border: none;")
        layout.addWidget(sep3)
        
        layout.addWidget(self.percentage_label)
        
        return sync_status
    
    def update_sync_status(self, total_tracks=0, matched_tracks=0, failed_tracks=0):
        """Update sync status display"""
        if self.sync_status_widget:
            self.total_tracks_label.setText(f"♪ {total_tracks}")
            self.matched_tracks_label.setText(f"✓ {matched_tracks}")
            self.failed_tracks_label.setText(f"✗ {failed_tracks}")
            
            if total_tracks > 0:
                processed_tracks = matched_tracks + failed_tracks
                percentage = int((processed_tracks / total_tracks) * 100)
                self.percentage_label.setText(f"{percentage}%")
            else:
                self.percentage_label.setText("0%")
    
    def set_sync_button_state(self, is_syncing):
        """Update sync button appearance based on sync state"""
        if self.sync_button:
            if is_syncing:
                # Change to Cancel Sync with red styling
                self.sync_button.setText("Cancel Sync")
                self.sync_button.setStyleSheet("""
                    QPushButton {
                        background: #e22134;
                        border: none;
                        border-radius: 22px;
                        color: #ffffff;
                        font-size: 13px;
                        font-weight: 600;
                        font-family: 'SF Pro Text';
                    }
                    QPushButton:hover {
                        background: #f44336;
                    }
                    QPushButton:pressed {
                        background: #c62828;
                    }
                """)
            else:
                # Change back to Sync This Playlist with green styling
                self.sync_button.setText("Sync This Playlist")
                self.sync_button.setStyleSheet("""
                    QPushButton {
                        background: #1db954;
                        border: none;
                        border-radius: 22px;
                        color: #ffffff;
                        font-size: 13px;
                        font-weight: 600;
                        font-family: 'SF Pro Text';
                    }
                    QPushButton:hover {
                        background: #1ed760;
                    }
                    QPushButton:pressed {
                        background: #169c46;
                    }
                """)
    
    def restore_sync_state(self):
        """Restore sync state when modal is reopened"""
        # Check if sync is ongoing for this playlist
        if self.parent_page and self.parent_page.is_playlist_syncing(self.playlist.id):
            self.is_syncing = True
            self.set_sync_button_state(True)
            
            # Find playlist item to get current progress
            playlist_item = self.parent_page.find_playlist_item_widget(self.playlist.id)
            if playlist_item:
                # Show sync status widget with current progress
                if self.sync_status_widget:
                    self.sync_status_widget.show()
                    self.update_sync_status(
                        playlist_item.sync_total_tracks,
                        playlist_item.sync_matched_tracks,
                        playlist_item.sync_failed_tracks
                    )
    
    def create_track_list(self):
        container = QFrame()
        container.setStyleSheet("""
            QFrame {
                background: #282828;
                border-radius: 16px;
                border: 1px solid #404040;
            }
        """)
        
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Track table with professional styling
        self.track_table = QTableWidget()
        self.track_table.setColumnCount(4)
        self.track_table.setHorizontalHeaderLabels(["Track", "Artist", "Album", "Duration"])
        
        # Set initial row count (may be 0 if tracks not loaded yet)
        track_count = len(self.playlist.tracks) if self.playlist.tracks else 1
        self.track_table.setRowCount(track_count)
        
        # Professional table styling
        self.track_table.setStyleSheet("""
            QTableWidget {
                background: #282828;
                border: none;
                border-radius: 16px;
                gridline-color: transparent;
                color: #ffffff;
                font-size: 11px;
                selection-background-color: rgba(29, 185, 84, 0.2);
            }
            QTableWidget::item {
                padding: 12px 16px;
                border-bottom: 1px solid rgba(255, 255, 255, 0.05);
                background: transparent;
            }
            QTableWidget::item:hover {
                background: rgba(255, 255, 255, 0.02);
            }
            QTableWidget::item:selected {
                background: rgba(29, 185, 84, 0.15);
                color: #ffffff;
            }
            QHeaderView {
                background: transparent;
                border: none;
            }
            QHeaderView::section {
                background: transparent;
                color: #b3b3b3;
                padding: 12px 16px;
                border: none;
                border-bottom: 2px solid rgba(255, 255, 255, 0.1);
                font-weight: 600;
                font-size: 10px;
                text-transform: uppercase;
                letter-spacing: 0.5px;
            }
            QHeaderView::section:hover {
                background: rgba(255, 255, 255, 0.02);
            }
        """)
        
        # Populate table with proper styling
        if self.playlist.tracks:
            for row, track in enumerate(self.playlist.tracks):
                # Track name with ellipsis label
                track_label = EllipsisLabel(track.name)
                track_label.setFont(QFont("SF Pro Text", 11, QFont.Weight.Medium))
                track_label.setStyleSheet("color: #ffffff; background: transparent; border: none;")
                self.track_table.setCellWidget(row, 0, track_label)
                
                # Artist(s) with ellipsis label  
                artists = ", ".join(track.artists)
                artist_label = EllipsisLabel(artists)
                artist_label.setFont(QFont("SF Pro Text", 11))
                artist_label.setStyleSheet("color: #ffffff; background: transparent; border: none;")
                self.track_table.setCellWidget(row, 1, artist_label)
                
                # Album with ellipsis label
                album_label = EllipsisLabel(track.album)
                album_label.setFont(QFont("SF Pro Text", 11))
                album_label.setStyleSheet("color: #ffffff; background: transparent; border: none;")
                self.track_table.setCellWidget(row, 2, album_label)
                
                # Duration with standard item (doesn't need scrolling)
                duration = self.format_duration(track.duration_ms)
                duration_item = QTableWidgetItem(duration)
                duration_item.setFlags(duration_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                duration_item.setFont(QFont("SF Mono", 10))
                self.track_table.setItem(row, 3, duration_item)
        else:
            # Show placeholder while tracks are being loaded
            placeholder_item = QTableWidgetItem("Loading tracks...")
            placeholder_item.setFlags(placeholder_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.track_table.setItem(0, 0, placeholder_item)
            self.track_table.setSpan(0, 0, 1, 4)
        
        # Professional column configuration
        header = self.track_table.horizontalHeader()
        header.setVisible(True)
        header.show()
        header.setStretchLastSection(False)
        header.setHighlightSections(False)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        
        # Calculate available width (modal is 1200px, account for margins)
        available_width = 1136  # 1200 - 64px margins
        
        # Professional proportional widths
        track_width = int(available_width * 0.35)    # ~398px
        artist_width = int(available_width * 0.28)   # ~318px  
        album_width = int(available_width * 0.28)    # ~318px
        duration_width = 100                         # Fixed 100px
        
        # Apply column widths with proper resize modes
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        
        self.track_table.setColumnWidth(0, track_width)
        self.track_table.setColumnWidth(1, artist_width)
        self.track_table.setColumnWidth(2, album_width)
        self.track_table.setColumnWidth(3, duration_width)
        
        # Set minimum widths for professional look
        header.setMinimumSectionSize(120)
        
        # Hide row numbers and configure table behavior
        self.track_table.verticalHeader().setVisible(False)
        self.track_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.track_table.setAlternatingRowColors(False)
        
        # Set uniform row height to accommodate the labels properly
        self.track_table.verticalHeader().setDefaultSectionSize(40)  # Height for each row
        
        layout.addWidget(self.track_table)
        
        return container
    
    def create_buttons(self):
        button_layout = QHBoxLayout()
        button_layout.setSpacing(16)
        button_layout.setContentsMargins(0, 0, 0, 0)
        
        # Close button with subtle styling
        close_btn = QPushButton("Close")
        close_btn.setFixedSize(100, 44)
        close_btn.clicked.connect(self.close)
        close_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.05);
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 22px;
                color: #ffffff;
                font-size: 13px;
                font-weight: 600;
                font-family: 'SF Pro Text';
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.08);
                border-color: rgba(255, 255, 255, 0.15);
            }
            QPushButton:pressed {
                background: rgba(255, 255, 255, 0.02);
            }
        """)
        
        # Download missing tracks button with outline style
        download_btn = QPushButton("Download Missing Tracks")
        download_btn.setFixedSize(200, 44)
        download_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: 1px solid #1db954;
                border-radius: 22px;
                color: #1db954;
                font-size: 13px;
                font-weight: 600;
                font-family: 'SF Pro Text';
            }
            QPushButton:hover {
                background: rgba(29, 185, 84, 0.08);
                border-color: #1ed760;
                color: #1ed760;
            }
            QPushButton:pressed {
                background: rgba(29, 185, 84, 0.15);
            }
        """)
        
        # Sync button with primary styling (store reference for state management)
        self.sync_button = QPushButton("Sync This Playlist")
        self.sync_button.setFixedSize(160, 44)
        self.sync_button.setStyleSheet("""
            QPushButton {
                background: #1db954;
                border: none;
                border-radius: 22px;
                color: #ffffff;
                font-size: 13px;
                font-weight: 600;
                font-family: 'SF Pro Text';
            }
            QPushButton:hover {
                background: #1ed760;
            }
            QPushButton:pressed {
                background: #169c46;
            }
        """)
        
        # Connect button signals
        download_btn.clicked.connect(self.on_download_missing_tracks_clicked)
        self.sync_button.clicked.connect(self.on_sync_playlist_clicked)
        
        button_layout.addStretch()
        button_layout.addWidget(close_btn)
        button_layout.addWidget(download_btn)
        button_layout.addWidget(self.sync_button)
        
        return button_layout
    
    def format_duration(self, duration_ms):
        """Convert milliseconds to MM:SS format"""
        seconds = duration_ms // 1000
        minutes = seconds // 60
        seconds = seconds % 60
        return f"{minutes}:{seconds:02d}"
    
    def on_download_missing_tracks_clicked(self):
        """Handle Download Missing Tracks button click"""
        print("🔄 Download Missing Tracks button clicked!")

        if not self.playlist or not self.playlist.tracks:
            QMessageBox.warning(self, "Error", "Playlist tracks not loaded")
            return

        playlist_item_widget = self.parent_page.find_playlist_item_widget(self.playlist.id)
        if not playlist_item_widget:
            QMessageBox.critical(self, "Error", "Could not find the associated playlist item on the main page.")
            return

        print("🚀 Creating DownloadMissingTracksModal...")
        modal = DownloadMissingTracksModal(self.playlist, playlist_item_widget, self.parent_page, self.parent_page.downloads_page)

        playlist_item_widget.download_modal = modal

        # --- FIX: Connect the cleanup signal immediately upon creation. ---
        # This ensures that when the modal closes for any reason, the SyncPage
        # is notified and can run its cleanup logic.
        modal.process_finished.connect(
            lambda: self.parent_page.on_download_process_finished(self.playlist.id)
        )

        self.accept()
        modal.show()

    def find_playlist_item_from_sync_modal(self):
        """Find the PlaylistItem widget for this playlist from sync modal"""
        if not hasattr(self.parent_page, 'current_playlists'):
            return None
        
        # Look through the parent page's playlist items
        for i in range(self.parent_page.playlist_layout.count()):
            item = self.parent_page.playlist_layout.itemAt(i)
            if item and item.widget() and isinstance(item.widget(), PlaylistItem):
                playlist_item = item.widget()
                if playlist_item.playlist and playlist_item.playlist.id == self.playlist.id:
                    return playlist_item
        return None
    
    def on_sync_playlist_clicked(self):
        """Handle Sync This Playlist button click"""
        if self.is_syncing:
            # Cancel sync
            self.cancel_sync()
            return
        
        if not self.playlist:
            QMessageBox.warning(self, "Error", "No playlist selected")
            return
            
        if not self.playlist.tracks:
            QMessageBox.warning(self, "Error", "Playlist tracks not loaded")
            return
        
        # Check if sync service is available
        if not hasattr(self.parent_page, 'sync_service'):
            # Create sync service if not available
            from services.sync_service import PlaylistSyncService
            self.parent_page.sync_service = PlaylistSyncService(
                self.parent_page.spotify_client,
                self.parent_page.plex_client,
                self.parent_page.soulseek_client,
                getattr(self.parent_page, 'jellyfin_client', None)
            )
        
        # Start sync
        self.start_sync()

    def start_sync(self):
        """Start playlist sync operation via parent page"""
        if self.parent_page and self.parent_page.start_playlist_sync(self.playlist):
            self.is_syncing = True
            
            # Update modal UI state
            self.set_sync_button_state(True)
            
            # Show sync status widget
            if self.sync_status_widget:
                self.sync_status_widget.show()
                self.update_sync_status(len(self.playlist.tracks), 0, 0)

    def cancel_sync(self):
        """Cancel ongoing sync operation via parent page"""
        if self.parent_page and self.parent_page.cancel_playlist_sync(self.playlist.id):
            self.is_syncing = False
            
            # Update modal UI state
            self.set_sync_button_state(False)
            
            # Hide sync status widget
            if self.sync_status_widget:
                self.sync_status_widget.hide()

    def on_sync_progress(self, playlist_id, progress):
        """Handle sync progress updates (called from parent page)"""
        if playlist_id == self.playlist.id:
            # Update modal status display
            self.update_sync_status(
                progress.total_tracks,
                progress.matched_tracks,
                progress.failed_tracks
            )

    def on_sync_finished(self, playlist_id, result):
        """Handle sync completion (called from parent page)"""
        if playlist_id == self.playlist.id:
            self.is_syncing = False
            
            # Update button state
            self.set_sync_button_state(False)
            
            # Update final status
            self.update_sync_status(
                result.total_tracks,
                result.matched_tracks,
                result.failed_tracks
            )

    def on_sync_error(self, playlist_id, error_msg):
        """Handle sync error (called from parent page)"""
        if playlist_id == self.playlist.id:
            self.is_syncing = False
            
            # Update button state
            self.set_sync_button_state(False)
            
            # Hide sync status widget
            if self.sync_status_widget:
                self.sync_status_widget.hide()
            
            # Show error message
            QMessageBox.critical(self, "Sync Failed", f"Sync failed: {error_msg}")
    
    def start_playlist_missing_tracks_download(self):
        """Start the process of downloading missing tracks from playlist"""
        track_count = len(self.playlist.tracks)
        
        # Start analysis worker
        self.start_track_analysis()
        
        # Show analysis started message
        from config.settings import config_manager
        active_server = config_manager.get_active_media_server()
        server_name = active_server.title()
        QMessageBox.information(self, "Analysis Started", 
                              f"Starting analysis of {track_count} tracks.\nChecking {server_name} library for existing tracks...")
    
    def start_track_analysis(self):
        """Start background track analysis against media library"""
        # Create analysis worker
        from config.settings import config_manager
        active_server = config_manager.get_active_media_server()
        
        if active_server == "plex":
            media_client = getattr(self.parent_page, 'plex_client', None)
        else:  # jellyfin
            media_client = getattr(self.parent_page, 'jellyfin_client', None)
            
        worker = PlaylistTrackAnalysisWorker(self.playlist.tracks, media_client, active_server)
        
        # Connect signals
        worker.signals.analysis_started.connect(self.on_analysis_started)
        worker.signals.track_analyzed.connect(self.on_track_analyzed)
        worker.signals.analysis_completed.connect(self.on_analysis_completed)
        worker.signals.analysis_failed.connect(self.on_analysis_failed)
        
        # Track worker for cleanup
        self.active_workers.append(worker)
        
        # Submit to thread pool
        if hasattr(self.parent_page, 'thread_pool'):
            self.parent_page.thread_pool.start(worker)
        else:
            # Create and track fallback thread pool
            thread_pool = QThreadPool()
            self.fallback_pools.append(thread_pool)
            thread_pool.start(worker)
    
    def on_analysis_started(self, total_tracks):
        """Handle analysis started signal"""
        print(f"Started analyzing {total_tracks} tracks against Plex library")
    
    def on_track_analyzed(self, track_index, result):
        """Handle individual track analysis completion"""
        track = result.spotify_track
        if result.exists_in_plex:
            print(f"Track {track_index}: '{track.name}' by {track.artists[0]} EXISTS in Plex (confidence: {result.confidence:.2f})")
        else:
            print(f"Track {track_index}: '{track.name}' by {track.artists[0]} MISSING from Plex - will download")
    
    def on_analysis_completed(self, results):
        """Handle analysis completion and start downloads for missing tracks"""
        missing_tracks = [r for r in results if not r.exists_in_plex]
        existing_tracks = [r for r in results if r.exists_in_plex]
        
        print(f"Analysis complete: {len(missing_tracks)} missing, {len(existing_tracks)} existing")
        
        if not missing_tracks:
            QMessageBox.information(self, "Analysis Complete", 
                                  "All tracks already exist in Plex library!\nNo downloads needed.")
            return
        
        # Show results to user
        message = f"Analysis complete!\n\n"
        message += f"Tracks already in Plex: {len(existing_tracks)}\n"
        message += f"Tracks to download: {len(missing_tracks)}\n\n"
        message += "Ready to start downloading missing tracks?"
        
        reply = QMessageBox.question(self, "Start Downloads?", message,
                                   QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        
        if reply == QMessageBox.StandardButton.Yes:
            self.start_missing_track_downloads(missing_tracks)
    
    def on_analysis_failed(self, error_message):
        """Handle analysis failure"""
        QMessageBox.critical(self, "Analysis Failed", f"Failed to analyze tracks: {error_message}")
    
    def start_missing_track_downloads(self, missing_tracks):
        """Start downloading the missing tracks"""
        # TODO: Implement Soulseek search and download queueing
        # For now, just show what would be downloaded
        track_list = []
        for result in missing_tracks:
            track = result.spotify_track
            artist = track.artists[0] if track.artists else "Unknown Artist"
            track_list.append(f"• {track.name} by {artist}")
        
        message = f"Would download {len(missing_tracks)} tracks:\n\n"
        message += "\n".join(track_list[:10])  # Show first 10
        if len(track_list) > 10:
            message += f"\n... and {len(track_list) - 10} more"
        
        QMessageBox.information(self, "Downloads Queued", message)
    
    def load_tracks_async(self):
        """Load tracks asynchronously using worker thread"""
        if not self.spotify_client:
            return
        
        # Show loading state in track table
        if hasattr(self, 'track_table'):
            self.track_table.setRowCount(1)
            loading_item = QTableWidgetItem("Loading tracks...")
            loading_item.setFlags(loading_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.track_table.setItem(0, 0, loading_item)
            self.track_table.setSpan(0, 0, 1, 4)
        
        # Create and submit worker to thread pool
        worker = TrackLoadingWorker(self.spotify_client, self.playlist.id, self.playlist.name)
        worker.signals.tracks_loaded.connect(self.on_tracks_loaded)
        worker.signals.loading_failed.connect(self.on_tracks_loading_failed)
        
        # Track active worker for cleanup
        self.active_workers.append(worker)
        
        # Submit to parent's thread pool if available, otherwise create one
        if hasattr(self.parent_page, 'thread_pool'):
            self.parent_page.thread_pool.start(worker)
        else:
            # Create and track fallback thread pool
            thread_pool = QThreadPool()
            self.fallback_pools.append(thread_pool)
            thread_pool.start(worker)
    
    def on_tracks_loaded(self, playlist_id, tracks):
        """Handle successful track loading"""
        # Validate modal state before processing
        if (playlist_id == self.playlist.id and 
            not self.is_closing and 
            not self.isHidden() and 
            hasattr(self, 'track_table')):
            
            self.playlist.tracks = tracks
            
            # Cache tracks in parent for future use
            if hasattr(self.parent_page, 'track_cache'):
                self.parent_page.track_cache[playlist_id] = tracks
            
            # Refresh the track table
            self.refresh_track_table()
    
    def on_tracks_loading_failed(self, playlist_id, error_message):
        """Handle track loading failure"""
        # Validate modal state before processing
        if (playlist_id == self.playlist.id and 
            not self.is_closing and 
            not self.isHidden() and 
            hasattr(self, 'track_table')):
            self.track_table.setRowCount(1)
            error_item = QTableWidgetItem(f"Error loading tracks: {error_message}")
            error_item.setFlags(error_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.track_table.setItem(0, 0, error_item)
            self.track_table.setSpan(0, 0, 1, 4)
    
    def refresh_track_table(self):
        """Refresh the track table with loaded tracks"""
        if not hasattr(self, 'track_table'):
            return
            
        # Limit tracks to prevent UI blocking on large playlists
        total_tracks = len(self.playlist.tracks)
        display_limit = 100
        tracks_to_show = self.playlist.tracks[:display_limit]
        
        self.track_table.setRowCount(len(tracks_to_show))
        self.track_table.clearSpans()  # Remove any spans from loading state
        
        # Populate table with limited tracks
        for row, track in enumerate(tracks_to_show):
            # Track name with ellipsis label
            track_label = EllipsisLabel(track.name)
            track_label.setFont(QFont("SF Pro Text", 11, QFont.Weight.Medium))
            track_label.setStyleSheet("color: #ffffff; background: transparent; border: none;")
            self.track_table.setCellWidget(row, 0, track_label)
            
            # Artist(s) with ellipsis label
            artists = ", ".join(track.artists)
            artist_label = EllipsisLabel(artists)
            artist_label.setFont(QFont("SF Pro Text", 11))
            artist_label.setStyleSheet("color: #ffffff; background: transparent; border: none;")
            self.track_table.setCellWidget(row, 1, artist_label)
            
            # Album with ellipsis label
            album_label = EllipsisLabel(track.album)
            album_label.setFont(QFont("SF Pro Text", 11))
            album_label.setStyleSheet("color: #ffffff; background: transparent; border: none;")
            self.track_table.setCellWidget(row, 2, album_label)
            
            # Duration with standard item (doesn't need scrolling)
            duration = self.format_duration(track.duration_ms)
            duration_item = QTableWidgetItem(duration)
            duration_item.setFlags(duration_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            duration_item.setFont(QFont("SF Mono", 10))
            self.track_table.setItem(row, 3, duration_item)
        
        # Add info message if tracks were limited
        if total_tracks > display_limit:
            # Update the modal title to show track count info
            if hasattr(self, 'setWindowTitle'):
                original_title = f"Playlist Details - {self.playlist.name}"
                self.setWindowTitle(f"{original_title} (Showing {display_limit} of {total_tracks:,} tracks)")
            
            # Also show a subtle message at the bottom of the table
            print(f"📊 Playlist Details: Showing first {display_limit} of {total_tracks:,} tracks for better performance")

class PlaylistItem(QFrame):
    view_details_clicked = pyqtSignal(object)  # Signal to emit playlist object
    
    def __init__(self, name: str, track_count: int, sync_status: str, playlist=None, parent=None):
        super().__init__(parent)
        self.name = name
        self.track_count = track_count
        self.sync_status = sync_status
        self.playlist = playlist
        self.is_selected = False
        self.download_modal = None
        
        # Sync state tracking
        self.is_syncing = False
        self.sync_total_tracks = 0
        self.sync_matched_tracks = 0
        self.sync_failed_tracks = 0
        self.sync_status_widget = None
        
        # Selection state tracking
        self._pending_click = False
        
        self.setup_ui()
    
    def on_checkbox_clicked(self):
        """Handle direct checkbox click - use same debounced logic"""
        print(f"Direct checkbox click for {self.name}")
        self.toggle_selection()
    
    def update_selection_style(self):
        """Update visual style based on selection state"""
        if self.is_selected:
            self.setStyleSheet("""
                PlaylistItem {
                    background: rgba(29, 185, 84, 0.1);
                    border-radius: 8px;
                    border: 2px solid #1db954;
                }
                PlaylistItem:hover {
                    background: rgba(29, 185, 84, 0.15);
                    border: 2px solid #1ed760;
                }
            """)
        else:
            self.setStyleSheet("""
                PlaylistItem {
                    background: #282828;
                    border-radius: 8px;
                    border: 1px solid #404040;
                }
                PlaylistItem:hover {
                    background: #333333;
                    border: 1px solid #1db954;
                }
            """)
    
    def setup_ui(self):
        self.setFixedHeight(80)
        self.setStyleSheet("""
            PlaylistItem {
                background: #282828;
                border-radius: 8px;
                border: 1px solid #404040;
            }
            PlaylistItem:hover {
                background: #333333;
                border: 1px solid #1db954;
            }
        """)
        
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 15, 20, 15)
        layout.setSpacing(15)
        
        self.checkbox = QCheckBox()
        self.checkbox.clicked.connect(self.on_checkbox_clicked)
        self.checkbox.setStyleSheet("""
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                border-radius: 9px;
                border: 2px solid #b3b3b3;
                background: transparent;
            }
            QCheckBox::indicator:checked {
                background: #1db954;
                border: 2px solid #1db954;
            }
            QCheckBox::indicator:checked:hover {
                background: #1ed760;
            }
        """)
        
        content_layout = QVBoxLayout()
        content_layout.setSpacing(5)
        
        name_label = QLabel(self.name)
        name_label.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        name_label.setStyleSheet("color: #ffffff;")
        
        info_layout = QHBoxLayout()
        info_layout.setSpacing(20)
        
        track_label = QLabel(f"{self.track_count} tracks")
        track_label.setFont(QFont("Arial", 10))
        track_label.setStyleSheet("color: #b3b3b3;")
        
        # **FIX**: Renamed this to `sync_status_label` to avoid conflicts
        self.sync_status_label = QLabel(self.sync_status)
        self.sync_status_label.setFont(QFont("Arial", 10))
        if "Synced" in self.sync_status:
            self.sync_status_label.setStyleSheet("color: #1db954;")
        elif self.sync_status == "Needs Sync":
            self.sync_status_label.setStyleSheet("color: #ffa500;")
        else:
            self.sync_status_label.setStyleSheet("color: #e22134;")
        
        info_layout.addWidget(track_label)
        info_layout.addWidget(self.sync_status_label)
        info_layout.addStretch()
        
        content_layout.addWidget(name_label)
        content_layout.addLayout(info_layout)
        
        self.action_btn = QPushButton("Sync / Download")
        self.action_btn.setFixedSize(120, 30)
        self.action_btn.clicked.connect(self.on_action_clicked)
        self.action_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: 1px solid #1db954;
                border-radius: 15px;
                color: #1db954;
                font-size: 10px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #1db954;
                color: #000000;
            }
        """)
        
        # **FIX**: Renamed this to `operation_status_button` to avoid conflicts
        self.operation_status_button = QPushButton()
        self.operation_status_button.setFixedSize(120, 30)
        self.operation_status_button.setStyleSheet("""
            QPushButton {
                background: #1db954;
                border: 1px solid #169441;
                border-radius: 15px;
                color: #000000;
                font-size: 10px;
                font-weight: bold;
                padding: 5px;
                text-align: center;
            }
            QPushButton:hover {
                background: #1ed760;
                
            }
        """)
        self.operation_status_button.clicked.connect(self.on_status_clicked)
        self.operation_status_button.hide()
        
        self.download_modal = None
        self.sync_status_widget = self.create_compact_sync_status()
        
        layout.addWidget(self.checkbox)
        layout.addLayout(content_layout)
        layout.addStretch()
        layout.addWidget(self.sync_status_widget)
        layout.addWidget(self.action_btn)
        layout.addWidget(self.operation_status_button)
        
        self.installEventFilter(self)
        for child in self.findChildren(QWidget):
            if child != self.action_btn and child != self.operation_status_button:
                child.installEventFilter(self)
    
    def eventFilter(self, source, event):
        """Filter events to handle clicks anywhere on the item"""
        if event.type() == event.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            # **FIX**: Updated to check for the correctly named button
            if source == self.action_btn or source == self.operation_status_button:
                return False
            
            print(f"Event filter caught click on {source} in playlist {self.name}")
            self.toggle_selection()
            return True
        
        return super().eventFilter(source, event)
    
    def toggle_selection(self):
        """Toggle the selection state of this playlist item immediately"""
        if self._pending_click:
            return
            
        self._pending_click = True
        
        sync_page = self
        while sync_page and not isinstance(sync_page, SyncPage):
            sync_page = sync_page.parent()
        
        if sync_page and self.playlist and self.playlist.id:
            currently_selected = self.playlist.id in sync_page.selected_playlists
            sync_page.toggle_playlist_selection(self.playlist.id)
            new_state = self.playlist.id in sync_page.selected_playlists
            self.is_selected = new_state
            
            self.checkbox.blockSignals(True)
            self.checkbox.setChecked(new_state)
            self.checkbox.blockSignals(False)
            
            self.update_selection_style()
            print(f"Processed click for {self.name}: {currently_selected} -> {new_state}")
        else:
            print(f"Could not process click for {self.name} - missing sync page or playlist ID")
        
        QTimer.singleShot(25, lambda: setattr(self, '_pending_click', False))
    
    def mousePressEvent(self, event):
        """Handle direct clicks on the playlist item background"""
        if event.button() == Qt.MouseButton.LeftButton:
            print(f"Direct click on playlist item: {self.name}")
            self.toggle_selection()
        super().mousePressEvent(event)
    
    def sync_selection_state(self):
        """Synchronize selection state with parent SyncPage (call when needed)"""
        sync_page = self
        while sync_page and not isinstance(sync_page, SyncPage):
            sync_page = sync_page.parent()
        
        if sync_page and self.playlist and self.playlist.id:
            actual_selected = self.playlist.id in sync_page.selected_playlists
            
            if self.is_selected != actual_selected:
                print(f"Syncing state for {self.name}: {self.is_selected} -> {actual_selected}")
                self.is_selected = actual_selected
                
                self.checkbox.blockSignals(True)
                self.checkbox.setChecked(actual_selected)
                self.checkbox.blockSignals(False)
                
                self.update_selection_style()
    
    def create_compact_sync_status(self):
        """Create compact sync status display for playlist item"""
        sync_status = QFrame()
        sync_status.setFixedHeight(36)
        sync_status.setStyleSheet("""
            QFrame {
                background: rgba(29, 185, 84, 0.1);
                border: 1px solid rgba(29, 185, 84, 0.3);
                border-radius: 15px;
            }
        """)
        sync_status.hide()
        
        layout = QHBoxLayout(sync_status)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(6)
        
        self.item_total_tracks_label = QLabel("♪ 0")
        self.item_total_tracks_label.setFont(QFont("SF Pro Text", 9, QFont.Weight.Medium))
        self.item_total_tracks_label.setStyleSheet("color: #ffa500; background: transparent; border: none;")
        
        self.item_matched_tracks_label = QLabel("✓ 0")
        self.item_matched_tracks_label.setFont(QFont("SF Pro Text", 9, QFont.Weight.Medium))
        self.item_matched_tracks_label.setStyleSheet("color: #1db954; background: transparent; border: none;")
        
        self.item_failed_tracks_label = QLabel("✗ 0")
        self.item_failed_tracks_label.setFont(QFont("SF Pro Text", 9, QFont.Weight.Medium))
        self.item_failed_tracks_label.setStyleSheet("color: #e22134; background: transparent; border: none;")
        
        self.item_percentage_label = QLabel("0%")
        self.item_percentage_label.setFont(QFont("SF Pro Text", 9, QFont.Weight.Bold))
        self.item_percentage_label.setStyleSheet("color: #1db954; background: transparent; border: none;")
        
        layout.addWidget(self.item_total_tracks_label)
        
        item_sep1 = QLabel("/")
        item_sep1.setFont(QFont("SF Pro Text", 9, QFont.Weight.Medium))
        item_sep1.setStyleSheet("color: #666666; background: transparent; border: none;")
        layout.addWidget(item_sep1)
        
        layout.addWidget(self.item_matched_tracks_label)
        
        item_sep2 = QLabel("/")
        item_sep2.setFont(QFont("SF Pro Text", 9, QFont.Weight.Medium))
        item_sep2.setStyleSheet("color: #666666; background: transparent; border: none;")
        layout.addWidget(item_sep2)
        
        layout.addWidget(self.item_failed_tracks_label)
        
        item_sep3 = QLabel("/")
        item_sep3.setFont(QFont("SF Pro Text", 9, QFont.Weight.Medium))
        item_sep3.setStyleSheet("color: #666666; background: transparent; border: none;")
        layout.addWidget(item_sep3)
        
        layout.addWidget(self.item_percentage_label)
        
        return sync_status
    
    def update_sync_status(self, total_tracks=0, matched_tracks=0, failed_tracks=0):
        """Update sync status display for playlist item"""
        self.sync_total_tracks = total_tracks
        self.sync_matched_tracks = matched_tracks
        self.sync_failed_tracks = failed_tracks
        
        if self.sync_status_widget and hasattr(self, 'item_total_tracks_label'):
            self.item_total_tracks_label.setText(f"♪ {total_tracks}")
            self.item_matched_tracks_label.setText(f"✓ {matched_tracks}")
            self.item_failed_tracks_label.setText(f"✗ {failed_tracks}")
            
            if total_tracks > 0:
                processed_tracks = matched_tracks + failed_tracks
                percentage = int((processed_tracks / total_tracks) * 100)
                self.item_percentage_label.setText(f"{percentage}%")
            else:
                self.item_percentage_label.setText("0%")
            
            if total_tracks > 0 or self.is_syncing:
                self.sync_status_widget.show()
            else:
                self.sync_status_widget.hide()

    def show_operation_status(self, status_text="View Progress"):
        """Changes the button to show an operation is in progress."""
        # **FIX**: Updated to use the correctly named button
        self.operation_status_button.setText(status_text)
        self.operation_status_button.show()
        self.action_btn.hide()

    def hide_operation_status(self):
        """Resets the button to its default state."""
        # **FIX**: Updated to use the correctly named button
        self.operation_status_button.hide()
        self.action_btn.show()
    
    def on_action_clicked(self):
        """If a download is in progress, show the modal. Otherwise, open details."""
        if self.download_modal:
            self.download_modal.show()
            self.download_modal.activateWindow()
        else:
            self.view_details_clicked.emit(self.playlist)
    
    def update_operation_status(self, status_text):
        """Update the operation status text"""
        # **FIX**: Updated to use the correctly named button
        self.operation_status_button.setText(status_text)
    
    def set_download_modal(self, modal):
        """Store reference to the download modal"""
        self.download_modal = modal
    
    def update_sync_status_text(self, new_status):
        """Update the sync status text and style the label accordingly"""
        self.sync_status = new_status
        if hasattr(self, 'sync_status_label'):
            self.sync_status_label.setText(new_status)
            
            # Update color based on status
            if "Synced" in new_status:
                self.sync_status_label.setStyleSheet("color: #1db954;")
            elif new_status == "Needs Sync":
                self.sync_status_label.setStyleSheet("color: #ffa500;")
            else:
                self.sync_status_label.setStyleSheet("color: #e22134;")
    
    def on_status_clicked(self):
        """Handle status button click - reopen modal"""
        if self.download_modal and not self.download_modal.isVisible():
            self.download_modal.show()
            self.download_modal.activateWindow()
            self.download_modal.raise_()

class YouTubePlaylistCard(QFrame):
    """YouTube playlist card with persistent state tracking across all phases"""
    card_clicked = pyqtSignal(str, str)  # Signal: (url, phase)
    
    def __init__(self, url: str, playlist_name: str = "Loading...", track_count: int = 0, parent=None):
        super().__init__(parent)
        self.url = url
        self.playlist_name = playlist_name
        self.track_count = track_count
        self.phase = "discovering"  # discovering, discovery_complete, syncing, sync_complete, downloading, download_complete
        self.progress_data = {'total': 0, 'matched': 0, 'failed': 0}
        
        # Modal references
        self.discovery_modal = None
        self.download_modal = None
        
        # State data
        self.playlist_data = None
        self.discovered_tracks = []
        
        self.setup_ui()
        self.update_display()
    
    def setup_ui(self):
        self.setFixedHeight(80)
        self.setStyleSheet("""
            YouTubePlaylistCard {
                background: #282828;
                border-radius: 8px;
                border: 1px solid #404040;
            }
            YouTubePlaylistCard:hover {
                background: #333333;
                border: 1px solid #ff0000;
            }
        """)
        
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 15, 20, 15)
        layout.setSpacing(15)
        
        # YouTube icon indicator
        yt_icon = QLabel("▶")
        yt_icon.setFixedSize(24, 24)
        yt_icon.setStyleSheet("""
            QLabel {
                color: #ff0000;
                font-size: 16px;
                font-weight: bold;
                background: transparent;
                text-align: center;
                border-radius: 12px;
                border: 1px solid #ff0000;
            }
        """)
        yt_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        # Content layout
        content_layout = QVBoxLayout()
        content_layout.setSpacing(5)
        
        self.name_label = QLabel(self.playlist_name)
        self.name_label.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        self.name_label.setStyleSheet("color: #ffffff;")
        
        info_layout = QHBoxLayout()
        info_layout.setSpacing(20)
        
        self.track_label = QLabel(f"{self.track_count} tracks")
        self.track_label.setFont(QFont("Arial", 10))
        self.track_label.setStyleSheet("color: #b3b3b3;")
        
        self.phase_label = QLabel(self.get_phase_text())
        self.phase_label.setFont(QFont("Arial", 10))
        self.update_phase_style()
        
        info_layout.addWidget(self.track_label)
        info_layout.addWidget(self.phase_label)
        info_layout.addStretch()
        
        content_layout.addWidget(self.name_label)
        content_layout.addLayout(info_layout)
        
        # Progress status widget (similar to PlaylistItem)
        self.progress_widget = self.create_progress_display()
        self.progress_widget.hide()  # Initially hidden
        
        # Action button
        self.action_btn = QPushButton(self.get_action_text())
        self.action_btn.setFixedSize(120, 30)
        self.action_btn.clicked.connect(self.on_action_clicked)
        self.action_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: 1px solid #ff0000;
                border-radius: 15px;
                color: #ff0000;
                font-size: 10px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #ff0000;
                color: #ffffff;
            }
        """)
        
        layout.addWidget(yt_icon)
        layout.addLayout(content_layout)
        layout.addWidget(self.progress_widget)
        layout.addWidget(self.action_btn)
    
    def create_progress_display(self):
        """Create sync status display widget like PlaylistItem"""
        sync_status = QFrame()
        sync_status.setFixedHeight(30)
        sync_status.setStyleSheet("""
            QFrame {
                background: rgba(0, 0, 0, 0.3);
                border-radius: 15px;
                border: 1px solid rgba(255, 255, 255, 0.1);
            }
        """)
        
        layout = QHBoxLayout(sync_status)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(8)
        
        # Create labels for progress display
        self.total_tracks_label = QLabel(f"♪ {self.progress_data['total']}")
        self.total_tracks_label.setFont(QFont("SF Pro Text", 9, QFont.Weight.Medium))
        self.total_tracks_label.setStyleSheet("color: #b3b3b3; background: transparent; border: none;")
        layout.addWidget(self.total_tracks_label)
        
        sep1 = QLabel("/")
        sep1.setFont(QFont("SF Pro Text", 9, QFont.Weight.Medium))
        sep1.setStyleSheet("color: #666666; background: transparent; border: none;")
        layout.addWidget(sep1)
        
        self.matched_tracks_label = QLabel(f"✓ {self.progress_data['matched']}")
        self.matched_tracks_label.setFont(QFont("SF Pro Text", 9, QFont.Weight.Medium))
        self.matched_tracks_label.setStyleSheet("color: #1db954; background: transparent; border: none;")
        layout.addWidget(self.matched_tracks_label)
        
        sep2 = QLabel("/")
        sep2.setFont(QFont("SF Pro Text", 9, QFont.Weight.Medium))
        sep2.setStyleSheet("color: #666666; background: transparent; border: none;")
        layout.addWidget(sep2)
        
        self.failed_tracks_label = QLabel(f"✗ {self.progress_data['failed']}")
        self.failed_tracks_label.setFont(QFont("SF Pro Text", 9, QFont.Weight.Medium))
        self.failed_tracks_label.setStyleSheet("color: #e22134; background: transparent; border: none;")
        layout.addWidget(self.failed_tracks_label)
        
        sep3 = QLabel("/")
        sep3.setFont(QFont("SF Pro Text", 9, QFont.Weight.Medium))
        sep3.setStyleSheet("color: #666666; background: transparent; border: none;")
        layout.addWidget(sep3)
        
        self.percentage_label = QLabel("0%")
        self.percentage_label.setFont(QFont("SF Pro Text", 9, QFont.Weight.Medium))
        self.percentage_label.setStyleSheet("color: #ffffff; background: transparent; border: none;")
        layout.addWidget(self.percentage_label)
        
        return sync_status
    
    def get_phase_text(self):
        """Get display text for current phase"""
        phase_texts = {
            'discovering': 'Discovering tracks...',
            'discovery_complete': 'Discovery complete',
            'syncing': 'Syncing...',
            'sync_complete': 'Sync complete',
            'downloading': 'Downloading...',
            'download_complete': 'Complete'
        }
        return phase_texts.get(self.phase, self.phase)
    
    def get_action_text(self):
        """Get action button text based on phase"""
        action_texts = {
            'discovering': 'View Progress',
            'discovery_complete': 'View Details',
            'syncing': 'View Progress',
            'sync_complete': 'Download Missing',
            'downloading': 'View Downloads',
            'download_complete': 'View Results'
        }
        return action_texts.get(self.phase, 'Open')
    
    def update_phase_style(self):
        """Update phase label color based on current phase"""
        phase_colors = {
            'discovering': '#ffa500',        # Orange
            'discovery_complete': '#1db954',  # Green
            'syncing': '#ffa500',            # Orange  
            'sync_complete': '#1db954',       # Green
            'downloading': '#ffa500',         # Orange
            'download_complete': '#1db954'    # Green
        }
        color = phase_colors.get(self.phase, '#b3b3b3')
        self.phase_label.setStyleSheet(f"color: {color};")
    
    def update_display(self):
        """Update all display elements based on current state"""
        self.name_label.setText(self.playlist_name)
        self.track_label.setText(f"{self.track_count} tracks")
        self.phase_label.setText(self.get_phase_text())
        self.action_btn.setText(self.get_action_text())
        self.update_phase_style()
    
    def set_phase(self, phase: str):
        """Update the current phase and refresh display"""
        self.phase = phase
        self.update_display()
        
        # Show/hide progress widget based on phase
        if phase in ['syncing', 'downloading', 'sync_complete']:
            print(f"🎬 Card phase set to {phase} - showing progress widget")
            self.progress_widget.show()
            self.action_btn.hide()
            # For syncing phase, initialize with current progress data
            if phase == 'syncing':
                # Ensure we show some initial progress data
                if self.progress_data['total'] == 0:
                    # Initialize with track count if available
                    self.progress_data['total'] = self.track_count
                    self.total_tracks_label.setText(f"♪ {self.progress_data['total']}")
                self.matched_tracks_label.setText(f"✓ {self.progress_data['matched']}")
                self.failed_tracks_label.setText(f"✗ {self.progress_data['failed']}")
            # For sync_complete, hide progress after a delay to show final results
            elif phase == 'sync_complete':
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(5000, lambda: self.progress_widget.hide() if self.phase == 'sync_complete' else None)
                QTimer.singleShot(5000, lambda: self.action_btn.show() if self.phase == 'sync_complete' else None)
        else:
            print(f"🎬 Card phase set to {phase} - hiding progress widget")
            self.progress_widget.hide()
            self.action_btn.show()
    
    def update_progress(self, total=None, matched=None, failed=None):
        """Update progress data and display"""
        print(f"🎬 Card update_progress called: total={total}, matched={matched}, failed={failed}, phase={self.phase}")
        
        if total is not None:
            self.progress_data['total'] = total
        if matched is not None:
            self.progress_data['matched'] = matched
        if failed is not None:
            self.progress_data['failed'] = failed
        
        # Update labels
        self.total_tracks_label.setText(f"♪ {self.progress_data['total']}")
        self.matched_tracks_label.setText(f"✓ {self.progress_data['matched']}")
        self.failed_tracks_label.setText(f"✗ {self.progress_data['failed']}")
        
        # Ensure progress widget is visible when progress is being updated
        # This ensures live status display is always shown during active operations
        if self.phase in ['syncing', 'downloading']:
            print(f"🎬 Card in {self.phase} phase - ensuring progress widget is visible")
            self.progress_widget.show()
            self.action_btn.hide()
        else:
            print(f"🎬 Card not in active phase ({self.phase}) - progress widget state unchanged")
        
        # Calculate percentage
        total = self.progress_data['total']
        if total > 0:
            processed = self.progress_data['matched'] + self.progress_data['failed']
            percentage = int((processed / total) * 100)
            self.percentage_label.setText(f"{percentage}%")
        else:
            self.percentage_label.setText("0%")
    
    def update_playlist_info(self, name: str, track_count: int):
        """Update playlist name and track count"""
        self.playlist_name = name
        self.track_count = track_count
        self.update_display()
    
    def set_playlist_data(self, data):
        """Store discovered playlist data"""
        self.playlist_data = data
        if hasattr(data, 'tracks'):
            self.discovered_tracks = data.tracks
            self.track_count = len(data.tracks)
            self.update_display()
    
    def on_action_clicked(self):
        """Handle action button click - emit signal with current phase"""
        self.card_clicked.emit(self.url, self.phase)
    
    def mousePressEvent(self, event):
        """Handle card clicks"""
        if event.button() == Qt.MouseButton.LeftButton:
            self.card_clicked.emit(self.url, self.phase)
        super().mousePressEvent(event)

class SyncOptionsPanel(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_ui()
    
    def setup_ui(self):
        self.setStyleSheet("""
            SyncOptionsPanel {
                background: #282828;
                border-radius: 8px;
                border: 1px solid #404040;
            }
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)
        
        # Title
        title_label = QLabel("Sync Options")
        title_label.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        title_label.setStyleSheet("color: #ffffff;")
        
        # Download missing tracks option
        self.download_missing = QCheckBox("Download missing tracks from Soulseek")
        self.download_missing.setChecked(True)
        self.download_missing.setStyleSheet("""
            QCheckBox {
                color: #ffffff;
                font-size: 11px;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border-radius: 8px;
                border: 2px solid #b3b3b3;
                background: transparent;
            }
            QCheckBox::indicator:checked {
                background: #1db954;
                border: 2px solid #1db954;
            }
        """)
        
        # Quality selection
        quality_layout = QHBoxLayout()
        quality_label = QLabel("Preferred Quality:")
        quality_label.setStyleSheet("color: #b3b3b3; font-size: 11px;")
        
        self.quality_combo = QComboBox()
        self.quality_combo.addItems(["FLAC", "320 kbps MP3", "256 kbps MP3", "Any"])
        self.quality_combo.setCurrentText("FLAC")
        self.quality_combo.setStyleSheet("""
            QComboBox {
                background: #404040;
                border: 1px solid #606060;
                border-radius: 4px;
                padding: 5px;
                color: #ffffff;
                font-size: 11px;
            }
            QComboBox::drop-down {
                border: none;
            }
            QComboBox::down-arrow {
                image: none;
                border: none;
            }
        """)
        
        quality_layout.addWidget(quality_label)
        quality_layout.addWidget(self.quality_combo)
        quality_layout.addStretch()
        
        layout.addWidget(title_label)
        layout.addWidget(self.download_missing)
        layout.addLayout(quality_layout)

class SyncPage(QWidget):
    # Signals for dashboard activity tracking
    sync_activity = pyqtSignal(str, str, str, str)  # icon, title, subtitle, time
    database_updated_externally = pyqtSignal()
    
    def __init__(self, spotify_client=None, plex_client=None, soulseek_client=None, downloads_page=None, jellyfin_client=None, parent=None):
        super().__init__(parent)
        self.spotify_client = spotify_client
        self.plex_client = plex_client
        self.jellyfin_client = jellyfin_client
        self.soulseek_client = soulseek_client
        self.downloads_page = downloads_page
        self.sync_statuses = load_sync_status()
        self.current_playlists = []
        self.playlist_loader = None
        self.active_download_processes = {}
        # Track cache for performance
        self.track_cache = {}  # playlist_id -> tracks
        
        # Sync worker management 
        self.active_sync_workers = {}  # playlist_id -> SyncWorker (for individual modal syncs)
        self.sequential_sync_worker = None  # Current sequential sync worker
        
        # Selection tracking
        self.selected_playlists = set()  # Set of selected playlist IDs
        self.sequential_sync_queue = []  # Queue for sequential syncing
        self.is_sequential_syncing = False
        
        # Thread pool for async operations (like downloads.py)
        self.thread_pool = QThreadPool()
        self.thread_pool.setMaxThreadCount(3)  # Limit concurrent Spotify API calls
        
        # YouTube playlist tracking
        self.active_youtube_processes = {}  # URL -> modal instance
        self.youtube_worker = None  # Current parsing worker
        self.youtube_status_widgets = {}  # playlist_id -> status widget
        
        # YouTube playlist download modal references for reopening
        self.active_youtube_download_modals = {}  # playlist_id -> modal instance
        
        # YouTube playlist card hub system
        self.youtube_playlist_states = {}  # url -> {phase, data, card, modals}
        self.youtube_cards = {}  # url -> YouTubePlaylistCard instance
        self.youtube_cards_container = None  # Container for all YouTube cards
        
        # Initialize unified media scan manager
        self.scan_manager = None
        try:
            from core.media_scan_manager import MediaScanManager
            self.scan_manager = MediaScanManager(delay_seconds=60)
            # Add automatic incremental database update after scan completion
            self.scan_manager.add_scan_completion_callback(self._on_media_scan_completed)
            logger.info("✅ MediaScanManager initialized for SyncPage")
        except Exception as e:
            logger.error(f"Failed to initialize MediaScanManager: {e}")
        
        self.setup_ui()
        
        # Don't auto-load on startup, but do auto-load when page becomes visible
        self.show_initial_state()
        self.playlists_loaded = False
    
    def set_toast_manager(self, toast_manager):
        """Set the toast manager for showing notifications"""
        self.toast_manager = toast_manager
    
    def _on_media_scan_completed(self):
        """Callback triggered when media scan completes - start automatic incremental database update"""
        try:
            # Import here to avoid circular imports
            from database import get_database
            from core.database_update_worker import DatabaseUpdateWorker
            from config.settings import config_manager
            
            # Get the active media client
            active_server = config_manager.get_active_media_server()
            if active_server == "jellyfin":
                media_client = getattr(self, 'jellyfin_client', None)
            else:
                media_client = getattr(self, 'plex_client', None)
            
            # Check if we should run incremental update
            if not media_client or not media_client.is_connected():
                logger.debug(f"{active_server.upper()} not connected - skipping automatic database update")
                return
            
            # Check if database has a previous full refresh
            database = get_database()
            last_full_refresh = database.get_last_full_refresh()
            if not last_full_refresh:
                logger.info("No previous full refresh found - skipping automatic incremental update")
                return
            
            # Check if database has sufficient content
            try:
                stats = database.get_database_info()
                track_count = stats.get('tracks', 0)
                
                if track_count < 100:
                    logger.info(f"Database has only {track_count} tracks - skipping automatic incremental update")
                    return
            except Exception as e:
                logger.warning(f"Could not check database stats - skipping automatic update: {e}")
                return
            
            # All conditions met - start incremental update
            logger.info(f"🎵 Starting automatic incremental database update after {active_server.upper()} scan")
            self._start_automatic_incremental_update()
            
        except Exception as e:
            logger.error(f"Error in media scan completion callback: {e}")
    
    def _start_automatic_incremental_update(self):
        """Start the automatic incremental database update"""
        try:
            from core.database_update_worker import DatabaseUpdateWorker
            
            # Avoid duplicate workers
            if hasattr(self, '_auto_database_worker') and self._auto_database_worker and self._auto_database_worker.isRunning():
                logger.debug("Automatic database update already running")
                return
            
            # Create worker for incremental update only
            self._auto_database_worker = DatabaseUpdateWorker(
                self.plex_client,
                "database/music_library.db",
                full_refresh=False  # Always incremental for automatic updates
            )
            
            # Connect completion signal to log result
            self._auto_database_worker.finished.connect(self._on_auto_update_finished)
            self._auto_database_worker.error.connect(self._on_auto_update_error)
            
            # Start the update
            self._auto_database_worker.start()
            
        except Exception as e:
            logger.error(f"Error starting automatic incremental update: {e}")
    
    def _on_auto_update_finished(self, total_artists, total_albums, total_tracks, successful, failed):
        """Handle completion of automatic database update"""
        try:
            if successful > 0:
                logger.info(f"✅ Automatic database update completed: {successful} items processed successfully")
            else:
                logger.info("💡 Automatic database update completed - no new content found")
            
            # Emit the signal to notify the dashboard to refresh its statistics
            self.database_updated_externally.emit()
            logger.info("📊 Emitted signal to refresh dashboard database statistics after auto update")
            
            # Clean up the worker
            if hasattr(self, '_auto_database_worker'):
                self._auto_database_worker.deleteLater()
                delattr(self, '_auto_database_worker')
                
        except Exception as e:
            logger.error(f"Error handling automatic update completion: {e}")
    
    def _on_auto_update_error(self, error_message):
        """Handle error in automatic database update"""
        logger.warning(f"Automatic database update encountered an error: {error_message}")
        
        # Clean up the worker
        if hasattr(self, '_auto_database_worker'):
            self._auto_database_worker.deleteLater()
            delattr(self, '_auto_database_worker')

    def _update_and_save_sync_status(self, playlist_id, result, snapshot_id):
        """Updates the sync status for a given playlist and saves to file."""
        # THE FIX: This function will now run even if there are failed tracks,
        # ensuring the sync time and snapshot_id are always recorded.
        playlist_obj = next((p for p in self.current_playlists if p.id == playlist_id), None)
        
        if playlist_obj:
            now = datetime.now()
            self.sync_statuses[playlist_id] = {
                'name': playlist_obj.name,
                'owner': playlist_obj.owner,
                'snapshot_id': snapshot_id,
                'last_synced': now.isoformat()
            }
            save_sync_status(self.sync_statuses)
            
            # This now targets the correct label for real-time UI updates
            playlist_item = self.find_playlist_item_widget(playlist_id)
            if playlist_item and hasattr(playlist_item, 'sync_status_label'):
                new_status_text = f"Synced: {now.strftime('%b %d, %H:%M')}"
                playlist_item.sync_status_label.setText(new_status_text)
                playlist_item.sync_status_label.setStyleSheet("color: #1db954;")

    def is_playlist_syncing(self, playlist_id):
        """Check if a playlist is currently syncing"""
        return playlist_id in self.active_sync_workers
    
    def get_playlist_sync_worker(self, playlist_id):
        """Get the sync worker for a playlist if it exists"""
        return self.active_sync_workers.get(playlist_id)
    
    def start_playlist_sync(self, playlist):
        """Start sync for a playlist (called from modal)"""
        if playlist.id in self.active_sync_workers:
            # Already syncing
            return False
        
        # Create sync service if not available
        if not hasattr(self, 'sync_service'):
            from services.sync_service import PlaylistSyncService
            self.sync_service = PlaylistSyncService(
                self.spotify_client,
                self.plex_client,
                self.soulseek_client,
                getattr(self, 'jellyfin_client', None)
            )
        
        # Create sync worker
        sync_worker = SyncWorker(
            playlist=playlist,
            sync_service=self.sync_service
        )
        
        # Connect worker signals
        sync_worker.signals.finished.connect(lambda result, sid: self.on_sync_finished(playlist.id, result, sid))

        sync_worker.signals.error.connect(lambda error: self.on_sync_error(playlist.id, error))
        sync_worker.signals.progress.connect(lambda progress: self.on_sync_progress(playlist.id, progress))
        
        # Store the worker
        self.active_sync_workers[playlist.id] = sync_worker
        
        # Emit activity signal for sync start
        self.sync_activity.emit("🔄", "Sync Started", f"Syncing playlist '{playlist.name}'", "Now")
        
        # Show toast notification for sync start
        if hasattr(self, 'toast_manager') and self.toast_manager:
            track_count = len(playlist.tracks) if hasattr(playlist, 'tracks') else 0
            if track_count > 0:
                self.toast_manager.show_toast(f"Starting sync for '{playlist.name}' ({track_count} tracks)", ToastType.INFO)
        
        # Start the worker
        self.thread_pool.start(sync_worker)
        
        # Update playlist item status
        playlist_item = self.find_playlist_item_widget(playlist.id)
        if playlist_item:
            playlist_item.is_syncing = True
            playlist_item.update_sync_status(len(playlist.tracks), 0, 0)
        
        # Log start
        if hasattr(self, 'log_area'):
            self.log_area.append(f"🔄 Starting sync for playlist: {playlist.name}")
        
        # Update refresh button state since we now have an active sync
        self.update_refresh_button_state()
        
        return True
    
    def start_sequential_playlist_sync(self, playlist):
        """Start sync for a playlist as part of sequential sync (separate from individual syncs)"""
        # Create sync service if not available
        if not hasattr(self, 'sync_service'):
            from services.sync_service import PlaylistSyncService
            self.sync_service = PlaylistSyncService(
                self.spotify_client,
                self.plex_client,
                self.soulseek_client,
                getattr(self, 'jellyfin_client', None)
            )
        
        # Create sync worker for sequential sync
        sync_worker = SyncWorker(
            playlist=playlist,
            sync_service=self.sync_service
        )
        
        # Connect worker signals for sequential sync
        sync_worker.signals.finished.connect(lambda result, sid: self.on_sequential_sync_finished(playlist.id, result, sid))
        sync_worker.signals.error.connect(lambda error: self.on_sequential_sync_error(playlist.id, error))
        sync_worker.signals.progress.connect(lambda progress: self.on_sync_progress(playlist.id, progress))
        
        # Store the sequential sync worker
        self.sequential_sync_worker = sync_worker
        
        # Start the worker
        self.thread_pool.start(sync_worker)
        
        # Update playlist item status
        playlist_item = self.find_playlist_item_widget(playlist.id)
        if playlist_item:
            playlist_item.is_syncing = True
            playlist_item.update_sync_status(len(playlist.tracks), 0, 0)
        
        # Log start
        if hasattr(self, 'log_area'):
            self.log_area.append(f"🔄 Starting sequential sync for playlist: {playlist.name}")
        
        # Show toast notification for sequential sync start
        if hasattr(self, 'toast_manager') and self.toast_manager:
            track_count = len(playlist.tracks) if hasattr(playlist, 'tracks') else 0
            if track_count > 0:
                self.toast_manager.show_toast(f"Starting sequential sync for '{playlist.name}' ({track_count} tracks)", ToastType.INFO)
        
        return True
    
    def toggle_playlist_selection(self, playlist_id):
        """Toggle selection state of a playlist"""
        if playlist_id in self.selected_playlists:
            self.selected_playlists.remove(playlist_id)
            print(f"Deselected playlist: {playlist_id}")
        else:
            self.selected_playlists.add(playlist_id)
            print(f"Selected playlist: {playlist_id}")
        
        print(f"Total selected: {len(self.selected_playlists)}")
        self.update_selection_ui()
    
    def update_selection_ui(self):
        """Update the selection info label and button state"""
        selected_count = len(self.selected_playlists)
        
        print(f"Updating UI with {selected_count} selected playlists, sequential syncing: {self.is_sequential_syncing}, individual syncs: {len(self.active_sync_workers)}")
        
        if selected_count == 0:
            self.selection_info.setText("Select playlists to sync")
            self.start_sync_btn.setEnabled(False)
            print("Button disabled - no selection")
        elif self.has_active_operations():
            # Don't change button state during any active operations
            print(f"Active operations in progress - keeping button as is")
        elif selected_count == 1:
            self.selection_info.setText("1 playlist selected")
            self.start_sync_btn.setEnabled(True)
            print("Button enabled - 1 playlist")
        else:
            self.selection_info.setText(f"{selected_count} playlists selected")
            self.start_sync_btn.setEnabled(True)
            print(f"Button enabled - {selected_count} playlists")
    
    def start_selected_playlist_sync(self):
        """Start syncing all selected playlists sequentially"""
        if not self.selected_playlists or self.is_sequential_syncing:
            return
        
        # Don't allow sequential sync if individual syncs are already running
        if self.active_sync_workers:
            print(f"DEBUG: Cannot start sequential sync - {len(self.active_sync_workers)} individual syncs are running")
            return
        
        # Get selected playlist objects
        selected_playlist_objects = []
        for playlist_item in self.get_all_playlist_items():
            if playlist_item.playlist.id in self.selected_playlists:
                selected_playlist_objects.append(playlist_item.playlist)
        
        if not selected_playlist_objects:
            return
        
        # Start sequential sync
        self.sequential_sync_queue = selected_playlist_objects.copy()
        self.is_sequential_syncing = True
        self.start_sync_btn.setText("Syncing...")
        self.start_sync_btn.setEnabled(False)
        
        # Disable refresh button during sequential sync
        self.update_refresh_button_state()
        
        # Start first sync
        self.process_next_in_sync_queue()
    
    def process_next_in_sync_queue(self):
        """Process the next playlist in the sequential sync queue."""
        print(f"DEBUG: process_next_in_sync_queue - queue length: {len(self.sequential_sync_queue)}, is_syncing: {self.is_sequential_syncing}")
        
        if self.sequential_sync_queue and self.is_sequential_syncing:
            # Get next playlist to sync
            next_playlist = self.sequential_sync_queue.pop(0)
            print(f"DEBUG: Starting sync for next playlist: {next_playlist.name}")
            
            # Start sync for this playlist
            if not self.start_sequential_playlist_sync(next_playlist):
                # If sync failed to start, immediately process the next one
                print("DEBUG: Sync failed to start, moving to next playlist")
                self.process_next_in_sync_queue()
        else:
            # If queue is empty or sync was cancelled, call the final completion handler
            print("DEBUG: Sequential sync queue is empty or syncing stopped - calling completion handler.")
            self.on_sequential_sync_complete()
    
    def on_sequential_sync_complete(self):
        """Handle completion of the entire sequential sync process."""
        # Ensure this runs only once at the very end
        if not self.is_sequential_syncing:
            return

        print("DEBUG: Sequential sync process complete. Resetting all states.")
        self.is_sequential_syncing = False
        self.sequential_sync_queue.clear()
        self.sequential_sync_worker = None # Ensure worker is cleared
        
        # Reset the button text and state authoritatively
        self.start_sync_btn.setText("Start Sync")
        
        # Update the entire UI based on the new, correct state
        self.update_selection_ui()
        self.update_refresh_button_state()
    
    def on_sequential_sync_finished(self, playlist_id, result, snapshot_id):
        """Handle completion of individual playlist in sequential sync"""
        print(f"DEBUG: Sequential sync finished for playlist {playlist_id}")

        # Clear sequential sync worker
        self.sequential_sync_worker = None

        # Update playlist item status
        playlist_item = self.find_playlist_item_widget(playlist_id)
        if playlist_item:
            playlist_item.is_syncing = False
            playlist_item.update_sync_status(
                result.total_tracks,
                result.matched_tracks,
                result.failed_tracks
            )

            # Hide status widget after completion with delay
            QTimer.singleShot(3000, lambda: playlist_item.sync_status_widget.hide() if playlist_item.sync_status_widget else None)

        # Update any open modals
        self.update_open_modals_completion(playlist_id, result)

        # Pass the snapshot_id to the save function
        self._update_and_save_sync_status(playlist_id, result, snapshot_id)

        # Log completion
        if hasattr(self, 'log_area'):
            success_rate = result.success_rate
            msg = f"✅ Sequential sync complete: {result.synced_tracks}/{result.total_tracks} tracks synced ({success_rate:.1f}%)"
            if result.failed_tracks > 0:
                msg += f", {result.failed_tracks} failed"
            self.log_area.append(msg)
        
        # Show toast notification for sequential sync completion
        if hasattr(self, 'toast_manager') and self.toast_manager:
            playlist_item = self.find_playlist_item_widget(playlist_id)
            playlist_name = playlist_item.name if playlist_item else "Unknown Playlist"
            if result.failed_tracks > 0:
                self.toast_manager.show_toast(f"'{playlist_name}' sync completed: {result.matched_tracks}/{result.total_tracks} tracks, {result.failed_tracks} failed", ToastType.WARNING)
            else:
                self.toast_manager.show_toast(f"'{playlist_name}' sync completed: {result.matched_tracks} tracks added", ToastType.SUCCESS)
            
        # **THE FIX**: Defer processing the next item to allow the event loop to catch up.
        # This ensures UI updates (like the status label) are processed before moving on.
        if self.is_sequential_syncing:
            print(f"DEBUG: Scheduling next playlist in sequence.")
            QTimer.singleShot(10, self.process_next_in_sync_queue)
    
    def on_sequential_sync_error(self, playlist_id, error_msg):
        """Handle error in individual playlist during sequential sync"""
        print(f"DEBUG: Sequential sync error for playlist {playlist_id}: {error_msg}")
        
        # Clear sequential sync worker
        self.sequential_sync_worker = None
        
        # Update playlist item status
        playlist_item = self.find_playlist_item_widget(playlist_id)
        if playlist_item:
            playlist_item.is_syncing = False
            if playlist_item.sync_status_widget:
                playlist_item.sync_status_widget.hide()
        
        # Update any open modals
        self.update_open_modals_error(playlist_id, error_msg)
        
        # Log error
        if hasattr(self, 'log_area'):
            self.log_area.append(f"❌ Sequential sync failed: {error_msg}")
        
        # Show toast notification for sequential sync error
        if hasattr(self, 'toast_manager') and self.toast_manager:
            playlist_item = self.find_playlist_item_widget(playlist_id)
            playlist_name = playlist_item.name if playlist_id else "Unknown Playlist"
            self.toast_manager.show_toast(f"Sequential sync failed for '{playlist_name}': {error_msg}", ToastType.ERROR)

        # **THE FIX**: Defer processing the next item to allow the event loop to catch up.
        if self.is_sequential_syncing:
            print(f"DEBUG: Scheduling next playlist in sequence despite error.")
            QTimer.singleShot(10, self.process_next_in_sync_queue)
    
    def get_all_playlist_items(self):
        """Get all PlaylistItem widgets from the playlist layout"""
        playlist_items = []
        for i in range(self.playlist_layout.count()):
            item = self.playlist_layout.itemAt(i)
            widget = item.widget()
            if isinstance(widget, PlaylistItem):
                playlist_items.append(widget)
        return playlist_items
    
    def cancel_playlist_sync(self, playlist_id):
        """Cancel sync for a playlist"""
        if playlist_id in self.active_sync_workers:
            worker = self.active_sync_workers[playlist_id]
            worker.cancel()
            
            # Remove from active workers
            del self.active_sync_workers[playlist_id]
            
            # Update playlist item status
            playlist_item = self.find_playlist_item_widget(playlist_id)
            if playlist_item:
                playlist_item.is_syncing = False
                if playlist_item.sync_status_widget:
                    playlist_item.sync_status_widget.hide()
            
            # Log cancellation
            if hasattr(self, 'log_area'):
                self.log_area.append(f"🚫 Sync cancelled for playlist")
            
            return True
        return False
    
    def on_sync_progress(self, playlist_id, progress):
        """Handle sync progress updates"""
        print(f"🚀 PARENT PAGE on_sync_progress called! playlist_id={playlist_id}")
        print(f"🚀 Progress: total={progress.total_tracks}, matched={progress.matched_tracks}, failed={progress.failed_tracks}")
        
        # Update playlist item status (for Spotify playlists)
        playlist_item = self.find_playlist_item_widget(playlist_id)
        if playlist_item:
            print(f"🚀 Found playlist item widget, updating status")
            playlist_item.update_sync_status(
                progress.total_tracks,
                progress.matched_tracks,
                progress.failed_tracks
            )
        else:
            print(f"🚀 No playlist item widget found for playlist_id: {playlist_id}")
        
        # Update YouTube card progress (for YouTube playlists)
        # Find the YouTube card by matching playlist IDs
        youtube_card_updated = False
        print(f"🎬 Searching for YouTube card with playlist_id: {playlist_id}")
        for url, state in self.youtube_playlist_states.items():
            playlist_data = state.get('playlist_data')
            if playlist_data and hasattr(playlist_data, 'id'):
                print(f"🎬 Checking YouTube card: URL={url}, stored playlist_id={playlist_data.id}")
                if playlist_data.id == playlist_id:
                    print(f"🎬 ✅ Found matching YouTube card for playlist_id: {playlist_id}, updating progress")
                    self.update_youtube_card_progress(
                        url,
                        total=progress.total_tracks,
                        matched=progress.matched_tracks,
                        failed=progress.failed_tracks
                    )
                    youtube_card_updated = True
                    break
                else:
                    print(f"🎬 ❌ Playlist ID mismatch: {playlist_data.id} != {playlist_id}")
            else:
                print(f"🎬 YouTube card state missing playlist_data or id: URL={url}")
        
        if not youtube_card_updated:
            print(f"🎬 ❌ No matching YouTube card found for playlist_id: {playlist_id}")
        
        if not playlist_item and not youtube_card_updated:
            print(f"🚀 No playlist widget OR YouTube card found for playlist_id: {playlist_id}")
        
        # Update any open modal for this playlist
        print(f"🚀 About to call update_open_modals_progress")
        self.update_open_modals_progress(playlist_id, progress)
    
    def on_sync_finished(self, playlist_id, result, snapshot_id):
        """Handle sync completion"""
        # Remove from active workers
        if playlist_id in self.active_sync_workers:
            del self.active_sync_workers[playlist_id]

        # Update playlist item status (for Spotify playlists)
        playlist_item = self.find_playlist_item_widget(playlist_id)
        playlist_name = "Unknown Playlist"
        
        if playlist_item:
            playlist_item.is_syncing = False
            playlist_item.update_sync_status(
                result.total_tracks,
                result.matched_tracks,
                result.failed_tracks
            )
            playlist_name = playlist_item.name
            # Hide status widget after completion with delay
            QTimer.singleShot(3000, lambda: playlist_item.sync_status_widget.hide() if playlist_item.sync_status_widget else None)

        # Update YouTube card status (for YouTube playlists)
        youtube_card_updated = False
        for url, state in self.youtube_playlist_states.items():
            playlist_data = state.get('playlist_data')
            if playlist_data and hasattr(playlist_data, 'id') and playlist_data.id == playlist_id:
                print(f"🎬 YouTube sync finished for playlist_id: {playlist_id}, updating card to sync_complete")
                self.update_youtube_card_phase(url, 'sync_complete')
                self.update_youtube_card_progress(
                    url,
                    total=result.total_tracks,
                    matched=result.matched_tracks,
                    failed=result.failed_tracks
                )
                playlist_name = playlist_data.name
                youtube_card_updated = True
                break

        # Update any open modals
        self.update_open_modals_completion(playlist_id, result)

        # Pass the snapshot_id to the save function
        self._update_and_save_sync_status(playlist_id, result, snapshot_id)
        
        # Emit activity signal for sync completion
        success_msg = f"Completed: {result.matched_tracks}/{result.total_tracks} tracks"
        self.sync_activity.emit("✅", "Sync Complete", f"'{playlist_name}' - {success_msg}", "Now")
        
        # Show toast notification for sync completion
        if hasattr(self, 'toast_manager') and self.toast_manager:
            if result.failed_tracks > 0:
                self.toast_manager.show_toast(f"Sync completed: {result.matched_tracks}/{result.total_tracks} tracks added, {result.failed_tracks} failed", ToastType.WARNING)
            else:
                self.toast_manager.show_toast(f"Sync completed: {result.matched_tracks} tracks added to queue", ToastType.SUCCESS)

        # Continue sequential sync if in progress
        if self.is_sequential_syncing:
            print(f"DEBUG: Sync finished for {playlist_id}, continuing sequential sync")
            self.process_next_in_sync_queue()
        else:
            print(f"DEBUG: Sync finished for {playlist_id}, not in sequential sync mode")

        # Update refresh button state since a sync completed
        self.update_refresh_button_state()

        # Log completion
        if hasattr(self, 'log_area'):
            success_rate = result.success_rate
            msg = f"✅ Sync complete: {result.synced_tracks}/{result.total_tracks} tracks synced ({success_rate:.1f}%)"
            if result.failed_tracks > 0:
                msg += f", {result.failed_tracks} failed"
            self.log_area.append(msg)
    
    def on_sync_error(self, playlist_id, error_msg):
        """Handle sync error"""
        # Remove from active workers
        if playlist_id in self.active_sync_workers:
            del self.active_sync_workers[playlist_id]
        
        # Update playlist item status
        playlist_item = self.find_playlist_item_widget(playlist_id)
        if playlist_item:
            playlist_item.is_syncing = False
            if playlist_item.sync_status_widget:
                playlist_item.sync_status_widget.hide()
        
        # Update any open modals
        self.update_open_modals_error(playlist_id, error_msg)
        
        # Emit activity signal for sync error
        playlist_name = playlist_item.name if playlist_item else "Unknown Playlist"
        self.sync_activity.emit("❌", "Sync Failed", f"'{playlist_name}' - {error_msg}", "Now")
        
        # Show toast notification for sync error
        if hasattr(self, 'toast_manager') and self.toast_manager:
            self.toast_manager.show_toast(f"Sync failed for '{playlist_name}': {error_msg}", ToastType.ERROR)
        
        # Continue sequential sync if in progress (even on error)
        if self.is_sequential_syncing:
            self.process_next_in_sync_queue()
        
        # Update refresh button state since a sync completed (with error)
        self.update_refresh_button_state()
        
        # Log error
        if hasattr(self, 'log_area'):
            self.log_area.append(f"❌ Sync failed: {error_msg}")
    
    def update_open_modals_progress(self, playlist_id, progress):
        """Update any open modals for this playlist with sync progress"""
        print(f"🔍 Looking for modals to update progress for playlist_id: {playlist_id}")
        print(f"🔍 Progress data: total={progress.total_tracks}, matched={progress.matched_tracks}, failed={progress.failed_tracks}")
        
        # Find all open modal instances for this playlist
        from PyQt6.QtWidgets import QApplication
        youtube_modals_found = 0
        spotify_modals_found = 0
        
        for widget in QApplication.topLevelWidgets():
            widget_name = type(widget).__name__
            print(f"🔍 Checking widget: {widget_name}")
            
            # Handle PlaylistDetailsModal
            if isinstance(widget, PlaylistDetailsModal):
                if hasattr(widget, 'playlist'):
                    widget_playlist_id = getattr(widget.playlist, 'id', 'NO_ID')
                    is_visible = widget.isVisible()
                    print(f"🔍 Spotify modal: playlist_id={widget_playlist_id}, visible={is_visible}, target={playlist_id}")
                    
                    if widget_playlist_id == playlist_id and is_visible:
                        print(f"📊 Updating Spotify modal progress: {playlist_id}")
                        spotify_modals_found += 1
                        widget.on_sync_progress(playlist_id, progress)
                else:
                    print(f"🔍 Spotify modal without playlist attribute")
            
            # Handle YouTubeDownloadMissingTracksModal
            elif isinstance(widget, YouTubeDownloadMissingTracksModal):
                youtube_modals_found += 1
                if hasattr(widget, 'playlist'):
                    widget_playlist_id = getattr(widget.playlist, 'id', 'NO_ID')
                    is_visible = widget.isVisible()
                    print(f"🔍 YouTube modal #{youtube_modals_found}: playlist_id={widget_playlist_id}, visible={is_visible}, target={playlist_id}")
                    
                    if widget_playlist_id == playlist_id:
                        print(f"📊 ✅ Found matching YouTube modal for playlist_id: {playlist_id}, calling on_sync_progress")
                        # Update the YouTube modal's progress display (even if hidden)
                        widget.on_sync_progress(playlist_id, progress)
                    else:
                        print(f"📊 ❌ YouTube modal playlist_id mismatch: {widget_playlist_id} vs {playlist_id}")
                else:
                    print(f"🔍 YouTube modal #{youtube_modals_found} without playlist attribute")
        
        print(f"🔍 Summary: Found {spotify_modals_found} Spotify modals, {youtube_modals_found} YouTube modals total")
    
    def update_open_modals_completion(self, playlist_id, result):
        """Update any open modals for this playlist with sync completion"""
        from PyQt6.QtWidgets import QApplication
        for widget in QApplication.topLevelWidgets():
            # Handle PlaylistDetailsModal
            if (isinstance(widget, PlaylistDetailsModal) and 
                hasattr(widget, 'playlist') and 
                widget.playlist.id == playlist_id and
                widget.isVisible()):
                # Update the modal's completion display
                widget.on_sync_finished(playlist_id, result)
            
            # Handle YouTubeDownloadMissingTracksModal
            elif (isinstance(widget, YouTubeDownloadMissingTracksModal) and 
                  hasattr(widget, 'playlist') and 
                  widget.playlist.id == playlist_id):
                # Update the YouTube modal's completion display (even if hidden)
                widget.on_sync_finished(playlist_id, result)
    
    def update_open_modals_error(self, playlist_id, error_msg):
        """Update any open modals for this playlist with sync error"""
        from PyQt6.QtWidgets import QApplication
        for widget in QApplication.topLevelWidgets():
            # Handle PlaylistDetailsModal
            if (isinstance(widget, PlaylistDetailsModal) and 
                hasattr(widget, 'playlist') and 
                widget.playlist.id == playlist_id and
                widget.isVisible()):
                # Update the modal's error display
                widget.on_sync_error(playlist_id, error_msg)
            
            # Handle YouTubeDownloadMissingTracksModal
            elif (isinstance(widget, YouTubeDownloadMissingTracksModal) and 
                  hasattr(widget, 'playlist') and 
                  widget.playlist.id == playlist_id):
                # Update the YouTube modal's error display (even if hidden)
                widget.on_sync_error(playlist_id, error_msg)
    
    # Add these three methods inside the SyncPage class
    def find_playlist_item_widget(self, playlist_id):
        """Finds the PlaylistItem widget in the UI that corresponds to a given playlist ID."""
        for i in range(self.playlist_layout.count()):
            item = self.playlist_layout.itemAt(i)
            widget = item.widget()
            if isinstance(widget, PlaylistItem) and widget.playlist.id == playlist_id:
                return widget
        return None

    def on_download_process_started(self, playlist_id, playlist_item_widget):
        """Disables refresh button and updates the playlist item UI."""
        print(f"Download process started for playlist: {playlist_id}. Disabling refresh.")
        self.active_download_processes[playlist_id] = playlist_item_widget
        playlist_item_widget.show_operation_status()
        
        # Use centralized refresh button management
        self.update_refresh_button_state()
        # --- FIX: Connect the finished signal from the modal ---
        # This ensures that when the modal is finished (or cancelled), the cleanup function is called.
        if playlist_item_widget.download_modal:
            playlist_item_widget.download_modal.process_finished.connect(
                lambda: self.on_download_process_finished(playlist_id)
            )

    def on_download_process_finished(self, playlist_id):
        """Re-enables refresh button if no other downloads are active."""
        print(f"Download process finished or cancelled for playlist: {playlist_id}.")
        
        # Skip refresh button updates for YouTube workflows (they don't affect Spotify playlist refresh)
        if playlist_id.startswith("youtube_"):
            print(f"Ignoring YouTube workflow finish for refresh button: {playlist_id}")
            return
        
        # Clear download modal reference even if not in active_download_processes
        playlist_item_widget = None
        if playlist_id in self.active_download_processes:
            playlist_item_widget = self.active_download_processes.pop(playlist_id)
        else:
            # Find the playlist item widget even if not in active processes
            playlist_item_widget = self.find_playlist_item_widget(playlist_id)
        
        # --- FIX: Reset the UI state of the playlist item ---
        if playlist_item_widget:
            playlist_item_widget.download_modal = None
            playlist_item_widget.hide_operation_status()

        if not self.active_download_processes:
            print("All download processes finished. Re-enabling refresh.")
            # Use centralized refresh button management
            self.update_refresh_button_state()
    
    
    def showEvent(self, event):
        """Auto-load playlists when page becomes visible (but not during app startup)"""
        super().showEvent(event)
        
        # Only auto-load once and only if we have a spotify client
        if (not self.playlists_loaded and 
            self.spotify_client and 
            self.spotify_client.is_authenticated()):
            
            # Small delay to ensure UI is fully rendered
            QTimer.singleShot(100, self.auto_load_playlists)
    
    def auto_load_playlists(self):
        """Auto-load playlists with proper UI transition"""
        # Clear the welcome state first
        self.clear_playlists()
        
        # Clear selection state when auto-loading
        self.selected_playlists.clear()
        self.update_selection_ui()
        
        # Start loading (this will set playlists_loaded = True)
        self.load_playlists_async()
    
    def show_initial_state(self):
        """Show initial state with option to load playlists"""
        # Add welcome message to playlist area
        welcome_message = QLabel("Ready to sync playlists!\nClick 'Load Playlists' to get started.")
        welcome_message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        welcome_message.setStyleSheet("""
            QLabel {
                color: #b3b3b3;
                font-size: 16px;
                padding: 60px;
                background: #282828;
                border-radius: 12px;
                border: 1px solid #404040;
                line-height: 1.5;
            }
        """)
        
        # Add load button
        load_btn = QPushButton("🎵 Load Playlists")
        load_btn.setFixedSize(200, 50)
        load_btn.clicked.connect(self.load_playlists_async)
        load_btn.setStyleSheet("""
            QPushButton {
                background: #1db954;
                border: none;
                border-radius: 25px;
                color: #000000;
                font-size: 14px;
                font-weight: bold;
                margin-top: 20px;
            }
            QPushButton:hover {
                background: #1ed760;
            }
        """)
        
        # Add them to the playlist layout  
        if hasattr(self, 'playlist_layout'):
            self.playlist_layout.addWidget(welcome_message)
            self.playlist_layout.addWidget(load_btn)
            self.playlist_layout.addStretch()
    
    def setup_ui(self):
        self.setStyleSheet("""
            SyncPage {
                background: #191414;
            }
        """)
        
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(30, 30, 30, 30)
        main_layout.setSpacing(25)
        
        # Header
        header = self.create_header()
        main_layout.addWidget(header)
        
        # Content area
        content_layout = QHBoxLayout()
        content_layout.setSpacing(15)  # Reduced from 25 to 15 for tighter spacing
        
        # Left side - Tabbed playlist section
        playlist_section = self.create_tabbed_playlist_section()
        content_layout.addWidget(playlist_section, 2)
        
        # Right side - Options and actions
        right_sidebar = self.create_right_sidebar()
        content_layout.addWidget(right_sidebar, 1)
        
        main_layout.addLayout(content_layout, 1)  # Allow content to stretch
    
    def create_header(self):
        header = QWidget()
        layout = QVBoxLayout(header)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        
        # Title
        title_label = QLabel("Playlist Sync")
        title_label.setFont(QFont("Arial", 28, QFont.Weight.Bold))
        title_label.setStyleSheet("color: #ffffff;")
        
        # Subtitle
        subtitle_label = QLabel("Synchronize your Spotify playlists with Plex")
        subtitle_label.setFont(QFont("Arial", 14))
        subtitle_label.setStyleSheet("color: #b3b3b3;")
        
        layout.addWidget(title_label)
        layout.addWidget(subtitle_label)
        
        return header
    
    def create_playlist_section(self):
        section = QWidget()
        layout = QVBoxLayout(section)
        layout.setSpacing(15)
        
        # Section header
        header_layout = QHBoxLayout()
        
        section_title = QLabel("Spotify Playlists")
        section_title.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        section_title.setStyleSheet("color: #ffffff;")
        
        self.refresh_btn = QPushButton("🔄 Refresh")
        self.refresh_btn.setFixedSize(100, 35)
        self.refresh_btn.clicked.connect(self.load_playlists_async)
        self.refresh_btn.setStyleSheet("""
            QPushButton {
                background: #1db954;
                border: none;
                border-radius: 17px;
                color: #000000;
                font-size: 11px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #1ed760;
            }
            QPushButton:pressed {
                background: #1aa34a;
            }
        """)
        
        header_layout.addWidget(section_title)
        header_layout.addStretch()
        header_layout.addWidget(self.refresh_btn)
        
        # Playlist container
        playlist_container = QScrollArea()
        playlist_container.setWidgetResizable(True)
        playlist_container.setStyleSheet("""
            QScrollArea {
                border: none;
                background: transparent;
            }
            QScrollBar:vertical {
                background: #282828;
                width: 8px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical {
                background: #1db954;
                border-radius: 4px;
            }
        """)
        
        self.playlist_widget = QWidget()
        self.playlist_layout = QVBoxLayout(self.playlist_widget)
        self.playlist_layout.setSpacing(10)
        
        # Playlists will be loaded asynchronously after UI setup
        
        self.playlist_layout.addStretch()
        playlist_container.setWidget(self.playlist_widget)
        
        layout.addLayout(header_layout)
        layout.addWidget(playlist_container)
        
        return section
    
    def create_tabbed_playlist_section(self):
        """Create tabbed section with Spotify and YouTube playlist tabs"""
        section = QWidget()
        layout = QVBoxLayout(section)
        layout.setSpacing(0)
        
        # Create tab widget
        self.playlist_tabs = QTabWidget()
        self.playlist_tabs.setStyleSheet("""
            QTabWidget::pane {
                border: 1px solid #404040;
                border-radius: 8px;
                background: #282828;
                margin: 0px;
            }
            QTabWidget::tab-bar {
                alignment: center;
            }
            QTabBar::tab {
                background: #181818;
                color: #b3b3b3;
                border: 1px solid #404040;
                border-bottom: none;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
                padding: 12px 24px;
                margin-right: 2px;
                font-size: 13px;
                font-weight: bold;
                min-width: 120px;
            }
            QTabBar::tab:selected {
                background: #1db954;
                color: #000000;
                border-color: #1db954;
            }
            QTabBar::tab:hover:!selected {
                background: #404040;
                color: #ffffff;
            }
        """)
        
        # Create Spotify tab (move existing functionality here)
        spotify_tab = self.create_spotify_playlist_tab()
        self.playlist_tabs.addTab(spotify_tab, "Spotify Playlists")
        
        # Create YouTube tab (placeholder for now)
        youtube_tab = self.create_youtube_playlist_tab()
        self.playlist_tabs.addTab(youtube_tab, "YouTube Playlists")
        
        # Set default to Spotify tab
        self.playlist_tabs.setCurrentIndex(0)
        
        layout.addWidget(self.playlist_tabs)
        
        return section
    
    def create_spotify_playlist_tab(self):
        """Create the Spotify playlist tab (existing functionality)"""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(15)
        layout.setContentsMargins(15, 15, 15, 15)
        
        # Section header (same as before)
        header_layout = QHBoxLayout()
        
        section_title = QLabel("Your Spotify Playlists")
        section_title.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        section_title.setStyleSheet("color: #ffffff;")
        
        self.refresh_btn = QPushButton("🔄 Refresh")
        self.refresh_btn.setFixedSize(100, 35)
        self.refresh_btn.clicked.connect(self.load_playlists_async)
        self.refresh_btn.setStyleSheet("""
            QPushButton {
                background: #1db954;
                border: none;
                border-radius: 17px;
                color: #000000;
                font-size: 11px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #1ed760;
            }
            QPushButton:pressed {
                background: #1aa34a;
            }
        """)
        
        header_layout.addWidget(section_title)
        header_layout.addStretch()
        header_layout.addWidget(self.refresh_btn)
        
        # Playlist container (same as before)
        playlist_container = QScrollArea()
        playlist_container.setWidgetResizable(True)
        playlist_container.setStyleSheet("""
            QScrollArea {
                border: none;
                background: transparent;
            }
            QScrollBar:vertical {
                background: #282828;
                width: 8px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical {
                background: #1db954;
                border-radius: 4px;
            }
        """)
        
        self.playlist_widget = QWidget()
        self.playlist_layout = QVBoxLayout(self.playlist_widget)
        self.playlist_layout.setSpacing(10)
        
        # Playlists will be loaded asynchronously after UI setup
        
        self.playlist_layout.addStretch()
        playlist_container.setWidget(self.playlist_widget)
        
        layout.addLayout(header_layout)
        layout.addWidget(playlist_container)
        
        return tab
    
    def create_youtube_playlist_tab(self):
        """Create the YouTube playlist tab (placeholder for future implementation)"""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(20)
        layout.setContentsMargins(15, 15, 15, 15)
        
        # Header
        header_label = QLabel("YouTube Music Playlists")
        header_label.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        header_label.setStyleSheet("color: #ffffff;")
        
        # URL input section
        url_section = QFrame()
        url_section.setStyleSheet("""
            QFrame {
                background: #181818;
                border: 1px solid #404040;
                border-radius: 8px;
                padding: 20px;
            }
        """)
        
        url_layout = QVBoxLayout(url_section)
        url_layout.setSpacing(15)
        
        url_label = QLabel("Paste YouTube Music Playlist URL:")
        url_label.setFont(QFont("Arial", 12))
        url_label.setStyleSheet("color: #b3b3b3;")
        
        self.youtube_url_input = QLineEdit()
        self.youtube_url_input.setPlaceholderText("https://music.youtube.com/playlist?list=...")
        self.youtube_url_input.setStyleSheet("""
            QLineEdit {
                background: #282828;
                border: 1px solid #404040;
                border-radius: 6px;
                padding: 12px;
                color: #ffffff;
                font-size: 12px;
            }
            QLineEdit:focus {
                border-color: #1db954;
            }
        """)
        
        self.parse_btn = QPushButton("Parse Playlist")
        self.parse_btn.setFixedHeight(40)
        self.parse_btn.clicked.connect(self.parse_youtube_playlist)
        self.parse_btn.setStyleSheet("""
            QPushButton {
                background: #1db954;
                border: none;
                border-radius: 20px;
                color: #000000;
                font-size: 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #1ed760;
            }
            QPushButton:pressed {
                background: #1aa34a;
            }
            QPushButton:disabled {
                background: #404040;
                color: #666666;
            }
        """)
        
        url_layout.addWidget(url_label)
        url_layout.addWidget(self.youtube_url_input)
        url_layout.addWidget(self.parse_btn)
        
        # Content area that will show placeholder or status widget
        self.youtube_content_area = QFrame()
        self.youtube_content_area.setStyleSheet("""
            QFrame {
                background: #181818;
                border: 1px solid #404040;
                border-radius: 8px;
            }
        """)
        
        self.youtube_content_layout = QVBoxLayout(self.youtube_content_area)
        self.youtube_content_layout.setContentsMargins(20, 20, 20, 20)
        
        # Initial placeholder content
        self.show_youtube_placeholder()
        
        
        # Add everything to main layout
        layout.addWidget(header_label)
        layout.addWidget(url_section)
        layout.addWidget(self.youtube_content_area, 1)  # Stretch to fill remaining space
        
        return tab
    
    def show_youtube_placeholder(self):
        """Show the placeholder content in YouTube tab"""
        # Clear existing content
        for i in reversed(range(self.youtube_content_layout.count())):
            child = self.youtube_content_layout.itemAt(i).widget()
            if child:
                child.setParent(None)
        
        placeholder_label = QLabel("YouTube playlist tracks will appear here")
        placeholder_label.setFont(QFont("Arial", 12))
        placeholder_label.setStyleSheet("color: #666666;")
        placeholder_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.youtube_content_layout.addStretch()  # Add stretch before to center
        self.youtube_content_layout.addWidget(placeholder_label)
        self.youtube_content_layout.addStretch()  # Add stretch after to center
    
    def show_youtube_download_status(self, playlist_name, track_count, playlist_id=None):
        """Show download status widget in YouTube tab - styled like PlaylistItem"""
        print(f"📋 show_youtube_download_status called with playlist_id: {playlist_id}")
        
        if playlist_id is None:
            playlist_id = f"youtube_{hash(playlist_name)}"
        
        # If a status widget for this playlist already exists, do nothing.
        if playlist_id in self.youtube_status_widgets:
            print(f"📋 Status widget for {playlist_id} already exists. No action taken.")
            return

        # --- THE FIX ---
        # The destructive loop that cleared the layout has been removed.
        # By the time this function is called, the placeholder is already gone
        # and the main card container is in place. There is no need to clear anything.
        # This function now only ADDS the status widget, preserving the main card.

        # Create playlist-style status widget (the "green card")
        status_widget = QFrame()
        status_widget.setFixedHeight(80)
        status_widget.setStyleSheet("""
            QFrame {
                background: #282828;
                border-radius: 8px;
                border: 1px solid #404040;
            }
            QFrame:hover {
                background: #333333;
                border: 1px solid #1db954;
            }
        """)
        status_widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        
        layout = QHBoxLayout(status_widget)
        layout.setContentsMargins(20, 15, 20, 15)
        layout.setSpacing(15)
        
        # Status icon (instead of checkbox)
        status_icon = QLabel("🎵")
        status_icon.setFont(QFont("Arial", 18))
        status_icon.setFixedSize(22, 22)
        status_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        # Content section (playlist name and info)
        content_layout = QVBoxLayout()
        content_layout.setSpacing(5)
        
        name_label = QLabel(playlist_name)
        name_label.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        name_label.setStyleSheet("color: #ffffff;")
        
        info_layout = QHBoxLayout()
        info_layout.setSpacing(20)
        
        track_label = QLabel(f"{track_count} tracks")
        track_label.setFont(QFont("Arial", 10))
        track_label.setStyleSheet("color: #b3b3b3;")
        
        status_label = QLabel("Downloading...")
        status_label.setFont(QFont("Arial", 10))
        status_label.setStyleSheet("color: #1db954;")
        
        info_layout.addWidget(track_label)
        info_layout.addWidget(status_label)
        info_layout.addStretch()
        
        content_layout.addWidget(name_label)
        content_layout.addLayout(info_layout)
        
        # View Progress button
        view_progress_btn = QPushButton("View Progress")
        view_progress_btn.setFixedSize(120, 30)
        view_progress_btn.clicked.connect(lambda: self.open_youtube_download_modal(playlist_id))
        view_progress_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: 1px solid #1db954;
                border-radius: 15px;
                color: #1db954;
                font-size: 10px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #1db954;
                color: #000000;
            }
        """)
        
        layout.addWidget(status_icon)
        layout.addLayout(content_layout)
        layout.addStretch()
        layout.addWidget(view_progress_btn)
        
        # Store widget reference and add it to the top of the layout
        self.youtube_status_widgets[playlist_id] = status_widget
        self.youtube_content_layout.insertWidget(0, status_widget)
    
    
    def open_youtube_download_modal(self, playlist_id):
        """Open the YouTube download modal when View Progress button is clicked"""
        print(f"🔍 Attempting to open modal for playlist_id: {playlist_id}")
        print(f"🔍 Available modals: {list(self.active_youtube_download_modals.keys())}")
        
        if playlist_id in self.active_youtube_download_modals:
            modal = self.active_youtube_download_modals[playlist_id]
            print(f"✅ Found modal, opening...")
            modal.show()
            modal.raise_()
            modal.activateWindow()
        else:
            print(f"❌ No modal found for playlist_id: {playlist_id}")
    
    def create_right_sidebar(self):
        section = QWidget()
        layout = QVBoxLayout(section)
        layout.setSpacing(20)
        
        # Action buttons
        actions_frame = QFrame()
        actions_frame.setStyleSheet("""
            QFrame {
                background: #282828;
                border-radius: 8px;
                border: 1px solid #404040;
            }
        """)
        
        actions_layout = QVBoxLayout(actions_frame)
        actions_layout.setContentsMargins(20, 20, 20, 20)
        actions_layout.setSpacing(15)
        
        # Selection info label
        self.selection_info = QLabel("Select playlists to sync")
        self.selection_info.setFont(QFont("Arial", 12))
        self.selection_info.setStyleSheet("color: #b3b3b3;")
        self.selection_info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        # Sync button (initially disabled)
        self.start_sync_btn = QPushButton("Start Sync")
        self.start_sync_btn.setFixedHeight(45)
        self.start_sync_btn.setEnabled(False)  # Disabled by default
        self.start_sync_btn.clicked.connect(self.start_selected_playlist_sync)
        self.start_sync_btn.setStyleSheet("""
            QPushButton {
                background: #1db954;
                border: none;
                border-radius: 22px;
                color: #000000;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover:enabled {
                background: #1ed760;
            }
            QPushButton:pressed:enabled {
                background: #1aa34a;
            }
            QPushButton:disabled {
                background: #404040;
                color: #666666;
            }
        """)
        
        actions_layout.addWidget(self.selection_info)
        actions_layout.addWidget(self.start_sync_btn)
        
        layout.addWidget(actions_frame)
        
        # Progress section below buttons
        progress_section = self.create_progress_section()
        layout.addWidget(progress_section, 1)  # Allow progress section to stretch
        
        return section
    
    def create_progress_section(self):
        section = QFrame()
        section.setMinimumHeight(200)  # Set minimum height instead of fixed
        section.setStyleSheet("""
            QFrame {
                background: #282828;
                border-radius: 8px;
                border: 1px solid #404040;
            }
        """)
        
        layout = QVBoxLayout(section)
        layout.setContentsMargins(20, 15, 20, 15)
        layout.setSpacing(10)
        
        # Progress header
        progress_header = QLabel("Sync Progress")
        progress_header.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        progress_header.setStyleSheet("color: #ffffff;")
        
        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(8)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: none;
                border-radius: 4px;
                background: #404040;
            }
            QProgressBar::chunk {
                background: #1db954;
                border-radius: 4px;
            }
        """)
        
        # Progress text
        self.progress_text = QLabel("Ready to sync...")
        self.progress_text.setFont(QFont("Arial", 11))
        self.progress_text.setStyleSheet("color: #b3b3b3;")
        
        # Log area
        self.log_area = QTextEdit()
        self.log_area.setMinimumHeight(80)  # Set minimum height instead of maximum
        
        # Override append method to limit to 200 lines
        original_append = self.log_area.append
        def limited_append(text):
            original_append(text)
            # Keep only last 200 lines
            text_content = self.log_area.toPlainText()
            lines = text_content.split('\n')
            if len(lines) > 200:
                trimmed_lines = lines[-200:]
                self.log_area.setPlainText('\n'.join(trimmed_lines))
                # Move cursor to end
                cursor = self.log_area.textCursor()
                cursor.movePosition(cursor.MoveOperation.End)
                self.log_area.setTextCursor(cursor)
        self.log_area.append = limited_append
        
        self.log_area.setStyleSheet("""
            QTextEdit {
                background: #181818;
                border: 1px solid #404040;
                border-radius: 4px;
                color: #ffffff;
                font-size: 10px;
                font-family: monospace;
            }
        """)
        self.log_area.setPlainText("Waiting for sync to start...")
        
        layout.addWidget(progress_header)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.progress_text)
        layout.addWidget(self.log_area, 1)  # Allow log area to stretch
        
        return section
    
    def load_playlists_async(self):
        """Start asynchronous playlist loading"""
        if self.playlist_loader and self.playlist_loader.isRunning():
            return
        
        # Mark as loaded to prevent duplicate auto-loading
        self.playlists_loaded = True
        
        # Clear existing playlists
        self.clear_playlists()
        
        # Clear selection state when refreshing
        self.selected_playlists.clear()
        self.update_selection_ui()
        
        # Add loading placeholder
        loading_label = QLabel("🔄 Loading playlists...")
        loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        loading_label.setStyleSheet("""
            QLabel {
                color: #b3b3b3;
                font-size: 14px;
                padding: 40px;
                background: #282828;
                border-radius: 8px;
                border: 1px solid #404040;
            }
        """)
        self.playlist_layout.insertWidget(0, loading_label)
        
        # Show loading state
        self.refresh_btn.setText("🔄 Loading...")
        self.refresh_btn.setEnabled(False)
        self.log_area.append("Starting playlist loading...")
        
        # Create and start loader thread
        self.playlist_loader = PlaylistLoaderThread(self.spotify_client)
        self.playlist_loader.playlist_loaded.connect(self.add_playlist_to_ui)
        self.playlist_loader.loading_finished.connect(self.on_loading_finished)
        self.playlist_loader.loading_failed.connect(self.on_loading_failed)
        self.playlist_loader.progress_updated.connect(self.update_progress)
        self.playlist_loader.start()
    
    def add_playlist_to_ui(self, playlist):
        """Add a single playlist to the UI as it's loaded"""
        # Start with simple sync status to avoid datetime operations during loading
        sync_status = "Checking..."
        item = PlaylistItem(playlist.name, playlist.total_tracks, sync_status, playlist, self)
        
        # Queue sync status update for after UI creation
        QTimer.singleShot(0, lambda: self.update_playlist_sync_status(item, playlist))
        item.view_details_clicked.connect(self.show_playlist_details)
        
        # Add subtle fade-in animation
        item.setStyleSheet(item.styleSheet() + "background: rgba(40, 40, 40, 0);")
        
        # Insert before the stretch item
        self.playlist_layout.insertWidget(self.playlist_layout.count() - 1, item)
        self.current_playlists.append(playlist)
        
        # Animate the item appearing
        self.animate_item_fade_in(item)
        
        # Update log
        self.log_area.append(f"Added playlist: {playlist.name} ({playlist.total_tracks} tracks)")
    
    def update_playlist_sync_status(self, playlist_item, playlist):
        """Update playlist sync status after UI creation to avoid blocking"""
        try:
            sync_info = self.sync_statuses.get(playlist.id)
            sync_status = "Never Synced"
            
            if sync_info and 'last_synced' in sync_info:
                current_snapshot_id = getattr(playlist, 'snapshot_id', None)
                stored_snapshot_id = sync_info.get('snapshot_id')
                
                if current_snapshot_id and stored_snapshot_id and current_snapshot_id != stored_snapshot_id:
                    sync_status = "Needs Sync"
                else:
                    try:
                        last_synced_dt = datetime.fromisoformat(sync_info['last_synced'])
                        sync_status = f"Synced: {last_synced_dt.strftime('%b %d, %H:%M')}"
                    except (ValueError, KeyError):
                        sync_status = "Synced (legacy)"
            
            # Update the playlist item's sync status
            playlist_item.update_sync_status_text(sync_status)
        except Exception as e:
            # Fallback to simple status if anything goes wrong
            playlist_item.update_sync_status_text("Unknown")
    
    def animate_item_fade_in(self, item):
        """Add a subtle fade-in animation to playlist items"""
        # Start with reduced opacity
        item.setStyleSheet("""
            PlaylistItem {
                background: #282828;
                border-radius: 8px;
                border: 1px solid #404040;
                opacity: 0.3;
            }
            PlaylistItem:hover {
                background: #333333;
                border: 1px solid #1db954;
            }
        """)
        
        # Animate to full opacity after a short delay
        QTimer.singleShot(50, lambda: item.setStyleSheet("""
            PlaylistItem {
                background: #282828;
                border-radius: 8px;
                border: 1px solid #404040;
            }
            PlaylistItem:hover {
                background: #333333;
                border: 1px solid #1db954;
            }
        """))
    
    def on_loading_finished(self, count):
        """Handle completion of playlist loading"""
        # Remove loading placeholder if it exists
        for i in range(self.playlist_layout.count()):
            item = self.playlist_layout.itemAt(i)
            if item and item.widget() and isinstance(item.widget(), QLabel):
                if "Loading playlists" in item.widget().text():
                    item.widget().deleteLater()
                    break
        
        self.refresh_btn.setText("🔄 Refresh")
        self.refresh_btn.setEnabled(True)
        self.log_area.append(f"✓ Loaded {count} Spotify playlists successfully")
        
        # Start background preloading of tracks for smaller playlists
        self.start_background_preloading()
    
    def start_background_preloading(self):
        """Start background preloading of tracks for smaller playlists"""
        if not self.spotify_client:
            return
        
        # Preload tracks for playlists with < 100 tracks to improve responsiveness
        for playlist in self.current_playlists:
            if (playlist.total_tracks < 100 and 
                playlist.id not in self.track_cache and 
                not playlist.tracks):
                
                # Create background worker
                worker = TrackLoadingWorker(self.spotify_client, playlist.id, playlist.name)
                worker.signals.tracks_loaded.connect(self.on_background_tracks_loaded)
                # Don't connect error signals for background loading to avoid spam
                
                # Submit with low priority
                self.thread_pool.start(worker)
                
                # Add delay between requests to be nice to Spotify API
                QTimer.singleShot(2000, lambda: None)  # 2 second delay
    
    def on_background_tracks_loaded(self, playlist_id, tracks):
        """Handle background track loading completion"""
        # Cache the tracks for future use
        self.track_cache[playlist_id] = tracks
        
        # Update the playlist object if we can find it
        for playlist in self.current_playlists:
            if playlist.id == playlist_id:
                playlist.tracks = tracks
                break
        
    def on_loading_failed(self, error_msg):
        """Handle playlist loading failure"""
        # Remove loading placeholder if it exists
        for i in range(self.playlist_layout.count()):
            item = self.playlist_layout.itemAt(i)
            if item and item.widget() and isinstance(item.widget(), QLabel):
                if "Loading playlists" in item.widget().text():
                    item.widget().deleteLater()
                    break
        
        self.refresh_btn.setText("🔄 Refresh")
        self.refresh_btn.setEnabled(True)
        self.log_area.append(f"✗ Failed to load playlists: {error_msg}")
        QMessageBox.critical(self, "Error", f"Failed to load playlists: {error_msg}")
    
    def update_progress(self, message):
        """Update progress text"""
        self.log_area.append(message)
    
    def disable_refresh_button(self, operation_name="Operation"):
        """Disable refresh button during sync/download operations"""
        self.refresh_btn.setEnabled(False)
        self.refresh_btn.setText(f"🔄 {operation_name}...")
    
    def enable_refresh_button(self):
        """Re-enable refresh button after operations complete"""
        self.refresh_btn.setEnabled(True)
        self.refresh_btn.setText("🔄 Refresh")
    
    def has_active_operations(self):
        """Check if any sync or download operations are currently active"""
        has_downloads = bool(self.active_download_processes)
        has_individual_syncs = bool(self.active_sync_workers)
        has_sequential_sync = self.is_sequential_syncing or self.sequential_sync_worker is not None
        
        print(f"DEBUG: Active operations check - downloads: {has_downloads}, individual syncs: {has_individual_syncs}, sequential: {has_sequential_sync}")
        return has_downloads or has_individual_syncs or has_sequential_sync
    
    def update_refresh_button_state(self):
        """Update refresh button state based on active operations"""
        if self.has_active_operations():
            if self.is_sequential_syncing:
                self.disable_refresh_button("Sequential Sync")
            elif self.active_sync_workers:
                self.disable_refresh_button("Sync")
            elif self.active_download_processes:
                self.disable_refresh_button("Download")
        else:
            self.enable_refresh_button()
    
    def load_initial_playlists(self):
        """Load initial playlist data (placeholder or real)"""
        if self.spotify_client and self.spotify_client.is_authenticated():
            self.refresh_playlists()
        else:
            # Show placeholder playlists
            playlists = [
                ("Liked Songs", 247, "Synced"),
                ("Discover Weekly", 30, "Needs Sync"),
                ("Chill Vibes", 89, "Synced"),
                ("Workout Mix", 156, "Needs Sync"),
                ("Road Trip", 67, "Never Synced"),
                ("Focus Music", 45, "Synced")
            ]
            
            for name, count, status in playlists:
                item = PlaylistItem(name, count, status, None, self)  # Set parent for placeholders too
                self.playlist_layout.addWidget(item)
    
    def refresh_playlists(self):
        """Refresh playlists from Spotify API using async loader"""
        if not self.spotify_client:
            QMessageBox.warning(self, "Error", "Spotify client not available")
            return
        
        if not self.spotify_client.is_authenticated():
            QMessageBox.warning(self, "Error", "Spotify not authenticated. Please check your settings.")
            return
        
        # Use the async loader
        self.load_playlists_async()
    
    def show_playlist_details(self, playlist):
        """Show playlist details modal"""
        if playlist:
            modal = PlaylistDetailsModal(playlist, self)
            modal.show()
    
    def clear_playlists(self):
        """Clear all playlist items from the layout"""
        # Clear the current playlists list
        self.current_playlists = []
        
        # Remove all items including welcome state
        for i in reversed(range(self.playlist_layout.count())):
            item = self.playlist_layout.itemAt(i)
            if item.widget():
                item.widget().deleteLater()
            elif item.spacerItem():
                continue  # Keep the stretch spacer
            else:
                self.playlist_layout.removeItem(item)
    
    def parse_youtube_playlist(self):
        """Parse YouTube playlist URL and create card immediately, then open discovery modal"""
        url = self.youtube_url_input.text().strip()
        
        if not url:
            self.show_youtube_error("Please enter a YouTube Music playlist URL")
            return
        
        # Basic URL validation
        if not ('youtube.com' in url or 'youtu.be' in url):
            self.show_youtube_error("Please enter a valid YouTube Music playlist URL")
            return
        
        # Check if this URL already has a card/state
        if url in self.youtube_playlist_states:
            # Card already exists - check if we need to reopen existing modal or create new one
            state = self.get_youtube_playlist_state(url)
            if state and state.get('discovery_modal') and state['discovery_modal'].isVisible():
                # Modal is already open, just bring it to front
                state['discovery_modal'].activateWindow()
                state['discovery_modal'].raise_()
                return
            elif state and state.get('playlist_data'):
                # We have data but no visible modal - recreate modal with existing data
                self.open_or_create_discovery_modal(url, state)
                return
            else:
                # Card exists but no data yet - this means parsing was cancelled/failed
                # Reset the card state and continue with new parsing
                print(f"🔄 Resetting existing card state for URL: {url}")
                self.reset_youtube_playlist_state(url)
        
        # Check if this URL is already being processed (legacy check)
        if url in self.active_youtube_processes:
            existing_modal = self.active_youtube_processes[url]
            if existing_modal and not existing_modal.isHidden():
                # Modal is still open - bring it to front
                existing_modal.show()
                existing_modal.raise_()
                existing_modal.activateWindow()
                return
            elif existing_modal:
                # Modal exists but is hidden - reopen it
                existing_modal.show()
                existing_modal.raise_()
                existing_modal.activateWindow()
                return
            else:
                # Stale reference - clean it up
                del self.active_youtube_processes[url]
        
        # Create YouTube playlist card immediately
        card = self.create_youtube_playlist_card(url)
        card.set_phase('discovering')
        
        # Show loading state
        self.parse_btn.setEnabled(False)
        self.parse_btn.setText("Parsing...")
        
        # Show modal immediately with loading state
        self.show_youtube_modal_loading(url)
        
        # Store URL for later use in completion handlers
        self.current_youtube_url = url
        
        # Start parsing in a separate thread to avoid blocking UI
        self.youtube_worker = YouTubeParsingWorker(url)
        self.youtube_worker.finished.connect(self.on_youtube_parsing_finished)
        self.youtube_worker.error.connect(self.on_youtube_parsing_error)
        self.youtube_worker.start()
    
    def show_youtube_modal_loading(self, url):
        """Show the YouTube modal immediately with loading state"""
        # Create a dummy playlist item widget (required by modal)
        dummy_playlist_item = type('DummyPlaylistItem', (), {
            'playlist_name': "Loading...",
            'track_count': 0,
            'download_modal': None,
            'show_operation_status': lambda self, status_text="View Progress": None,
            'hide_operation_status': lambda self: None
        })()
        
        # Create empty playlist for loading state
        empty_playlist = type('Playlist', (), {
            'name': f"Parsing YouTube Playlist...",
            'tracks': [],
            'total_tracks': 0
        })()
        
        # Open the modal in loading state
        print("🚀 Opening YouTubeDownloadMissingTracksModal in loading state...")
        self.current_youtube_modal = YouTubeDownloadMissingTracksModal(
            empty_playlist, 
            dummy_playlist_item,
            self, 
            self.downloads_page
        )
        
        # Store URL in modal for cleanup purposes
        self.current_youtube_modal.youtube_url = url
        
        # Register this modal for the URL to prevent duplicates
        self.active_youtube_processes[url] = self.current_youtube_modal
        
        # Link modal with card state system
        if url in self.youtube_playlist_states:
            self.youtube_playlist_states[url]['discovery_modal'] = self.current_youtube_modal
        
        # Show a loading message in the modal
        self.current_youtube_modal.show_loading_state()
        self.current_youtube_modal.show()
    
    def on_youtube_parsing_finished(self, playlist):
        """Handle successful YouTube playlist parsing"""
        try:
            print(f"✅ Successfully parsed YouTube playlist: {playlist.name}")
            print(f"🔍 Playlist ID: {playlist.id}")
            
            # Reset button state
            self.parse_btn.setEnabled(True)
            self.parse_btn.setText("Parse Playlist")
            
            # Update the card with discovered playlist info
            if hasattr(self, 'current_youtube_url'):
                url = self.current_youtube_url
                
                # Update card state and playlist info
                self.set_youtube_card_playlist_data(url, playlist)
                self.update_youtube_card_playlist_info(url, playlist.name, len(playlist.tracks))
                self.update_youtube_card_phase(url, 'discovery_complete')
                
                # Store modal reference in state
                if url in self.youtube_playlist_states and hasattr(self, 'current_youtube_modal'):
                    self.youtube_playlist_states[url]['discovery_modal'] = self.current_youtube_modal
            
            # Update the existing modal with the parsed playlist data
            print(f"🔍 Has current_youtube_modal: {hasattr(self, 'current_youtube_modal') and self.current_youtube_modal is not None}")
            if hasattr(self, 'current_youtube_modal') and self.current_youtube_modal:
                print(f"🔍 Calling populate_with_playlist_data...")
                self.current_youtube_modal.populate_with_playlist_data(playlist)
            else:
                # Fallback: create new modal if loading modal wasn't created
                dummy_playlist_item = type('DummyPlaylistItem', (), {
                    'playlist_name': playlist.name,
                    'track_count': len(playlist.tracks),
                    'download_modal': None,
                    'show_operation_status': lambda self, status_text="View Progress": None,
                    'hide_operation_status': lambda self: None
                })()
                
                modal = YouTubeDownloadMissingTracksModal(
                    playlist, 
                    dummy_playlist_item, 
                    self, 
                    self.downloads_page
                )
                
                # Store URL and register in tracking
                if hasattr(self, 'current_youtube_url'):
                    modal.youtube_url = self.current_youtube_url
                    self.active_youtube_processes[self.current_youtube_url] = modal
                
                modal.exec()
            
            # Clear the URL input after successful parsing
            self.youtube_url_input.clear()
            
        except Exception as e:
            print(f"❌ Error handling YouTube parsing result: {e}")
            self.on_youtube_parsing_error(str(e))
    
    def on_youtube_parsing_error(self, error_message):
        """Handle YouTube playlist parsing error"""
        print(f"❌ YouTube parsing error: {error_message}")
        
        # Update card state on error (remove the card since parsing failed)
        if hasattr(self, 'current_youtube_url'):
            url = self.current_youtube_url
            self.remove_youtube_playlist_card(url)
        
        # Clean up URL tracking on error
        if hasattr(self, 'current_youtube_url') and self.current_youtube_url in self.active_youtube_processes:
            print(f"🧹 Cleaning up URL tracking on error for: {self.current_youtube_url}")
            del self.active_youtube_processes[self.current_youtube_url]
        
        # Reset button state
        self.parse_btn.setEnabled(True)
        self.parse_btn.setText("Parse Playlist")
        
        # Show error message
        self.show_youtube_error(f"Failed to parse playlist: {error_message}")
    
    def show_youtube_error(self, message):
        """Show error message for YouTube functionality"""
        # You can enhance this with a proper toast notification if available
        if hasattr(self, 'toast_manager') and self.toast_manager:
            self.toast_manager.show_toast(message, ToastType.ERROR)
        else:
            print(f"⚠️ YouTube Error: {message}")
            # Fallback to a simple message box
            msg_box = QMessageBox(self)
            msg_box.setIcon(QMessageBox.Icon.Warning)
            msg_box.setWindowTitle("YouTube Playlist Error")
            msg_box.setText(message)
            msg_box.exec()

    # ===============================
    # YouTube Playlist Card Hub System
    # ===============================
    
    def create_youtube_playlist_card(self, url: str, playlist_name: str = "Loading...", track_count: int = 0):
        """Create a new YouTube playlist card and add to the cards container"""
        if url in self.youtube_cards:
            return self.youtube_cards[url]  # Return existing card
        
        # Create new card
        card = YouTubePlaylistCard(url, playlist_name, track_count, self)
        card.card_clicked.connect(self.on_youtube_card_clicked)
        
        # Store card reference
        self.youtube_cards[url] = card
        
        # Initialize state tracking
        self.youtube_playlist_states[url] = {
            'phase': 'discovering',
            'playlist_data': None,
            'discovered_tracks': [],
            'card': card,
            'discovery_modal': None,
            'download_modal': None
        }
        
        # Ensure cards container exists
        if self.youtube_cards_container is None:
            self.setup_youtube_cards_container()
        
        # Add card to container at the top (most recent first)
        card_layout = self.youtube_cards_container.layout()
        if card_layout:
            # Insert at position 0 so newest cards appear at the top
            card_layout.insertWidget(0, card)
        
        return card
    
    def setup_youtube_cards_container(self):
        """Setup the container for YouTube playlist cards"""
        # Clear existing placeholder content
        for i in reversed(range(self.youtube_content_layout.count())):
            child = self.youtube_content_layout.itemAt(i).widget()
            if child:
                child.setParent(None)
        
        # Create cards container
        self.youtube_cards_container = QFrame()
        self.youtube_cards_container.setStyleSheet("""
            QFrame {
                background: transparent;
                border: none;
            }
        """)
        
        cards_layout = QVBoxLayout(self.youtube_cards_container)
        cards_layout.setContentsMargins(0, 0, 0, 0)
        cards_layout.setSpacing(10)
        # Set alignment to ensure cards stick to the top
        cards_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        cards_layout.addStretch()  # Stretch at bottom to align cards to top
        
        # Add container to main layout at the top
        self.youtube_content_layout.insertWidget(0, self.youtube_cards_container)
    
    def update_youtube_card_phase(self, url: str, phase: str):
        """Update the YouTube card's phase - cards are the single source of truth for state"""
        if url not in self.youtube_cards or url not in self.youtube_playlist_states:
            return

        card = self.youtube_cards[url]
        state = self.youtube_playlist_states[url]
        
        # Update the internal state - card handles its own visual appearance
        card.set_phase(phase)
        state['phase'] = phase
        
        # Clean up any existing status widgets for this playlist when changing phases
        playlist_data = state.get('playlist_data')
        if playlist_data and hasattr(playlist_data, 'id'):
            if playlist_data.id in self.youtube_status_widgets:
                status_widget = self.youtube_status_widgets.pop(playlist_data.id, None)
                if status_widget:
                    status_widget.setParent(None)
                    status_widget.deleteLater()
                    print(f"🧹 Cleaned up status widget for phase change to: {phase}")
        
        # Ensure card is always visible - it manages its own appearance
        card.show()
    
    def update_youtube_card_progress(self, url: str, total=None, matched=None, failed=None):
        """Update progress display on a YouTube playlist card"""
        if url in self.youtube_cards:
            card = self.youtube_cards[url]
            card.update_progress(total=total, matched=matched, failed=failed)
    
    def update_youtube_card_playlist_info(self, url: str, name: str, track_count: int):
        """Update playlist info on a YouTube playlist card"""
        if url in self.youtube_cards:
            card = self.youtube_cards[url]
            card.update_playlist_info(name, track_count)
    
    def set_youtube_card_playlist_data(self, url: str, playlist_data):
        """Store playlist data for a YouTube card"""
        if url in self.youtube_playlist_states:
            self.youtube_playlist_states[url]['playlist_data'] = playlist_data
            if hasattr(playlist_data, 'tracks'):
                self.youtube_playlist_states[url]['discovered_tracks'] = playlist_data.tracks
            
            # Update card with playlist info
            if url in self.youtube_cards:
                card = self.youtube_cards[url]
                card.set_playlist_data(playlist_data)
    
    def get_youtube_playlist_state(self, url: str):
        """Get the current state data for a YouTube playlist"""
        return self.youtube_playlist_states.get(url, None)
    
    def reset_youtube_playlist_state(self, url: str):
        """Reset YouTube playlist state (for cancel operations)"""
        if url in self.youtube_playlist_states:
            state = self.youtube_playlist_states[url]
            state['phase'] = 'discovering'
            state['playlist_data'] = None
            state['discovered_tracks'] = []
            state['discovery_modal'] = None
            state['download_modal'] = None
            
            # Reset card to initial state
            if url in self.youtube_cards:
                card = self.youtube_cards[url]
                card.set_phase('discovering')
                card.update_playlist_info("Loading...", 0)
                card.update_progress(0, 0, 0)
    
    def remove_youtube_playlist_card(self, url: str):
        """Remove a YouTube playlist card (for full cleanup)"""
        if url in self.youtube_cards:
            card = self.youtube_cards[url]
            card.setParent(None)
            del self.youtube_cards[url]
        
        if url in self.youtube_playlist_states:
            del self.youtube_playlist_states[url]
    
    def on_youtube_card_clicked(self, url: str, phase: str):
        """Handle YouTube playlist card clicks - route to appropriate modal"""
        print(f"🎬 YouTube card clicked: URL={url}, Phase={phase}")
        
        state = self.get_youtube_playlist_state(url)
        if not state:
            print(f"⚠️ No state found for URL: {url}")
            return
        
        # Route to appropriate modal based on current phase
        if phase in ['discovering', 'discovery_complete']:
            self.open_or_create_discovery_modal(url, state)
        elif phase in ['sync_complete', 'downloading', 'download_complete']:
            # For downloading phase, check if download modal actually exists
            # If not, route back to discovery modal (handles case where download modal was closed)
            playlist_data = state.get('playlist_data')
            if (playlist_data and hasattr(playlist_data, 'id') and 
                playlist_data.id in self.active_youtube_download_modals):
                self.open_or_create_download_modal(url, state)
            else:
                print(f"📍 Download modal not found, routing to discovery modal instead")
                self.open_or_create_discovery_modal(url, state)
        elif phase == 'syncing':
            # Show sync progress - could be same as discovery modal or separate
            self.open_or_create_discovery_modal(url, state)
    
    def open_or_create_discovery_modal(self, url: str, state: dict):
        """Open or create the discovery modal for a YouTube playlist"""
        # Check if modal already exists and is visible
        if state.get('discovery_modal') and state['discovery_modal'].isVisible():
            state['discovery_modal'].activateWindow()
            state['discovery_modal'].raise_()
            return
        
        # Check if modal exists but is hidden - reopen it
        if state.get('discovery_modal') and not state['discovery_modal'].isVisible():
            print(f"🔍 Reopening existing hidden discovery modal for URL: {url}")
            state['discovery_modal'].show()
            state['discovery_modal'].activateWindow()
            state['discovery_modal'].raise_()
            return
        
        # Check if we have playlist data already (discovery_complete state)
        if state.get('playlist_data') and state['phase'] == 'discovery_complete':
            print(f"🔍 Opening existing discovery modal with data for URL: {url}")
            
            # Create a new modal with the existing data
            dummy_playlist_item = type('DummyPlaylistItem', (), {
                'playlist_name': state['playlist_data'].name,
                'track_count': len(state['playlist_data'].tracks),
                'download_modal': None,
                'show_operation_status': lambda self, status_text="View Progress": None,
                'hide_operation_status': lambda self: None
            })()
            
            modal = YouTubeDownloadMissingTracksModal(
                state['playlist_data'], 
                dummy_playlist_item,
                self, 
                self.downloads_page
            )
            
            # Store URL and register modal
            modal.youtube_url = url
            state['discovery_modal'] = modal
            self.active_youtube_processes[url] = modal
            
            modal.show()
            modal.activateWindow()
            modal.raise_()
            
        else:
            # No existing data - start new discovery process
            print(f"🔍 Starting new discovery for URL: {url}")
            
            # Store URL in input field 
            self.youtube_url_input.setText(url)
            
            # Directly start the parsing worker instead of calling parse_youtube_playlist
            # to avoid recursion loop
            self.start_youtube_parsing_worker(url)
    
    def start_youtube_parsing_worker(self, url: str):
        """Start YouTube parsing worker directly (used to avoid recursion)"""
        # Show loading state
        self.parse_btn.setEnabled(False)
        self.parse_btn.setText("Parsing...")
        
        # Show modal immediately with loading state
        self.show_youtube_modal_loading(url)
        
        # Store URL for later use in completion handlers
        self.current_youtube_url = url
        
        # Start parsing in a separate thread to avoid blocking UI
        self.youtube_worker = YouTubeParsingWorker(url)
        self.youtube_worker.finished.connect(self.on_youtube_parsing_finished)
        self.youtube_worker.error.connect(self.on_youtube_parsing_error)
        self.youtube_worker.start()
    
    def open_or_create_download_modal(self, url: str, state: dict):
        """Open or create the download modal for a YouTube playlist"""
        playlist_data = state.get('playlist_data')
        if not playlist_data:
            print(f"⚠️ No playlist data available for URL: {url}")
            return
        
        # Check if modal already exists
        if state.get('download_modal') and state['download_modal'].isVisible():
            state['download_modal'].activateWindow()
            state['download_modal'].raise_()
            return
        
        # Create new download modal
        print(f"📥 Opening download modal for URL: {url}")
        
        # Check existing modal system first
        if hasattr(playlist_data, 'id') and playlist_data.id in self.active_youtube_download_modals:
            modal = self.active_youtube_download_modals[playlist_data.id]
            modal.show()
            modal.activateWindow()
            modal.raise_()
            state['download_modal'] = modal
        else:
            # Create new download modal using the existing modal creation pattern
            # This would transition to the download missing tracks phase
            # For now, route back to discovery modal (sync_complete means ready for download)
            self.open_or_create_discovery_modal(url, state)


class OptimizedSpotifyDiscoveryWorkerSignals(QObject):
    track_discovered = pyqtSignal(int, object, str)  # row, spotify_track, status
    progress_updated = pyqtSignal(int)  # current progress
    finished = pyqtSignal(int)  # total successful discoveries

class OptimizedSpotifyDiscoveryWorker(QRunnable):
    def __init__(self, youtube_tracks, spotify_client, matching_engine):
        super().__init__()
        self.youtube_tracks = youtube_tracks
        self.spotify_client = spotify_client
        self.matching_engine = matching_engine
        self.signals = OptimizedSpotifyDiscoveryWorkerSignals()
        self.is_cancelled = False
    
    def cancel(self):
        self.is_cancelled = True
    
    def run(self):
        """Discover Spotify tracks for YouTube tracks with optimized timing"""
        successful_discoveries = 0
        
        for i, youtube_track in enumerate(self.youtube_tracks):
            if self.is_cancelled:
                break
                
            try:
                # Create search query from YouTube track data
                if youtube_track.artists:
                    query = f"{youtube_track.artists[0]} {youtube_track.name}"
                else:
                    query = youtube_track.name
                
                # Debug logging for search queries
                print(f"🔍 Spotify search query: '{query}' (track: '{youtube_track.name}', artist: '{youtube_track.artists[0] if youtube_track.artists else 'None'}')")
                
                # Search Spotify - get more results for validation
                spotify_results = self.spotify_client.search_tracks(query, limit=10)
                
                # Debug logging for search results
                if spotify_results:
                    print(f"📊 Found {len(spotify_results)} Spotify results:")
                    for idx, result in enumerate(spotify_results[:3]):  # Show first 3
                        album_name = result.album if isinstance(result.album, str) else getattr(result.album, 'name', 'Unknown')
                        print(f"   {idx+1}. '{result.name}' by '{result.artists[0] if result.artists else 'Unknown'}' from '{album_name}'")
                else:
                    print(f"❌ No Spotify results for query: '{query}'")
                
                if spotify_results:
                    # Use matching engine to find the best validated match
                    best_track = self.find_best_validated_match(youtube_track, spotify_results)
                    if best_track:
                        self.signals.track_discovered.emit(i, best_track, "found")
                        successful_discoveries += 1
                    else:
                        # Try swapping artist and track name (sometimes YouTube data is swapped)
                        best_track = self.retry_with_swapped_fields(youtube_track)
                        if best_track:
                            print(f"🔄 Found match after swapping artist/track for: '{youtube_track.name}' by '{youtube_track.artists[0] if youtube_track.artists else 'Unknown'}'")
                            self.signals.track_discovered.emit(i, best_track, "found")
                            successful_discoveries += 1
                        else:
                            # Third resort: try with uncleaned original data
                            best_track = self.retry_with_uncleaned_data(youtube_track)
                            if best_track:
                                print(f"🔍 Found match with uncleaned data for: '{youtube_track.name}' by '{youtube_track.artists[0] if youtube_track.artists else 'Unknown'}'")
                                self.signals.track_discovered.emit(i, best_track, "found")
                                successful_discoveries += 1
                            else:
                                # Final resort: try with raw title + raw artist combined
                                best_track = self.retry_with_raw_title_and_artist(youtube_track)
                                if best_track:
                                    print(f"🎯 Found match with title+artist fallback for: '{youtube_track.name}' by '{youtube_track.artists[0] if youtube_track.artists else 'Unknown'}'")
                                    self.signals.track_discovered.emit(i, best_track, "found")
                                    successful_discoveries += 1
                                else:
                                    # No result met confidence threshold even after all retries
                                    self.signals.track_discovered.emit(i, None, "low_confidence")
                else:
                    # No Spotify search results found - try swapping before giving up
                    best_track = self.retry_with_swapped_fields(youtube_track)
                    if best_track:
                        print(f"🔄 Found match after swapping artist/track for: '{youtube_track.name}' by '{youtube_track.artists[0] if youtube_track.artists else 'Unknown'}'")
                        self.signals.track_discovered.emit(i, best_track, "found")
                        successful_discoveries += 1
                    else:
                        # Third resort: try with uncleaned original data
                        best_track = self.retry_with_uncleaned_data(youtube_track)
                        if best_track:
                            print(f"🔍 Found match with uncleaned data for: '{youtube_track.name}' by '{youtube_track.artists[0] if youtube_track.artists else 'Unknown'}'")
                            self.signals.track_discovered.emit(i, best_track, "found")
                            successful_discoveries += 1
                        else:
                            # Final resort: try with raw title + raw artist combined
                            best_track = self.retry_with_raw_title_and_artist(youtube_track)
                            if best_track:
                                print(f"🎯 Found match with title+artist fallback for: '{youtube_track.name}' by '{youtube_track.artists[0] if youtube_track.artists else 'Unknown'}'")
                                self.signals.track_discovered.emit(i, best_track, "found")
                                successful_discoveries += 1
                            else:
                                self.signals.track_discovered.emit(i, None, "not_found")
            
            except Exception as e:
                print(f"❌ Error searching Spotify for track {i}: {e}")
                self.signals.track_discovered.emit(i, None, f"error: {str(e)}")
            
            # Update progress
            self.signals.progress_updated.emit(i + 1)
            
            # Reduced delay for faster processing - Spotify client has built-in rate limiting
            if not self.is_cancelled:
                import time
                time.sleep(0.15)  # 150ms between requests
        
        self.signals.finished.emit(successful_discoveries)
    
    def find_best_validated_match(self, youtube_track, spotify_results):
        """Find the best Spotify match using the matching engine for validation"""
        if not spotify_results:
            return None
        
        # Clean YouTube track name for better matching (already cleaned in parsing, but ensure consistency)
        cleaned_youtube_name = self.clean_for_youtube_matching(youtube_track.name)
        
        # Create a mock Spotify track from YouTube data for comparison
        youtube_as_spotify = type('Track', (), {
            'name': cleaned_youtube_name,
            'artists': youtube_track.artists if youtube_track.artists else ["Unknown"],
            'album': getattr(youtube_track, 'album', 'Unknown Album'),
            'duration_ms': getattr(youtube_track, 'duration_ms', 0)
        })()
        
        best_match = None
        best_confidence = 0.0
        best_match_type = "no_match"
        
        # Score each Spotify result using your matching engine
        for spotify_track in spotify_results:
            try:
                # Clean the Spotify track name for better YouTube-to-Spotify matching
                cleaned_spotify_track = self.create_cleaned_spotify_track_for_matching(spotify_track)
                
                # Debug logging for track cleaning
                if cleaned_spotify_track.name != spotify_track.name:
                    print(f"🧹 Cleaned Spotify track: '{spotify_track.name}' -> '{cleaned_spotify_track.name}'")
                
                # Use your matching engine to calculate confidence
                confidence, match_type = self.matching_engine.calculate_match_confidence(
                    youtube_as_spotify, 
                    self.convert_spotify_to_plex_format(cleaned_spotify_track)
                )
                
                # Apply album preference bonus (your existing logic)
                album_bonus = self.calculate_album_preference_bonus(spotify_track)
                adjusted_confidence = confidence + album_bonus
                
                if adjusted_confidence > best_confidence:
                    best_confidence = adjusted_confidence
                    best_match = spotify_track
                    best_match_type = match_type
                    
            except Exception as e:
                print(f"⚠️ Error calculating match confidence: {e}")
                continue
        
        # Apply your matching engine's confidence threshold (0.8 for high confidence)
        confidence_threshold = 0.75  # Slightly lower for YouTube discovery
        
        if best_confidence >= confidence_threshold:
            print(f"✅ Validated match: '{best_match.name}' by '{best_match.artists[0]}' (confidence: {best_confidence:.3f}, type: {best_match_type})")
            return best_match
        else:
            print(f"❌ No high-confidence match found. Best was {best_confidence:.3f} < {confidence_threshold}")
            if best_match:
                print(f"   Best candidate was: '{best_match.name}' by '{best_match.artists[0] if best_match.artists else 'Unknown'}'")
            return None
    
    def convert_spotify_to_plex_format(self, spotify_track):
        """Convert Spotify track to Plex format for matching engine compatibility"""
        return type('PlexTrackInfo', (), {
            'title': spotify_track.name,
            'artist': spotify_track.artists[0] if spotify_track.artists else "Unknown",
            'album': spotify_track.album if isinstance(spotify_track.album, str) else getattr(spotify_track.album, 'name', 'Unknown Album'),
            'duration': getattr(spotify_track, 'duration_ms', 0)
        })()
    
    def calculate_album_preference_bonus(self, spotify_track):
        """Calculate album preference bonus (simplified version of your existing logic)"""
        try:
            album_info = spotify_track.album if hasattr(spotify_track, 'album') else None
            if album_info and not isinstance(album_info, str):
                album_type = getattr(album_info, 'album_type', album_info.get('album_type', 'unknown') if hasattr(album_info, 'get') else 'unknown')
                
                if isinstance(album_type, str):
                    if album_type.lower() == 'album':
                        return 0.05  # Small bonus for albums
                    elif album_type.lower() == 'single':
                        return -0.02  # Small penalty for singles
                    elif album_type.lower() == 'compilation':
                        return 0.02  # Small bonus for compilations
            
            return 0.0
        except:
            return 0.0
    
    def choose_best_spotify_match(self, spotify_results):
        """Choose the best Spotify track from search results, preferring album versions"""
        if not spotify_results:
            return None
            
        # If only one result, return it
        if len(spotify_results) == 1:
            return spotify_results[0]
        
        # Score each track based on preference criteria
        scored_tracks = []
        
        for track in spotify_results:
            score = 0
            
            # 1. Prefer album tracks over singles (highest priority)
            try:
                # Access album type through the album attribute
                album_info = track.album if hasattr(track, 'album') else None
                if album_info:
                    # Handle both string and dict album info
                    if isinstance(album_info, str):
                        # If album is just a string name, we can't determine type
                        score += 50  # Medium score for unknown type
                    else:
                        # Try to get album_type from album object or dict
                        album_type = getattr(album_info, 'album_type', album_info.get('album_type', 'unknown') if hasattr(album_info, 'get') else 'unknown')
                        
                        if isinstance(album_type, str):
                            if album_type.lower() == 'album':
                                score += 100  # Strong preference for albums
                            elif album_type.lower() == 'single':
                                score += 20   # Lower preference for singles
                            elif album_type.lower() == 'compilation':
                                score += 60   # Medium preference for compilations
                        else:
                            score += 50  # Unknown type gets medium score
                else:
                    score += 30  # No album info gets low score
            except Exception as e:
                print(f"⚠️ Error accessing album type: {e}")
                score += 30  # Error case gets low score
            
            # 2. Prefer tracks with more total tracks in album (indicates full album)
            try:
                album_info = track.album if hasattr(track, 'album') else None
                if album_info and not isinstance(album_info, str):
                    total_tracks = getattr(album_info, 'total_tracks', album_info.get('total_tracks', 0) if hasattr(album_info, 'get') else 0)
                    
                    if total_tracks > 10:
                        score += 50  # Full album
                    elif total_tracks > 5:
                        score += 30  # EP
                    elif total_tracks > 1:
                        score += 10  # Multi-track release
                    # Singles (1 track) get no bonus
            except Exception as e:
                print(f"⚠️ Error accessing total_tracks: {e}")
                pass
            
            # 3. Consider popularity as tiebreaker
            try:
                popularity = getattr(track, 'popularity', 0)
                score += popularity * 0.1  # Small influence from popularity
            except:
                pass
            
            # 4. Prefer tracks with explicit marking if available (often more complete metadata)
            try:
                if hasattr(track, 'explicit') and track.explicit is not None:
                    score += 5
            except:
                pass
            
            scored_tracks.append((score, track))
            
        # Sort by score (highest first) and return the best match
        scored_tracks.sort(key=lambda x: x[0], reverse=True)
        best_track = scored_tracks[0][1]
        
        # Debug logging for first few tracks
        if len(self.youtube_tracks) <= 5 or len(scored_tracks) > 1:
            try:
                album_name = best_track.album if isinstance(best_track.album, str) else getattr(best_track.album, 'name', 'Unknown Album')
                print(f"🎯 Chose: '{best_track.name}' from '{album_name}' (score: {scored_tracks[0][0]:.1f})")
                if len(scored_tracks) > 1:
                    alt_album = scored_tracks[1][1].album if isinstance(scored_tracks[1][1].album, str) else getattr(scored_tracks[1][1].album, 'name', 'Unknown Album')
                    print(f"   vs. '{scored_tracks[1][1].name}' from '{alt_album}' (score: {scored_tracks[1][0]:.1f})")
            except:
                pass  # Don't let debug logging crash the worker
        
        return best_track
    
    def clean_for_youtube_matching(self, track_name):
        """Clean track name for YouTube-to-Spotify matching"""
        if not track_name:
            return ""
        
        cleaned = track_name
        
        # Remove all parentheses content for YouTube matching
        # This handles cases like "MOUSTACHE (Feat. Netta)" -> "MOUSTACHE"
        cleaned = re.sub(r'\s*\([^)]*\)', '', cleaned)
        
        # Remove brackets content
        cleaned = re.sub(r'\s*\[[^\]]*\]', '', cleaned)
        
        # Remove extra whitespace and return
        return cleaned.strip()
    
    def create_cleaned_spotify_track_for_matching(self, spotify_track):
        """Create a cleaned version of Spotify track for better YouTube matching"""
        # Clean the track name 
        cleaned_name = self.clean_for_youtube_matching(spotify_track.name)
        
        # Create a copy of the track with cleaned name
        cleaned_track = type('Track', (), {
            'id': getattr(spotify_track, 'id', ''),
            'name': cleaned_name,  # Use cleaned name
            'artists': spotify_track.artists,
            'album': spotify_track.album,
            'duration_ms': getattr(spotify_track, 'duration_ms', 0),
            'popularity': getattr(spotify_track, 'popularity', 0),
            'preview_url': getattr(spotify_track, 'preview_url', None),
            'external_urls': getattr(spotify_track, 'external_urls', None)
        })()
        
        return cleaned_track
    
    def retry_with_swapped_fields(self, youtube_track):
        """Retry search with artist and track names swapped (handles YouTube data inconsistencies)"""
        if not youtube_track.artists or not youtube_track.artists[0]:
            return None
        
        try:
            # Create swapped query: use track name as artist and artist as track
            swapped_artist = youtube_track.name
            swapped_track = youtube_track.artists[0]
            
            # Clean the swapped values
            swapped_artist_clean = clean_youtube_artist(swapped_artist)
            swapped_track_clean = clean_youtube_track_title(swapped_track, swapped_artist_clean)
            
            swapped_query = f"{swapped_artist_clean} {swapped_track_clean}"
            
            print(f"🔄 Retrying with swapped fields: '{swapped_query}' (was '{youtube_track.artists[0]} {youtube_track.name}')")
            
            # Search Spotify with swapped query
            spotify_results = self.spotify_client.search_tracks(swapped_query, limit=10)
            
            if spotify_results:
                # Create a swapped YouTube track for matching
                swapped_youtube_track = type('Track', (), {
                    'name': swapped_track_clean,
                    'artists': [swapped_artist_clean],
                    'album': getattr(youtube_track, 'album', 'Unknown Album'),
                    'duration_ms': getattr(youtube_track, 'duration_ms', 0)
                })()
                
                # Use matching engine to validate the swapped results
                best_track = self.find_best_validated_match(swapped_youtube_track, spotify_results)
                return best_track
            
            return None
            
        except Exception as e:
            print(f"❌ Error in retry with swapped fields: {e}")
            return None
    
    def retry_with_uncleaned_data(self, youtube_track):
        """Last resort: retry search with original uncleaned YouTube data"""
        # Check if we have raw uncleaned data
        if not hasattr(youtube_track, 'raw_title') or not hasattr(youtube_track, 'raw_uploader'):
            print("🔍 No raw data available for uncleaned fallback search")
            return None
        
        try:
            # Use completely uncleaned data
            raw_title = youtube_track.raw_title
            raw_uploader = youtube_track.raw_uploader
            
            # Create query with minimal cleaning - just basic text normalization
            uncleaned_query = f"{raw_uploader} {raw_title}".strip()
            
            print(f"🔍 Last resort: Trying uncleaned data: '{uncleaned_query}' (was '{youtube_track.artists[0]} {youtube_track.name}')")
            
            # Search Spotify with uncleaned query
            spotify_results = self.spotify_client.search_tracks(uncleaned_query, limit=10)
            
            if spotify_results:
                print(f"📊 Found {len(spotify_results)} results with uncleaned data")
                
                # Create an uncleaned YouTube track for comparison
                uncleaned_youtube_track = type('Track', (), {
                    'name': raw_title,  # Use raw title
                    'artists': [raw_uploader],  # Use raw uploader  
                    'album': getattr(youtube_track, 'album', 'Unknown Album'),
                    'duration_ms': getattr(youtube_track, 'duration_ms', 0)
                })()
                
                # Use matching engine to validate results with lower confidence threshold
                # Note: We don't clean Spotify tracks here since we're using raw data
                best_match = None
                best_confidence = 0.0
                
                for spotify_track in spotify_results:
                    try:
                        # Use original Spotify track names (no cleaning) for raw data matching
                        confidence, match_type = self.matching_engine.calculate_match_confidence(
                            uncleaned_youtube_track,
                            self.convert_spotify_to_plex_format(spotify_track)
                        )
                        
                        if confidence > best_confidence:
                            best_confidence = confidence
                            best_match = spotify_track
                    except Exception as e:
                        print(f"⚠️ Error calculating confidence for uncleaned fallback: {e}")
                        continue
                
                # Use lower confidence threshold for uncleaned fallback (0.6 instead of 0.75)
                confidence_threshold = 0.6
                
                if best_confidence >= confidence_threshold:
                    print(f"✅ Uncleaned fallback match: '{best_match.name}' by '{best_match.artists[0]}' (confidence: {best_confidence:.3f})")
                    return best_match
                else:
                    print(f"❌ Uncleaned fallback: Best confidence {best_confidence:.3f} < {confidence_threshold}")
            
            return None
            
        except Exception as e:
            print(f"❌ Error in retry with uncleaned data: {e}")
            return None
    
    def retry_with_raw_title_and_artist(self, youtube_track):
        """Final fallback: search with raw title + raw artist as combined query"""
        # Check if we have raw uncleaned data
        if not hasattr(youtube_track, 'raw_title') or not hasattr(youtube_track, 'raw_uploader'):
            print("🔍 No raw data available for title+artist fallback search")
            return None
        
        try:
            raw_title = youtube_track.raw_title
            raw_uploader = youtube_track.raw_uploader
            
            # Create a combined query with raw title and raw artist
            # This is different from the previous fallback which used "uploader title"
            # This uses "title artist" order which sometimes works better
            combined_query = f"{raw_title} {raw_uploader}".strip()
            
            print(f"🔍 Final fallback: Trying raw title+artist: '{combined_query}'")
            
            # Search Spotify with the combined query
            spotify_results = self.spotify_client.search_tracks(combined_query, limit=10)
            
            if spotify_results:
                print(f"📊 Found {len(spotify_results)} results with title+artist search")
                
                # Create a track object for matching with raw data in title+artist order
                combined_youtube_track = type('Track', (), {
                    'name': raw_title,
                    'artists': [raw_uploader],
                    'album': getattr(youtube_track, 'album', 'Unknown Album'),
                    'duration_ms': getattr(youtube_track, 'duration_ms', 0)
                })()
                
                # Use matching engine with even lower confidence threshold
                best_match = None
                best_confidence = 0.0
                
                for spotify_track in spotify_results:
                    try:
                        confidence, match_type = self.matching_engine.calculate_match_confidence(
                            combined_youtube_track,
                            self.convert_spotify_to_plex_format(spotify_track)
                        )
                        
                        if confidence > best_confidence:
                            best_confidence = confidence
                            best_match = spotify_track
                    except Exception as e:
                        print(f"⚠️ Error calculating confidence for title+artist fallback: {e}")
                        continue
                
                # Use very low confidence threshold for this final attempt (0.5 instead of 0.6)
                confidence_threshold = 0.5
                
                if best_confidence >= confidence_threshold:
                    print(f"✅ Title+artist fallback match: '{best_match.name}' by '{best_match.artists[0]}' (confidence: {best_confidence:.3f})")
                    return best_match
                else:
                    print(f"❌ Title+artist fallback: Best confidence {best_confidence:.3f} < {confidence_threshold}")
            else:
                print(f"❌ No results found for title+artist query: '{combined_query}'")
            
            return None
            
        except Exception as e:
            print(f"❌ Error in retry with title+artist: {e}")
            return None

class SpotifyDiscoveryManagerSignals(QObject):
    track_discovered = pyqtSignal(int, object, str)  # row, spotify_track, status
    progress_updated = pyqtSignal(int)  # current progress
    all_finished = pyqtSignal(int)  # total successful discoveries

class SpotifyDiscoveryWorker(QRunnable):
    def __init__(self, track_batch, spotify_client, worker_id, manager_signals):
        super().__init__()
        self.track_batch = track_batch  # List of (index, youtube_track) tuples
        self.spotify_client = spotify_client
        self.worker_id = worker_id
        self.signals = manager_signals
        self.is_cancelled = False
    
    def cancel(self):
        self.is_cancelled = True
    
    def run(self):
        """Process a batch of tracks with staggered delays to avoid rate limits"""
        import time
        
        # Stagger start times to spread out API calls
        initial_delay = self.worker_id * 0.2  # 200ms stagger between workers
        time.sleep(initial_delay)
        
        successful_discoveries = 0
        
        for track_index, youtube_track in self.track_batch:
            if self.is_cancelled:
                break
                
            try:
                # Create search query from YouTube track data
                if youtube_track.artists:
                    query = f"{youtube_track.artists[0]} {youtube_track.name}"
                else:
                    query = youtube_track.name
                
                # Search Spotify with rate limiting (built into spotify_client)
                spotify_results = self.spotify_client.search_tracks(query, limit=10)
                
                if spotify_results:
                    # Choose the best match preferring album versions
                    best_track = choose_best_spotify_match(spotify_results)
                    self.signals.track_discovered.emit(track_index, best_track, "found")
                    successful_discoveries += 1
                else:
                    # No Spotify match found
                    self.signals.track_discovered.emit(track_index, None, "not_found")
            
            except Exception as e:
                print(f"❌ Worker {self.worker_id} error searching Spotify for track {track_index}: {e}")
                self.signals.track_discovered.emit(track_index, None, f"error: {str(e)}")
            
            # Update progress
            self.signals.progress_updated.emit(track_index)
            
            # Distributed rate limiting - longer delay since we have multiple workers
            if not self.is_cancelled:
                time.sleep(0.5)  # 500ms delay with 3 workers = ~6 requests/second total
        
        print(f"🎵 Worker {self.worker_id} completed: {successful_discoveries} discoveries")

class SpotifyDiscoveryManager:
    def __init__(self, youtube_tracks, spotify_client, num_workers=3):
        self.youtube_tracks = youtube_tracks
        self.spotify_client = spotify_client
        self.num_workers = num_workers
        self.signals = SpotifyDiscoveryManagerSignals()
        self.workers = []
        self.completed_workers = 0
        self.total_successful = 0
        self.processed_tracks = set()
    
    def start_discovery(self):
        """Start concurrent Spotify discovery with multiple workers"""
        print(f"🚀 Starting Spotify discovery with {self.num_workers} concurrent workers")
        
        # Divide tracks among workers
        track_batches = self.distribute_tracks()
        
        # Create and start workers
        for worker_id, batch in enumerate(track_batches):
            worker = SpotifyDiscoveryWorker(batch, self.spotify_client, worker_id, self.signals)
            
            # Connect to progress tracking
            worker.signals.track_discovered.connect(self.on_track_discovered)
            worker.signals.progress_updated.connect(self.on_progress_updated)
            
            self.workers.append(worker)
            QThreadPool.globalInstance().start(worker)
    
    def distribute_tracks(self):
        """Distribute tracks evenly among workers"""
        total_tracks = len(self.youtube_tracks)
        tracks_per_worker = total_tracks // self.num_workers
        remainder = total_tracks % self.num_workers
        
        batches = []
        start_idx = 0
        
        for worker_id in range(self.num_workers):
            # Add one extra track to first 'remainder' workers
            batch_size = tracks_per_worker + (1 if worker_id < remainder else 0)
            end_idx = start_idx + batch_size
            
            # Create batch with (index, track) tuples
            batch = [(i, self.youtube_tracks[i]) for i in range(start_idx, end_idx)]
            batches.append(batch)
            
            print(f"📦 Worker {worker_id}: tracks {start_idx}-{end_idx-1} ({len(batch)} tracks)")
            start_idx = end_idx
        
        return batches
    
    def on_track_discovered(self, track_index, spotify_track, status):
        """Handle track discovery from any worker"""
        self.processed_tracks.add(track_index)
        if status == "found":
            self.total_successful += 1
        
        # Forward to UI
        self.signals.track_discovered.emit(track_index, spotify_track, status)
    
    def on_progress_updated(self, track_index):
        """Handle progress updates"""
        # Update overall progress based on completed tracks
        completed_count = len(self.processed_tracks)
        self.signals.progress_updated.emit(completed_count)
        
        # Check if all tracks are processed
        if completed_count >= len(self.youtube_tracks):
            self.signals.all_finished.emit(self.total_successful)
    
    def cancel_all(self):
        """Cancel all running workers"""
        for worker in self.workers:
            worker.cancel()
    
def choose_best_spotify_match(spotify_results):
    """Choose the best Spotify track from search results, preferring album versions"""
    if not spotify_results:
        return None
        
    # If only one result, return it
    if len(spotify_results) == 1:
        return spotify_results[0]
    
    # For now, just return the first result to avoid the complex scoring
    # TODO: Re-implement the scoring logic once we identify the attribute access issue
    return spotify_results[0]

class YouTubeParsingWorker(QThread):
    """Worker thread for parsing YouTube playlists without blocking the UI"""
    finished = pyqtSignal(object)  # Emits the playlist object
    error = pyqtSignal(str)  # Emits error message
    
    def __init__(self, url):
        super().__init__()
        self.url = url
    
    def run(self):
        """Parse the YouTube playlist in a separate thread"""
        try:
            print(f"🎵 Starting YouTube playlist parsing for: {self.url}")
            
            # Parse tracks using yt-dlp
            tracks_data, playlist_title = parse_youtube_playlist(self.url)
            
            if not tracks_data:
                self.error.emit("No tracks found in the playlist")
                return
            
            # Create playlist object with actual title
            playlist = create_youtube_playlist_object(tracks_data, self.url, playlist_title)
            
            print(f"✅ Successfully created playlist with {len(playlist.tracks)} tracks")
            self.finished.emit(playlist)
            
        except Exception as e:
            error_message = str(e)
            print(f"❌ YouTube parsing worker error: {error_message}")
            self.error.emit(error_message)


class ManualMatchModal(QDialog):
    """
    A completely redesigned modal for manually searching and resolving a failed track download.
    Features controlled searching, cancellation, and a UI consistent with the main application.
    This version dynamically updates its track list from the parent modal and has a live-updating count.
    """
    track_resolved = pyqtSignal(object)

    def __init__(self, parent_modal):
        """Initializes the modal with a direct reference to the parent."""
        super().__init__(parent_modal)
        self.parent_modal = parent_modal
        
        # Handle different parent modal types with flexible attribute access
        try:
            # Try the standard structure first (DownloadMissingTracksModal, DownloadMissingAlbumTracksModal)
            self.soulseek_client = parent_modal.parent_page.soulseek_client
            self.downloads_page = parent_modal.downloads_page
        except AttributeError:
            # Fallback for dashboard wishlist modal or other structures
            try:
                # Dashboard wishlist modal might have soulseek_client directly
                self.soulseek_client = getattr(parent_modal, 'soulseek_client', None)
                self.downloads_page = getattr(parent_modal, 'downloads_page', None)
                
                # If still not found, try to get from parent widget hierarchy
                if not self.soulseek_client:
                    current_widget = parent_modal.parent()
                    while current_widget and not self.soulseek_client:
                        self.soulseek_client = getattr(current_widget, 'soulseek_client', None)
                        self.downloads_page = getattr(current_widget, 'downloads_page', None)
                        current_widget = current_widget.parent()
                        
            except AttributeError:
                pass
                
        # Validate we have the required clients
        if not self.soulseek_client:
            raise RuntimeError("Could not find soulseek_client in parent modal or widget hierarchy")
        
        self.failed_tracks = []
        self.current_track_index = 0
        self.current_track_info = None
        self.search_worker = None
        self.thread_pool = QThreadPool.globalInstance()

        # Timer to delay automatic search
        self.search_delay_timer = QTimer(self)
        self.search_delay_timer.setSingleShot(True)
        self.search_delay_timer.timeout.connect(self.perform_manual_search)

        # Timer to periodically check for updates to the total failed track count
        self.live_update_timer = QTimer(self)
        self.live_update_timer.timeout.connect(self._check_and_update_count)
        self.live_update_timer.start(1000) # Check every second

        self.setup_ui()
        self.load_current_track()

    def setup_ui(self):
        """Set up the visually redesigned UI."""
        self.setWindowTitle("Manual Track Correction")
        self.setMinimumSize(900, 700)
        self.setStyleSheet("""
            QDialog { background-color: #1e1e1e; color: #ffffff; }
            QLabel { color: #ffffff; font-size: 14px; }
            QLineEdit {
                background-color: #3a3a3a;
                border: 1px solid #555555;
                border-radius: 6px;
                padding: 10px;
                color: #ffffff;
                font-size: 13px;
            }
            QScrollArea { border: none; background-color: #2d2d2d; }
            QWidget#resultsWidget { background-color: #2d2d2d; }
        """)
        
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(15)

        # --- Failed Track Info Card ---
        info_frame = QFrame()
        info_frame.setStyleSheet("""
            QFrame {
                background-color: #2d2d2d;
                border: 1px solid #444444;
                border-radius: 8px;
                padding: 15px;
            }
        """)
        info_layout = QVBoxLayout(info_frame)
        self.info_label = QLabel("Loading track...")
        self.info_label.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        self.info_label.setStyleSheet("color: #ffc107;") # Amber color for warning
        self.info_label.setWordWrap(True)
        info_layout.addWidget(self.info_label)
        main_layout.addWidget(info_frame)

        # --- Search Input and Controls ---
        search_frame = QFrame()
        search_layout = QHBoxLayout(search_frame)
        search_layout.setContentsMargins(0,0,0,0)
        search_layout.setSpacing(10)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Enter a new search query or use the suggestion...")
        self.search_input.returnPressed.connect(self.perform_manual_search)

        self.search_btn = QPushButton("Search")
        self.search_btn.clicked.connect(self.perform_manual_search)
        self.search_btn.setStyleSheet("""
            QPushButton {
                background-color: #1db954; color: #000000; border: none;
                border-radius: 6px; font-size: 13px; font-weight: bold;
                padding: 10px 20px;
            }
            QPushButton:hover { background-color: #1ed760; }
        """)

        self.cancel_search_btn = QPushButton("Cancel")
        self.cancel_search_btn.clicked.connect(self.cancel_current_search)
        self.cancel_search_btn.setStyleSheet("""
            QPushButton {
                background-color: #d32f2f; color: #ffffff; border: none;
                border-radius: 6px; font-size: 13px; font-weight: bold;
                padding: 10px 20px;
            }
            QPushButton:hover { background-color: #f44336; }
        """)
        self.cancel_search_btn.hide() # Initially hidden

        search_layout.addWidget(self.search_input, 1)
        search_layout.addWidget(self.search_btn)
        search_layout.addWidget(self.cancel_search_btn)
        main_layout.addWidget(search_frame)

        # --- Search Results Area ---
        self.results_scroll = QScrollArea()
        self.results_scroll.setWidgetResizable(True)
        self.results_widget = QWidget()
        self.results_widget.setObjectName("resultsWidget")
        self.results_layout = QVBoxLayout(self.results_widget)
        self.results_layout.setSpacing(8)
        self.results_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.results_scroll.setWidget(self.results_widget)
        main_layout.addWidget(self.results_scroll, 1)

        # --- Navigation and Close Buttons ---
        nav_layout = QHBoxLayout()
        self.prev_btn = QPushButton("← Previous")
        self.prev_btn.clicked.connect(self.load_previous_track)
        
        self.track_position_label = QLabel()
        self.track_position_label.setStyleSheet("color: #ffffff; font-weight: bold;")
        
        self.next_btn = QPushButton("Next →")
        self.next_btn.clicked.connect(self.load_next_track)
        
        self.close_btn = QPushButton("Close")
        self.close_btn.setStyleSheet("""
            QPushButton { background-color: #616161; color: #ffffff; }
            QPushButton:hover { background-color: #757575; }
        """)
        self.close_btn.clicked.connect(self.reject)
        
        for btn in [self.prev_btn, self.next_btn, self.close_btn]:
            btn.setFixedSize(120, 40)

        nav_layout.addWidget(self.prev_btn)
        nav_layout.addStretch()
        nav_layout.addWidget(self.track_position_label)
        nav_layout.addStretch()
        nav_layout.addWidget(self.next_btn)
        nav_layout.addWidget(self.close_btn)
        
        main_layout.addLayout(nav_layout)

    def _check_and_update_count(self):
        """
        Periodically called by a timer to check if the total number of failed
        tracks has changed and updates the navigation label if needed.
        """
        try:
            live_total = len(self.parent_modal.permanently_failed_tracks)
            
            # Extract the current total from the label text "Track X of Y"
            parts = self.track_position_label.text().split(' of ')
            if len(parts) == 2:
                displayed_total = int(parts[1])
                if live_total != displayed_total:
                    # If the total has changed, refresh the navigation state
                    self.update_navigation_state()
            else:
                # If the label is not in the expected format, update it anyway
                self.update_navigation_state()
        except (ValueError, IndexError):
            # Handle cases where the label text is not yet set or in an unexpected format
            self.update_navigation_state()


    def _update_track_list(self):
        """
        Syncs the modal's internal track list with the parent's live list,
        preserving the user's current position.
        """
        live_failed_tracks = self.parent_modal.permanently_failed_tracks
        old_count = len(self.failed_tracks) if hasattr(self, 'failed_tracks') else 0
        
        current_track_id = None
        if self.current_track_info:
            current_track_id = self.current_track_info.get('download_index')

        self.failed_tracks = list(live_failed_tracks)
        new_count = len(self.failed_tracks)
        
        print(f"🔄 Track list sync: {old_count} → {new_count} failed tracks, current_track_id={current_track_id}")

        if not self.failed_tracks:
            print("⚠️ No failed tracks remaining")
            return

        new_index = -1
        if current_track_id is not None:
            for i, track in enumerate(self.failed_tracks):
                if track.get('download_index') == current_track_id:
                    new_index = i
                    break
        
        old_index = self.current_track_index
        if new_index != -1:
            self.current_track_index = new_index
        else:
            # If the current track was resolved, stay at the same index
            # but check bounds against the new list length.
            if self.current_track_index >= len(self.failed_tracks):
                self.current_track_index = len(self.failed_tracks) - 1
        
        if self.current_track_index < 0:
            self.current_track_index = 0
            
        if old_index != self.current_track_index:
            print(f"📍 Index changed: {old_index} → {self.current_track_index}")

    def load_current_track(self):
        """Loads the current failed track's info and intelligently triggers a search."""
        self.cancel_current_search()
        self.clear_results()
        
        # Only sync track list if we don't already have the current track loaded
        # This prevents the index from being reset when navigating
        if not hasattr(self, 'failed_tracks') or len(self.failed_tracks) == 0:
            self._update_track_list()

        if not self.failed_tracks:
            QMessageBox.information(self, "Complete", "All failed tracks have been addressed.")
            self.accept()
            return

        # Ensure current_track_index is still valid after any potential sync
        if self.current_track_index >= len(self.failed_tracks):
            self.current_track_index = len(self.failed_tracks) - 1
        if self.current_track_index < 0:
            self.current_track_index = 0

        self.update_navigation_state()
        
        self.current_track_info = self.failed_tracks[self.current_track_index]
        spotify_track = self.current_track_info['spotify_track']
        artist = spotify_track.artists[0] if spotify_track.artists else "Unknown"
        
        print(f"📍 Loading track at index {self.current_track_index}: {spotify_track.name} by {artist}")
        
        # Use the original track name for the info label
        self.info_label.setText(f"Could not find: <b>{spotify_track.name}</b><br>by {artist}")
        
        # Use the ORIGINAL, UNCLEANED track name for the initial search query
        self.search_input.setText(f"{artist} {spotify_track.name}")
        
        self.search_delay_timer.start(1000)

    def load_next_track(self):
        """Navigate to the next failed track."""
        # Sync the track list first to handle any resolved tracks
        self._update_track_list()
        
        print(f"🔄 Next clicked: current_index={self.current_track_index}, failed_tracks_count={len(self.failed_tracks)}")
        
        if self.current_track_index < len(self.failed_tracks) - 1:
            self.current_track_index += 1
            print(f"✅ Moving to next track: new_index={self.current_track_index}")
            self.load_current_track()
        else:
            print(f"⚠️ Already at last track (index {self.current_track_index} of {len(self.failed_tracks)})")
    
    def load_previous_track(self):
        """Navigate to the previous failed track."""
        # Sync the track list first to handle any resolved tracks
        self._update_track_list()
        
        if self.current_track_index > 0:
            self.current_track_index -= 1
            self.load_current_track()
    
    def update_navigation_state(self):
        """Update the 'Track X of Y' label and enable/disable nav buttons."""
        # Use the internal synchronized list for consistency
        total_tracks = len(self.failed_tracks)
        
        # Ensure current_track_index is valid even if list shrinks
        if self.current_track_index >= total_tracks:
            self.current_track_index = max(0, total_tracks - 1)

        current_pos = self.current_track_index + 1 if total_tracks > 0 else 0
        
        self.track_position_label.setText(f"Track {current_pos} of {total_tracks}")
        self.prev_btn.setEnabled(self.current_track_index > 0)
        self.next_btn.setEnabled(self.current_track_index < total_tracks - 1)

    def perform_manual_search(self):
        """Initiates a search for the current query, cancelling any existing search."""
        self.search_delay_timer.stop()
        self.cancel_current_search()

        query = self.search_input.text().strip()
        if not query: return

        self.clear_results()
        self.results_layout.addWidget(QLabel(f"<h3>Searching for '{query}'...</h3>"))
        self.search_btn.hide()
        self.cancel_search_btn.show()

        self.search_worker = self.SearchWorker(self.soulseek_client, query)
        self.search_worker.signals.completed.connect(self.on_manual_search_completed)
        self.search_worker.signals.failed.connect(self.on_manual_search_failed)
        self.thread_pool.start(self.search_worker)

    def cancel_current_search(self):
        """Stops the currently running search worker."""
        if self.search_worker:
            self.search_worker.cancel()
            self.search_worker = None
        self.search_btn.show()
        self.cancel_search_btn.hide()

    def on_manual_search_completed(self, results):
        """Handles successful search results."""
        if not self.search_worker or self.search_worker.is_cancelled:
            return

        self.cancel_current_search()
        self.clear_results()

        if not results:
            self.results_layout.addWidget(QLabel("<h3>No results found for this query.</h3>"))
            return

        for result in results:
            self.results_layout.addWidget(self.create_result_widget(result))

    def on_manual_search_failed(self, error):
        """Handles a failed search attempt."""
        if not self.search_worker or self.search_worker.is_cancelled:
            return

        self.cancel_current_search()
        self.clear_results()
        self.results_layout.addWidget(QLabel(f"<h3>Search failed:</h3><p>{error}</p>"))

    def create_result_widget(self, result: TrackResult):
        """Creates a styled widget for a single search result."""
        widget = QFrame()
        widget.setStyleSheet("""
            QFrame {
                background-color: #3a3a3a;
                border: 1px solid #555555;
                border-radius: 6px;
                padding: 10px;
            }
            QFrame:hover {
                border: 1px solid #1db954;
            }
        """)
        layout = QHBoxLayout(widget)
        
        path_parts = result.filename.replace('\\', '/').split('/')
        filename = path_parts[-1]
        path_structure = '/'.join(path_parts[:-1])
        
        size_kb = result.size // 1024
        info_text = (f"<b>{filename}</b><br>"
                     f"<i style='color:#aaaaaa;'>{path_structure}</i><br>"
                     f"Quality: <b>{result.quality.upper()}</b>, "
                     f"Size: <b>{size_kb:,} KB</b>, "
                     f"User: <b>{result.username}</b>")
        info_label = QLabel(info_text)
        info_label.setWordWrap(True)
        
        select_btn = QPushButton("Select")
        select_btn.setFixedWidth(100)
        select_btn.setStyleSheet("""
            QPushButton {
                background-color: #1db954; color: #000000;
            }
            QPushButton:hover {
                background-color: #1ed760;
            }
        """)
        select_btn.clicked.connect(lambda: self.on_selection_made(result))
        
        layout.addWidget(info_label, 1)
        layout.addWidget(select_btn)
        return widget

    def on_selection_made(self, slskd_result):
        """
        Handles user selecting a track. The parent modal removes the track from the
        live list, and this modal will sync with that change on the next load.
        """
        print(f"Manual selection made: {slskd_result.filename}")
        
        self.parent_modal.start_validated_download_parallel(
            slskd_result, 
            self.current_track_info['spotify_track'], 
            self.current_track_info['track_index'], 
            self.current_track_info['table_index'], 
            self.current_track_info['download_index']
        )
        
        self.track_resolved.emit(self.current_track_info)
        
        # Auto-advance to the next failed track after successful selection
        # Use a small delay to allow the parent modal to update the failed tracks list
        QTimer.singleShot(100, self._advance_to_next_track_after_resolution)

    def _advance_to_next_track_after_resolution(self):
        """
        Advances to the next failed track after a successful manual resolution.
        If no more tracks remain, closes the modal with a success message.
        """
        # Sync the track list to reflect the resolved track being removed
        self._update_track_list()
        
        if not self.failed_tracks:
            # No more failed tracks - show success and close
            QMessageBox.information(self, "Complete", "All failed tracks have been resolved! 🎉")
            self.accept()
            return
            
        # Check if we need to adjust the current index after removal
        if self.current_track_index >= len(self.failed_tracks):
            self.current_track_index = len(self.failed_tracks) - 1
            
        # Load the next track (which might be at the same index if current was removed)
        print(f"🔄 Auto-advancing after resolution: index {self.current_track_index} of {len(self.failed_tracks)} remaining")
        self.load_current_track()

    def clear_results(self):
        """Removes all widgets from the results layout."""
        while self.results_layout.count():
            child = self.results_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

    def closeEvent(self, event):
        """Ensures any running search is cancelled when the modal is closed."""
        self.cancel_current_search()
        self.search_delay_timer.stop()
        self.live_update_timer.stop() # Stop the live update timer
        super().closeEvent(event)

    # --- Inner classes for self-contained search worker ---
    class SearchWorkerSignals(QObject):
        completed = pyqtSignal(list)
        failed = pyqtSignal(str)

    class SearchWorker(QRunnable):
        def __init__(self, soulseek_client, query):
            super().__init__()
            self.soulseek_client = soulseek_client
            self.query = query
            self.signals = ManualMatchModal.SearchWorkerSignals()
            self.is_cancelled = False

        def cancel(self):
            self.is_cancelled = True

        def run(self):
            if self.is_cancelled:
                return
            
            loop = None
            try:
                import asyncio
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

                search_result = loop.run_until_complete(self.soulseek_client.search(self.query))
                
                if self.is_cancelled:
                    return

                if isinstance(search_result, tuple) and len(search_result) >= 1:
                    results_list = search_result[0] if search_result[0] else []
                else:
                    results_list = []

                self.signals.completed.emit(results_list)

            except Exception as e:
                if not self.is_cancelled:
                    self.signals.failed.emit(str(e))
            finally:
                if loop:
                    loop.close()


class DownloadMissingTracksModal(QDialog):
    """Enhanced modal for downloading missing tracks with live progress tracking"""
    process_finished = pyqtSignal()
    def __init__(self, playlist, playlist_item, parent_page, downloads_page, is_youtube_workflow=False):
        super().__init__(parent_page)
        self.playlist = playlist
        self.playlist_item = playlist_item
        self.parent_page = parent_page
        self.parent_sync_page = parent_page  # Reference to sync page for scan manager
        self.downloads_page = downloads_page
        self.matching_engine = MusicMatchingEngine()
        self.wishlist_service = get_wishlist_service()
        self.is_youtube_workflow = is_youtube_workflow  # Flag to track if this is from YouTube discovery
        
        # State tracking
        self.total_tracks = len(playlist.tracks)
        self.matched_tracks_count = 0
        self.tracks_to_download_count = 0
        self.downloaded_tracks_count = 0
        self.analysis_complete = False
        
        # --- FIX: Initialize attributes to prevent crash on close ---
        self.download_in_progress = False
        self.cancel_requested = False
        
        self.permanently_failed_tracks = [] 
        self.cancelled_tracks = set()  # Track indices of cancelled tracks
        
        print(f"📊 Total tracks: {self.total_tracks}")
        
        # Track analysis results
        self.analysis_results = []
        self.missing_tracks = []
        
        # Worker tracking
        self.active_workers = []
        self.fallback_pools = []

        # Status Polling
        self.download_status_pool = QThreadPool()
        self.download_status_pool.setMaxThreadCount(1)
        self._is_status_update_running = False

        self.download_status_timer = QTimer(self)
        self.download_status_timer.timeout.connect(self.poll_all_download_statuses)
        self.download_status_timer.start(2000) 

        self.active_downloads = [] 
        
        print("🎨 Setting up UI...")
        self.setup_ui()
        print("✅ Modal initialization complete")

    def generate_smart_search_queries(self, artist_name, track_name):
        """
        Generate smart search query variations with album-in-title detection.
        Enhanced version with fallback strategies.
        """
        # Create a mock spotify track object for the matching engine
        class MockSpotifyTrack:
            def __init__(self, name, artists, album=None):
                self.name = name
                self.artists = artists if isinstance(artists, list) else [artists] if artists else []
                self.album = album
        
        # Try to get album information from the track context if available
        # In sync context, we might not always have album info, but try to extract it
        album_title = None
        # If track_name contains potential album info, we'll let the detection handle it
        
        mock_track = MockSpotifyTrack(track_name, [artist_name] if artist_name else [], album_title)
        
        # Use the enhanced matching engine to generate queries
        queries = self.matching_engine.generate_download_queries(mock_track)
        
        # Add some legacy fallback queries for compatibility
        legacy_queries = []
        
        # Add first word of artist approach (legacy compatibility)
        if artist_name:
            artist_words = artist_name.split()
            if artist_words:
                first_word = artist_words[0]
                if first_word.lower() == 'the' and len(artist_words) > 1:
                    first_word = artist_words[1]
                
                if len(first_word) > 1:
                    legacy_queries.append(f"{track_name} {first_word}".strip())
        
        # Add track-only query
        legacy_queries.append(track_name.strip())
        
        # Add traditional cleaned queries
        import re
        cleaned_name = re.sub(r'\s*\([^)]*\)', '', track_name).strip()
        cleaned_name = re.sub(r'\s*\[[^\]]*\]', '', cleaned_name).strip()
        
        if cleaned_name and cleaned_name.lower() != track_name.lower():
            legacy_queries.append(cleaned_name.strip())
        
        # Combine enhanced queries with legacy fallbacks
        all_queries = queries + legacy_queries
        
        # Remove duplicates while preserving order
        unique_queries = []
        seen = set()
        for query in all_queries:
            if query and query.lower() not in seen:
                unique_queries.append(query)
                seen.add(query.lower())
        
        print(f"🧠 Generated {len(unique_queries)} smart queries for '{track_name}' (enhanced with album detection)")
        for i, query in enumerate(unique_queries):
            print(f"   {i+1}. '{query}'")
        
        return unique_queries

    def setup_ui(self):
        """Set up the enhanced modal UI"""
        self.setWindowTitle(f"Download Missing Tracks - {self.playlist.name}")
        self.resize(1200, 900)
        self.setWindowFlags(Qt.WindowType.Window)
        # self.setWindowFlags(Qt.WindowType.Dialog)
        
        self.setStyleSheet("""
            QDialog { background-color: #1e1e1e; color: #ffffff; }
            QLabel { color: #ffffff; }
            QPushButton {
                background-color: #1db954; color: #000000; border: none;
                border-radius: 6px; font-size: 13px; font-weight: bold;
                padding: 10px 20px; min-width: 100px;
            }
            QPushButton:hover { background-color: #1ed760; }
            QPushButton:disabled { background-color: #404040; color: #888888; }
        """)
        
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(25, 25, 25, 25)
        main_layout.setSpacing(15)
        
        top_section = self.create_compact_top_section()
        main_layout.addWidget(top_section)
        
        progress_section = self.create_progress_section()
        main_layout.addWidget(progress_section)
        
        table_section = self.create_track_table()
        main_layout.addWidget(table_section, stretch=1)
        
        button_section = self.create_buttons()
        main_layout.addWidget(button_section)
    
    def is_downloading(self):
        """Check if any downloads are currently in progress"""
        return (self.download_in_progress or 
                not self.analysis_complete or
                len(self.active_workers) > 0 or
                (hasattr(self, 'tracks_to_download_count') and 
                 hasattr(self, 'downloaded_tracks_count') and 
                 self.downloaded_tracks_count < self.tracks_to_download_count))
        
    def create_compact_top_section(self):
        """Create compact top section with header and dashboard combined"""
        top_frame = QFrame()
        top_frame.setStyleSheet("""
            QFrame {
                background-color: #2d2d2d; border: 1px solid #444444;
                border-radius: 8px; padding: 15px;
            }
        """)
        
        layout = QVBoxLayout(top_frame)
        layout.setSpacing(15)
        
        header_layout = QHBoxLayout()
        title_section = QVBoxLayout()
        title_section.setSpacing(2)
        
        title = QLabel("Download Missing Tracks")
        title.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        title.setStyleSheet("color: #1db954;")
        
        subtitle = QLabel(f"Playlist: {self.playlist.name}")
        subtitle.setFont(QFont("Arial", 11))
        subtitle.setStyleSheet("color: #aaaaaa;")
        
        title_section.addWidget(title)
        title_section.addWidget(subtitle)
        
        dashboard_layout = QHBoxLayout()
        dashboard_layout.setSpacing(20)
        
        self.total_card = self.create_compact_counter_card("📀 Total", str(self.total_tracks), "#1db954")
        self.matched_card = self.create_compact_counter_card("✅ Found", "0", "#4CAF50")
        self.download_card = self.create_compact_counter_card("⬇️ Missing", "0", "#ff6b6b")
        self.downloaded_card = self.create_compact_counter_card("✅ Downloaded", "0", "#4CAF50")
        
        dashboard_layout.addWidget(self.total_card)
        dashboard_layout.addWidget(self.matched_card)
        dashboard_layout.addWidget(self.download_card)
        dashboard_layout.addWidget(self.downloaded_card)
        dashboard_layout.addStretch()
        
        header_layout.addLayout(title_section)
        header_layout.addStretch()
        header_layout.addLayout(dashboard_layout)
        
        layout.addLayout(header_layout)
        return top_frame
        
    def create_compact_counter_card(self, title, count, color):
        """Create a compact counter card widget"""
        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{
                background-color: #3a3a3a; border: 2px solid {color};
                border-radius: 6px; padding: 8px 12px; min-width: 80px;
            }}
        """)
        
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        
        count_label = QLabel(count)
        count_label.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        count_label.setStyleSheet(f"color: {color}; background: transparent;")
        count_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        title_label = QLabel(title)
        title_label.setFont(QFont("Arial", 9))
        title_label.setStyleSheet("color: #cccccc; background: transparent;")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        layout.addWidget(count_label)
        layout.addWidget(title_label)
        
        if "Total" in title: self.total_count_label = count_label
        elif "Found" in title: self.matched_count_label = count_label
        elif "Missing" in title: self.download_count_label = count_label
        elif "Downloaded" in title: self.downloaded_count_label = count_label
            
        return card
        
    def create_progress_section(self):
        """Create compact dual progress bar section"""
        progress_frame = QFrame()
        progress_frame.setStyleSheet("""
            QFrame {
                background-color: #2d2d2d; border: 1px solid #444444;
                border-radius: 8px; padding: 12px;
            }
        """)
        
        layout = QVBoxLayout(progress_frame)
        layout.setSpacing(8)
        
        analysis_container = QVBoxLayout()
        analysis_container.setSpacing(4)
        
        analysis_label = QLabel("🔍 Plex Analysis")
        analysis_label.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        analysis_label.setStyleSheet("color: #cccccc;")
        
        self.analysis_progress = QProgressBar()
        self.analysis_progress.setFixedHeight(20)
        self.analysis_progress.setStyleSheet("""
            QProgressBar {
                border: 1px solid #555555; border-radius: 10px; text-align: center;
                background-color: #444444; color: #ffffff; font-size: 11px; font-weight: bold;
            }
            QProgressBar::chunk { background-color: #1db954; border-radius: 9px; }
        """)
        self.analysis_progress.setVisible(False)
        
        analysis_container.addWidget(analysis_label)
        analysis_container.addWidget(self.analysis_progress)
        
        download_container = QVBoxLayout()
        download_container.setSpacing(4)
        
        download_label = QLabel("⬇️ Download Progress")
        download_label.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        download_label.setStyleSheet("color: #cccccc;")
        
        self.download_progress = QProgressBar()
        self.download_progress.setFixedHeight(20)
        self.download_progress.setStyleSheet("""
            QProgressBar {
                border: 1px solid #555555; border-radius: 10px; text-align: center;
                background-color: #444444; color: #ffffff; font-size: 11px; font-weight: bold;
            }
            QProgressBar::chunk { background-color: #ff6b6b; border-radius: 9px; }
        """)
        self.download_progress.setVisible(False)
        
        download_container.addWidget(download_label)
        download_container.addWidget(self.download_progress)
        
        layout.addLayout(analysis_container)
        layout.addLayout(download_container)
        
        return progress_frame
        
    def create_track_table(self):
        """Create enhanced track table"""
        table_frame = QFrame()
        table_frame.setStyleSheet("""
            QFrame {
                background-color: #2d2d2d; border: 1px solid #444444;
                border-radius: 8px; padding: 0px;
            }
        """)
        
        layout = QVBoxLayout(table_frame)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)
        
        header_label = QLabel("📋 Track Analysis")
        header_label.setFont(QFont("Arial", 13, QFont.Weight.Bold))
        header_label.setStyleSheet("color: #ffffff; padding: 5px;")
        
        self.track_table = QTableWidget()
        self.track_table.setColumnCount(6)
        self.track_table.setHorizontalHeaderLabels(["Track", "Artist", "Duration", "Matched", "Status", "Cancel"])
        self.track_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.track_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.track_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        self.track_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self.track_table.setColumnWidth(2, 90)
        self.track_table.setColumnWidth(3, 140)
        self.track_table.setColumnWidth(5, 70)
        
        self.track_table.setStyleSheet("""
            QTableWidget {
                background-color: #3a3a3a; alternate-background-color: #424242;
                selection-background-color: #1db954; selection-color: #000000;
                gridline-color: #555555; color: #ffffff; border: 1px solid #555555;
                font-size: 12px;
            }
            QHeaderView::section {
                background-color: #1db954; color: #000000; font-weight: bold;
                font-size: 13px; padding: 12px 8px; border: none;
            }
            QTableWidget::item { padding: 12px 8px; border-bottom: 1px solid #4a4a4a; }
        """)
        
        self.track_table.setAlternatingRowColors(True)
        self.track_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.track_table.verticalHeader().setDefaultSectionSize(50)
        self.track_table.verticalHeader().setVisible(False)
        
        self.populate_track_table()
        
        layout.addWidget(header_label)
        layout.addWidget(self.track_table)
        
        return table_frame
    
    def populate_track_table(self):
        """Populate track table with playlist tracks"""
        self.track_table.setRowCount(len(self.playlist.tracks))
        for i, track in enumerate(self.playlist.tracks):
            self.track_table.setItem(i, 0, QTableWidgetItem(track.name))
            artist_name = track.artists[0] if track.artists else "Unknown"
            self.track_table.setItem(i, 1, QTableWidgetItem(artist_name))
            duration = self.format_duration(track.duration_ms)
            duration_item = QTableWidgetItem(duration)
            duration_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.track_table.setItem(i, 2, duration_item)
            matched_item = QTableWidgetItem("⏳ Pending")
            matched_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.track_table.setItem(i, 3, matched_item)
            status_item = QTableWidgetItem("—")
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.track_table.setItem(i, 4, status_item)
            
            # Create empty container for cancel button (will be populated later for missing tracks only)
            container = QWidget()
            container.setStyleSheet("background: transparent;")
            layout = QVBoxLayout(container)
            layout.setContentsMargins(5, 5, 5, 5)
            layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            
            self.track_table.setCellWidget(i, 5, container)
            
            for col in range(5):
                self.track_table.item(i, col).setFlags(self.track_table.item(i, col).flags() & ~Qt.ItemFlag.ItemIsEditable)

    def format_duration(self, duration_ms):
        """Convert milliseconds to MM:SS format"""
        seconds = duration_ms // 1000
        return f"{seconds // 60}:{seconds % 60:02d}"
    
    def add_cancel_button_to_row(self, row):
        """Add cancel button to a specific row (only for missing tracks)"""
        container = self.track_table.cellWidget(row, 5)
        if container and container.layout().count() == 0:  # Only add if container is empty
            cancel_button = QPushButton("×")
            cancel_button.setFixedSize(20, 20)
            cancel_button.setMinimumSize(20, 20)
            cancel_button.setMaximumSize(20, 20)
            cancel_button.setStyleSheet("""
                QPushButton {
                    background-color: #dc3545; 
                    color: white; 
                    border: 1px solid #c82333;
                    border-radius: 3px; 
                    font-size: 14px; 
                    font-weight: bold;
                    padding: 0px;
                    margin: 0px;
                    text-align: center;
                    min-width: 20px;
                    max-width: 20px;
                    width: 20px;
                }
                QPushButton:hover { 
                    background-color: #c82333; 
                    border-color: #bd2130;
                }
                QPushButton:pressed { 
                    background-color: #bd2130; 
                    border-color: #b21f2d;
                }
                QPushButton:disabled { 
                    background-color: #28a745; 
                    color: white; 
                    border-color: #1e7e34;
                }
            """)
            cancel_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            cancel_button.clicked.connect(lambda checked, row_idx=row: self.cancel_track(row_idx))
            
            layout = container.layout()
            layout.addWidget(cancel_button)
    
    def hide_cancel_button_for_row(self, row):
        """Hide cancel button for a specific row (when track is downloaded)"""
        container = self.track_table.cellWidget(row, 5)
        if container:
            layout = container.layout()
            if layout and layout.count() > 0:
                cancel_button = layout.itemAt(0).widget()
                if cancel_button:
                    cancel_button.setVisible(False)
                    print(f"🫥 Hidden cancel button for downloaded track at row {row}")
    
    def cancel_track(self, row):
        """Cancel a specific track - works at any phase"""
        # Get cancel button and disable it
        container = self.track_table.cellWidget(row, 5)
        if container:
            layout = container.layout()
            if layout and layout.count() > 0:
                cancel_button = layout.itemAt(0).widget()
                if cancel_button:
                    cancel_button.setEnabled(False)
                    cancel_button.setText("✓")
        
        # Update status to cancelled
        self.track_table.setItem(row, 4, QTableWidgetItem("🚫 Cancelled"))
        
        # Add to cancelled tracks set
        if not hasattr(self, 'cancelled_tracks'):
            self.cancelled_tracks = set()
        self.cancelled_tracks.add(row)
        
        track = self.playlist.tracks[row]
        print(f"🚫 Track cancelled: {track.name} (row {row})")
        
        # If downloads are active, also handle active download cancellation
        download_index = None
        
        # Check active_downloads list
        if hasattr(self, 'active_downloads'):
            for download in self.active_downloads:
                if download.get('table_index') == row:
                    download_index = download.get('download_index', row)
                    print(f"🚫 Found active download {download_index} for cancelled track")
                    break
        
        # Check parallel_search_tracking for download index
        if download_index is None and hasattr(self, 'parallel_search_tracking'):
            for idx, track_info in self.parallel_search_tracking.items():
                if track_info.get('table_index') == row:
                    download_index = idx
                    print(f"🚫 Found parallel tracking {download_index} for cancelled track")
                    break
        
        # If we found an active download, trigger completion to free up the worker
        if download_index is not None and hasattr(self, 'on_parallel_track_completed'):
            print(f"🚫 Triggering completion for active download {download_index}")
            self.on_parallel_track_completed(download_index, success=False)
        
    def create_buttons(self):
            """Create improved button section"""
            button_frame = QFrame(styleSheet="background-color: transparent; padding: 10px;")
            layout = QHBoxLayout(button_frame)
            layout.setSpacing(15)
            layout.setContentsMargins(0, 10, 0, 0)

            self.correct_failed_btn = QPushButton("🔧 Correct Failed Matches")
            self.correct_failed_btn.setFixedWidth(220)
            self.correct_failed_btn.setStyleSheet("""
                QPushButton { background-color: #ffc107; color: #000000; border-radius: 20px; font-weight: bold; }
                QPushButton:hover { background-color: #ffca28; }
            """)
            self.correct_failed_btn.clicked.connect(self.on_correct_failed_matches_clicked)
            self.correct_failed_btn.hide()
            
            self.begin_search_btn = QPushButton("Begin Search")
            self.begin_search_btn.setFixedSize(160, 40)
            # THIS IS THE FIX: The specific stylesheet for this button is restored below
            self.begin_search_btn.setStyleSheet("""
                QPushButton {
                    background-color: #1db954; color: #000000; border: none;
                    border-radius: 20px; font-size: 14px; font-weight: bold;
                }
                QPushButton:hover { background-color: #1ed760; }
            """)
            self.begin_search_btn.clicked.connect(self.on_begin_search_clicked)
            
            self.cancel_btn = QPushButton("Cancel")
            self.cancel_btn.setFixedSize(110, 40)
            self.cancel_btn.setStyleSheet("""
                QPushButton { background-color: #d32f2f; color: #ffffff; border-radius: 20px;}
                QPushButton:hover { background-color: #f44336; }
            """)
            self.cancel_btn.clicked.connect(self.on_cancel_clicked)
            self.cancel_btn.hide()
            
            self.close_btn = QPushButton("Close")
            self.close_btn.setFixedSize(110, 40)
            self.close_btn.setStyleSheet("""
                QPushButton { background-color: #616161; color: #ffffff; border-radius: 20px;}
                QPushButton:hover { background-color: #757575; }
            """)
            self.close_btn.clicked.connect(self.on_close_clicked)
            
            layout.addStretch()
            layout.addWidget(self.begin_search_btn)
            layout.addWidget(self.cancel_btn)
            layout.addWidget(self.correct_failed_btn)
            layout.addWidget(self.close_btn)
            
            return button_frame
        

    def on_begin_search_clicked(self):
        """Handle Begin Search button click - starts Plex analysis"""
        # Only update refresh button state for Spotify workflows, not YouTube workflows
        if not self.is_youtube_workflow:
            # --- FIX: Trigger the UI change on the main page ---
            # This is the correct point to signal that the process has started.
            self.parent_page.on_download_process_started(self.playlist.id, self.playlist_item)

        self.begin_search_btn.hide()
        self.cancel_btn.show()
        self.analysis_progress.setVisible(True)
        self.analysis_progress.setMaximum(self.total_tracks)
        self.analysis_progress.setValue(0)
        self.download_in_progress = True # Set flag
        self.start_plex_analysis()

        
    def start_plex_analysis(self):
        """Start media server analysis using existing worker"""
        from config.settings import config_manager
        active_server = config_manager.get_active_media_server()
        
        if active_server == "plex":
            media_client = getattr(self.parent_page, 'plex_client', None)
        else:  # jellyfin
            media_client = getattr(self.parent_page, 'jellyfin_client', None)
            
        worker = PlaylistTrackAnalysisWorker(self.playlist.tracks, media_client, active_server)
        worker.signals.analysis_started.connect(self.on_analysis_started)
        worker.signals.track_analyzed.connect(self.on_track_analyzed)
        worker.signals.analysis_completed.connect(self.on_analysis_completed)
        worker.signals.analysis_failed.connect(self.on_analysis_failed)
        self.active_workers.append(worker)
        QThreadPool.globalInstance().start(worker)
            
    def on_analysis_started(self, total_tracks):
        print(f"🔍 Analysis started for {total_tracks} tracks")
        
    def on_track_analyzed(self, track_index, result):
        """Handle individual track analysis completion with live UI updates"""
        self.analysis_progress.setValue(track_index)
        row_index = track_index - 1
        if result.exists_in_plex:
            matched_text = f"✅ Found ({result.confidence:.1f})"
            self.matched_tracks_count += 1
            self.matched_count_label.setText(str(self.matched_tracks_count))
        else:
            matched_text = "❌ Missing"
            self.tracks_to_download_count += 1
            self.download_count_label.setText(str(self.tracks_to_download_count))
            # Add cancel button for missing tracks only
            self.add_cancel_button_to_row(row_index)
        self.track_table.setItem(row_index, 3, QTableWidgetItem(matched_text))
        
    def on_analysis_completed(self, results):
        """Handle analysis completion"""
        self.analysis_complete = True
        self.analysis_results = results
        self.missing_tracks = [r for r in results if not r.exists_in_plex]
        print(f"✅ Analysis complete: {len(self.missing_tracks)} to download")
        if self.missing_tracks:
            # --- FIX: This line was missing, which prevented downloads from starting. ---
            self.start_download_progress()
        else:
            # Handle case where no tracks are missing
            self.download_in_progress = False # Mark process as finished
            self.cancel_btn.hide()
            
            # If this is a YouTube workflow, clean up status widget (no downloads needed)
            if self.is_youtube_workflow and hasattr(self.parent_page, 'show_youtube_placeholder'):
                self.parent_page.show_youtube_placeholder()
                if self.playlist.id in self.parent_page.active_youtube_download_modals:
                    del self.parent_page.active_youtube_download_modals[self.playlist.id]
            
            # The modal now stays open.
            # The process_finished signal is still emitted to unlock the main UI.
            self.process_finished.emit() 
            QMessageBox.information(self, "Analysis Complete", "All tracks already exist in Plex! No downloads needed.")
            
    def on_analysis_failed(self, error_message):
        print(f"❌ Analysis failed: {error_message}")
        QMessageBox.critical(self, "Analysis Failed", f"Failed to analyze tracks: {error_message}")
        self.cancel_btn.hide()
        self.begin_search_btn.show()
        
    def start_download_progress(self):
        """Start actual download progress tracking"""
        self.download_progress.setVisible(True)
        self.download_progress.setMaximum(len(self.missing_tracks))
        self.download_progress.setValue(0)
        self.start_parallel_downloads()
    
    def start_parallel_downloads(self):
        """Start multiple track downloads in parallel for better performance"""
        self.active_parallel_downloads = 0
        self.download_queue_index = 0
        self.failed_downloads = 0
        self.completed_downloads = 0
        self.successful_downloads = 0
        self.start_next_batch_of_downloads()
    
    def start_next_batch_of_downloads(self, max_concurrent=3):
        """Start the next batch of downloads up to the concurrent limit"""
        while (self.active_parallel_downloads < max_concurrent and 
               self.download_queue_index < len(self.missing_tracks)):
            track_result = self.missing_tracks[self.download_queue_index]
            track = track_result.spotify_track
            track_index = self.find_track_index_in_playlist(track)
            
            # Skip if track was cancelled
            if hasattr(self, 'cancelled_tracks') and track_index in self.cancelled_tracks:
                print(f"🚫 Skipping cancelled track at index {track_index}: {track.name}")
                self.download_queue_index += 1
                self.completed_downloads += 1
                continue
                
            self.track_table.setItem(track_index, 4, QTableWidgetItem("🔍 Searching..."))
            self.search_and_download_track_parallel(track, self.download_queue_index, track_index)
            self.active_parallel_downloads += 1
            self.download_queue_index += 1
        
        # Check if we're done: either all downloads completed OR all remaining work is done
        downloads_complete = (self.download_queue_index >= len(self.missing_tracks) and self.active_parallel_downloads == 0)
        all_work_complete = (self.completed_downloads >= len(self.missing_tracks))
        
        if downloads_complete or all_work_complete:
            self.on_all_downloads_complete()
    
    def search_and_download_track_parallel(self, spotify_track, download_index, track_index):
        """Search for track and download via infrastructure path - PARALLEL VERSION"""
        artist_name = spotify_track.artists[0] if spotify_track.artists else ""
        search_queries = self.generate_smart_search_queries(artist_name, spotify_track.name)
        self.start_track_search_with_queries_parallel(spotify_track, search_queries, track_index, track_index, download_index)
    
    def start_track_search_with_queries_parallel(self, spotify_track, search_queries, track_index, table_index, download_index):
        """Start track search with parallel completion handling"""
        if not hasattr(self, 'parallel_search_tracking'):
            self.parallel_search_tracking = {}
        
        self.parallel_search_tracking[download_index] = {
            'spotify_track': spotify_track, 'track_index': track_index,
            'table_index': table_index, 'download_index': download_index,
            'completed': False, 'used_sources': set(), 'candidates': [], 'retry_count': 0
        }
        self.start_search_worker_parallel(search_queries, spotify_track, track_index, table_index, 0, download_index)

    def start_search_worker_parallel(self, queries, spotify_track, track_index, table_index, query_index, download_index):
        """Start search worker with parallel completion handling."""
        if query_index >= len(queries):
            self.on_parallel_track_failed(download_index, "All search strategies failed")
            return

        query = queries[query_index]
        worker = self.ParallelSearchWorker(self.parent_page.soulseek_client, query)
        
        worker.signals.search_completed.connect(
            lambda r, q: self.on_search_query_completed_parallel(r, queries, spotify_track, track_index, table_index, query_index, q, download_index)
        )
        worker.signals.search_failed.connect(
            lambda q, e: self.on_search_query_completed_parallel([], queries, spotify_track, track_index, table_index, query_index, q, download_index)
        )
        QThreadPool.globalInstance().start(worker)

    def on_search_query_completed_parallel(self, results, queries, spotify_track, track_index, table_index, query_index, query, download_index):
        """Handle completion of a parallel search query. If it fails, trigger the next query."""
        if hasattr(self, 'cancel_requested') and self.cancel_requested: return
            
        valid_candidates = self.get_valid_candidates(results, spotify_track, query)
        
        if valid_candidates:
            # IMPORTANT: Cache the candidates for future retries
            self.parallel_search_tracking[download_index]['candidates'] = valid_candidates
            best_match = valid_candidates[0]
            self.start_validated_download_parallel(best_match, spotify_track, track_index, table_index, download_index)
            return

        next_query_index = query_index + 1
        if next_query_index < len(queries):
            self.start_search_worker_parallel(queries, spotify_track, track_index, table_index, next_query_index, download_index)
        else:
            self.on_parallel_track_failed(download_index, f"No valid results after trying all {len(queries)} queries.")

    def start_validated_download_parallel(self, slskd_result, spotify_metadata, track_index, table_index, download_index):
        """
        Start download with validated metadata. This is used for both initial downloads
        and for manual retries from the 'Correct Failed Matches' modal.
        """
        track_info = self.parallel_search_tracking[download_index]

        # --- FIX ---
        # If this track was previously marked as 'completed' (e.g., from a failure),
        # we need to reset its state to allow the new download attempt to be tracked correctly.
        if track_info.get('completed', False):
            print(f"🔄 Resetting state for manually retried track (index: {download_index}).")
            track_info['completed'] = False
            
            # Decrement the failed count since we are retrying it.
            if self.failed_downloads > 0:
                self.failed_downloads -= 1
            
            # This download is now active again. The counter was decremented when it failed,
            # so we increment it here to reflect its new active status.
            self.active_parallel_downloads += 1
            
            # The 'completed_downloads' counter was incremented when the track originally failed.
            # We decrement it here so the overall progress calculation remains accurate when
            # this new download attempt completes.
            if self.completed_downloads > 0:
                self.completed_downloads -= 1

        # Add the new download source to the used sources to prevent retrying with the same user/file
        source_key = f"{getattr(slskd_result, 'username', 'unknown')}_{slskd_result.filename}"
        track_info['used_sources'].add(source_key)
        
        # Update UI to show the new download has been queued
        spotify_based_result = self.create_spotify_based_search_result_from_validation(slskd_result, spotify_metadata)
        print(f"🔧 Updating table at index {table_index} to '... Queued' for manual retry")
        self.track_table.setItem(table_index, 4, QTableWidgetItem("... Queued"))
        
        # Start the actual download process
        self.start_matched_download_via_infrastructure_parallel(spotify_based_result, track_index, table_index, download_index)
    
    def start_matched_download_via_infrastructure_parallel(self, spotify_based_result, track_index, table_index, download_index):
        """Start infrastructure download with parallel completion tracking"""
        try:
            artist = type('Artist', (), {'name': spotify_based_result.artist})()
            download_item = self.downloads_page._start_download_with_artist(spotify_based_result, artist)
            
            if download_item:
                self.active_downloads.append({
                    'download_index': download_index, 'track_index': track_index,
                    'table_index': table_index, 'download_id': download_item.download_id,
                    'slskd_result': spotify_based_result, 'candidates': self.parallel_search_tracking[download_index]['candidates']
                })
            else:
                self.on_parallel_track_failed(download_index, "Failed to start download")
        except Exception as e:
            self.on_parallel_track_failed(download_index, str(e))
    
    def poll_all_download_statuses(self):
        """
        Starts the background worker to process download statuses.
        This version is updated to use the new worker and pass the correct data.
        """
        if self._is_status_update_running or not self.active_downloads:
            return
        self._is_status_update_running = True
        
        # Create a snapshot of data needed by the worker thread
        items_to_check = []
        for d in self.active_downloads:
            # Ensure slskd_result exists and has a filename
            if d.get('slskd_result') and hasattr(d['slskd_result'], 'filename'):
                # Pass the current missing count to the worker so it can be incremented
                items_to_check.append({
                    'widget_id': d['download_index'], 
                    'download_id': d.get('download_id'), # Use .get for safety
                    'file_path': d['slskd_result'].filename,
                    'api_missing_count': d.get('api_missing_count', 0)
                })

        if not items_to_check:
            self._is_status_update_running = False
            return
        
        # The new worker doesn't need the transfers directory.
        worker = SyncStatusProcessingWorker(
            self.parent_page.soulseek_client, 
            items_to_check
        )
        
        worker.signals.completed.connect(self._handle_processed_status_updates)
        worker.signals.error.connect(lambda e: print(f"Status Worker Error: {e}"))
        self.download_status_pool.start(worker)




    def _handle_processed_status_updates(self, results):
        """
        Applies status updates from the background worker and triggers retry logic.
        This version correctly handles the payload from the new worker and adds a timeout for stuck downloads.
        """
        import time
        
        # Create a lookup for faster access to active download items
        active_downloads_map = {d['download_index']: d for d in self.active_downloads}

        for result in results:
            download_index = result['widget_id']
            new_status = result['status']
            
            download_info = active_downloads_map.get(download_index)
            if not download_info:
                continue

            # Update the main download_info object with the latest missing count from the worker
            # This is important for the grace period logic to work across polls.
            if 'api_missing_count' in result:
                 download_info['api_missing_count'] = result['api_missing_count']

            # Update the download_id if the worker found a match by filename
            if result.get('transfer_id') and download_info.get('download_id') != result['transfer_id']:
                print(f"ℹ️ Corrected download ID for '{download_info['slskd_result'].filename}'")
                download_info['download_id'] = result['transfer_id']

            # Handle terminal states (completed, failed, cancelled)
            if new_status in ['failed', 'cancelled']:
                if download_info in self.active_downloads:
                    self.active_downloads.remove(download_info)
                self.retry_parallel_download_with_fallback(download_info)

            elif new_status == 'completed':
                if download_info in self.active_downloads:
                    self.active_downloads.remove(download_info)
                self.on_parallel_track_completed(download_index, success=True)

            # Handle transient states (downloading, queued)
            elif new_status == 'downloading':
                 progress = result.get('progress', 0)
                 self.track_table.setItem(download_info['table_index'], 4, QTableWidgetItem(f"⏬ Downloading ({progress}%)"))
                 
                 # Reset queue timer if it exists
                 if 'queued_start_time' in download_info:
                     del download_info['queued_start_time']

                 # --- FIX: Add timeout for downloads stuck at 0% ---
                 # This handles cases where the API reports "InProgress" but no data is moving.
                 if progress < 1:
                     if 'downloading_start_time' not in download_info:
                         download_info['downloading_start_time'] = time.time()
                     # 90-second timeout for being stuck at 0%
                     elif time.time() - download_info['downloading_start_time'] > 90:
                         print(f"⚠️ Download for '{download_info['slskd_result'].filename}' is stuck at 0%. Cancelling and retrying.")
                         # Cancel the old download before retry
                         self.cancel_download_before_retry(download_info)
                         if download_info in self.active_downloads:
                             self.active_downloads.remove(download_info)
                         self.retry_parallel_download_with_fallback(download_info)
                 else:
                     # Progress is being made, reset the timer
                     if 'downloading_start_time' in download_info:
                         del download_info['downloading_start_time']


            elif new_status == 'queued':
                 self.track_table.setItem(download_info['table_index'], 4, QTableWidgetItem("... Queued"))
                 # Start a timer to detect if it's stuck in queue
                 if 'queued_start_time' not in download_info:
                     download_info['queued_start_time'] = time.time()
                 elif time.time() - download_info['queued_start_time'] > 90: # 90-second timeout
                     print(f"⚠️ Download for '{download_info['slskd_result'].filename}' is stuck in queue. Cancelling and retrying.")
                     # Cancel the old download before retry
                     self.cancel_download_before_retry(download_info)
                     if download_info in self.active_downloads:
                         self.active_downloads.remove(download_info)
                     self.retry_parallel_download_with_fallback(download_info)
        
        self._is_status_update_running = False

    def cancel_download_before_retry(self, download_info):
        """Cancel the current download before retrying with alternative source"""
        try:
            slskd_result = download_info.get('slskd_result')
            if not slskd_result:
                print("⚠️ No slskd_result found in download_info for cancellation")
                return
            
            # Extract download details for cancellation
            download_id = download_info.get('download_id')
            username = getattr(slskd_result, 'username', None)
            
            if download_id and username:
                print(f"🚫 Cancelling timed-out download: {download_id} from {username}")
                
                # Use asyncio to call the async cancel method
                import asyncio
                try:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    success = loop.run_until_complete(
                        self.soulseek_client.cancel_download(download_id, username, remove=False)
                    )
                    if success:
                        print(f"✅ Successfully cancelled download {download_id}")
                    else:
                        print(f"⚠️ Failed to cancel download {download_id}")
                finally:
                    loop.close()
            else:
                print(f"⚠️ Missing download_id ({download_id}) or username ({username}) for cancellation")
                
        except Exception as e:
            print(f"❌ Error cancelling download: {e}")

    def retry_parallel_download_with_fallback(self, failed_download_info):
        """Retries a failed download by selecting the next-best cached candidate."""
        download_index = failed_download_info['download_index']
        track_info = self.parallel_search_tracking[download_index]
        
        track_info['retry_count'] += 1
        if track_info['retry_count'] > 2: # Max 3 attempts total (1 initial + 2 retries)
            self.on_parallel_track_failed(download_index, "All retries failed.")
            return

        candidates = failed_download_info.get('candidates', [])
        used_sources = track_info.get('used_sources', set())
        
        next_candidate = None
        for candidate in candidates:
            source_key = f"{getattr(candidate, 'username', 'unknown')}_{candidate.filename}"
            if source_key not in used_sources:
                next_candidate = candidate
                break

        if not next_candidate:
            self.on_parallel_track_failed(download_index, "No alternative sources in cache")
            return

        print(f"🔄 Retrying download {download_index + 1} with next candidate: {next_candidate.filename}")
        self.track_table.setItem(failed_download_info['table_index'], 4, QTableWidgetItem(f"🔄 Retrying ({track_info['retry_count']})..."))
        
        self.start_validated_download_parallel(
            next_candidate, track_info['spotify_track'], track_info['track_index'],
            track_info['table_index'], download_index
        )

    def on_parallel_track_completed(self, download_index, success):
        """Handle completion of a parallel track download"""
        if not hasattr(self, 'parallel_search_tracking'):
            print(f"⚠️ parallel_search_tracking not initialized yet, skipping completion for download {download_index}")
            return
        track_info = self.parallel_search_tracking.get(download_index)
        if not track_info or track_info.get('completed', False): return
        
        track_info['completed'] = True
        if success:
            print(f"🔧 Track {download_index} completed successfully - updating table index {track_info['table_index']} to '✅ Downloaded'")
            self.track_table.setItem(track_info['table_index'], 4, QTableWidgetItem("✅ Downloaded"))
            # Hide cancel button since track is now downloaded
            self.hide_cancel_button_for_row(track_info['table_index'])
            self.downloaded_tracks_count += 1
            # --- FIX ---
            # Corrected the label update to use the incremented counter variable.
            self.downloaded_count_label.setText(str(self.downloaded_tracks_count))
            self.successful_downloads += 1
            
            # Update YouTube card progress if this is a YouTube workflow
            if self.is_youtube_workflow and hasattr(self, 'youtube_url') and hasattr(self.parent_page, 'update_youtube_card_progress'):
                self.parent_page.update_youtube_card_progress(
                    self.youtube_url,
                    total=len(self.missing_tracks),
                    matched=self.successful_downloads,
                    failed=len(self.permanently_failed_tracks)
                )
        else:
            # Check if track was cancelled (don't overwrite cancelled status)
            table_index = track_info['table_index']
            current_status = self.track_table.item(table_index, 4)
            if current_status and "🚫 Cancelled" in current_status.text():
                print(f"🔧 Track {download_index} was cancelled - preserving cancelled status")
            else:
                print(f"🔧 Track {download_index} failed - updating table index {table_index} to '❌ Failed'")
                self.track_table.setItem(table_index, 4, QTableWidgetItem("❌ Failed"))
                if track_info not in self.permanently_failed_tracks:
                    self.permanently_failed_tracks.append(track_info)
                self.update_failed_matches_button()
            self.failed_downloads += 1
            
            # Update YouTube card progress if this is a YouTube workflow
            if self.is_youtube_workflow and hasattr(self, 'youtube_url') and hasattr(self.parent_page, 'update_youtube_card_progress'):
                self.parent_page.update_youtube_card_progress(
                    self.youtube_url,
                    total=len(self.missing_tracks),
                    matched=self.successful_downloads,
                    failed=len(self.permanently_failed_tracks)
                )
        
        self.completed_downloads += 1
        self.active_parallel_downloads -= 1
        self.download_progress.setValue(self.completed_downloads)
        self.start_next_batch_of_downloads()
    
    def on_parallel_track_failed(self, download_index, reason):
        """Handle failure of a parallel track download"""
        print(f"❌ Parallel download {download_index + 1} failed: {reason}")
        self.on_parallel_track_completed(download_index, False)
    
    def update_failed_matches_button(self):
        """Shows, hides, and updates the counter on the 'Correct Failed Matches' button."""
        count = len(self.permanently_failed_tracks)
        if count > 0:
            self.correct_failed_btn.setText(f"🔧 Correct {count} Failed Match{'es' if count > 1 else ''}")
            self.correct_failed_btn.show()
        else:
            self.correct_failed_btn.hide()

    def on_correct_failed_matches_clicked(self):
        """Opens the modal to manually correct failed downloads."""
        if not self.permanently_failed_tracks: return
        manual_modal = ManualMatchModal(self)
        manual_modal.track_resolved.connect(self.on_manual_match_resolved)
        manual_modal.exec()

    def on_manual_match_resolved(self, resolved_track_info):
        """Handles a track being successfully resolved by the ManualMatchModal."""
        print(f"🔧 Manual match resolved - download_index: {resolved_track_info.get('download_index')}, table_index: {resolved_track_info.get('table_index')}")
        original_failed_track = next((t for t in self.permanently_failed_tracks if t['download_index'] == resolved_track_info['download_index']), None)
        if original_failed_track:
            self.permanently_failed_tracks.remove(original_failed_track)
            print(f"✅ Removed track from permanently_failed_tracks - remaining: {len(self.permanently_failed_tracks)}")
            
            # Update progress bar to account for manually resolved track
            # The track was manually resolved, so we need to count it as "completed"
            self.successful_downloads += 1
            self.completed_downloads += 1
            # Update the progress bar maximum to reflect the actual remaining work
            total_remaining_work = len(self.missing_tracks) - (self.successful_downloads - len(self.permanently_failed_tracks))
            if total_remaining_work > 0:
                # Recalculate progress: completed work / total original work
                progress_value = self.completed_downloads
                self.download_progress.setValue(progress_value)
                print(f"📊 Updated progress: {progress_value}/{self.download_progress.maximum()} (manual fix)")
        else:
            print("⚠️ Could not find original failed track to remove")
        self.update_failed_matches_button()
            
    def find_track_index_in_playlist(self, spotify_track):
        """Find the table row index for a given Spotify track"""
        for i, playlist_track in enumerate(self.playlist.tracks):
            if playlist_track.id == spotify_track.id:
                return i
        return None
        
    def on_all_downloads_complete(self):
            """Handle completion of all downloads"""
            self.download_in_progress = False
            print("🎉 All downloads completed!")
            self.cancel_btn.hide()
            
            # If this is a YouTube workflow, update card and clean up
            if self.is_youtube_workflow:
                # Update card to download_complete phase
                if hasattr(self, 'youtube_url') and hasattr(self.parent_page, 'update_youtube_card_phase'):
                    self.parent_page.update_youtube_card_phase(self.youtube_url, 'download_complete')
                
                # Clean up status widget
                if hasattr(self.parent_page, 'show_youtube_placeholder'):
                    self.parent_page.show_youtube_placeholder()
                if self.playlist.id in self.parent_page.active_youtube_download_modals:
                    del self.parent_page.active_youtube_download_modals[self.playlist.id]
            
            # The process_finished signal is still emitted to unlock the main UI.
            self.process_finished.emit()
            
            # Request Plex library scan if we have successful downloads
            if self.successful_downloads > 0 and hasattr(self, 'parent_sync_page') and self.parent_sync_page.scan_manager:
                self.parent_sync_page.scan_manager.request_scan(f"Playlist download completed ({self.successful_downloads} tracks)")

            # Add cancelled tracks that were missing from Plex to permanently_failed_tracks for wishlist inclusion
            if hasattr(self, 'cancelled_tracks') and hasattr(self, 'missing_tracks'):
                for cancelled_row in self.cancelled_tracks:
                    # Check if this cancelled track was actually missing from Plex
                    cancelled_track = self.playlist.tracks[cancelled_row]
                    missing_track_result = None
                    
                    # Find the corresponding missing track result
                    for missing_result in self.missing_tracks:
                        if missing_result.spotify_track.id == cancelled_track.id:
                            missing_track_result = missing_result
                            break
                    
                    # Only add to wishlist if track was actually missing from Plex AND not successfully downloaded
                    if missing_track_result:
                        # Check if track was successfully downloaded (don't add downloaded tracks to wishlist)
                        status_item = self.track_table.item(cancelled_row, 4)
                        current_status = status_item.text() if status_item else ""
                        
                        if "✅ Downloaded" in current_status:
                            print(f"🚫 Cancelled track {cancelled_track.name} was already downloaded, skipping wishlist addition")
                        else:
                            cancelled_track_info = {
                                'download_index': cancelled_row,
                                'table_index': cancelled_row,
                                'track': cancelled_track,
                                'track_name': cancelled_track.name,
                                'artist_name': cancelled_track.artists[0] if cancelled_track.artists else "Unknown",
                                'retry_count': 0,
                                'spotify_track': missing_track_result.spotify_track  # Include the spotify track for wishlist
                            }
                            # Check if not already in permanently_failed_tracks
                            if not any(t.get('table_index') == cancelled_row for t in self.permanently_failed_tracks):
                                self.permanently_failed_tracks.append(cancelled_track_info)
                                print(f"🚫 Added cancelled missing track {cancelled_track.name} to failed list for wishlist")
                    else:
                        print(f"🚫 Cancelled track {cancelled_track.name} was not missing from Plex, skipping wishlist addition")

            # Add permanently failed tracks to wishlist before showing completion message
            failed_count = len(self.permanently_failed_tracks)
            wishlist_added_count = 0
            
            if self.permanently_failed_tracks:
                try:
                    # Add failed tracks to wishlist
                    source_context = {
                        'playlist_name': getattr(self.playlist, 'name', 'Unknown Playlist'),
                        'playlist_id': getattr(self.playlist, 'id', None),
                        'added_from': 'sync_page_modal',
                        'timestamp': datetime.now().isoformat()
                    }
                    
                    for failed_track_info in self.permanently_failed_tracks:
                        try:
                            success = self.wishlist_service.add_failed_track_from_modal(
                                track_info=failed_track_info,
                                source_type='playlist',
                                source_context=source_context
                            )
                            if success:
                                wishlist_added_count += 1
                        except Exception as e:
                            logger.error(f"Failed to add track to wishlist: {e}")
                            
                    if wishlist_added_count > 0:
                        logger.info(f"Added {wishlist_added_count} failed tracks to wishlist from playlist '{self.playlist.name}'")
                        
                except Exception as e:
                    logger.error(f"Error adding failed tracks to wishlist: {e}")

            # Determine the final message based on success or failure.
            if self.permanently_failed_tracks:
                final_message = f"Completed downloading {self.successful_downloads}/{len(self.missing_tracks)} missing tracks!\n\n"
                
                if wishlist_added_count > 0:
                    final_message += f"✨ Added {wishlist_added_count} failed track{'s' if wishlist_added_count != 1 else ''} to wishlist for automatic retry.\n\n"
                
                final_message += "You can also manually correct failed downloads or check the wishlist on the dashboard."
                
                # If there are failures, ensure the modal is visible and bring it to the front.
                if self.isHidden():
                    self.show()
                self.activateWindow()
                self.raise_()
            else:
                final_message = f"Completed downloading {self.successful_downloads}/{len(self.missing_tracks)} missing tracks!\n\nAll tracks were downloaded successfully!"

            QMessageBox.information(self, "Downloads Complete", final_message)

    def on_cancel_clicked(self):
        """Handle Cancel button - cancels operations, resets state, and closes modal."""
        print("🛑 Cancel button clicked - cancelling all operations and cleaning up")
        
        self.cancel_operations()
        self.download_in_progress = False  # CRITICAL: Reset the state flag.

        if self.is_youtube_workflow:
            # Revert the main card to the discovery phase.
            if hasattr(self, 'youtube_url') and hasattr(self.parent_page, 'update_youtube_card_phase'):
                print("🔄 Returning YouTube playlist to discovery_complete state")
                self.parent_page.update_youtube_card_phase(self.youtube_url, 'discovery_complete')
            
            # Clean up this modal's reference.
            if self.playlist.id in self.parent_page.active_youtube_download_modals:
                del self.parent_page.active_youtube_download_modals[self.playlist.id]

            # --- THE FIX ---
            # This block now correctly finds and removes the temporary "green card" (the status widget)
            # without affecting the main playlist card. This prevents the card from disappearing
            # on subsequent cancellations.
            if (hasattr(self.parent_page, 'youtube_status_widgets') and
                self.playlist.id in self.parent_page.youtube_status_widgets):
                print(f"🧹 Cleaning up YouTube status widget on cancel for playlist: {self.playlist.id}")
                status_widget = self.parent_page.youtube_status_widgets.pop(self.playlist.id, None)
                if status_widget:
                    status_widget.setParent(None)
                    status_widget.deleteLater()
        
        self.process_finished.emit()
        self.reject()  # This properly closes and destroys the modal.
        
    def on_close_clicked(self):
        """Handle the 'Close' button by triggering the modal's unified close event."""
        self.close()
        
    def cancel_operations(self):
        """Cancel any ongoing operations, including active slskd downloads."""
        print("🛑 Cancelling all operations for this playlist...")
        self.cancel_requested = True # Flag to stop any new workers from starting.

        # --- FIX: Actively cancel downloads on the slskd server ---
        if self.active_downloads:
            print(f"Requesting cancellation for {len(self.active_downloads)} active download(s)...")
            
            import asyncio
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            soulseek_client = self.parent_page.soulseek_client
            
            # Create tasks to cancel all active downloads concurrently
            tasks = []
            for download_info in self.active_downloads:
                download_id = download_info.get('download_id')
                # Assumes the soulseek_client has a method to make raw API calls.
                # A DELETE request is standard for cancellation in RESTful APIs like slskd's.
                if download_id and hasattr(soulseek_client, '_make_request'):
                    tasks.append(
                        soulseek_client._make_request('DELETE', f'transfers/downloads/{download_id}')
                    )
            
            if tasks:
                try:
                    # Wait for all cancellation requests to be sent
                    loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
                    print("All cancellation requests sent to slskd.")
                except Exception as e:
                    print(f"An error occurred while sending cancellation requests: {e}")

        # Cancel background workers (like the initial Plex analysis)
        for worker in self.active_workers:
            if hasattr(worker, 'cancel'):
                worker.cancel()
        self.active_workers.clear()

        # Clean up any fallback thread pools
        for pool in self.fallback_pools:
            pool.waitForDone(1000)
        self.fallback_pools.clear()

        # Stop the status polling timer to prevent further checks
        self.download_status_timer.stop()
        print("🛑 Modal operations cancelled successfully.")
        
    def closeEvent(self, event):
        """Override the window's close event to provide custom logic."""
        if self.download_in_progress and not self.cancel_requested:
            print("Download in progress. Hiding modal and updating card phase.")
            self.hide()
            event.ignore()  # Prevent the modal from being destroyed.
            
            # --- THE FIX ---
            # Instead of showing the status widget directly, this now tells the main
            # page to transition the card to the 'downloading' phase.
            if self.is_youtube_workflow and hasattr(self, 'youtube_url'):
                if hasattr(self.parent_page, 'update_youtube_card_phase'):
                    self.parent_page.update_youtube_card_phase(self.youtube_url, 'downloading')
            return

        print("No download in progress or cancel requested. Performing full cleanup.")
        self.on_cancel_clicked()
        # on_cancel_clicked() calls self.reject(), which will properly accept the close event.

    # Inner class for the search worker
    class ParallelSearchWorker(QRunnable):
        def __init__(self, soulseek_client, query):
            super().__init__()
            self.soulseek_client = soulseek_client
            self.query = query
            self.signals = self.create_signals()

        def create_signals(self):
            class Signals(QObject):
                search_completed = pyqtSignal(list, str)
                search_failed = pyqtSignal(str, str)
            return Signals()

        def run(self):
            loop = None
            try:
                import asyncio
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                search_result = loop.run_until_complete(self.soulseek_client.search(self.query))
                results_list = search_result[0] if isinstance(search_result, tuple) and search_result else []
                
                # Check if signals object is still valid before emitting
                try:
                    self.signals.search_completed.emit(results_list, self.query)
                except RuntimeError:
                    # Qt objects deleted during shutdown, ignore
                    logger.debug(f"Search completed for '{self.query}' but UI already closed")
                    
            except Exception as e:
                try:
                    self.signals.search_failed.emit(self.query, str(e))
                except RuntimeError:
                    # Qt objects deleted during shutdown, ignore
                    logger.debug(f"Search failed for '{self.query}' but UI already closed: {e}")
            finally:
                if loop: loop.close()

    def get_valid_candidates(self, results, spotify_track, query):
        """
        Scores and filters search results, then performs a strict artist verification
        by checking the file path. This prevents downloading tracks from the wrong artist.
        """
        if not results:
            return []

        # Step 1: Get initial confident matches with version-aware scoring
        # This gives us a sorted list of potential candidates, preferring originals.
        initial_candidates = self.matching_engine.find_best_slskd_matches_enhanced(spotify_track, results)

        if not initial_candidates:
            print(f"⚠️ No initial candidates found for '{spotify_track.name}' from query '{query}'.")
            return []
            
        print(f"✅ Found {len(initial_candidates)} initial candidates for '{spotify_track.name}'. Now verifying artist...")

        # Step 2: Perform strict artist verification on the initial candidates.
        verified_candidates = []
        spotify_artist_name = spotify_track.artists[0] if spotify_track.artists else ""
        
        # **IMPROVEMENT**: More robust normalization for both artist name and file path.
        # This removes all non-alphanumeric characters and converts to lowercase.
        # e.g., "Virtual Mage" -> "virtualmage", "virtual-mage" -> "virtualmage"
        normalized_spotify_artist = re.sub(r'[^a-zA-Z0-9]', '', spotify_artist_name).lower()

        for candidate in initial_candidates:
            # The 'filename' from Soulseek includes the full folder path.
            slskd_full_path = candidate.filename
            
            # Apply the same robust normalization to the Soulseek path.
            normalized_slskd_path = re.sub(r'[^a-zA-Z0-9]', '', slskd_full_path).lower()
            
            # **THE CRITICAL CHECK**: See if the cleaned artist's name is in the cleaned folder path.
            if normalized_spotify_artist in normalized_slskd_path:
                # Artist name was found in the path, this is a valid candidate.
                print(f"✔️ Artist '{spotify_artist_name}' VERIFIED in path: '{slskd_full_path}'")
                verified_candidates.append(candidate)
            else:
                # Artist name was NOT found. Discard this candidate.
                print(f"❌ Artist '{spotify_artist_name}' NOT found in path: '{slskd_full_path}'. Discarding candidate.")

        if verified_candidates:
            # Apply quality preference filtering before returning
            from config.settings import config_manager
            quality_preference = config_manager.get_quality_preference()
            
            # Filter candidates by quality preference with smart fallback
            if hasattr(self.parent_page, 'soulseek_client'):
                quality_filtered = self.parent_page.soulseek_client.filter_results_by_quality_preference(
                    verified_candidates, quality_preference
                )
                
                if quality_filtered:
                    verified_candidates = quality_filtered
                    print(f"🎯 Applied quality filtering ({quality_preference}): {len(verified_candidates)} candidates remain")
                else:
                    print(f"⚠️ Quality filtering ({quality_preference}) removed all candidates, keeping originals")
            
            best_confidence = verified_candidates[0].confidence
            best_version = getattr(verified_candidates[0], 'version_type', 'unknown')
            best_quality = getattr(verified_candidates[0], 'quality', 'unknown')
            print(f"✅ Found {len(verified_candidates)} VERIFIED matches for '{spotify_track.name}'. Best: {best_confidence:.2f} ({best_version}, {best_quality.upper()})")
            
            # Log version breakdown for debugging
            for candidate in verified_candidates[:3]:  # Show top 3
                version = getattr(candidate, 'version_type', 'unknown')
                penalty = getattr(candidate, 'version_penalty', 0.0)
                quality = getattr(candidate, 'quality', 'unknown')
                bitrate_info = f" {candidate.bitrate}kbps" if hasattr(candidate, 'bitrate') and candidate.bitrate else ""
                print(f"   🎵 {candidate.confidence:.2f} - {version} ({quality.upper()}{bitrate_info}) (penalty: {penalty:.2f}) - {candidate.filename[:80]}...")
                
        else:
            print(f"⚠️ No verified matches found for '{spotify_track.name}' after checking file paths.")

        return verified_candidates
    def create_spotify_based_search_result_from_validation(self, slskd_result, spotify_metadata):
        """Create SpotifyBasedSearchResult from validation results"""
        class SpotifyBasedSearchResult:
            def __init__(self):
                self.filename = getattr(slskd_result, 'filename', f"{spotify_metadata.name}.flac")
                self.username = getattr(slskd_result, 'username', 'unknown')
                self.size = getattr(slskd_result, 'size', 0)
                self.quality = getattr(slskd_result, 'quality', 'flac')
                self.artist = spotify_metadata.artists[0] if spotify_metadata.artists else "Unknown"
                self.title = spotify_metadata.name
                self.album = spotify_metadata.album
        return SpotifyBasedSearchResult()


class YouTubeDownloadMissingTracksModal(QDialog):
    """Enhanced modal for downloading YouTube playlist tracks with Spotify discovery"""
    process_finished = pyqtSignal()
    
    def __init__(self, playlist, playlist_item, parent_page, downloads_page):
        super().__init__(parent_page)
        self.playlist = playlist  # YouTube playlist with cleaned tracks
        self.playlist_item = playlist_item
        self.parent_page = parent_page
        self.downloads_page = downloads_page
        self.total_tracks = len(playlist.tracks) if playlist else 0
        
        # Progress tracking
        self.spotify_discovered_tracks = [None] * self.total_tracks  # List of discovered Spotify tracks
        self.spotify_search_completed = False
        self.spotify_worker = None
        
        # UI components
        self.track_table = None
        self.analysis_progress = None
        self.spotify_progress = None
        self.begin_search_btn = None
        self.cancel_btn = None
        self.sync_btn = None
        
        # Sync state tracking
        self.sync_in_progress = False
        self.is_youtube_workflow = True
        
        self.setup_ui()
        if self.playlist and self.total_tracks > 0:
            self.populate_initial_table()
            self.start_spotify_discovery()
    
    def setup_ui(self):
        """Set up the YouTube-specific modal UI"""
        self.setWindowTitle(f"YouTube Playlist Discovery - {self.playlist.name}")
        self.resize(1400, 900)
        self.setWindowFlags(Qt.WindowType.Window)
        
        self.setStyleSheet("""
            QDialog { background-color: #1e1e1e; color: #ffffff; }
            QLabel { color: #ffffff; }
            QPushButton {
                background-color: #1db954; color: #000000; border: none;
                border-radius: 6px; font-size: 13px; font-weight: bold;
                padding: 10px 20px; min-width: 100px;
            }
            QPushButton:hover { background-color: #1ed760; }
            QPushButton:disabled { background-color: #404040; color: #888888; }
        """)
        
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(25, 25, 25, 25)
        main_layout.setSpacing(15)
        
        # Header section
        header_section = self.create_header_section()
        main_layout.addWidget(header_section)
        
        # Progress section
        progress_section = self.create_progress_section()
        main_layout.addWidget(progress_section)
        
        # Table section
        table_section = self.create_track_table()
        main_layout.addWidget(table_section, stretch=1)
        
        # Button section
        button_section = self.create_buttons()
        main_layout.addWidget(button_section)
    
    def create_header_section(self):
        """Create header with title and summary"""
        header_frame = QFrame()
        header_frame.setStyleSheet("""
            QFrame {
                background-color: #2d2d2d; border: 1px solid #444444;
                border-radius: 8px; padding: 15px;
            }
        """)
        
        layout = QVBoxLayout(header_frame)
        
        title = QLabel("🎵 YouTube Playlist Discovery")
        title.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        title.setStyleSheet("color: #1db954;")
        
        subtitle = QLabel(f"Playlist: {self.playlist.name} ({self.total_tracks} tracks)")
        subtitle.setFont(QFont("Arial", 11))
        subtitle.setStyleSheet("color: #aaaaaa;")
        
        description = QLabel("Discovering clean Spotify metadata for YouTube tracks...")
        description.setFont(QFont("Arial", 10))
        description.setStyleSheet("color: #888888;")
        
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addWidget(description)
        
        return header_frame
    
    def create_progress_section(self):
        """Create progress tracking section"""
        progress_frame = QFrame()
        progress_frame.setStyleSheet("""
            QFrame {
                background-color: #2d2d2d; border: 1px solid #444444;
                border-radius: 8px; padding: 15px;
            }
        """)
        
        layout = QVBoxLayout(progress_frame)
        
        # Spotify discovery progress
        spotify_label = QLabel("🔍 Spotify Discovery Progress")
        spotify_label.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        
        self.spotify_progress = QProgressBar()
        self.spotify_progress.setFixedHeight(20)
        self.spotify_progress.setMaximum(self.total_tracks)
        self.spotify_progress.setValue(0)
        self.spotify_progress.setStyleSheet("""
            QProgressBar {
                border: 1px solid #555555; border-radius: 10px; text-align: center;
                background-color: #444444; color: #ffffff; font-size: 11px; font-weight: bold;
            }
            QProgressBar::chunk { background-color: #1db954; border-radius: 9px; }
        """)
        
        # Plex analysis progress (hidden initially)
        analysis_label = QLabel("📊 Plex Analysis Progress")
        analysis_label.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        
        self.analysis_progress = QProgressBar()
        self.analysis_progress.setFixedHeight(20)
        self.analysis_progress.setStyleSheet("""
            QProgressBar {
                border: 1px solid #555555; border-radius: 10px; text-align: center;
                background-color: #444444; color: #ffffff; font-size: 11px; font-weight: bold;
            }
            QProgressBar::chunk { background-color: #ff6b6b; border-radius: 9px; }
        """)
        self.analysis_progress.setVisible(False)
        analysis_label.setVisible(False)
        
        layout.addWidget(spotify_label)
        layout.addWidget(self.spotify_progress)
        layout.addWidget(analysis_label)
        layout.addWidget(self.analysis_progress)
        
        return progress_frame
    
    def create_track_table(self):
        """Create track table with YouTube-specific columns"""
        table_frame = QFrame()
        table_frame.setStyleSheet("""
            QFrame {
                background-color: #2d2d2d; border: 1px solid #444444;
                border-radius: 8px; padding: 0px;
            }
        """)
        
        layout = QVBoxLayout(table_frame)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)
        
        header_label = QLabel("📋 Track Discovery & Analysis")
        header_label.setFont(QFont("Arial", 13, QFont.Weight.Bold))
        header_label.setStyleSheet("color: #ffffff; padding: 5px;")
        
        self.track_table = QTableWidget()
        self.track_table.setColumnCount(7)
        self.track_table.setHorizontalHeaderLabels([
            "YT Track", "YT Artist", "Spotify Match Status", 
            "Spotify Track", "Spotify Artist", "Spotify Album", "Status"
        ])
        
        # Set columns to span full width evenly
        header = self.track_table.horizontalHeader()
        
        for i in range(7):
            header.setSectionResizeMode(i, QHeaderView.ResizeMode.Stretch)
        
        self.track_table.setAlternatingRowColors(True)
        self.track_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.track_table.setStyleSheet("""
            QTableWidget {
                background-color: #1e1e1e; color: #ffffff;
                gridline-color: #404040; border: none;
            }
            QTableWidget::item { padding: 8px; border-bottom: 1px solid #333333; }
            QTableWidget::item:selected { background-color: #404040; }
            QHeaderView::section {
                background-color: #333333; color: #ffffff; border: none;
                padding: 10px; font-weight: bold; font-size: 11px;
            }
        """)
        
        layout.addWidget(header_label)
        layout.addWidget(self.track_table)
        
        return table_frame
    
    def create_buttons(self):
        """Create button section"""
        button_frame = QFrame()
        layout = QHBoxLayout(button_frame)
        layout.setSpacing(10)
        
        layout.addStretch()
        
        # Create sync status display (hidden by default)
        self.sync_status_widget = self.create_sync_status_display()
        layout.addWidget(self.sync_status_widget)
        
        # Sync button - appears to the left of Begin Search
        self.sync_btn = QPushButton("🔄 Sync This Playlist")
        self.sync_btn.setEnabled(False)  # Disabled until Spotify discovery completes
        self.sync_btn.clicked.connect(self.on_sync_clicked)
        self.sync_btn.setStyleSheet("""
            QPushButton {
                background-color: #ff6b6b; color: #ffffff; border: none;
                border-radius: 6px; font-size: 13px; font-weight: bold;
                padding: 10px 20px; min-width: 120px;
            }
            QPushButton:hover { background-color: #ff5252; }
            QPushButton:disabled { background-color: #404040; color: #888888; }
        """)
        
        self.begin_search_btn = QPushButton("🔍 Download Missing Tracks")
        self.begin_search_btn.setEnabled(False)  # Disabled until Spotify discovery completes
        self.begin_search_btn.clicked.connect(self.on_begin_plex_analysis)
        
        self.cancel_btn = QPushButton("❌ Cancel")
        self.cancel_btn.clicked.connect(self.on_cancel_clicked)
        
        # Close button - hides modal without clearing data
        self.close_btn = QPushButton("🏠 Close")
        self.close_btn.clicked.connect(self.on_close_clicked)
        self.close_btn.setStyleSheet("""
            QPushButton {
                background-color: #6c757d; color: #ffffff; border: none;
                border-radius: 6px; font-size: 13px; font-weight: bold;
                padding: 10px 20px; min-width: 100px;
            }
            QPushButton:hover { background-color: #5a6268; }
        """)
        
        layout.addWidget(self.sync_btn)
        layout.addWidget(self.begin_search_btn)
        layout.addWidget(self.close_btn)
        layout.addWidget(self.cancel_btn)
        
        return button_frame
    
    def create_sync_status_display(self):
        """Create sync status display widget (hidden by default) - same as Spotify modal"""
        sync_status = QFrame()
        sync_status.setStyleSheet("""
            QFrame {
                background: rgba(29, 185, 84, 0.1);
                border: 1px solid rgba(29, 185, 84, 0.3);
                border-radius: 12px;
            }
        """)
        sync_status.setMinimumHeight(36)
        sync_status.hide()  # Hidden by default
        
        layout = QHBoxLayout(sync_status)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(12)
        
        # Total tracks
        self.total_tracks_label = QLabel("♪ 0")
        self.total_tracks_label.setFont(QFont("SF Pro Text", 12, QFont.Weight.Medium))
        self.total_tracks_label.setStyleSheet("color: #ffa500; background: transparent; border: none;")
        
        # Matched tracks
        self.matched_tracks_label = QLabel("✓ 0")
        self.matched_tracks_label.setFont(QFont("SF Pro Text", 12, QFont.Weight.Medium))
        self.matched_tracks_label.setStyleSheet("color: #1db954; background: transparent; border: none;")
        
        # Failed tracks
        self.failed_tracks_label = QLabel("✗ 0")
        self.failed_tracks_label.setFont(QFont("SF Pro Text", 12, QFont.Weight.Medium))
        self.failed_tracks_label.setStyleSheet("color: #e22134; background: transparent; border: none;")
        
        # Percentage
        self.percentage_label = QLabel("0%")
        self.percentage_label.setFont(QFont("SF Pro Text", 12, QFont.Weight.Bold))
        self.percentage_label.setStyleSheet("color: #1db954; background: transparent; border: none;")
        
        layout.addWidget(self.total_tracks_label)
        
        # Separator 1
        sep1 = QLabel("/")
        sep1.setFont(QFont("SF Pro Text", 12, QFont.Weight.Medium))
        sep1.setStyleSheet("color: #666666; background: transparent; border: none;")
        layout.addWidget(sep1)
        
        layout.addWidget(self.matched_tracks_label)
        
        # Separator 2
        sep2 = QLabel("/")
        sep2.setFont(QFont("SF Pro Text", 12, QFont.Weight.Medium))
        sep2.setStyleSheet("color: #666666; background: transparent; border: none;")
        layout.addWidget(sep2)
        
        layout.addWidget(self.failed_tracks_label)
        
        # Separator 3
        sep3 = QLabel("/")
        sep3.setFont(QFont("SF Pro Text", 12, QFont.Weight.Medium))
        sep3.setStyleSheet("color: #666666; background: transparent; border: none;")
        layout.addWidget(sep3)
        
        layout.addWidget(self.percentage_label)
        
        return sync_status
    
    def update_sync_status(self, total_tracks=0, matched_tracks=0, failed_tracks=0):
        """Update sync status display"""
        if self.sync_status_widget:
            self.total_tracks_label.setText(f"♪ {total_tracks}")
            self.matched_tracks_label.setText(f"✓ {matched_tracks}")
            self.failed_tracks_label.setText(f"✗ {failed_tracks}")
            
            if total_tracks > 0:
                processed_tracks = matched_tracks + failed_tracks
                percentage = int((processed_tracks / total_tracks) * 100)
                self.percentage_label.setText(f"{percentage}%")
            else:
                self.percentage_label.setText("0%")
    
    def populate_initial_table(self):
        """Populate table with initial YouTube track data"""
        self.track_table.setRowCount(self.total_tracks)
        
        for i, track in enumerate(self.playlist.tracks):
            # YT Track
            yt_track_item = QTableWidgetItem(track.name)
            yt_track_item.setFlags(yt_track_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.track_table.setItem(i, 0, yt_track_item)
            
            # YT Artist
            yt_artist = track.artists[0] if track.artists else "Unknown"
            yt_artist_item = QTableWidgetItem(yt_artist)
            yt_artist_item.setFlags(yt_artist_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.track_table.setItem(i, 1, yt_artist_item)
            
            # Spotify Match Status
            status_item = QTableWidgetItem("Pending...")
            status_item.setFlags(status_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.track_table.setItem(i, 2, status_item)
            
            # Empty cells for Spotify data (to be filled during discovery)
            for col in [3, 4, 5, 6]:
                empty_item = QTableWidgetItem("")
                empty_item.setFlags(empty_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.track_table.setItem(i, col, empty_item)
    
    def start_spotify_discovery(self):
        """Start the Spotify discovery process using background worker"""
        print(f"🔍 Starting Spotify discovery for {self.total_tracks} tracks...")
        
        # Update all rows to show "Searching..." status
        for row in range(self.total_tracks):
            status_item = self.track_table.item(row, 2)
            if status_item:
                status_item.setText("🔍 Pending...")
        
        # Create and start a single optimized Spotify discovery worker
        # Import matching engine for validation
        from core.matching_engine import MusicMatchingEngine
        matching_engine = MusicMatchingEngine()
        
        self.spotify_worker = OptimizedSpotifyDiscoveryWorker(
            self.playlist.tracks, 
            self.parent_page.spotify_client,
            matching_engine
        )
        
        # Connect signals
        self.spotify_worker.signals.track_discovered.connect(self.on_track_discovered)
        self.spotify_worker.signals.progress_updated.connect(self.on_discovery_progress)
        self.spotify_worker.signals.finished.connect(self.on_spotify_discovery_finished)
        
        # Start the worker
        QThreadPool.globalInstance().start(self.spotify_worker)
    
    def on_track_discovered(self, row, spotify_track, status):
        """Handle a track being discovered (or not) on Spotify"""
        try:
            if status == "found" and spotify_track:
                self.spotify_discovered_tracks[row] = spotify_track
                self.update_table_with_spotify_match(row, spotify_track)
            elif status == "not_found":
                self.update_table_with_no_match(row)
            elif status == "low_confidence":
                self.update_table_with_low_confidence(row)
            else:  # error
                self.update_table_with_error(row, status.replace("error: ", ""))
        except Exception as e:
            print(f"❌ Error updating UI for track {row}: {e}")
    
    def on_discovery_progress(self, current):
        """Update the discovery progress"""
        self.spotify_progress.setValue(current)
    
    def on_spotify_discovery_finished(self, successful_discoveries):
        """Handle Spotify discovery completion"""
        self.spotify_discovery_completed()
        print(f"🎵 Spotify discovery completed: {successful_discoveries}/{self.total_tracks} tracks found")
    
    def update_table_with_spotify_match(self, row, spotify_track):
        """Update table row with successful Spotify match"""
        # Spotify Match Status
        status_item = self.track_table.item(row, 2)
        status_item.setText("✅ Found")
        status_item.setForeground(QBrush(QColor("#4CAF50")))
        
        # Spotify Track
        spotify_track_item = QTableWidgetItem(spotify_track.name)
        spotify_track_item.setFlags(spotify_track_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.track_table.setItem(row, 3, spotify_track_item)
        
        # Spotify Artist
        spotify_artist = spotify_track.artists[0] if spotify_track.artists else "Unknown"
        spotify_artist_item = QTableWidgetItem(spotify_artist)
        spotify_artist_item.setFlags(spotify_artist_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.track_table.setItem(row, 4, spotify_artist_item)
        
        # Spotify Album
        album_name = spotify_track.album if isinstance(spotify_track.album, str) else getattr(spotify_track.album, 'name', 'Unknown Album')
        spotify_album_item = QTableWidgetItem(album_name)
        spotify_album_item.setFlags(spotify_album_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.track_table.setItem(row, 5, spotify_album_item)
        
        # Status
        status_col_item = self.track_table.item(row, 6)
        status_col_item.setText("Ready for Plex analysis")
    
    def update_table_with_no_match(self, row):
        """Update table row when no Spotify match found"""
        # Spotify Match Status
        status_item = self.track_table.item(row, 2)
        status_item.setText("❌ Not Found")
        status_item.setForeground(QBrush(QColor("#ff6b6b")))
        
        # Status
        status_col_item = self.track_table.item(row, 6)
        status_col_item.setText("Skipped - No Spotify match")
        status_col_item.setForeground(QBrush(QColor("#888888")))
    
    def update_table_with_low_confidence(self, row):
        """Update table row when Spotify matches were found but confidence too low"""
        # Spotify Match Status
        status_item = self.track_table.item(row, 2)
        status_item.setText("⚠️ Low Confidence")
        status_item.setForeground(QBrush(QColor("#FFA500")))
        
        # Status
        status_col_item = self.track_table.item(row, 6)
        status_col_item.setText("Skipped - No reliable match")
        status_col_item.setForeground(QBrush(QColor("#FFA500")))
    
    def update_table_with_error(self, row, error_msg):
        """Update table row when search error occurred"""
        # Spotify Match Status
        status_item = self.track_table.item(row, 2)
        status_item.setText("⚠️ Error")
        status_item.setForeground(QBrush(QColor("#FFA500")))
        
        # Status
        status_col_item = self.track_table.item(row, 6)
        status_col_item.setText(f"Error: {error_msg[:30]}...")
        status_col_item.setForeground(QBrush(QColor("#FFA500")))
    
    def spotify_discovery_completed(self):
        """Called when Spotify discovery is complete"""
        self.spotify_search_completed = True
        
        # Count successful discoveries
        successful_discoveries = sum(1 for track in self.spotify_discovered_tracks if track is not None)
        
        print(f"🎵 Spotify discovery completed: {successful_discoveries}/{self.total_tracks} tracks found")
        
        # Enable the Plex analysis and sync buttons
        self.begin_search_btn.setEnabled(True)
        self.begin_search_btn.setText(f"🔍 Download Missing Tracks ({successful_discoveries} tracks)")
        
        self.sync_btn.setEnabled(True)
    
    def on_begin_plex_analysis(self):
        """Create discovered playlist and open regular download modal"""
        # Filter out tracks that weren't found on Spotify
        valid_spotify_tracks = [track for track in self.spotify_discovered_tracks if track is not None]
        
        if not valid_spotify_tracks:
            QMessageBox.warning(self, "No Tracks", "No tracks were successfully discovered on Spotify.")
            return
        
        print(f"🎵 Creating discovered playlist with {len(valid_spotify_tracks)} Spotify tracks...")
        
        # Create a Spotify-compatible playlist from discovered tracks
        discovered_playlist = self.create_discovered_playlist(valid_spotify_tracks)
        
        # Mark that we're transitioning to download modal (don't clean up URL tracking)
        self.transitioning_to_download = True
        
        # Close this discovery modal
        self.accept()
        
        # Create a dummy playlist item for the regular modal
        dummy_playlist_item = type('DummyPlaylistItem', (), {
            'playlist_name': discovered_playlist.name,
            'track_count': len(discovered_playlist.tracks),
            'download_modal': None,
            'show_operation_status': lambda self, status_text="View Progress": None,
            'hide_operation_status': lambda self: None
        })()
        
        # Open the regular DownloadMissingTracksModal with the discovered playlist
        print("🚀 Opening regular DownloadMissingTracksModal with discovered tracks...")
        modal = DownloadMissingTracksModal(
            discovered_playlist,
            dummy_playlist_item,
            self.parent_page,
            self.downloads_page,
            is_youtube_workflow=True  # Flag to indicate this is from YouTube discovery
        )
        
        # Transfer URL tracking from discovery modal to download modal
        if hasattr(self, 'youtube_url'):
            modal.youtube_url = self.youtube_url
            self.parent_page.active_youtube_processes[self.youtube_url] = modal
            print(f"🔄 Transferred URL tracking to download modal: {self.youtube_url}")
            
            # Update card to downloading phase
            if hasattr(self.parent_page, 'update_youtube_card_phase'):
                self.parent_page.update_youtube_card_phase(self.youtube_url, 'downloading')
        
        # Store the modal reference using the ID of the NEWLY created playlist object.
        print(f"📝 Storing modal with CORRECT discovered_playlist.id: {discovered_playlist.id}")
        self.parent_page.active_youtube_download_modals[discovered_playlist.id] = modal
        
        modal.exec()
    
    def on_sync_clicked(self):
        """Handle Sync This Playlist button click"""
        if self.sync_in_progress:
            # Cancel ongoing sync
            print(f"🛑 Cancelling sync for playlist: {self.playlist.name}")
            
            if hasattr(self.parent_page, 'cancel_playlist_sync'):
                self.parent_page.cancel_playlist_sync(self.playlist.id)
            
            # Reset sync state immediately (don't wait for callback)
            self.sync_in_progress = False
            self.sync_btn.setText("🔄 Sync This Playlist")
            self.sync_btn.setStyleSheet("""
                QPushButton {
                    background-color: #ff6b6b; color: #ffffff; border: none;
                    border-radius: 6px; font-size: 13px; font-weight: bold;
                    padding: 10px 20px; min-width: 120px;
                }
                QPushButton:hover { background-color: #ff5252; }
                QPushButton:disabled { background-color: #404040; color: #888888; }
            """)
            
            # Status widgets are no longer used for sync - cards handle their own state
            print("🔄 Sync cancelled - card will update its own state")
        
        else:
            # Start sync using the parent page's sync infrastructure
            print(f"🔄 Starting sync for playlist: {self.playlist.name}")
            
            if hasattr(self.parent_page, 'start_playlist_sync') and self.parent_page.start_playlist_sync(self.playlist):
                print(f"✅ Sync started successfully for: {self.playlist.name}")
                
                # Update UI to show sync is active
                self.sync_in_progress = True
                self.sync_btn.setText("Cancel Sync")
                self.sync_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #e22134; color: #ffffff; border: none;
                        border-radius: 6px; font-size: 13px; font-weight: bold;
                        padding: 10px 20px; min-width: 120px;
                    }
                    QPushButton:hover { background-color: #d32f2f; }
                """)
                
                # Show sync status widget (same as Spotify modal)
                if self.sync_status_widget:
                    self.sync_status_widget.show()
                    self.update_sync_status(len(self.playlist.tracks), 0, 0)
                
                # Update card to syncing phase
                if hasattr(self, 'youtube_url') and hasattr(self.parent_page, 'update_youtube_card_phase'):
                    print(f"🎬 Discovery modal: Setting card to syncing phase for URL: {self.youtube_url}")
                    print(f"🎬 Discovery modal: Using playlist.id: {self.playlist.id}")
                    self.parent_page.update_youtube_card_phase(self.youtube_url, 'syncing')
                
                # The card itself will show sync progress - no need for separate status widget
                    
            else:
                print(f"❌ Failed to start sync for: {self.playlist.name}")
                QMessageBox.warning(self, "Sync Failed", "Failed to start playlist sync. Please try again.")
    
    
    def on_cancel_clicked(self):
        """Handle cancel button click - cancel sync or close modal"""
        print("🛑 Cancel button clicked")
        
        # Cancel any running Spotify discovery worker
        if self.spotify_worker:
            print("🛑 Cancelling Spotify discovery worker")
            self.spotify_worker.cancel()
            self.spotify_worker = None
        
        if self.sync_in_progress:
            # Cancel sync operation
            print("🛑 Cancelling sync operation")
            if hasattr(self.parent_page, 'cancel_playlist_sync'):
                self.parent_page.cancel_playlist_sync(self.playlist.id)
            
            # Reset sync state
            self.sync_in_progress = False
            self.sync_btn.setText("🔄 Sync This Playlist")
            self.sync_btn.setStyleSheet("""
                QPushButton {
                    background-color: #ff6b6b; color: #ffffff; border: none;
                    border-radius: 6px; font-size: 13px; font-weight: bold;
                    padding: 10px 20px; min-width: 120px;
                }
                QPushButton:hover { background-color: #ff5252; }
                QPushButton:disabled { background-color: #404040; color: #888888; }
            """)
            
            # Status widgets are no longer used for sync - cards handle their own state
            print("🔄 Sync cancelled - card will update its own state")
        
        # Clean up URL tracking before closing (but not during download transition)
        if (hasattr(self, 'youtube_url') and hasattr(self.parent_page, 'active_youtube_processes') and
            not getattr(self, 'transitioning_to_download', False)):
            if self.youtube_url in self.parent_page.active_youtube_processes:
                print(f"🧹 Cleaning up URL tracking on cancel for: {self.youtube_url}")
                del self.parent_page.active_youtube_processes[self.youtube_url]
        
        # Always close/hide the modal when cancel is clicked
        print("🛑 Closing modal")
        
        # Update card state - reset to initial discovering state for Cancel
        if hasattr(self, 'youtube_url') and hasattr(self.parent_page, 'reset_youtube_playlist_state'):
            self.parent_page.reset_youtube_playlist_state(self.youtube_url)
        
        self.reject()
    
    def on_close_clicked(self):
        """Handle Close button click - hide modal but preserve discovery data"""
        print("🏠 Close button clicked - preserving discovery data")
        
        # Check if sync is currently in progress - if so, preserve the syncing state
        if hasattr(self, 'youtube_url') and hasattr(self.parent_page, 'update_youtube_card_phase'):
            if self.sync_in_progress:
                # Sync is running - keep the card in syncing state
                print("🔄 Sync in progress - preserving syncing state")
                # Don't change the card phase - it should stay as 'syncing'
            elif self.spotify_search_completed:
                # No sync running, discovery complete - safe to set to discovery_complete
                self.parent_page.update_youtube_card_phase(self.youtube_url, 'discovery_complete')
            else:
                # Discovery still running - keep as discovering but hide modal
                print("🔄 Discovery still in progress - keeping discovering state")
        
        # Just hide the modal, don't reset any data
        self.hide()
    
    def on_sync_progress(self, playlist_id, progress):
        """Handle sync progress updates (called from parent page)"""
        try:
            print(f"🔍 YouTube modal sync progress called: playlist_id={playlist_id}, my_id={self.playlist.id}")
            print(f"🔍 YouTube modal sync_in_progress={self.sync_in_progress}")
            print(f"🔍 YouTube modal progress data: total={progress.total_tracks}, matched={progress.matched_tracks}, failed={progress.failed_tracks}")
            
            if playlist_id == self.playlist.id:
                print(f"🔄 ✅ Playlist ID matches - processing sync progress for YouTube playlist")
                if self.sync_in_progress:
                    print(f"🔄 ✅ Sync in progress - updating status widget")
                    
                    # Show and update the sync status widget (same as Spotify modal)
                    if self.sync_status_widget:
                        print(f"📊 ✅ Status widget exists - showing and updating")
                        self.sync_status_widget.show()
                        self.update_sync_status(
                            progress.total_tracks,
                            progress.matched_tracks, 
                            progress.failed_tracks
                        )
                        print(f"📊 ✅ Status widget updated successfully")
                        
                        # Update card progress as well
                        if hasattr(self, 'youtube_url') and hasattr(self.parent_page, 'update_youtube_card_progress'):
                            self.parent_page.update_youtube_card_progress(
                                self.youtube_url,
                                total=progress.total_tracks,
                                matched=progress.matched_tracks,
                                failed=progress.failed_tracks
                            )
                    else:
                        print("❌ sync_status_widget is None!")
                else:
                    print(f"🔄 ❌ Sync not in progress (sync_in_progress={self.sync_in_progress})")
            else:
                print(f"🔄 ❌ Playlist ID mismatch: {playlist_id} != {self.playlist.id}")
                
        except Exception as e:
            print(f"💥 EXCEPTION in YouTube modal on_sync_progress: {e}")
            import traceback
            print(f"💥 Traceback: {traceback.format_exc()}")
            
            # Update the card progress display instead of creating status widgets
            if hasattr(self, 'youtube_url') and hasattr(self.parent_page, 'update_youtube_card_progress'):
                self.parent_page.update_youtube_card_progress(
                    self.youtube_url,
                    total=progress.total_tracks,
                    matched=progress.matched_tracks,
                    failed=progress.failed_tracks
                )

    def on_sync_finished(self, playlist_id, result):
        """Handle sync completion (called from parent page)"""
        if playlist_id == self.playlist.id:
            print(f"🎉 Sync completed for YouTube playlist: {self.playlist.name}")
            
            # Reset sync state
            self.sync_in_progress = False
            
            # Hide sync status widget (same as Spotify modal)
            if self.sync_status_widget:
                self.sync_status_widget.hide()
            
            # Reset sync button to original state
            self.sync_btn.setText("🔄 Sync This Playlist")
            self.sync_btn.setStyleSheet("""
                QPushButton {
                    background-color: #ff6b6b; color: #ffffff; border: none;
                    border-radius: 6px; font-size: 13px; font-weight: bold;
                    padding: 10px 20px; min-width: 120px;
                }
                QPushButton:hover { background-color: #ff5252; }
                QPushButton:disabled { background-color: #404040; color: #888888; }
            """)
            
            # Update card to sync_complete phase
            if hasattr(self, 'youtube_url') and hasattr(self.parent_page, 'update_youtube_card_phase'):
                self.parent_page.update_youtube_card_phase(self.youtube_url, 'sync_complete')
    
    def on_sync_error(self, playlist_id, error_msg):
        """Handle sync error (called from parent page)"""
        if playlist_id == self.playlist.id:
            print(f"❌ Sync error for YouTube playlist: {self.playlist.name} - {error_msg}")
            
            # Reset sync state
            self.sync_in_progress = False
            
            # Hide sync status widget (same as Spotify modal)
            if self.sync_status_widget:
                self.sync_status_widget.hide()
            
            # Reset sync button to original state
            self.sync_btn.setText("🔄 Sync This Playlist")
            self.sync_btn.setStyleSheet("""
                QPushButton {
                    background-color: #ff6b6b; color: #ffffff; border: none;
                    border-radius: 6px; font-size: 13px; font-weight: bold;
                    padding: 10px 20px; min-width: 120px;
                }
                QPushButton:hover { background-color: #ff5252; }
                QPushButton:disabled { background-color: #404040; color: #888888; }
            """)
    
    def create_discovered_playlist(self, spotify_tracks):
        """Create a playlist object from discovered Spotify tracks, reusing the original ID and name."""
        playlist_id = self.playlist.id
        
        print(f"🎵 Creating discovered playlist with consistent ID: {playlist_id}")

        discovered_playlist = type('Playlist', (), {
            'id': playlist_id,
            # --- THE FIX ---
            # This now uses the original, clean playlist name without adding any prefixes.
            'name': self.playlist.name,
            'description': f"Discovered from YouTube playlist with {len(spotify_tracks)} matched tracks",
            'owner': "YouTube Discovery",
            'public': False,
            'collaborative': False,
            'tracks': spotify_tracks,
            'total_tracks': len(spotify_tracks)
        })()
        
        return discovered_playlist
    
    def show_loading_state(self):
        """Show loading state in the modal"""
        # Update window title
        self.setWindowTitle("YouTube Playlist Discovery - Loading...")
        
        # Clear the table
        self.track_table.setRowCount(0)
        
        # Show loading message
        loading_item = QTableWidgetItem("🔄 Parsing YouTube playlist...")
        loading_item.setFlags(loading_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        
        self.track_table.setRowCount(1)
        self.track_table.setSpan(0, 0, 1, 7)  # Span all columns
        self.track_table.setItem(0, 0, loading_item)
        
        # Disable buttons
        self.begin_search_btn.setEnabled(False)
        self.begin_search_btn.setText("Waiting for playlist data...")
    
    def populate_with_playlist_data(self, playlist):
        """Populate the modal with actual playlist data"""
        print(f"📊 Populating modal with {len(playlist.tracks)} tracks")
        
        # Update modal properties
        self.playlist = playlist
        
        # --- THE FIX ---
        # The block of code that was here was incorrectly adding this discovery modal
        # to the parent page's tracking dictionary for DOWNLOAD modals, often with
        # multiple, inconsistent IDs. This was the root cause of the state corruption
        # and the "No modal found" error. By removing it, we ensure that only the
        # correct modal (the DownloadMissingTracksModal) is ever added to that list,
        # which resolves the entire issue.
        
        self.total_tracks = len(playlist.tracks)
        self.spotify_discovered_tracks = [None] * self.total_tracks
        
        # Update window title
        self.setWindowTitle(f"YouTube Playlist Discovery - {playlist.name}")
        
        # Update progress bars
        self.spotify_progress.setMaximum(self.total_tracks)
        self.spotify_progress.setValue(0)
        
        # Populate the table with actual track data
        self.populate_initial_table()
        
        # Start Spotify discovery
        self.start_spotify_discovery()
    
    def closeEvent(self, event):
        """Handle modal closing - hide when sync is active, otherwise close"""
        print(f"🔍 DEBUG: YouTube modal closeEvent - sync_in_progress: {self.sync_in_progress}")
        
        # If sync is in progress, just hide the modal (don't close)
        if self.sync_in_progress:
            print("🔍 DEBUG: Sync in progress - hiding modal instead of closing")
            event.ignore()  # Prevent actual closing
            self.hide()
            return
        
        # Normal close behavior - cancel any running workers
        if self.spotify_worker:
            print("🛑 closeEvent: Cancelling Spotify discovery worker")
            self.spotify_worker.cancel()
            self.spotify_worker = None
        
        # Clean up URL tracking when modal is actually closed (not just hidden)
        # But don't clean up if we're transitioning to download modal
        if (hasattr(self, 'youtube_url') and hasattr(self.parent_page, 'active_youtube_processes') and
            not getattr(self, 'transitioning_to_download', False)):
            if self.youtube_url in self.parent_page.active_youtube_processes:
                print(f"🧹 Cleaning up URL tracking for: {self.youtube_url}")
                del self.parent_page.active_youtube_processes[self.youtube_url]
        
        super().closeEvent(event)



