from typing import List, Optional, Dict, Any, Tuple
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
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
        return self.plex_track is not None and self.confidence >= 0.7

class MusicMatchingEngine:
    def __init__(self):
        self.title_patterns = [
            r'\(.*?\)',
            r'\[.*?\]',
            r'\s*-\s*remaster.*',
            r'\s*-\s*remix.*',
            r'\s*-\s*live.*',
            r'\s*-\s*acoustic.*',
            r'\s*feat\..*',
            r'\s*ft\..*',
            r'\s*featuring.*',
        ]
        
        self.artist_patterns = [
            r'\s*feat\..*',
            r'\s*ft\..*',
            r'\s*featuring.*',
            r'\s*&.*',
            r'\s*and.*',
        ]
    
    def normalize_string(self, text: str) -> str:
        if not text:
            return ""
        
        text = text.lower().strip()
        
        text = re.sub(r'[^\w\s]', '', text)
        
        text = re.sub(r'\s+', ' ', text)
        
        return text
    
    def clean_title(self, title: str) -> str:
        cleaned = title
        
        for pattern in self.title_patterns:
            cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE)
        
        return self.normalize_string(cleaned)
    
    def clean_artist(self, artist: str) -> str:
        cleaned = artist
        
        for pattern in self.artist_patterns:
            cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE)
        
        return self.normalize_string(cleaned)
    
    def extract_main_artist(self, artists: List[str]) -> str:
        if not artists:
            return ""
        
        main_artist = artists[0]
        return self.clean_artist(main_artist)
    
    def similarity_score(self, str1: str, str2: str) -> float:
        if not str1 or not str2:
            return 0.0
        
        return SequenceMatcher(None, str1, str2).ratio()
    
    def duration_similarity(self, duration1: int, duration2: int) -> float:
        if duration1 == 0 or duration2 == 0:
            return 0.5
        
        max_duration = max(duration1, duration2)
        min_duration = min(duration1, duration2)
        
        if max_duration == 0:
            return 0.5
        
        diff_ratio = abs(max_duration - min_duration) / max_duration
        
        if diff_ratio <= 0.05:
            return 1.0
        elif diff_ratio <= 0.1:
            return 0.8
        elif diff_ratio <= 0.2:
            return 0.6
        else:
            return 0.3
    
    def calculate_match_confidence(self, spotify_track: SpotifyTrack, plex_track: PlexTrackInfo) -> Tuple[float, str]:
        spotify_title = self.clean_title(spotify_track.name)
        plex_title = self.clean_title(plex_track.title)
        
        spotify_artist = self.extract_main_artist(spotify_track.artists)
        plex_artist = self.clean_artist(plex_track.artist)
        
        spotify_album = self.normalize_string(spotify_track.album)
        plex_album = self.normalize_string(plex_track.album)
        
        title_score = self.similarity_score(spotify_title, plex_title)
        artist_score = self.similarity_score(spotify_artist, plex_artist)
        album_score = self.similarity_score(spotify_album, plex_album)
        
        # CORRECTED: Plex duration is already in milliseconds.
        duration_score = self.duration_similarity(
            spotify_track.duration_ms, 
            plex_track.duration if plex_track.duration else 0
        )
        
        if title_score >= 0.9 and artist_score >= 0.9 and album_score >= 0.8:
            return 0.95, "exact_match"
        elif title_score >= 0.8 and artist_score >= 0.8:
            return 0.85, "high_confidence"
        elif title_score >= 0.7 and artist_score >= 0.7:
            return 0.75, "medium_confidence"
        elif title_score >= 0.6 and artist_score >= 0.6:
            return 0.65, "low_confidence"
        else:
            return 0.0, "no_match"
    
    def find_best_match(self, spotify_track: SpotifyTrack, plex_tracks: List[PlexTrackInfo]) -> MatchResult:
        best_match = None
        best_confidence = 0.0
        best_match_type = "no_match"
        
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
    
    def match_playlist_tracks(self, spotify_tracks: List[SpotifyTrack], plex_tracks: List[PlexTrackInfo]) -> List[MatchResult]:
        results = []
        
        logger.info(f"Matching {len(spotify_tracks)} Spotify tracks against {len(plex_tracks)} Plex tracks")
        
        for spotify_track in spotify_tracks:
            match_result = self.find_best_match(spotify_track, plex_tracks)
            results.append(match_result)
            
            if match_result.is_match:
                logger.debug(f"Matched: {spotify_track.name} by {spotify_track.artists[0]} -> {match_result.plex_track.title} (confidence: {match_result.confidence:.2f})")
            else:
                logger.debug(f"No match found for: {spotify_track.name} by {spotify_track.artists[0]}")
        
        matched_count = sum(1 for r in results if r.is_match)
        logger.info(f"Successfully matched {matched_count}/{len(spotify_tracks)} tracks")
        
        return results
    
    def get_match_statistics(self, match_results: List[MatchResult]) -> Dict[str, Any]:
        total_tracks = len(match_results)
        matched_tracks = sum(1 for r in match_results if r.is_match)
        
        match_types = {}
        for result in match_results:
            if result.is_match:
                match_types[result.match_type] = match_types.get(result.match_type, 0) + 1
        
        confidence_distribution = {
            "high (>0.8)": sum(1 for r in match_results if r.confidence > 0.8),
            "medium (0.7-0.8)": sum(1 for r in match_results if 0.7 <= r.confidence <= 0.8),
            "low (0.6-0.7)": sum(1 for r in match_results if 0.6 <= r.confidence < 0.7),
            "no_match (<0.6)": sum(1 for r in match_results if r.confidence < 0.6)
        }
        
        return {
            "total_tracks": total_tracks,
            "matched_tracks": matched_tracks,
            "match_percentage": (matched_tracks / total_tracks * 100) if total_tracks > 0 else 0,
            "match_types": match_types,
            "confidence_distribution": confidence_distribution
        }
    
    def create_search_queries(self, spotify_track: SpotifyTrack) -> List[str]:
        queries = []
        
        main_artist = self.extract_main_artist(spotify_track.artists)
        clean_title = self.clean_title(spotify_track.name)
        clean_album = self.normalize_string(spotify_track.album)
        
        queries.append(f"{clean_title} {main_artist}")
        queries.append(f"{main_artist} {clean_title}")
        queries.append(f"{clean_title} {main_artist} {clean_album}")
        queries.append(f"{clean_album} {main_artist}")
        
        if len(spotify_track.artists) > 1:
            all_artists = " ".join([self.clean_artist(a) for a in spotify_track.artists])
            queries.append(f"{clean_title} {all_artists}")
        
        return queries
    
    def generate_download_query(self, spotify_track: SpotifyTrack) -> str:
        main_artist = self.extract_main_artist(spotify_track.artists)
        clean_title = self.clean_title(spotify_track.name)
        
        return f"{main_artist} {clean_title}"

matching_engine = MusicMatchingEngine()
