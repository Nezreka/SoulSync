"""WebMetadataUpdateWorker — lifted from web_server.py.

Body is byte-identical to the original. The module-level
``metadata_update_state`` global must be initialized via ``init()`` before
``WebMetadataUpdateWorker`` is instantiated, so the dict reference inside
the class body resolves to the same dict that web_server.py owns.
"""
import logging
import threading
import requests
from datetime import datetime

from core.matching_engine import MusicMatchingEngine
from core.runtime_state import add_activity_item
from config.settings import config_manager

logger = logging.getLogger(__name__)

# Injected at runtime via init() — points to web_server.metadata_update_state.
metadata_update_state = None


def init(state):
    """Bind the shared metadata_update_state dict from web_server."""
    global metadata_update_state
    metadata_update_state = state


class WebMetadataUpdateWorker:
    """Web-based metadata update worker - EXACT port of dashboard.py MetadataUpdateWorker"""

    def __init__(self, artists, media_client, spotify_client, server_type, refresh_interval_days=30):
        self.artists = artists
        self.media_client = media_client  # Can be plex_client or jellyfin_client
        self.spotify_client = spotify_client
        self.server_type = server_type  # "plex" or "jellyfin"
        self.matching_engine = MusicMatchingEngine()
        self.refresh_interval_days = refresh_interval_days
        self.should_stop = False
        self.processed_count = 0
        self.successful_count = 0
        self.failed_count = 0
        self.max_workers = 1
        # DB-first: reuse existing metadata from SoulSync database
        try:
            from database.music_database import MusicDatabase
            self._db = MusicDatabase()
        except Exception:
            self._db = None
        self.thread_lock = threading.Lock()
    
    def stop(self):
        self.should_stop = True
    
    def get_artist_name(self, artist):
        """Get artist name consistently across Plex and Jellyfin"""
        return getattr(artist, 'title', 'Unknown Artist')
    
    def run(self):
        """Process all artists one by one - EXACT copy from dashboard.py"""
        global metadata_update_state
        
        try:
            # Load artists in background if not provided - EXACTLY like dashboard.py
            if self.artists is None:
                # Enable lightweight mode for Jellyfin to skip track caching
                if self.server_type == "jellyfin":
                    self.media_client.set_metadata_only_mode(True)
                elif self.server_type == "navidrome":
                    # Navidrome doesn't need special mode setting
                    pass
                
                all_artists = self.media_client.get_all_artists()
                logger.debug(f"Raw artists returned: {[getattr(a, 'title', 'NO_TITLE') for a in (all_artists or [])]}")
                if not all_artists:
                    metadata_update_state['status'] = 'error'
                    metadata_update_state['error'] = f"No artists found in {self.server_type.title()} library"
                    add_activity_item("", "Metadata Update", metadata_update_state['error'], "Now")
                    return
                
                # Filter artists that need processing
                artists_to_process = [artist for artist in all_artists if self.artist_needs_processing(artist)]
                self.artists = artists_to_process
                
                # Emit loaded signal equivalent - EXACTLY like dashboard.py
                if len(artists_to_process) == 0:
                    metadata_update_state['status'] = 'completed'
                    metadata_update_state['completed_at'] = datetime.now()
                    add_activity_item("", "Metadata Update", "All artists already have good metadata", "Now")
                    return
                else:
                    add_activity_item("", "Metadata Update", f"Processing {len(artists_to_process)} of {len(all_artists)} artists", "Now")
                
                if not artists_to_process:
                    metadata_update_state['status'] = 'completed'
                    metadata_update_state['completed_at'] = datetime.now()
                    return
            
            total_artists = len(self.artists)
            metadata_update_state['total'] = total_artists
            
            # Process artists in parallel using ThreadPoolExecutor - EXACTLY like dashboard.py
            def process_single_artist(artist):
                """Process a single artist and return results"""
                if self.should_stop or metadata_update_state['status'] == 'stopping':
                    return None
                    
                artist_name = getattr(artist, 'title', 'Unknown Artist')
                
                # Double-check ignore flag right before processing
                if self.media_client.is_artist_ignored(artist):
                    return (artist_name, True, "Skipped (ignored)")
                
                try:
                    success, details = self.update_artist_metadata(artist)
                    return (artist_name, success, details)
                except Exception as e:
                    return (artist_name, False, f"Error: {str(e)}")
            
            # Process artists sequentially with rate limiting
            # (no ThreadPoolExecutor — API rate limits make parallelism counterproductive)
            import time
            for artist in self.artists:
                if self.should_stop or metadata_update_state['status'] == 'stopping':
                    break

                result = process_single_artist(artist)
                if result is None:
                    continue

                artist_name, success, details = result

                with self.thread_lock:
                    self.processed_count += 1
                    if success:
                        self.successful_count += 1
                    else:
                        self.failed_count += 1

                progress_percent = (self.processed_count / total_artists) * 100
                metadata_update_state.update({
                    'current_artist': artist_name,
                    'processed': self.processed_count,
                    'percentage': progress_percent,
                    'successful': self.successful_count,
                    'failed': self.failed_count
                })

                # Rate limit: 1.5s between artists (this actually runs between artists now)
                time.sleep(1.5)
            
            # Mark as completed - equivalent to finished.emit
            metadata_update_state['status'] = 'completed'
            metadata_update_state['completed_at'] = datetime.now()
            metadata_update_state['current_artist'] = 'Completed'
            
            summary = f"Processed {self.processed_count} artists: {self.successful_count} updated, {self.failed_count} failed"
            add_activity_item("", "Metadata Complete", summary, "Now")
            
        except Exception as e:
            logger.error(f"Metadata update failed: {e}")
            metadata_update_state['status'] = 'error'
            metadata_update_state['error'] = str(e)
            add_activity_item("", "Metadata Error", str(e), "Now")
    
    def artist_needs_processing(self, artist):
        """Check if an artist needs metadata processing using age-based detection - EXACT copy from dashboard.py"""
        try:
            # Check if artist is manually ignored
            if self.media_client.is_artist_ignored(artist):
                return False
            
            # Use media client's age-based checking with configured interval
            return self.media_client.needs_update_by_age(artist, self.refresh_interval_days)
            
        except Exception as e:
            logger.error(f"Error checking artist {getattr(artist, 'title', 'Unknown')}: {e}")
            return True  # Process if we can't determine status
    
    def _check_db_artist(self, artist_name):
        """Check SoulSync DB for existing artist metadata (genres, spotify_artist_id).

        NOTE: DB thumb_url is a Plex/Jellyfin internal path, NOT a downloadable URL.
        Photos must be checked via the media server object, not the DB.

        Returns (db_artist_dict, has_genres, spotify_artist_id) or (None, False, None) if not found."""
        if not self._db:
            return None, False, None
        try:
            db_artists = self._db.search_artists(artist_name, limit=5)
            if not db_artists:
                return None, False, None
            # Find best name match
            best = None
            best_score = 0.0
            norm_name = self.matching_engine.normalize_string(artist_name)
            for dba in db_artists:
                score = self.matching_engine.similarity_score(
                    norm_name, self.matching_engine.normalize_string(dba.name))
                if score > best_score:
                    best_score = score
                    best = dba
            if not best or best_score < 0.85:
                return None, False, None
            has_genres = bool(best.genres and len(best.genres) > 0)
            # Get spotify_artist_id from raw DB row (not in dataclass)
            spotify_artist_id = None
            try:
                raw = self._db.api_get_artist(best.id)
                if raw:
                    spotify_artist_id = raw.get('spotify_artist_id')
            except Exception as e:
                logger.debug("get spotify_artist_id failed: %s", e)
            return best, has_genres, spotify_artist_id
        except Exception:
            return None, False, None

    def update_artist_metadata(self, artist):
        """Update a single artist's metadata. Checks SoulSync DB first to avoid unnecessary API calls.

        DB-first strategy:
        - Genres: DB stores real genre strings → can apply directly, skip Spotify
        - spotify_artist_id: DB may have it from enrichment → skip search_artists() call
        - Photos/album art: DB thumb_url is a media-server internal path (not downloadable)
          so these MUST come from Spotify API
        """
        try:
            artist_name = getattr(artist, 'title', 'Unknown Artist')

            # Skip processing for artists with no valid name
            if artist_name == 'Unknown Artist' or not artist_name or not artist_name.strip():
                return False, "Skipped: No valid artist name"

            # DB-first: check what we already have cached
            db_artist, db_has_genres, db_spotify_id = self._check_db_artist(artist_name)

            # Check what the media server artist is currently missing
            needs_photo = not self.artist_has_valid_photo(artist) if self.server_type != "jellyfin" else True
            needs_genres = not getattr(artist, 'genres', None)
            needs_album_art = self.server_type == "plex"

            # If media server already has valid photo + genres + album art, skip entirely
            if not needs_photo and not needs_genres and not needs_album_art:
                self.media_client.update_artist_biography(artist)
                return True, "Already up to date"

            # Determine if we actually need Spotify
            # Photos and album art MUST come from Spotify (DB only has internal media server paths)
            # Genres CAN come from DB if available
            need_spotify = needs_photo or needs_album_art or (needs_genres and not db_has_genres)

            spotify_artist = None
            highest_score = 0.0

            if need_spotify:
                # Try direct lookup by cached spotify_artist_id first (1 API call vs search)
                if db_spotify_id:
                    try:
                        from core.spotify_client import Artist as SpotifyArtistDC
                        raw = self.spotify_client.get_artist(db_spotify_id)
                        if raw and 'name' in raw:
                            spotify_artist = SpotifyArtistDC.from_spotify_artist(raw)
                            highest_score = 1.0
                            logger.debug(f"Metadata updater: direct Spotify lookup for '{artist_name}' via cached ID {db_spotify_id}")
                    except Exception as e:
                        logger.debug(f"Direct Spotify lookup failed for {db_spotify_id}: {e}")
                        spotify_artist = None

                # Fall back to search if direct lookup didn't work
                if not spotify_artist:
                    spotify_artists = self.spotify_client.search_artists(artist_name, limit=5)
                    if not spotify_artists:
                        # Spotify failed — apply DB genres if available, skip photos/art
                        changes_made = []
                        if needs_genres and db_has_genres and db_artist:
                            if self._apply_db_genres(artist, db_artist.genres):
                                changes_made.append("genres (DB)")
                        if changes_made:
                            self.media_client.update_artist_biography(artist)
                            return True, f"Updated {', '.join(changes_made)} (Spotify unavailable)"
                        return False, "Not found on Spotify"

                    # Find the best match
                    best_match = None
                    plex_artist_normalized = self.matching_engine.normalize_string(artist_name)

                    for sa in spotify_artists:
                        spotify_artist_normalized = self.matching_engine.normalize_string(sa.name)
                        score = self.matching_engine.similarity_score(plex_artist_normalized, spotify_artist_normalized)
                        if score > highest_score:
                            highest_score = score
                            best_match = sa

                    if not best_match or highest_score < 0.7:
                        # No good Spotify match — still try DB genres
                        changes_made = []
                        if needs_genres and db_has_genres and db_artist:
                            if self._apply_db_genres(artist, db_artist.genres):
                                changes_made.append("genres (DB)")
                        if changes_made:
                            self.media_client.update_artist_biography(artist)
                            return True, f"Updated {', '.join(changes_made)} (no Spotify match)"
                        return False, f"No confident match found (best: '{getattr(best_match, 'name', 'N/A')}', score: {highest_score:.2f})"

                    spotify_artist = best_match

            changes_made = []

            # Update photo (always from Spotify — DB only has media server paths)
            if needs_photo and spotify_artist:
                photo_updated = self.update_artist_photo(artist, spotify_artist)
                if photo_updated:
                    changes_made.append("photo")

            # Update genres — use DB if available, otherwise Spotify
            if needs_genres:
                if db_has_genres and db_artist:
                    genres_updated = self._apply_db_genres(artist, db_artist.genres)
                    if genres_updated:
                        changes_made.append("genres (DB)")
                    elif spotify_artist:
                        # DB genres didn't result in changes, try Spotify for newer/different genres
                        genres_updated = self.update_artist_genres(artist, spotify_artist)
                        if genres_updated:
                            changes_made.append("genres")
                elif spotify_artist:
                    genres_updated = self.update_artist_genres(artist, spotify_artist)
                    if genres_updated:
                        changes_made.append("genres")

            # Update album artwork (only for Plex, always from Spotify)
            if self.server_type == "plex" and spotify_artist:
                albums_updated = self.update_album_artwork(artist, spotify_artist)
                if albums_updated > 0:
                    changes_made.append(f"{albums_updated} album art")
            elif self.server_type != "plex":
                logger.info(f"Skipping album artwork updates for Jellyfin artist: {artist.title}")

            if changes_made:
                biography_updated = self.media_client.update_artist_biography(artist)
                if biography_updated:
                    changes_made.append("timestamp")

                source = f"match: '{spotify_artist.name}', score: {highest_score:.2f}" if spotify_artist else "DB cache"
                details = f"Updated {', '.join(changes_made)} ({source})"
                return True, details
            else:
                self.media_client.update_artist_biography(artist)
                return True, "Already up to date"

        except Exception as e:
            return False, str(e)

    def _apply_db_genres(self, artist, genres):
        """Apply genres from DB cache to media server."""
        try:
            if not genres:
                return False
            existing_genres = set(genre.tag if hasattr(genre, 'tag') else str(genre)
                                for genre in (getattr(artist, 'genres', None) or []))
            db_genres = set(g for g in genres if g and g.strip() and len(g.strip()) > 1)
            if db_genres and db_genres != existing_genres:
                return self.media_client.update_artist_genres(artist, list(db_genres)[:10])
            return False
        except Exception:
            return False
    
    def update_artist_photo(self, artist, spotify_artist):
        """Update artist photo from Spotify - EXACT copy from dashboard.py"""
        try:
            # Check if artist already has a good photo (skip check for Jellyfin)
            if self.server_type != "jellyfin" and self.artist_has_valid_photo(artist):
                logger.info(f"Skipping {artist.title}: already has valid photo ({getattr(artist, 'thumb', 'None')})")
                return False

            # Get the image URL from Spotify
            if not spotify_artist.image_url:
                logger.warning(f"Skipping {artist.title}: no Spotify image URL available")
                return False

            logger.info(f"Processing {artist.title}: downloading from Spotify...")
                
            image_url = spotify_artist.image_url
            
            # Download and validate image
            response = requests.get(image_url, timeout=10)
            response.raise_for_status()

            # Validate and convert image (skip conversion for Jellyfin to preserve format)
            if self.server_type == "jellyfin":
                # For Jellyfin, use raw image data to preserve original format
                image_data = response.content
                logger.info(f"Using raw image data for Jellyfin ({len(image_data)} bytes)")
            else:
                # For other servers, validate and convert
                image_data = self.validate_and_convert_image(response.content)
                if not image_data:
                    return False
            
            # Upload to media server using client's method
            return self.media_client.update_artist_poster(artist, image_data)
            
        except Exception as e:
            logger.error(f"Error updating photo for {getattr(artist, 'title', 'Unknown')}: {e}")
            return False
    
    def update_artist_genres(self, artist, spotify_artist):
        """Update artist genres from Spotify and albums - EXACT copy from dashboard.py"""
        try:
            # Get existing genres
            existing_genres = set(genre.tag if hasattr(genre, 'tag') else str(genre) 
                                for genre in (artist.genres or []))
            
            # Get Spotify artist genres
            spotify_genres = set(spotify_artist.genres or [])
            
            # Get genres from all albums
            album_genres = set()
            try:
                for album in artist.albums():
                    if hasattr(album, 'genres') and album.genres:
                        album_genres.update(genre.tag if hasattr(genre, 'tag') else str(genre) 
                                          for genre in album.genres)
            except Exception:
                pass  # Albums might not be accessible
            
            # Combine all genres (prioritize Spotify genres)
            all_genres = spotify_genres.union(album_genres)
            
            # Filter out empty/invalid genres
            all_genres = {g for g in all_genres if g and g.strip() and len(g.strip()) > 1}
            
            # Only update if we have new genres and they're different
            if all_genres and (not existing_genres or all_genres != existing_genres):
                # Convert to list and limit to 10 genres
                genre_list = list(all_genres)[:10]
                
                # Use media client API to update genres
                success = self.media_client.update_artist_genres(artist, genre_list)
                if success:
                    return True
                else:
                    return False
            else:
                return False
            
        except Exception as e:
            logger.error(f"Error updating genres for {getattr(artist, 'title', 'Unknown')}: {e}")
            return False
    
    def update_album_artwork(self, artist, spotify_artist):
        """Update album artwork for all albums by this artist from Spotify.
        DB thumb_url is a media-server internal path, so album art must come from Spotify."""
        try:
            updated_count = 0
            skipped_count = 0

            # Get all albums for this artist
            try:
                albums = list(artist.albums())
            except Exception:
                logger.error(f"Could not access albums for artist '{artist.title}'")
                return 0

            if not albums:
                logger.warning(f"No albums found for artist '{artist.title}'")
                return 0

            import time
            for album in albums:
                try:
                    album_title = getattr(album, 'title', 'Unknown Album')

                    # Check if album already has good artwork on the media server
                    if self.album_has_valid_artwork(album):
                        skipped_count += 1
                        continue

                    # Rate limit between album API calls
                    time.sleep(0.5)

                    # Search for this specific album on Spotify
                    album_query = f"album:{album_title} artist:{spotify_artist.name}"
                    spotify_albums = self.spotify_client.search_albums(album_query, limit=3)

                    if not spotify_albums:
                        continue

                    # Find the best matching album
                    best_album = None
                    highest_score = 0.0

                    plex_album_normalized = self.matching_engine.normalize_string(album_title)

                    for spotify_album in spotify_albums:
                        spotify_album_normalized = self.matching_engine.normalize_string(spotify_album.name)
                        score = self.matching_engine.similarity_score(plex_album_normalized, spotify_album_normalized)

                        if score > highest_score:
                            highest_score = score
                            best_album = spotify_album

                    # If we found a good match with artwork, download it
                    if best_album and highest_score > 0.7 and best_album.image_url:
                        if self.download_and_upload_album_artwork(album, best_album.image_url):
                            updated_count += 1

                except Exception as e:
                    logger.error(f"Error processing album '{getattr(album, 'title', 'Unknown')}': {e}")
                    continue

            return updated_count

        except Exception as e:
            logger.error(f"Error updating album artwork for artist '{getattr(artist, 'title', 'Unknown')}': {e}")
            return 0
    
    def album_has_valid_artwork(self, album):
        """Check if album has valid artwork - EXACT copy from dashboard.py"""
        try:
            if not hasattr(album, 'thumb') or not album.thumb:
                return False
            
            thumb_url = str(album.thumb)
            
            # Completely empty or None
            if not thumb_url or thumb_url.strip() == '':
                return False
            
            # Obvious placeholder text in URL
            obvious_placeholders = ['no-image', 'placeholder', 'missing', 'default-album', 'blank.jpg', 'empty.png']
            thumb_lower = thumb_url.lower()
            for placeholder in obvious_placeholders:
                if placeholder in thumb_lower:
                    return False
            
            # Extremely short URLs (likely broken)
            if len(thumb_url) < 20:
                return False
            
            return True
            
        except Exception as e:
            return True
    
    def download_and_upload_album_artwork(self, album, image_url):
        """Download artwork from Spotify and upload to media server - EXACT copy from dashboard.py"""
        try:
            # Download image from Spotify
            response = requests.get(image_url, timeout=10)
            response.raise_for_status()
            
            # Validate and convert image
            image_data = self.validate_and_convert_image(response.content)
            if not image_data:
                return False
            
            # Upload using media client
            success = self.media_client.update_album_poster(album, image_data)
            return success
            
        except Exception as e:
            logger.error(f"Error downloading/uploading artwork for album '{getattr(album, 'title', 'Unknown')}': {e}")
            return False
    
    def artist_has_valid_photo(self, artist):
        """Check if artist has a valid photo - EXACT copy from dashboard.py"""
        try:
            if not hasattr(artist, 'thumb') or not artist.thumb:
                return False
            
            thumb_url = str(artist.thumb)
            if 'default' in thumb_url.lower() or len(thumb_url) < 50:
                return False
            
            return True
            
        except Exception:
            return False
    
    def validate_and_convert_image(self, image_data):
        """Validate and convert image for media server compatibility - EXACT copy from dashboard.py"""
        try:
            from PIL import Image
            import io
            
            # Open and validate image
            image = Image.open(io.BytesIO(image_data))
            
            # Check minimum dimensions
            width, height = image.size
            if width < 200 or height < 200:
                return None
            
            # Convert to JPEG for consistency
            if image.format != 'JPEG':
                buffer = io.BytesIO()
                image.convert('RGB').save(buffer, format='JPEG', quality=95)
                return buffer.getvalue()
            
            return image_data
            
        except Exception:
            return None
    
    def upload_artist_poster(self, artist, image_data):
        """Upload poster using media client - EXACT copy from dashboard.py"""
        try:
            # Use media client's update method if available
            if hasattr(self.media_client, 'update_artist_poster'):
                return self.media_client.update_artist_poster(artist, image_data)
            
            # Fallback for Plex: direct API call
            if self.server_type == "plex":
                import requests
                server = self.media_client.server
                upload_url = f"{server._baseurl}/library/metadata/{artist.ratingKey}/posters"
                headers = {
                    'X-Plex-Token': server._token,
                    'Content-Type': 'image/jpeg'
                }
                
                response = requests.post(upload_url, data=image_data, headers=headers)
                response.raise_for_status()
                
                # Refresh artist to see changes
                artist.refresh()
                return True

            # Jellyfin: Use Jellyfin API to upload artist image
            elif self.server_type == "jellyfin":
                import requests
                jellyfin_config = config_manager.get_jellyfin_config()
                jellyfin_base_url = jellyfin_config.get('base_url', '')
                jellyfin_token = jellyfin_config.get('api_key', '')

                if not jellyfin_base_url or not jellyfin_token:
                    logger.warning("Jellyfin configuration missing for image upload")
                    return False

                upload_url = f"{jellyfin_base_url.rstrip('/')}/Items/{artist.ratingKey}/Images/Primary"
                headers = {
                    'Authorization': f'MediaBrowser Token="{jellyfin_token}"',
                    'Content-Type': 'image/jpeg'
                }

                response = requests.post(upload_url, data=image_data, headers=headers)
                response.raise_for_status()
                return True

            # Navidrome: Currently not supported (Subsonic API doesn't support image uploads)
            elif self.server_type == "navidrome":
                logger.info("ℹ️ Navidrome does not support artist image uploads via Subsonic API")
                return False

            else:
                # Unknown server type
                return False
            
        except Exception as e:
            logger.error(f"Error uploading poster: {e}")
            return False
