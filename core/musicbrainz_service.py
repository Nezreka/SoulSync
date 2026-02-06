from typing import Optional, Dict, Any
import json
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from utils.logging_config import get_logger
from core.musicbrainz_client import MusicBrainzClient
from database.music_database import MusicDatabase

logger = get_logger("musicbrainz_service")

class MusicBrainzService:
    """Service layer for MusicBrainz integration with caching and matching logic"""
    
    def __init__(self, database: MusicDatabase, app_name: str = "SoulSync", app_version: str = "1.0", contact_email: str = ""):
        self.db = database
        self.mb_client = MusicBrainzClient(app_name, app_version, contact_email)
        self.retry_days = 30  # Retry 'not_found' items after 30 days
    
    def _calculate_similarity(self, str1: str, str2: str) -> float:
        """Calculate string similarity score (0.0 to 1.0)"""
        if not str1 or not str2:
            return 0.0
        
        # Normalize for comparison
        s1 = str1.lower().strip()
        s2 = str2.lower().strip()
        
        if s1 == s2:
            return 1.0
        
        return SequenceMatcher(None, s1, s2).ratio()
    
    def _check_cache(self, entity_type: str, entity_name: str, artist_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Check if we have a cached MusicBrainz result"""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            
            # Fix: Match exact artist_name (not OR artist_name IS NULL)
            # This prevents getting wrong cached results
            if artist_name is not None:
                cursor.execute("""
                    SELECT musicbrainz_id, metadata_json, match_confidence, last_updated
                    FROM musicbrainz_cache
                    WHERE entity_type = ? AND entity_name = ? AND artist_name = ?
                    ORDER BY last_updated DESC
                    LIMIT 1
                """, (entity_type, entity_name, artist_name))
            else:
                cursor.execute("""
                    SELECT musicbrainz_id, metadata_json, match_confidence, last_updated
                    FROM musicbrainz_cache
                    WHERE entity_type = ? AND entity_name = ? AND artist_name IS NULL
                    ORDER BY last_updated DESC
                    LIMIT 1
                """, (entity_type, entity_name))
            
            row = cursor.fetchone()
            
            if row:
                # Don't use cache if it's older than 90 days
                last_updated = datetime.fromisoformat(row[3]) if row[3] else None
                if last_updated and (datetime.now() - last_updated).days > 90:
                    logger.debug(f"Cache entry for {entity_type} '{entity_name}' is stale (> 90 days)")
                    return None
                
                # Parse JSON with error handling
                try:
                    metadata = json.loads(row[1]) if row[1] else None
                except json.JSONDecodeError:
                    logger.warning(f"Invalid JSON in cache for {entity_type} '{entity_name}', ignoring")
                    metadata = None
                
                return {
                    'musicbrainz_id': row[0],
                    'metadata': metadata,
                    'confidence': row[2]
                }
            
            return None
            
        except Exception as e:
            logger.error(f"Error checking cache: {e}")
            return None
        finally:
            if conn:
                conn.close()
    
    def _save_to_cache(self, entity_type: str, entity_name: str, artist_name: Optional[str],
                       musicbrainz_id: Optional[str], metadata: Optional[Dict], confidence: int):
        """Save MusicBrainz result to cache"""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            metadata_json = json.dumps(metadata) if metadata else None

            cursor.execute("""
                INSERT OR REPLACE INTO musicbrainz_cache
                (entity_type, entity_name, artist_name, musicbrainz_id, metadata_json, match_confidence, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (entity_type, entity_name, artist_name, musicbrainz_id, metadata_json, confidence, datetime.now()))

            conn.commit()

            logger.debug(f"Cached {entity_type} '{entity_name}' (MBID: {musicbrainz_id}, confidence: {confidence})")

        except Exception as e:
            logger.error(f"Error saving to cache: {e}")
            if conn:
                conn.rollback()
        finally:
            if conn:
                conn.close()
    
    def match_artist(self, artist_name: str) -> Optional[Dict[str, Any]]:
        """
        Match an artist by name to MusicBrainz
        
        Returns:
            Dict with 'mbid', 'name', 'confidence' or None if no good match
        """
        # Check cache first
        cached = self._check_cache('artist', artist_name)
        if cached:
            logger.debug(f"Cache hit for artist '{artist_name}'")
            return {
                'mbid': cached['musicbrainz_id'],
                'name': artist_name,
                'confidence': cached['confidence'],
                'cached': True
            }
        
        # Search MusicBrainz
        try:
            results = self.mb_client.search_artist(artist_name, limit=5)
            
            if not results:
                logger.info(f"No MusicBrainz results for artist '{artist_name}'")
                self._save_to_cache('artist', artist_name, None, None, None, 0)
                return None
            
            # Find best match
            best_match = None
            best_confidence = 0
            
            for result in results:
                mb_name = result.get('name', '')
                mb_score = result.get('score', 0)  # MusicBrainz search score
                
                # Calculate our own similarity
                similarity = self._calculate_similarity(artist_name, mb_name)
                
                # Combine MusicBrainz score with our similarity (weighted)
                # Cap at 100 to prevent edge cases where MB score > 100
                confidence = min(100, int((similarity * 60) + (mb_score / 100 * 40)))
                
                if confidence > best_confidence:
                    best_confidence = confidence
                    best_match = result
            
            # Only return matches with confidence >= 70%
            if best_match and best_confidence >= 70:
                mbid = best_match.get('id')
                mb_name = best_match.get('name')
                
                # Save to cache
                self._save_to_cache('artist', artist_name, None, mbid, best_match, best_confidence)
                
                logger.info(f"Matched artist '{artist_name}' → '{mb_name}' (MBID: {mbid}, confidence: {best_confidence})")
                
                return {
                    'mbid': mbid,
                    'name': mb_name,
                    'confidence': best_confidence,
                    'cached': False
                }
            else:
                logger.info(f"Low confidence match for artist '{artist_name}' (best: {best_confidence})")
                self._save_to_cache('artist', artist_name, None, None, None, best_confidence)
                return None
                
        except Exception as e:
            logger.error(f"Error matching artist '{artist_name}': {e}")
            return None
    
    def match_release(self, album_name: str, artist_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Match a release (album) by name to MusicBrainz
        
        Returns:
            Dict with 'mbid', 'title', 'confidence' or None if no good match
        """
        # Check cache first
        cached = self._check_cache('release', album_name, artist_name)
        if cached:
            logger.debug(f"Cache hit for release '{album_name}'")
            return {
                'mbid': cached['musicbrainz_id'],
                'title': album_name,
                'confidence': cached['confidence'],
                'cached': True
            }
        
        # Search MusicBrainz
        try:
            results = self.mb_client.search_release(album_name, artist_name, limit=5)
            
            if not results:
                logger.info(f"No MusicBrainz results for release '{album_name}'")
                self._save_to_cache('release', album_name, artist_name, None, None, 0)
                return None
            
            # Find best match
            best_match = None
            best_confidence = 0
            
            for result in results:
                mb_title = result.get('title', '')
                mb_score = result.get('score', 0)
                
                # Calculate title similarity
                title_similarity = self._calculate_similarity(album_name, mb_title)
                
                # If we have artist info, check artist match too
                artist_bonus = 0
                if artist_name and 'artist-credit' in result:
                    artist_credits = result['artist-credit']
                    for credit in artist_credits:
                        if isinstance(credit, dict) and 'artist' in credit:
                            mb_artist = credit['artist'].get('name', '')
                            artist_similarity = self._calculate_similarity(artist_name, mb_artist)
                            if artist_similarity > 0.7:
                                artist_bonus = 20
                                break
                
                # Combine scores - cap at 100
                confidence = min(100, int((title_similarity * 50) + (mb_score / 100 * 30) + artist_bonus))
                
                if confidence > best_confidence:
                    best_confidence = confidence
                    best_match = result
            
            # Only return matches with confidence >= 70%
            if best_match and best_confidence >= 70:
                mbid = best_match.get('id')
                mb_title = best_match.get('title')
                
                # Save to cache
                self._save_to_cache('release', album_name, artist_name, mbid, best_match, best_confidence)
                
                logger.info(f"Matched release '{album_name}' → '{mb_title}' (MBID: {mbid}, confidence: {best_confidence})")
                
                return {
                    'mbid': mbid,
                    'title': mb_title,
                    'confidence': best_confidence,
                    'cached': False
                }
            else:
                logger.info(f"Low confidence match for release '{album_name}' (best: {best_confidence})")
                self._save_to_cache('release', album_name, artist_name, None, None, best_confidence)
                return None
                
        except Exception as e:
            logger.error(f"Error matching release '{album_name}': {e}")
            return None
    
    def match_recording(self, track_name: str, artist_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Match a recording (track) by name to MusicBrainz
        
        Returns:
            Dict with 'mbid', 'title', 'confidence' or None if no good match
        """
        # Check cache first
        cached = self._check_cache('recording', track_name, artist_name)
        if cached:
            logger.debug(f"Cache hit for recording '{track_name}'")
            return {
                'mbid': cached['musicbrainz_id'],
                'title': track_name,
                'confidence': cached['confidence'],
                'cached': True
            }
        
        # Search MusicBrainz
        try:
            results = self.mb_client.search_recording(track_name, artist_name, limit=5)
            
            if not results:
                logger.info(f"No MusicBrainz results for recording '{track_name}'")
                self._save_to_cache('recording', track_name, artist_name, None, None, 0)
                return None
            
            # Find best match
            best_match = None
            best_confidence = 0
            
            for result in results:
                mb_title = result.get('title', '')
                mb_score = result.get('score', 0)
                
                # Calculate title similarity
                title_similarity = self._calculate_similarity(track_name, mb_title)
                
                # If we have artist info, check artist match too
                artist_bonus = 0
                if artist_name and 'artist-credit' in result:
                    artist_credits = result['artist-credit']
                    for credit in artist_credits:
                        if isinstance(credit, dict) and 'artist' in credit:
                            mb_artist = credit['artist'].get('name', '')
                            artist_similarity = self._calculate_similarity(artist_name, mb_artist)
                            if artist_similarity > 0.7:
                                artist_bonus = 20
                                break
                
                # Combine scores - cap at 100
                confidence = min(100, int((title_similarity * 50) + (mb_score / 100 * 30) + artist_bonus))
                
                if confidence > best_confidence:
                    best_confidence = confidence
                    best_match = result
            
            # Only return matches with confidence >= 70%
            if best_match and best_confidence >= 70:
                mbid = best_match.get('id')
                mb_title = best_match.get('title')
                
                # Save to cache
                self._save_to_cache('recording', track_name, artist_name, mbid, best_match, best_confidence)
                
                logger.info(f"Matched recording '{track_name}' → '{mb_title}' (MBID: {mbid}, confidence: {best_confidence})")
                
                return {
                    'mbid': mbid,
                    'title': mb_title,
                    'confidence': best_confidence,
                    'cached': False
                }
            else:
                logger.info(f"Low confidence match for recording '{track_name}' (best: {best_confidence})")
                self._save_to_cache('recording', track_name, artist_name, None, None, best_confidence)
                return None
                
        except Exception as e:
            logger.error(f"Error matching recording '{track_name}': {e}")
            return None
    
    def update_artist_mbid(self, artist_id: int, mbid: Optional[str], status: str):
        """Update artist with MusicBrainz ID"""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE artists
                SET musicbrainz_id = ?,
                    musicbrainz_last_attempted = ?,
                    musicbrainz_match_status = ?
                WHERE id = ?
            """, (mbid, datetime.now(), status, artist_id))
            
            conn.commit()
            
            logger.debug(f"Updated artist {artist_id} with MBID: {mbid}, status: {status}")
            
        except Exception as e:
            logger.error(f"Error updating artist {artist_id}: {e}")
            if conn:
                conn.rollback()
        finally:
            if conn:
                conn.close()
    
    def update_album_mbid(self, album_id: int, mbid: Optional[str], status: str):
        """Update album with MusicBrainz release ID"""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE albums
                SET musicbrainz_release_id = ?,
                    musicbrainz_last_attempted = ?,
                    musicbrainz_match_status = ?
                WHERE id = ?
            """, (mbid, datetime.now(), status, album_id))
            
            conn.commit()
            
            logger.debug(f"Updated album {album_id} with MBID: {mbid}, status: {status}")
            
        except Exception as e:
            logger.error(f"Error updating album {album_id}: {e}")
            if conn:
                conn.rollback()
        finally:
            if conn:
                conn.close()
    
    def update_track_mbid(self, track_id: int, mbid: Optional[str], status: str):
        """Update track with MusicBrainz recording ID"""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                UPDATE tracks
                SET musicbrainz_recording_id = ?,
                    musicbrainz_last_attempted = ?,
                    musicbrainz_match_status = ?
                WHERE id = ?
            """, (mbid, datetime.now(), status, track_id))

            conn.commit()

            logger.debug(f"Updated track {track_id} with MBID: {mbid}, status: {status}")

        except Exception as e:
            logger.error(f"Error updating track {track_id}: {e}")
            if conn:
                conn.rollback()
        finally:
            if conn:
                conn.close()

