#!/usr/bin/env python3

import os
from utils.logging_config import get_logger

logger = get_logger("lyrics_client")

class LyricsClient:
    """
    Minimal, elegant LRClib client for automatic lyrics fetching.
    Generates .lrc sidecar files during post-processing.
    """

    def __init__(self):
        self.api = None
        self._init_api()

    def _init_api(self):
        """Initialize LRClib API with graceful fallback"""
        try:
            from lrclib import LrcLibAPI
            self.api = LrcLibAPI(user_agent="SoulSync/1.0 (WebUI)")
            logger.debug("LRClib API client initialized")
        except ImportError:
            logger.warning("LRClib API not available - lyrics functionality disabled")
            self.api = None
        except Exception as e:
            logger.error(f"Error initializing LRClib API: {e}")
            self.api = None

    def create_lrc_file(self, audio_file_path: str, track_name: str, artist_name: str,
                       album_name: str = None, duration_seconds: int = None) -> bool:
        """
        Create .lrc sidecar file for the given audio file.

        Args:
            audio_file_path: Path to the audio file
            track_name: Track title
            artist_name: Artist name
            album_name: Album name (optional)
            duration_seconds: Track duration in seconds (optional)

        Returns:
            bool: True if LRC file was created successfully
        """
        if not self.api:
            logger.debug("LRClib API not available - skipping lyrics")
            return False

        try:
            # Generate LRC file path (same name as audio file, .lrc extension)
            lrc_path = os.path.splitext(audio_file_path)[0] + '.lrc'

            # Skip if LRC file already exists
            if os.path.exists(lrc_path):
                logger.debug(f"LRC file already exists: {os.path.basename(lrc_path)}")
                return True

            # Fetch lyrics from LRClib
            logger.debug(f"Fetching lyrics for: {artist_name} - {track_name}")

            lyrics_data = None

            # Strategy 1: Exact match with duration (most accurate)
            if duration_seconds and album_name:
                try:
                    logger.debug(f"Trying exact match: {track_name} by {artist_name} from {album_name} ({duration_seconds}s)")
                    lyrics_data = self.api.get_lyrics(
                        track_name=track_name,
                        artist_name=artist_name,
                        album_name=album_name,
                        duration=duration_seconds
                    )
                    if lyrics_data:
                        logger.debug("Exact match found!")
                except Exception as e:
                    logger.debug(f"Exact match failed: {e}")

            # Strategy 2: Search without duration
            if not lyrics_data:
                try:
                    logger.debug(f"Trying search: {track_name} by {artist_name}")
                    search_results = self.api.search_lyrics(
                        track_name=track_name,
                        artist_name=artist_name
                    )
                    if search_results:
                        lyrics_data = search_results[0]  # Take first result
                        logger.debug(f"Search found {len(search_results)} results, using first")
                except Exception as e:
                    logger.debug(f"Search fallback failed: {e}")

            # No lyrics found
            if not lyrics_data:
                logger.debug(f"No lyrics found for: {artist_name} - {track_name}")
                return False

            # Prefer synced lyrics, fallback to plain text
            # LRClib API uses synced_lyrics and plain_lyrics attributes
            lrc_content = getattr(lyrics_data, 'synced_lyrics', None) or getattr(lyrics_data, 'plain_lyrics', None)

            logger.debug(f"Synced lyrics available: {bool(getattr(lyrics_data, 'synced_lyrics', None))}")
            logger.debug(f"Plain lyrics available: {bool(getattr(lyrics_data, 'plain_lyrics', None))}")
            logger.debug(f"LRC content found: {bool(lrc_content)}")

            if not lrc_content:
                logger.debug(f"No usable lyrics content for: {artist_name} - {track_name}")
                return False

            # Write LRC file
            with open(lrc_path, 'w', encoding='utf-8') as f:
                f.write(lrc_content)

            lyrics_type = "synced" if getattr(lyrics_data, 'synced_lyrics', None) else "plain"
            logger.info(f"✅ Created {lyrics_type} LRC file: {os.path.basename(lrc_path)}")
            return True

        except Exception as e:
            logger.error(f"Error creating LRC file for {track_name}: {e}")
            return False


# Global instance for easy import
lyrics_client = LyricsClient()