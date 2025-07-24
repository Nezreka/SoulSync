from typing import List, Optional, Dict, Any, Tuple
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from unidecode import unidecode
from utils.logging_config import get_logger
from core.spotify_client import Track as SpotifyTrack
from core.plex_client import PlexTrackInfo

logger = get_logger("matching_engine")

@dataclass
class MatchResult:
    spotify_track: SpotifyTrack
    plex_track: Optional[PlexTrackInfo]
    confidence: float
    match_type: str
    
    @property
    def is_match(self) -> bool:
        return self.plex_track is not None and self.confidence >= 0.8

class MusicMatchingEngine:
    def __init__(self):
        # More comprehensive patterns to strip extra info from titles
        self.title_patterns = [
            # Patterns inside parentheses or brackets
            r'\(feat\.?.*\)',
            r'\[feat\.?.*\]',
            r'\(with.*\)',
            r'\(ft\.?.*\)',
            r'\[ft\.?.*\]',
            r'\(remix\)',
            r'\(live\)',
            r'\(acoustic\)',
            r'\(radio edit\)',
            r'\(album version\)',
            r'\(original mix\)',
            # Patterns after a hyphen
            r'-\s*single version',
            r'-\s*remaster.*',
            r'-\s*live.*',
            r'-\s*remix',
            r'-\s*radio edit',
            # Patterns in the open title string (not in brackets)
            r'\s+feat\.?.*',
            r'\s+ft\.?.*',
            r'\s+featuring.*'
        ]
        
        self.artist_patterns = [
            r'\s*feat\..*',
            r'\s*ft\..*',
            r'\s*featuring.*',
            r'\s*&.*',
            r'\s*and.*',
            r',.*'
        ]
    
    def normalize_string(self, text: str) -> str:
        """
        Normalizes string by converting to ASCII, lowercasing, and removing
        specific punctuation while keeping alphanumeric characters.
        """
        if not text:
            return ""
        
        # Transliterate Unicode characters (e.g., ñ -> n, é -> e) to ASCII
        text = unidecode(text)
        
        # Convert to lowercase
        text = text.lower()
        
        # Remove specific punctuation but keep alphanumeric and spaces
        text = re.sub(r'[^\w\s-]', '', text)
        
        # Collapse multiple spaces into one
        text = re.sub(r'\s+', ' ', text).strip()
        
        return text
    
    def clean_title(self, title: str) -> str:
        """Cleans title by removing common extra info using regex."""
        cleaned = title
        
        for pattern in self.title_patterns:
            cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE).strip()
        
        return self.normalize_string(cleaned)
    
    def clean_artist(self, artist: str) -> str:
        """Cleans artist name by removing featured artists and other noise."""
        cleaned = artist
        
        for pattern in self.artist_patterns:
            cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE).strip()
        
        return self.normalize_string(cleaned)
    
    def similarity_score(self, str1: str, str2: str) -> float:
        """Calculates similarity score between two strings."""
        if not str1 or not str2:
            return 0.0
        
        return SequenceMatcher(None, str1, str2).ratio()
    
    def duration_similarity(self, duration1: int, duration2: int) -> float:
        """Calculates similarity score based on track duration (in ms)."""
        if duration1 == 0 or duration2 == 0:
            return 0.5 # Neutral score if a duration is missing
        
        # Allow a 5-second tolerance (5000 ms)
        if abs(duration1 - duration2) <= 5000:
            return 1.0
        
        # Penalize larger differences
        diff_ratio = abs(duration1 - duration2) / max(duration1, duration2)
        return max(0, 1.0 - diff_ratio * 5) # Scale penalty

    def calculate_match_confidence(self, spotify_track: SpotifyTrack, plex_track: PlexTrackInfo) -> Tuple[float, str]:
        """Calculates a confidence score for a potential match with weighted factors."""
        
        spotify_title_cleaned = self.clean_title(spotify_track.name)
        plex_title_cleaned = self.clean_title(plex_track.title)

        # --- Enhanced Artist Scoring ---
        # Get a list of all cleaned artist names from Spotify
        spotify_artists_cleaned = [self.clean_artist(a) for a in spotify_track.artists if a]
        plex_artist_cleaned = self.clean_artist(plex_track.artist)
        plex_artist_normalized = self.normalize_string(plex_track.artist) # For substring check

        # Calculate the best possible artist score by checking each Spotify artist against the Plex artist
        best_artist_score = 0.0
        for spotify_artist in spotify_artists_cleaned:
            # First, check for a direct substring match, which is a very strong signal
            if spotify_artist in plex_artist_normalized:
                score = 1.0
            else:
                # Otherwise, calculate similarity on the cleaned versions
                score = self.similarity_score(spotify_artist, plex_artist_cleaned)
            
            if score > best_artist_score:
                best_artist_score = score
                # If we get a perfect match, we can stop early
                if best_artist_score == 1.0:
                    break
        
        artist_score = best_artist_score
        
        # --- Calculate other scores ---
        title_score = self.similarity_score(spotify_title_cleaned, plex_title_cleaned)
        duration_score = self.duration_similarity(spotify_track.duration_ms, plex_track.duration if plex_track.duration else 0)
        
        # --- Weighted confidence calculation ---
        # Weights: Title (50%), Artist (30%), Duration (20%)
        confidence = (title_score * 0.5) + (artist_score * 0.3) + (duration_score * 0.2)
        
        # Determine match type based on scores
        if title_score > 0.95 and artist_score > 0.9 and duration_score > 0.9:
            match_type = "perfect_match"
            confidence = max(confidence, 0.98) # Boost confidence for perfect matches
        elif title_score > 0.85 and artist_score > 0.8:
            match_type = "high_confidence"
        elif title_score > 0.75:
            match_type = "medium_confidence"
        else:
            match_type = "low_confidence"

        return confidence, match_type
    
    def find_best_match(self, spotify_track: SpotifyTrack, plex_tracks: List[PlexTrackInfo]) -> MatchResult:
        """Finds the best Plex track match from a list of candidates."""
        best_match = None
        best_confidence = 0.0
        best_match_type = "no_match"
        
        if not plex_tracks:
            return MatchResult(spotify_track, None, 0.0, "no_candidates")

        for plex_track in plex_tracks:
            confidence, match_type = self.calculate_match_confidence(spotify_track, plex_track)
            
            if confidence > best_confidence:
                best_confidence = confidence
                best_match = plex_track
                best_match_type = match_type
        
        return MatchResult(
            spotify_track=spotify_track,
            plex_track=best_match,
            confidence=best_confidence,
            match_type=best_match_type
        )
