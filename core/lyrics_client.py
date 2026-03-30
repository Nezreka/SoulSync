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
            txt_path = os.path.splitext(audio_file_path)[0] + '.txt'

            # Skip if lyrics file already exists (either .lrc or .txt)
            if os.path.exists(lrc_path) or os.path.exists(txt_path):
                logger.debug(f"Lyrics file already exists for: {os.path.basename(audio_file_path)}")
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

            # LRClib API provides synced_lyrics (timestamped) and plain_lyrics (text only)
            synced = getattr(lyrics_data, 'synced_lyrics', None)
            plain = getattr(lyrics_data, 'plain_lyrics', None)

            logger.debug(f"Synced lyrics available: {bool(synced)}")
            logger.debug(f"Plain lyrics available: {bool(plain)}")

            if not synced and not plain:
                logger.debug(f"No usable lyrics content for: {artist_name} - {track_name}")
                return False

            if synced:
                # Synced lyrics have timestamps → valid .lrc format
                with open(lrc_path, 'w', encoding='utf-8') as f:
                    f.write(synced)
                # Embed synced lyrics in audio tags
                self._embed_lyrics(audio_file_path, synced)
                logger.info(f"✅ Created synced LRC + embedded: {os.path.basename(lrc_path)}")
            else:
                # Plain lyrics only → write as .txt (not .lrc, which requires timestamps)
                with open(txt_path, 'w', encoding='utf-8') as f:
                    f.write(plain)
                # Still embed plain lyrics in audio tags (players can display unsynced lyrics)
                self._embed_lyrics(audio_file_path, plain)
                logger.info(f"✅ Created plain lyrics .txt + embedded: {os.path.basename(txt_path)}")
            return True

        except Exception as e:
            logger.error(f"Error creating LRC file for {track_name}: {e}")
            return False


    def _embed_lyrics(self, audio_file_path: str, lyrics_text: str):
        """Embed lyrics directly into audio file tags."""
        try:
            from mutagen import File as MutagenFile
            from mutagen.flac import FLAC
            from mutagen.oggvorbis import OggVorbis
            from mutagen.mp4 import MP4
            from mutagen.id3 import ID3, USLT

            audio = MutagenFile(audio_file_path)
            if audio is None:
                return

            if audio.tags is None:
                return  # Don't create tags just for lyrics

            if isinstance(audio.tags, ID3):
                audio.tags.delall('USLT')
                audio.tags.add(USLT(encoding=3, lang='eng', desc='', text=lyrics_text))
                audio.save(v1=0, v2_version=4)
            elif isinstance(audio, (FLAC, OggVorbis)) or type(audio).__name__ == 'OggOpus':
                audio['lyrics'] = [lyrics_text]
                if isinstance(audio, FLAC):
                    audio.save(deleteid3=True)
                else:
                    audio.save()
            elif isinstance(audio, MP4):
                audio['\xa9lyr'] = [lyrics_text]
                audio.save()

            logger.debug(f"Embedded lyrics in: {os.path.basename(audio_file_path)}")
        except Exception as e:
            logger.warning(f"Could not embed lyrics in {os.path.basename(audio_file_path)}: {e}")


# Global instance for easy import
lyrics_client = LyricsClient()