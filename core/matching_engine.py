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
        # The order of these patterns is important. More general patterns go first.
        self.title_patterns = [
            # General patterns to remove all content in brackets/parentheses
            r'\(.*\)',
            r'\[.*\]',
            # General pattern to remove everything after a hyphen
            r'\s-\s.*',
            # Patterns to remove featuring artists from the title itself
            r'\sfeat\.?.*',
            r'\sft\.?.*',
            r'\sfeaturing.*'
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
        
        text = unidecode(text)
        text = text.lower()
        # Keep alphanumeric, spaces, and hyphens, but remove other punctuation like '.' or ','
        text = re.sub(r'[^\w\s-]', '', text)
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
        
        diff_ratio = abs(duration1 - duration2) / max(duration1, duration2)
        return max(0, 1.0 - diff_ratio * 5)

    def calculate_match_confidence(self, spotify_track: SpotifyTrack, plex_track: PlexTrackInfo) -> Tuple[float, str]:
        """Calculates a confidence score for a potential match with a more robust, prioritized logic."""
        
        spotify_title_cleaned = self.clean_title(spotify_track.name)
        plex_title_cleaned = self.clean_title(plex_track.title)

        # --- Artist Scoring ---
        spotify_artists_cleaned = [self.clean_artist(a) for a in spotify_track.artists if a]
        plex_artist_normalized = self.normalize_string(plex_track.artist)

        best_artist_score = 0.0
        for spotify_artist in spotify_artists_cleaned:
            if spotify_artist and spotify_artist in plex_artist_normalized:
                best_artist_score = 1.0
                break
            score = self.similarity_score(spotify_artist, self.clean_artist(plex_track.artist))
            if score > best_artist_score:
                best_artist_score = score
        
        artist_score = best_artist_score
        
        # --- Title and Duration Scoring ---
        title_score = self.similarity_score(spotify_title_cleaned, plex_title_cleaned)
        duration_score = self.duration_similarity(spotify_track.duration_ms, plex_track.duration if plex_track.duration else 0)
        
        # --- Prioritized Confidence Logic ---
        # Priority 1: Near-perfect title and artist match is a very strong signal.
        if title_score > 0.98 and artist_score > 0.9:
            confidence = 0.98
            match_type = "strong_match"
        # Priority 2: Exact title match, even with a weaker artist match, should have high confidence.
        # This helps with short titles like "Girls" or "LIL DEMON".
        elif title_score > 0.98:
            confidence = 0.90 + (artist_score * 0.05) # Base of 0.9, with a small artist bonus
            match_type = "exact_title_match"
        # Priority 3: High title similarity is still a good indicator.
        elif title_score > 0.9:
            confidence = (title_score * 0.6) + (artist_score * 0.3) + (duration_score * 0.1)
            match_type = "high_confidence"
        # Default: Standard weighted calculation for all other cases.
        else:
            confidence = (title_score * 0.5) + (artist_score * 0.3) + (duration_score * 0.2)
            match_type = "standard_match"

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
