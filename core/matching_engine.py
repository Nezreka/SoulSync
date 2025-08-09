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
            # Only remove featured artists, not parts of main artist names
            r'\s*feat\..*',
            r'\s*ft\..*',
            r'\s*featuring.*',
            # REMOVED: r'\s*&.*' - This breaks "Daryl Hall & John Oates", "Blood & Water"
            # REMOVED: r'\s*and.*' - This breaks artist names with "and"  
            # REMOVED: r',.*' - This can break legitimate artist names with commas
        ]
    
    def normalize_string(self, text: str) -> str:
        """
        Normalizes string by handling common stylizations, converting to ASCII,
        lowercasing, and replacing separators with spaces.
        """
        if not text:
            return ""
        # Handle Korn/KoЯn variations - both uppercase Я (U+042F) and lowercase я (U+044F)
        char_map = {
            'Я': 'R',  # Cyrillic 'Ya' to 'R'
            'я': 'r',  # Lowercase Cyrillic 'ya' to 'r'
        }

        # Apply the character replacements before other normalization steps
        for original, replacement in char_map.items():
            text = text.replace(original, replacement)
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
        """Calculates similarity score between two strings with enhanced version handling."""
        if not str1 or not str2:
            return 0.0
        
        # Standard similarity
        standard_ratio = SequenceMatcher(None, str1, str2).ratio()
        
        # Enhanced logic: Check if one string is a version of the other
        # This handles cases like "Back & forth" vs "Back & forth original mix"
        shorter, longer = (str1, str2) if len(str1) <= len(str2) else (str2, str1)
        
        # If the shorter string is at the start of the longer string
        if longer.startswith(shorter):
            # Extract the extra content
            extra_content = longer[len(shorter):].strip()
            
            # Check if the extra content looks like version info
            version_keywords = [
                'original mix', 'radio mix', 'club mix', 'extended mix',
                'slowed', 'reverb', 'sped up', 'acoustic', 'remix', 'remaster',
                'live', 'demo', 'instrumental', 'clean', 'explicit', 
                'radio edit', 'extended', 'version'
            ]
            
            # Normalize extra content for comparison
            extra_normalized = extra_content.lower().strip(' -()[]')
            
            # If the extra content matches version keywords, boost the similarity
            for keyword in version_keywords:
                if keyword in extra_normalized:
                    # High similarity but not perfect (to distinguish from exact matches)
                    return max(standard_ratio, 0.85)
        
        return standard_ratio
    
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
            # SAFETY CHECK: Only give high confidence if artist also matches reasonably well
            # This prevents "Artist A - Girls" from matching "Artist Z - Girls" with high confidence
            if artist_score >= 0.75:  # Require decent artist match
                # If the core titles are identical and artists match, we are highly confident
                confidence = 0.90 + (artist_score * 0.09) # Max score of 0.99
                return confidence, "core_title_match"
            # If artist score is too low, fall through to standard weighted calculation

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
    
    def detect_album_in_title(self, track_title: str, album_name: str = None) -> Tuple[str, bool]:
        """
        Detect if album name appears in track title and return cleaned version.
        Returns (cleaned_title, album_detected) tuple.
        """
        if not track_title:
            return "", False
            
        original_title = track_title
        title_lower = track_title.lower()
        
        # Common patterns where album name appears in track titles
        album_patterns = [
            r'\s*-\s*(.+)$',      # "Track - Album" (most common)
            r'\s*\|\s*(.+)$',     # "Track | Album" 
            r'\s*\(\s*(.+)\s*\)$' # "Track (Album)" 
        ]
        
        # If we have album name, check if it appears in the title
        if album_name:
            album_clean = album_name.lower().strip()
            
            for pattern in album_patterns:
                match = re.search(pattern, track_title)
                if match:
                    potential_album = match.group(1).lower().strip()
                    
                    # Check if the extracted part matches the album name with better fuzzy matching
                    similarity_threshold = 0.8
                    
                    # Calculate similarity between potential album and actual album
                    if potential_album == album_clean:
                        similarity = 1.0  # Exact match
                    elif potential_album in album_clean or album_clean in potential_album:
                        # Substring match - calculate how much overlap
                        shorter = min(len(potential_album), len(album_clean))
                        longer = max(len(potential_album), len(album_clean))
                        similarity = shorter / longer if longer > 0 else 0.0
                    else:
                        # Use string similarity for fuzzy matching
                        similarity = self.similarity_score(potential_album, album_clean)
                    
                    if similarity >= similarity_threshold:
                        # Remove the album part from the title
                        cleaned_title = re.sub(pattern, '', track_title).strip()
                        
                        # SAFETY CHECK: Don't return empty or too-short titles
                        if not cleaned_title or len(cleaned_title.strip()) < 2:
                            logger.warning(f"Album removal would create empty title: '{original_title}' → '{cleaned_title}' - keeping original")
                            return track_title, False
                        
                        # SAFETY CHECK: Don't remove if it would leave only articles or very short words
                        words = cleaned_title.split()
                        meaningful_words = [w for w in words if len(w) > 2 and w.lower() not in ['the', 'and', 'or', 'of', 'a', 'an']]
                        if not meaningful_words:
                            logger.warning(f"Album removal would leave only short words: '{original_title}' → '{cleaned_title}' - keeping original")
                            return track_title, False
                        
                        logger.debug(f"Detected album in title: '{original_title}' → '{cleaned_title}' (removed: '{match.group(1)}', similarity: {similarity:.2f})")
                        return cleaned_title, True
        
        # Fallback: detect common album-like suffixes even without album context
        # Look for patterns that might be album names (usually after dash)
        dash_pattern = r'\s*-\s*([A-Za-z][A-Za-z0-9\s&\-\']{3,30})$'
        match = re.search(dash_pattern, track_title)
        if match:
            potential_album_part = match.group(1).strip()
            
            # Heuristics: likely an album name if it:
            # - Doesn't contain common track descriptors
            # - Is reasonable length (4-30 chars)
            # - Doesn't look like a feature/remix indicator
            exclude_patterns = [
                r'\b(remix|mix|edit|version|live|acoustic|instrumental|demo|feat|ft|featuring)\b'
            ]
            
            is_likely_album = True
            for exclude_pattern in exclude_patterns:
                if re.search(exclude_pattern, potential_album_part.lower()):
                    is_likely_album = False
                    break
            
            if is_likely_album and 4 <= len(potential_album_part) <= 30:
                cleaned_title = re.sub(dash_pattern, '', track_title).strip()
                print(f"🎵 Heuristic album detection: '{original_title}' → '{cleaned_title}' (removed: '{potential_album_part}')")
                return cleaned_title, True
        
        return track_title, False

    def generate_download_queries(self, spotify_track: SpotifyTrack) -> List[str]:
        """
        Generate multiple search query variations for better matching.
        Returns queries in order of preference (cleaned titles first, then original).
        """
        queries = []
        
        if not spotify_track.artists:
            # No artist info - just use track name variations
            queries.append(self.clean_title(spotify_track.name))
            return queries
            
        artist = self.clean_artist(spotify_track.artists[0])
        original_title = spotify_track.name
        
        # Get album name if available - try multiple attribute names
        album_name = None
        for attr in ['album', 'album_name', 'album_title']:
            album_name = getattr(spotify_track, attr, None)
            if album_name:
                break
        
        # PRIORITY 1: Try removing potential album from title FIRST
        cleaned_title, album_detected = self.detect_album_in_title(original_title, album_name)
        if album_detected and cleaned_title != original_title:
            cleaned_track = self.clean_title(cleaned_title)
            if cleaned_track:
                queries.append(f"{artist} {cleaned_track}".strip())
                logger.debug(f"PRIORITY 1: Album-cleaned query: '{artist} {cleaned_track}'")
        
        # PRIORITY 2: Try simplified versions, but preserve important version info
        # Only remove content that's likely to be album names or noise, not version info
        
        # Pattern 1: Intelligently handle content after " - "
        # Only remove if it looks like album names, preserve version info like "slowed", "remix", etc.
        dash_pattern = r'^([^-]+?)\s*-\s*(.+)$'
        match = re.search(dash_pattern, original_title.strip())
        if match:
            title_part = match.group(1).strip()
            dash_content = match.group(2).strip().lower()
            
            # Define version keywords that should be preserved
            preserve_keywords = [
                'slowed', 'reverb', 'sped up', 'speed up', 'spedup', 'slowdown',
                'remix', 'mix', 'edit', 'version', 'remaster', 'acoustic', 
                'live', 'demo', 'instrumental', 'radio', 'extended', 'club',
                'original', 'clean', 'explicit', 'mashup', 'bootleg'
            ]
            
            # Check if the dash content contains version keywords
            should_preserve = any(keyword in dash_content for keyword in preserve_keywords)
            
            if not should_preserve and title_part and len(title_part) >= 3:
                # This looks like album content, safe to remove
                dash_clean = self.clean_title(title_part)
                if dash_clean and dash_clean not in [self.clean_title(q.split(' ', 1)[1]) for q in queries if ' ' in q]:
                    queries.append(f"{artist} {dash_clean}".strip())
                    logger.debug(f"PRIORITY 2: Dash-cleaned query (removed album): '{artist} {dash_clean}'")
            elif should_preserve:
                logger.debug(f"PRESERVED: Keeping dash content '{dash_content}' as it appears to be version info")
        
        # Pattern 2: Only remove parentheses that contain noise (feat, explicit, etc), not version info
        # Check if parentheses contain version-related keywords before removing
        paren_pattern = r'^(.+?)\s*\(([^)]+)\)(.*)$'
        paren_match = re.search(paren_pattern, original_title)
        if paren_match:
            before_paren = paren_match.group(1).strip()
            paren_content = paren_match.group(2).strip().lower()
            after_paren = paren_match.group(3).strip()
            
            # Define what we consider "noise" vs "important version info"
            noise_keywords = ['feat', 'ft', 'featuring', 'explicit', 'clean']
            # Expanded version keywords to match the dash preserve keywords
            version_keywords = [
                'slowed', 'reverb', 'sped up', 'speed up', 'spedup', 'slowdown',
                'remix', 'mix', 'edit', 'version', 'remaster', 'acoustic', 
                'live', 'demo', 'instrumental', 'radio', 'extended', 'club',
                'original', 'mashup', 'bootleg'
            ]
            
            # Only remove parentheses if they contain noise, not version info
            is_noise = any(keyword in paren_content for keyword in noise_keywords)
            is_version = any(keyword in paren_content for keyword in version_keywords)
            
            if is_noise and not is_version and before_paren:
                simple_title = (before_paren + ' ' + after_paren).strip()
                if simple_title and len(simple_title) >= 3:
                    simple_clean = self.clean_title(simple_title)
                    if simple_clean and simple_clean not in [self.clean_title(q.split(' ', 1)[1]) for q in queries if ' ' in q]:
                        queries.append(f"{artist} {simple_clean}".strip())
                        logger.debug(f"PRIORITY 2: Noise-removed query: '{artist} {simple_clean}'")
            elif is_version:
                logger.debug(f"PRESERVED: Keeping parentheses content '({paren_content})' as it appears to be version info")
        
        # PRIORITY 3: Original query (ONLY if no album was detected or if it's different)
        original_track_clean = self.clean_title(original_title)
        if not album_detected or not queries:  # Only add original if no album detected or no other queries
            if original_track_clean not in [q.split(' ', 1)[1] for q in queries if ' ' in q]:
                queries.append(f"{artist} {original_track_clean}".strip())
                logger.debug(f"PRIORITY 3: Original query: '{artist} {original_track_clean}'")
        
        # Remove duplicates while preserving order
        unique_queries = []
        seen = set()
        for query in queries:
            if query.lower() not in seen:
                unique_queries.append(query)
                seen.add(query.lower())
        
        return unique_queries

    def generate_download_query(self, spotify_track: SpotifyTrack) -> str:
        """
        Generate optimized search query for downloading tracks.
        Returns the most specific query (backward compatibility).
        """
        queries = self.generate_download_queries(spotify_track)
        return queries[0] if queries else ""
        
    
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
                quality_bonus = 0.07  # Reduced from 0.1 to prevent low-confidence FLAC beating high-confidence MP3
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
    
    def detect_version_type(self, filename: str) -> Tuple[str, float]:
        """
        Detect version type from filename and return (version_type, penalty).
        Penalties are applied to prefer original versions over variants.
        """
        if not filename:
            return 'original', 0.0
            
        filename_lower = filename.lower()
        
        # Define version patterns and their penalties (higher penalty = lower priority)
        version_patterns = {
            'remix': {
                'patterns': [r'\bremix\b', r'\brmx\b', r'\brework\b', r'\bedit\b(?!ion)'],
                'penalty': 0.15  # -15% penalty for remixes
            },
            'live': {
                'patterns': [r'\blive\b', r'\bconcert\b', r'\btour\b', r'\bperformance\b'],
                'penalty': 0.20  # -20% penalty for live versions
            },
            'acoustic': {
                'patterns': [r'\bacoustic\b', r'\bunplugged\b', r'\bstripped\b'],
                'penalty': 0.12  # -12% penalty for acoustic
            },
            'instrumental': {
                'patterns': [r'\binstrumental\b', r'\bkaraoke\b', r'\bminus one\b'],
                'penalty': 0.25  # -25% penalty for instrumentals (most different from original)
            },
            'radio': {
                'patterns': [r'\bradio\s*edit\b', r'\bradio\s*version\b', r'\bclean\s*edit\b'],
                'penalty': 0.08  # -8% penalty for radio edits (minor difference)
            },
            'extended': {
                'patterns': [r'\bextended\b', r'\bfull\s*version\b', r'\blong\s*version\b'],
                'penalty': 0.05  # -5% penalty for extended (close to original)
            },
            'demo': {
                'patterns': [r'\bdemo\b', r'\broughcut\b', r'\bunreleased\b'],
                'penalty': 0.18  # -18% penalty for demos
            },
            'explicit': {
                'patterns': [r'\bexplicit\b', r'\buncensored\b'],
                'penalty': 0.02  # -2% minor penalty (might be preferred by some)
            }
        }
        
        # Check each version type
        for version_type, config in version_patterns.items():
            for pattern in config['patterns']:
                if re.search(pattern, filename_lower):
                    return version_type, config['penalty']
        
        # No version indicators found - assume original
        return 'original', 0.0
    
    def calculate_slskd_match_confidence_enhanced(self, spotify_track: SpotifyTrack, slskd_track: TrackResult) -> Tuple[float, str]:
        """
        Enhanced version of calculate_slskd_match_confidence with version-aware scoring.
        Returns (confidence, version_type) tuple.
        """
        # Get base confidence using existing logic
        base_confidence = self.calculate_slskd_match_confidence(spotify_track, slskd_track)
        
        # Detect version type and get penalty
        version_type, penalty = self.detect_version_type(slskd_track.filename)
        
        # Apply version penalty
        if version_type != 'original':
            adjusted_confidence = max(0.0, base_confidence - penalty)
            # Store version info on the track object for UI display
            slskd_track.version_type = version_type
            slskd_track.version_penalty = penalty
        else:
            adjusted_confidence = base_confidence
            slskd_track.version_type = 'original'
            slskd_track.version_penalty = 0.0
        
        return adjusted_confidence, version_type
    
    def find_best_slskd_matches_enhanced(self, spotify_track: SpotifyTrack, slskd_results: List[TrackResult]) -> List[TrackResult]:
        """
        Enhanced version of find_best_slskd_matches with version-aware scoring.
        Returns candidates sorted by adjusted confidence (preferring originals).
        """
        if not slskd_results:
            return []

        scored_results = []
        for slskd_track in slskd_results:
            # Use enhanced confidence calculation
            confidence, version_type = self.calculate_slskd_match_confidence_enhanced(spotify_track, slskd_track)
            
            # Store the adjusted confidence and version info
            slskd_track.confidence = confidence
            slskd_track.version_type = getattr(slskd_track, 'version_type', 'original')
            scored_results.append(slskd_track)

        # Sort by confidence score (descending), then by version preference, then by size
        def sort_key(r):
            # Primary: confidence score
            # Secondary: prefer originals (original=0, others=penalty value for tie-breaking)
            version_priority = 0.0 if r.version_type == 'original' else getattr(r, 'version_penalty', 0.1)
            # Tertiary: file size
            return (r.confidence, -version_priority, r.size)
        
        sorted_results = sorted(scored_results, key=sort_key, reverse=True)
        
        # Filter out very low-confidence results
        # Lower the threshold to 0.45 to account for version penalties and album-in-title scenarios
        confident_results = [r for r in sorted_results if r.confidence > 0.45]
        
        # Debug logging for troubleshooting
        if scored_results and not confident_results:
            print(f"⚠️ DEBUG: Found {len(scored_results)} scored results but none met confidence threshold 0.45")
            for i, result in enumerate(sorted_results[:3]):  # Show top 3
                print(f"   {i+1}. {result.confidence:.3f} - {getattr(result, 'version_type', 'unknown')} - {result.filename[:60]}...")
        elif confident_results:
            print(f"✅ DEBUG: {len(confident_results)} results passed confidence threshold 0.45")
            for i, result in enumerate(confident_results[:3]):  # Show top 3
                print(f"   {i+1}. {result.confidence:.3f} - {getattr(result, 'version_type', 'unknown')} - {result.filename[:60]}...")

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
