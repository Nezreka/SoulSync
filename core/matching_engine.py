from typing import List, Optional, Dict, Any, Tuple
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from unidecode import unidecode
from utils.logging_config import get_logger
from core.spotify_client import Track as SpotifyTrack
from core.plex_client import PlexTrackInfo
from core.soulseek_client import TrackResult


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
        # Conservative title patterns - only remove clear noise, preserve meaningful differences like remixes
        self.title_patterns = [
            # Only remove explicit/clean markers - preserve remixes, versions, and content after hyphens
            r'\s*\(explicit\)',
            r'\s*\(clean\)',
            # Remove featuring artists from the title itself
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
        Normalizes string by handling common stylizations, converting to ASCII,
        lowercasing, and replacing separators with spaces.
        """
        if not text:
            return ""
        
        text = unidecode(text)
        text = text.lower()
        
        # --- IMPROVEMENT V4 ---
        # The user correctly pointed out that replacing '$' with 's' was incorrect
        # as it breaks searching for stylized names like A$AP Rocky.
        # The new approach is to PRESERVE the '$' symbol during normalization.
        
        # Replace common separators with spaces to preserve word boundaries.
        text = re.sub(r'[._/]', ' ', text)
        
        # Keep alphanumeric characters, spaces, hyphens, AND the '$' sign.
        text = re.sub(r'[^a-z0-9\s$-]', '', text)
        
        # Consolidate multiple spaces into one
        text = re.sub(r'\s+', ' ', text).strip()
        
        return text
    
    def get_core_string(self, text: str) -> str:
        """Returns a 'core' version of a string with only letters and numbers for a strict comparison."""
        if not text:
            return ""
        # Transliterate, lowercase, and remove everything that isn't a letter or digit.
        text = unidecode(text).lower()
        return re.sub(r'[^a-z0-9]', '', text)

    def clean_title(self, title: str) -> str:
        """Cleans title by removing common extra info using regex for fuzzy matching."""
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
    
    def clean_album_name(self, album_name: str) -> str:
        """Clean album name by removing version info, deluxe editions, etc."""
        if not album_name:
            return ""
        
        cleaned = album_name
        
        # Common album suffixes to remove
        album_patterns = [
            # Add pattern to remove trailing info after a hyphen, common for remasters/editions.
            r'\s-\s.*',
            r'\s*\(deluxe\s*edition?\)',
            r'\s*\(expanded\s*edition?\)',
            r'\s*\(platinum\s*edition?\)',  # Fix for "Fearless (Platinum Edition)"
            r'\s*\(remastered?\)',
            r'\s*\(remaster\)',
            r'\s*\(anniversary\s*edition?\)',
            r'\s*\(special\s*edition?\)',
            r'\s*\(bonus\s*track\s*version\)',
            r'\s*\(.*version\)',  # Covers "Taylor's Version", "Radio Version", etc.
            r'\s*\[deluxe\]',
            r'\s*\[remastered?\]',
            r'\s*\[.*version\]',
            r'\s*-\s*deluxe',
            r'\s*-\s*platinum\s*edition?',  # Handle "Album - Platinum Edition"
            r'\s*-\s*remastered?',
            r'\s+platinum\s*edition?$',  # Handle "Album Platinum Edition" at end
            r'\s*\d{4}\s*remaster',  # Year remaster
            r'\s*\(\d{4}\s*remaster\)'
        ]
        
        for pattern in album_patterns:
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
        """Calculates a confidence score using a prioritized model, starting with a strict 'core' title check."""
        
        # --- Artist Scoring (calculated once) ---
        spotify_artists_cleaned = [self.clean_artist(a) for a in spotify_track.artists if a]
        plex_artist_normalized = self.normalize_string(plex_track.artist)
        plex_artist_cleaned = self.clean_artist(plex_track.artist)

        best_artist_score = 0.0
        for spotify_artist in spotify_artists_cleaned:
            if spotify_artist and spotify_artist in plex_artist_normalized:
                best_artist_score = 1.0
                break
            score = self.similarity_score(spotify_artist, plex_artist_cleaned)
            if score > best_artist_score:
                best_artist_score = score
        artist_score = best_artist_score
        
        # --- Priority 1: Core Title Match (for exact matches like "Girls", "APT.", "LIL DEMON") ---
        spotify_core_title = self.get_core_string(spotify_track.name)
        plex_core_title = self.get_core_string(plex_track.title)

        if spotify_core_title and spotify_core_title == plex_core_title:
            # If the core titles are identical, we are highly confident.
            # The final score is a high base (0.9) plus a bonus for artist similarity.
            confidence = 0.90 + (artist_score * 0.09) # Max score of 0.99
            return confidence, "core_title_match"

        # --- Priority 2: Fuzzy Title Match (for variations, typos, etc.) ---
        spotify_title_cleaned = self.clean_title(spotify_track.name)
        plex_title_cleaned = self.clean_title(plex_track.title)
        
        title_score = self.similarity_score(spotify_title_cleaned, plex_title_cleaned)
        duration_score = self.duration_similarity(spotify_track.duration_ms, plex_track.duration if plex_track.duration else 0)

        # Use a standard weighted calculation if the core titles didn't match
        confidence = (title_score * 0.60) + (artist_score * 0.30) + (duration_score * 0.10)
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
    
    def generate_download_query(self, spotify_track: SpotifyTrack) -> str:
        """Generate optimized search query for downloading tracks"""
        # Use artist + track name for more precise matching
        if spotify_track.artists:
            # Use first artist and clean track name
            artist = self.clean_artist(spotify_track.artists[0])
            track = self.clean_title(spotify_track.name)
            return f"{artist} {track}".strip()
        else:
            # Fallback to just track name if no artist
            return self.clean_title(spotify_track.name)
        
    
    def calculate_slskd_match_confidence(self, spotify_track: SpotifyTrack, slskd_track: TrackResult) -> float:
        """
        Calculates a confidence score for a Soulseek track against a Spotify track.
        This is the core of the new matching logic.
        """
        # Normalize the Spotify track info once for efficiency
        spotify_title_norm = self.normalize_string(spotify_track.name)
        spotify_artists_norm = [self.normalize_string(a) for a in spotify_track.artists]

        # The slskd filename is our primary source of truth, so normalize it
        slskd_filename_norm = self.normalize_string(slskd_track.filename)

        # 1. Title Score: How well does the Spotify title appear in the filename?
        # We use the cleaned, core title for a strict check. This avoids matching remixes.
        spotify_cleaned_title = self.clean_title(spotify_track.name)
        title_score = 0.0
        if spotify_cleaned_title in slskd_filename_norm:
            title_score = 0.9  # High score for direct inclusion
            # Bonus for being a standalone word/phrase, penalizing partial matches like 'in' in 'finland'
            if re.search(r'\b' + re.escape(spotify_cleaned_title) + r'\b', slskd_filename_norm):
                 title_score = 1.0
        
        # 2. Artist Score: How well do the Spotify artists appear in the filename?
        artist_score = 0.0
        for artist in spotify_artists_norm:
            if artist in slskd_filename_norm:
                artist_score = 1.0 # Perfect match if any artist is found
                break
        
        # 3. Duration Score: How similar are the track lengths?
        # We give this a lower weight as slskd duration data can be unreliable.
        duration_score = self.duration_similarity(spotify_track.duration_ms, slskd_track.duration if slskd_track.duration else 0)

        # 4. Quality Bonus: Add a small bonus for higher quality formats
        quality_bonus = 0.0
        if slskd_track.quality:
            if slskd_track.quality.lower() == 'flac':
                quality_bonus = 0.1
            elif slskd_track.quality.lower() == 'mp3' and (slskd_track.bitrate or 0) >= 320:
                quality_bonus = 0.05

        # --- Final Weighted Score ---
        # Title and Artist are the most important factors for an accurate match.
        final_confidence = (title_score * 0.60) + (artist_score * 0.35) + (duration_score * 0.05)
        
        # Add the quality bonus to the final score
        final_confidence += quality_bonus
        
        # Ensure the final score doesn't exceed 1.0
        return min(final_confidence, 1.0)


    def find_best_slskd_matches(self, spotify_track: SpotifyTrack, slskd_results: List[TrackResult]) -> List[TrackResult]:
        """
        Scores and sorts a list of Soulseek results against a Spotify track.
        Returns the list of candidates sorted from best to worst match.
        """
        if not slskd_results:
            return []

        scored_results = []
        for slskd_track in slskd_results:
            confidence = self.calculate_slskd_match_confidence(spotify_track, slskd_track)
            # We temporarily store the confidence score on the object itself for sorting
            slskd_track.confidence = confidence 
            scored_results.append(slskd_track)

        # Sort by confidence score (descending), and then by size as a tie-breaker
        sorted_results = sorted(scored_results, key=lambda r: (r.confidence, r.size), reverse=True)
        
        # Filter out very low-confidence results to avoid bad matches.
        # A threshold of 0.6 means the title and artist had to have some reasonable similarity.
        confident_results = [r for r in sorted_results if r.confidence > 0.6]

        return confident_results
    
    def calculate_album_confidence(self, spotify_album, plex_album_info: Dict[str, Any]) -> float:
        """Calculate confidence score for album matching"""
        if not spotify_album or not plex_album_info:
            return 0.0
        
        score = 0.0
        
        # 1. Album name similarity (40% weight)
        spotify_album_clean = self.clean_album_name(spotify_album.name)
        plex_album_clean = self.clean_album_name(plex_album_info['title'])
        
        name_similarity = self.similarity_score(spotify_album_clean, plex_album_clean)
        score += name_similarity * 0.4
        
        # 2. Artist similarity (40% weight)
        if spotify_album.artists and plex_album_info.get('artist'):
            spotify_artist_clean = self.clean_artist(spotify_album.artists[0])
            plex_artist_clean = self.clean_artist(plex_album_info['artist'])
            
            artist_similarity = self.similarity_score(spotify_artist_clean, plex_artist_clean)
            score += artist_similarity * 0.4
        
        # 3. Track count similarity (10% weight)
        spotify_track_count = getattr(spotify_album, 'total_tracks', 0)
        plex_track_count = plex_album_info.get('track_count', 0)
        
        if spotify_track_count > 0 and plex_track_count > 0:
            # Calculate track count similarity (perfect match = 1.0, close matches get partial credit)
            track_diff = abs(spotify_track_count - plex_track_count)
            if track_diff == 0:
                track_similarity = 1.0
            elif track_diff <= 2:  # Allow for slight differences (bonus tracks, etc.)
                track_similarity = 0.8
            elif track_diff <= 5:
                track_similarity = 0.5
            else:
                track_similarity = 0.2
            
            score += track_similarity * 0.1
        
        # 4. Year similarity bonus (10% weight)
        spotify_year = spotify_album.release_date[:4] if spotify_album.release_date else None
        plex_year = str(plex_album_info.get('year', '')) if plex_album_info.get('year') else None
        
        if spotify_year and plex_year:
            if spotify_year == plex_year:
                score += 0.1  # Perfect year match
            elif abs(int(spotify_year) - int(plex_year)) <= 1:
                score += 0.05  # Close year match (remaster, etc.)
        
        return min(score, 1.0)  # Cap at 1.0
    
    def find_best_album_match(self, spotify_album, plex_albums: List[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], float]:
        """Find the best matching album from Plex candidates"""
        if not plex_albums:
            return None, 0.0
        
        best_match = None
        best_confidence = 0.0
        
        for plex_album in plex_albums:
            confidence = self.calculate_album_confidence(spotify_album, plex_album)
            
            if confidence > best_confidence:
                best_confidence = confidence
                best_match = plex_album
        
        # Only return matches above confidence threshold
        if best_confidence >= 0.8:  # High threshold for album matching
            return best_match, best_confidence
        else:
            return None, best_confidence
