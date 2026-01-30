from typing import List, Optional, Dict, Any, Tuple
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from unidecode import unidecode
from utils.logging_config import get_logger
from config.settings import config_manager

from core.spotify_client import Track as SpotifyTrack
from core.plex_client import PlexTrackInfo
from core.soulseek_client import TrackResult, AlbumResult


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
        
        # Expand specific abbreviations for better matching
        abbreviation_map = {
            r'\bpt\.': 'part',      # "pt." → "part"
            r'\bvol\.': 'volume',   # "vol." → "volume"
            r'\bfeat\.': 'featured' # "feat." → "featured"
            # Removed "ft." → "featured" (ambiguous: could be "feet" in measurements)
        }
        
        for pattern, replacement in abbreviation_map.items():
            text = re.sub(pattern, replacement, text)
        
        # --- IMPROVEMENT V4 ---
        # The user correctly pointed out that replacing '$' with 's' was incorrect
        # as it breaks searching for stylized names like A$AP Rocky.
        # The new approach is to PRESERVE the '$' symbol during normalization.
        
        # Replace common separators with spaces to preserve word boundaries.
        # Include hyphen in separator replacement for artist names like "AC/DC" vs "AC-DC"
        text = re.sub(r'[._/-]', ' ', text)

        # Keep alphanumeric characters, spaces, AND the '$' sign.
        text = re.sub(r'[^a-z0-9\s$]', '', text)
        
        # Consolidate multiple spaces into one
        text = re.sub(r'\s+', ' ', text).strip()
        
        return text
    
    def get_core_string(self, text: str) -> str:
        """Returns a 'core' version of a string with only letters and numbers for a strict comparison."""
        if not text:
            return ""
        # Use normalize_string first to get abbreviation expansion, then strip to core
        normalized = self.normalize_string(text)
        return re.sub(r'[^a-z0-9]', '', normalized)

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
        """
        Calculates similarity score between two strings with STRICT version handling.

        IMPORTANT: Different versions (remix, live, acoustic) should NOT match the original.
        This prevents false positives during sync where "Song Title (Remix)" matches "Song Title".
        """
        if not str1 or not str2:
            return 0.0

        # Exact match - highest score
        if str1 == str2:
            return 1.0

        # Standard similarity
        standard_ratio = SequenceMatcher(None, str1, str2).ratio()

        # STRICT VERSION CHECKING: Different versions should score LOW
        # This prevents "Song Title" from matching "Song Title (Remix)" during sync
        shorter, longer = (str1, str2) if len(str1) <= len(str2) else (str2, str1)

        # If the shorter string is at the start of the longer string
        if longer.startswith(shorter):
            # Extract the extra content
            extra_content = longer[len(shorter):].strip()

            # Check if the extra content looks like version info
            # Separate remasters from other versions - they should be treated differently
            remaster_keywords = ['remaster', 'remastered']

            different_version_keywords = [
                'remix', 'mix', 'rmx',  # Remixes (different song)
                'live', 'live at', 'live from',  # Live versions (different recording)
                'acoustic', 'unplugged',  # Acoustic versions (different arrangement)
                'slowed', 'reverb', 'sped up', 'speed up',  # TikTok edits (different)
                'radio edit', 'radio version',  # Radio edits (different)
                'instrumental', 'karaoke',  # Instrumental (different)
                'extended', 'extended version',  # Extended (different length)
                'demo', 'rough cut',  # Demos (different recording)
            ]

            # Normalize extra content for comparison
            extra_normalized = extra_content.lower().strip(' -()[]')

            # Check for remasters first - apply light penalty (might still match)
            for keyword in remaster_keywords:
                if keyword in extra_normalized:
                    # Light penalty for remasters (same song, different mastering)
                    # 0.75 = 75% match - likely still matches with 0.70 threshold
                    # With 50/50 title/artist split: 0.75 * 0.5 + 1.0 * 0.5 = 0.875 > 0.7 threshold
                    logger.debug(f"Remaster detected: '{str1}' vs '{str2}' (keyword: '{keyword}') - applying light penalty")
                    return 0.75

            # Check for different versions - apply heavy penalty (won't match)
            for keyword in different_version_keywords:
                if keyword in extra_normalized:
                    # Heavy penalty for different versions (remix, live, acoustic, etc.)
                    # 0.3 = 30% match - low enough to fail the 0.7 threshold
                    # With 50/50 title/artist split: 0.3 * 0.5 + 1.0 * 0.5 = 0.65 < 0.7 threshold
                    logger.debug(f"Version mismatch detected: '{str1}' vs '{str2}' (keyword: '{keyword}') - applying heavy penalty")
                    return 0.30

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
        
        # PRIORITY 0: Try exact Artist + Album + Title (Best for OSTs)
        # Often YouTube videos are titled "Artist - Album - Title" or similar
        # Only include if mode is youtube or hybrid (safe for Soulseek default)
        download_mode = config_manager.get('download_source.mode', 'soulseek')
        if download_mode in ['youtube', 'hybrid'] and album_name and album_name.lower() not in ['single', 'ep', 'greatest hits']:
             album_clean = self.clean_album_name(album_name)
             if album_clean:
                 # Standard query: Artist Album Title
                 queries.append(f"{artist} {album_clean} {self.clean_title(original_title)}".strip())
                 logger.debug(f"PRIORITY 0: Artist + Album + Title query: '{artist} {album_clean} {self.clean_title(original_title)}'")

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
        Uses full-string similarity matching (like Soularr) instead of substring matching
        to prevent false positives like "Girls" matching "Girls Girls Girls".
        """
        # Normalize the Spotify track info once for efficiency
        spotify_title_norm = self.normalize_string(spotify_track.name)
        spotify_artists_norm = [self.normalize_string(a) for a in spotify_track.artists]

        # The slskd filename is our primary source of truth, so normalize it
        slskd_filename_norm = self.normalize_string(slskd_track.filename)

        # 1. Title Score: Use full-string similarity instead of substring matching
        # This prevents false positives like "Love" matching "Loveless"
        spotify_cleaned_title = self.clean_title(spotify_track.name)

        # Calculate full-string similarity ratio (0.0 to 1.0) like Soularr does
        title_ratio = SequenceMatcher(None, spotify_cleaned_title, slskd_filename_norm).ratio()

        # Boost score if title appears as a complete word in filename
        has_word_boundary = bool(re.search(r'\b' + re.escape(spotify_cleaned_title) + r'\b', slskd_filename_norm))

        if has_word_boundary:
            # Title exists as complete word - significant bonus
            title_score = min(1.0, title_ratio + 0.3)
        else:
            # No word boundary match - rely on similarity ratio only
            title_score = title_ratio

        # 2. Artist Score: Keep substring matching for artists (they're more unique)
        # But add similarity-based fallback for better matching
        artist_score = 0.0
        best_artist_similarity = 0.0

        for artist in spotify_artists_norm:
            if artist in slskd_filename_norm:
                artist_score = 1.0  # Perfect match if any artist is found
                break
            else:
                # Try similarity matching as fallback for misspellings/variations
                artist_ratio = SequenceMatcher(None, artist, slskd_filename_norm).ratio()
                best_artist_similarity = max(best_artist_similarity, artist_ratio)

        # If no exact artist match, use best similarity with penalty
        if artist_score == 0.0 and best_artist_similarity > 0:
            artist_score = best_artist_similarity * 0.7  # Penalize similarity-only matches

        # 3. Duration Score: Increased weight for better accuracy
        duration_score = self.duration_similarity(spotify_track.duration_ms, slskd_track.duration if slskd_track.duration else 0)

        # 4. Quality Bonus: Reduced to prevent boosting bad matches
        quality_bonus = 0.0
        if slskd_track.quality:
            if slskd_track.quality.lower() == 'flac':
                quality_bonus = 0.03  # Reduced from 0.07
            elif slskd_track.quality.lower() == 'mp3' and (slskd_track.bitrate or 0) >= 320:
                quality_bonus = 0.02  # Reduced from 0.05

        # 5. Special handling for short titles (high false positive risk)
        # Titles like "Run", "Love", "Girls", "Stay" need stricter artist matching
        title_words = spotify_cleaned_title.split()
        is_short_title = len(spotify_cleaned_title) <= 5 or len(title_words) == 1

        # --- Final Weighted Score ---
        is_youtube = slskd_track.username == 'youtube'
        
        if is_youtube:
            # For YouTube, rely more on Title and Duration since Artist is often missing from video titles
            # and the search query already filtered by artist to some extent.
            # New weights: Title 70%, Artist 10%, Duration 20%
            final_confidence = (title_score * 0.70) + (artist_score * 0.10) + (duration_score * 0.20)
        else:
            # Standard weights for Soulseek (Artist is critical for correctness)
            # Rebalanced weights: Artist matching is now more important to prevent false positives
            final_confidence = (title_score * 0.45) + (artist_score * 0.40) + (duration_score * 0.15)

        # Apply short title penalty AFTER calculating base confidence
        # This allows perfect matches to still pass, but penalizes weak artist matches
        # For YouTube, skip penalty since artist matching is less reliable (searches are track-name-only)
        if is_short_title and artist_score < 0.5 and not is_youtube:
            # Heavy penalty but not complete rejection
            # Multiply by 0.4 (60% penalty) - still possible to pass if title+duration are perfect
            logger.debug(f"Short title '{spotify_cleaned_title}' with low artist match ({artist_score:.2f}) - applying 60% penalty")
            final_confidence *= 0.4

        # Add the quality bonus to the final score
        final_confidence += quality_bonus

        # Store individual scores for debugging (used in enhanced version)
        slskd_track.title_score = title_score
        slskd_track.artist_score = artist_score
        slskd_track.duration_score = duration_score

        # Debug logging to track matching decisions
        if final_confidence > 0.3:  # Only log potential matches
            logger.debug(
                f"Match scoring ({'YT' if is_youtube else 'SLSK'}): '{spotify_track.name}' by {spotify_track.artists[0] if spotify_track.artists else 'Unknown'} "
                f"vs '{slskd_track.filename[:60]}...' | "
                f"Title: {title_score:.2f} (ratio: {title_ratio:.2f}, boundary: {has_word_boundary}), "
                f"Artist: {artist_score:.2f}, Duration: {duration_score:.2f}, "
                f"Final: {final_confidence:.2f} {'✅ PASS' if final_confidence > 0.63 else '❌ FAIL'}"
            )
        
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
        # Threshold at 0.63 (63%) balances false positive reduction with match rate
        # Testing showed: 0.65 → 2.2% fewer matches, 0.63 should recover ~1% while keeping safety
        confident_results = [r for r in sorted_results if r.confidence > 0.63]

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

        STRICT VERSION MATCHING:
        - Live versions are ONLY accepted if Spotify track title contains "live" or "live version"
        - Remixes are ONLY accepted if Spotify track title contains "remix" or "mix"
        - Acoustic versions are ONLY accepted if Spotify track title contains "acoustic"
        - etc.
        """
        # Get base confidence using existing logic
        base_confidence = self.calculate_slskd_match_confidence(spotify_track, slskd_track)

        # Detect version type in Soulseek result
        version_type, penalty = self.detect_version_type(slskd_track.filename)

        # Check if Spotify track title contains version indicators
        spotify_title_lower = spotify_track.name.lower()

        # STRICT VERSION MATCHING: Reject mismatched versions
        if version_type == 'live':
            # Only accept live versions if Spotify title has live as a VERSION INDICATOR
            # Patterns: (Live), - Live, [Live], Live at, Live from, Live in, Live Version
            # NOT: words ending with 'live' like "Let Me Live" or starting like "Lively"
            live_patterns = [
                r'\(live\)',           # (Live) or (Live at Wembley)
                r'\[live\]',           # [Live]
                r'[-–—]\s*live\b',     # - Live or – Live
                r'\blive\s+at\b',      # Live at
                r'\blive\s+from\b',    # Live from
                r'\blive\s+in\b',      # Live in
                r'\blive\s+version\b', # Live Version
                r'\blive\s+recording\b' # Live Recording
            ]
            has_live_indicator = any(re.search(pattern, spotify_title_lower) for pattern in live_patterns)

            if not has_live_indicator:
                # Reject: Soulseek has live version but Spotify doesn't want it
                return 0.0, 'rejected_version_mismatch'

        elif version_type == 'remix':
            # Only accept remixes if Spotify title has remix as a VERSION INDICATOR
            # Patterns: (Remix), - Remix, [Remix], Remix, Mix
            remix_patterns = [
                r'\(.*?(remix|mix|rmx).*?\)',  # (Remix) or (DJ Remix)
                r'\[.*?(remix|mix|rmx).*?\]',  # [Remix]
                r'[-–—]\s*(remix|mix|rmx)\b',  # - Remix
                r'\b(remix|mix|rmx)\s*$',      # Remix at end
            ]
            has_remix_indicator = any(re.search(pattern, spotify_title_lower) for pattern in remix_patterns)

            if not has_remix_indicator:
                # Reject: Soulseek has remix but Spotify wants original
                return 0.0, 'rejected_version_mismatch'

        elif version_type == 'acoustic':
            # Only accept acoustic if Spotify title has acoustic as a VERSION INDICATOR
            acoustic_patterns = [
                r'\(.*?acoustic.*?\)',         # (Acoustic)
                r'\[.*?acoustic.*?\]',         # [Acoustic]
                r'[-–—]\s*acoustic\b',         # - Acoustic
                r'\bacoustic\s+version\b',     # Acoustic Version
            ]
            has_acoustic_indicator = any(re.search(pattern, spotify_title_lower) for pattern in acoustic_patterns)

            if not has_acoustic_indicator:
                # Reject: Soulseek has acoustic but Spotify wants original
                return 0.0, 'rejected_version_mismatch'

        elif version_type == 'instrumental':
            # Only accept instrumental if Spotify title has instrumental as a VERSION INDICATOR
            instrumental_patterns = [
                r'\(.*?instrumental.*?\)',     # (Instrumental)
                r'\[.*?instrumental.*?\]',     # [Instrumental]
                r'[-–—]\s*instrumental\b',     # - Instrumental
                r'\binstrumental\s+version\b', # Instrumental Version
            ]
            has_instrumental_indicator = any(re.search(pattern, spotify_title_lower) for pattern in instrumental_patterns)

            if not has_instrumental_indicator:
                # Reject: Soulseek has instrumental but Spotify wants original
                return 0.0, 'rejected_version_mismatch'

        # Apply version penalty (for matching versions, slight penalty for quality differences)
        if version_type != 'original':
            adjusted_confidence = max(0.0, base_confidence - (penalty * 0.5))  # Reduced penalty since it's a match
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
        # Threshold at 0.58 (58%) to prevent false positives while maintaining good match rate
        # Testing showed: 0.60 was slightly too strict, 0.58 balances accuracy and recall
        confident_results = [r for r in sorted_results if r.confidence > 0.58]
        
        # Debug logging for troubleshooting
        if scored_results and not confident_results:
            print(f"⚠️ DEBUG: Found {len(scored_results)} scored results but none met confidence threshold 0.58")
            for i, result in enumerate(sorted_results[:3]):  # Show top 3
                print(f"   {i+1}. {result.confidence:.3f} - {getattr(result, 'version_type', 'unknown')} - {result.filename[:60]}...")
        elif confident_results:
            print(f"✅ DEBUG: {len(confident_results)} results passed confidence threshold 0.58")
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

    def match_album_result_to_spotify_tracks(
        self,
        album_result: AlbumResult,
        spotify_tracks: List[SpotifyTrack],
        spotify_album_name: str,
        spotify_artist_name: str
    ) -> Tuple[float, Dict[str, TrackResult]]:
        """
        Match an AlbumResult from Soulseek against a list of Spotify tracks.

        Returns:
            Tuple of:
            - album_confidence: float (0.0-1.0) indicating overall album match quality
            - track_mapping: Dict mapping spotify_track_id -> matched TrackResult
        """
        if not album_result or not spotify_tracks:
            return 0.0, {}

        # Gate check: album title similarity
        cleaned_slskd_album = self.clean_album_name(album_result.album_title)
        cleaned_spotify_album = self.clean_album_name(spotify_album_name)
        album_title_score = self.similarity_score(cleaned_slskd_album, cleaned_spotify_album)

        if album_title_score < 0.65:
            logger.debug(f"Album title mismatch: '{album_result.album_title}' vs '{spotify_album_name}' (score: {album_title_score:.2f})")
            return 0.0, {}

        # Gate check: artist similarity
        # Method 1: Compare parsed artist name if available
        cleaned_spotify_artist = self.clean_artist(spotify_artist_name)
        artist_score = 0.0

        if album_result.artist:
            cleaned_slskd_artist = self.clean_artist(album_result.artist)
            artist_score = self.similarity_score(cleaned_slskd_artist, cleaned_spotify_artist)

        # Method 2: If artist name wasn't parsed or scored low, check if artist
        # appears in the full album path (e.g., "Music/Pink Floyd/Album Name/")
        # This mirrors the artist verification in get_valid_candidates()
        if artist_score < 0.60 and album_result.album_path:
            normalized_spotify_artist = re.sub(r'[^a-zA-Z0-9]', '', spotify_artist_name).lower()
            normalized_album_path = re.sub(r'[^a-zA-Z0-9]', '', album_result.album_path).lower()
            if normalized_spotify_artist and normalized_spotify_artist in normalized_album_path:
                artist_score = 0.85  # High confidence — artist name found in path
                logger.debug(f"Artist found in album path: '{spotify_artist_name}' in '{album_result.album_path}'")

        if artist_score < 0.60:
            logger.debug(f"Artist mismatch: '{album_result.artist}' vs '{spotify_artist_name}' (score: {artist_score:.2f}, path: '{album_result.album_path}')")
            return 0.0, {}

        # Per-track matching: build score matrix
        slskd_tracks = album_result.tracks
        score_triples = []  # (spotify_idx, slskd_idx, score)

        for sp_idx, sp_track in enumerate(spotify_tracks):
            sp_title_cleaned = self.clean_title(sp_track.name)
            sp_track_num = sp_idx + 1  # 1-based track number from Spotify order

            for sl_idx, sl_track in enumerate(slskd_tracks):
                # Title similarity (weight 0.50)
                sl_title = sl_track.title if sl_track.title else ''
                if not sl_title and sl_track.filename:
                    # Parse title from filename as fallback
                    fname = sl_track.filename.replace('\\', '/').split('/')[-1]
                    fname = re.sub(r'\.\w{3,4}$', '', fname)  # Remove extension
                    fname = re.sub(r'^\d+[\s.\-_]+', '', fname)  # Remove leading track number
                    sl_title = fname
                sl_title_cleaned = self.clean_title(sl_title)
                title_score = self.similarity_score(sp_title_cleaned, sl_title_cleaned)

                # Duration similarity (weight 0.30)
                sl_duration = sl_track.duration or 0
                duration_score = self.duration_similarity(sp_track.duration_ms, sl_duration)

                # Track number match (weight 0.20)
                sl_track_num = sl_track.track_number or 0
                if sl_track_num > 0 and sp_track_num > 0:
                    if sl_track_num == sp_track_num:
                        track_num_score = 1.0
                    elif abs(sl_track_num - sp_track_num) == 1:
                        track_num_score = 0.5
                    else:
                        track_num_score = 0.0
                else:
                    track_num_score = 0.3  # Neutral when track number unavailable

                combined = (title_score * 0.50) + (duration_score * 0.30) + (track_num_score * 0.20)
                score_triples.append((sp_idx, sl_idx, combined))

        # Greedy assignment: sort descending by score, assign without double-use
        score_triples.sort(key=lambda x: x[2], reverse=True)
        assigned_spotify = set()
        assigned_slskd = set()
        track_mapping = {}
        matched_scores = []

        for sp_idx, sl_idx, score in score_triples:
            if sp_idx in assigned_spotify or sl_idx in assigned_slskd:
                continue
            if score < 0.55:
                continue  # Below minimum per-track threshold

            sp_track = spotify_tracks[sp_idx]
            track_mapping[sp_track.id] = slskd_tracks[sl_idx]
            assigned_spotify.add(sp_idx)
            assigned_slskd.add(sl_idx)
            matched_scores.append(score)

        # Calculate album confidence
        match_ratio = len(track_mapping) / len(spotify_tracks) if spotify_tracks else 0.0
        avg_track_score = sum(matched_scores) / len(matched_scores) if matched_scores else 0.0
        track_count_ratio = (
            min(album_result.track_count, len(spotify_tracks)) /
            max(album_result.track_count, len(spotify_tracks))
        ) if spotify_tracks else 0.0

        album_confidence = (
            (match_ratio * 0.40) +
            (avg_track_score * 0.25) +
            (album_title_score * 0.20) +
            (artist_score * 0.10) +
            (track_count_ratio * 0.05)
        )

        logger.info(
            f"Album match: '{album_result.album_title}' by {album_result.username} -> "
            f"confidence={album_confidence:.2f}, matched={len(track_mapping)}/{len(spotify_tracks)}, "
            f"title={album_title_score:.2f}, artist={artist_score:.2f}, tracks_avg={avg_track_score:.2f}"
        )

        return album_confidence, track_mapping

    def find_best_album_source(
        self,
        album_results: List[AlbumResult],
        spotify_tracks: List[SpotifyTrack],
        spotify_album_name: str,
        spotify_artist_name: str,
        expected_track_count: int,
        quality_filter_fn=None
    ) -> Tuple[Optional[AlbumResult], float, Dict[str, TrackResult]]:
        """
        Find the best AlbumResult source for a complete album download.

        Returns:
            Tuple of (best_album, best_confidence, track_mapping) or (None, 0.0, {})
        """
        if not album_results or not spotify_tracks:
            return None, 0.0, {}

        best_album = None
        best_confidence = 0.0
        best_mapping = {}

        for album in album_results:
            # Skip tiny results
            if album.track_count < 2:
                continue

            # Quality filter if provided
            if quality_filter_fn:
                try:
                    if not quality_filter_fn(album):
                        logger.debug(f"Album '{album.album_title}' from {album.username} rejected by quality filter (dominant: {album.dominant_quality})")
                        continue
                except Exception as e:
                    logger.warning(f"Quality filter error for album '{album.album_title}': {e}")

            confidence, mapping = self.match_album_result_to_spotify_tracks(
                album, spotify_tracks, spotify_album_name, spotify_artist_name
            )

            if confidence > best_confidence:
                best_confidence = confidence
                best_album = album
                best_mapping = mapping

        # Minimum thresholds
        if best_confidence < 0.60:
            logger.info(f"No album source met confidence threshold (best: {best_confidence:.2f})")
            return None, 0.0, {}

        matched_ratio = len(best_mapping) / len(spotify_tracks) if spotify_tracks else 0.0
        if matched_ratio < 0.50:
            logger.info(f"Best album source matched too few tracks ({len(best_mapping)}/{len(spotify_tracks)})")
            return None, 0.0, {}

        logger.info(
            f"Best album source: '{best_album.album_title}' from {best_album.username} "
            f"(confidence={best_confidence:.2f}, matched={len(best_mapping)}/{len(spotify_tracks)}, "
            f"quality={best_album.dominant_quality})"
        )
        return best_album, best_confidence, best_mapping
