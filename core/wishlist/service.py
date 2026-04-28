#!/usr/bin/env python3

"""
Wishlist Service - High-level service for managing failed download track wishlist
"""

from typing import Any, Dict, List, Optional

from core.wishlist.payloads import extract_spotify_track_from_modal_info
from database.music_database import get_database
from utils.logging_config import get_logger


logger = get_logger("wishlist_service")


class WishlistService:
    """Service for managing the wishlist of failed download tracks"""

    def __init__(self, database_path: str = "database/music_library.db"):
        self.database_path = database_path
        self._database = None

    @property
    def database(self):
        """Get database instance (lazy loading)"""
        if self._database is None:
            self._database = get_database(self.database_path)
        return self._database

    def add_failed_track_from_modal(
        self,
        track_info: Dict[str, Any],
        source_type: str = "unknown",
        source_context: Dict[str, Any] = None,
        profile_id: int = 1,
    ) -> bool:
        """
        Add a failed track from a download modal to the wishlist.

        Args:
            track_info: Track info dictionary from modal's permanently_failed_tracks
            source_type: Type of source ('playlist', 'album', 'manual')
            source_context: Additional context (playlist name, album info, etc.)
        """
        try:
            # Extract Spotify track data from the track_info structure
            spotify_track = extract_spotify_track_from_modal_info(track_info)
            if not spotify_track:
                logger.error("Could not extract Spotify track data from modal info")
                return False

            # Get failure reason from track_info if available
            failure_reason = track_info.get("failure_reason", "Download failed")

            # Create source info
            source_info = source_context or {}

            # Clean up candidates to avoid TrackResult serialization issues
            candidates = track_info.get("candidates", [])
            cleaned_candidates = []
            for candidate in candidates:
                if hasattr(candidate, "__dict__"):
                    # Convert TrackResult objects to simple dictionaries
                    cleaned_candidates.append(
                        {
                            "title": getattr(candidate, "title", "Unknown"),
                            "artist": getattr(candidate, "artist", "Unknown"),
                            "filename": getattr(candidate, "filename", "Unknown"),
                        }
                    )
                else:
                    # Keep simple data as-is
                    cleaned_candidates.append(candidate)

            source_info["original_modal_data"] = {
                "download_index": track_info.get("download_index"),
                "table_index": track_info.get("table_index"),
                "candidates": cleaned_candidates,
            }

            # Add to wishlist via database
            return self.database.add_to_wishlist(
                spotify_track_data=spotify_track,
                failure_reason=failure_reason,
                source_type=source_type,
                source_info=source_info,
                profile_id=profile_id,
            )

        except Exception as e:
            logger.error(f"Error adding failed track to wishlist: {e}")
            return False

    def add_spotify_track_to_wishlist(
        self,
        spotify_track_data: Dict[str, Any],
        failure_reason: str,
        source_type: str = "manual",
        source_context: Dict[str, Any] = None,
        profile_id: int = 1,
    ) -> bool:
        """
        Directly add a Spotify track to the wishlist.

        Args:
            spotify_track_data: Full Spotify track data dictionary
            failure_reason: Reason for the failure
            source_type: Source type ('playlist', 'album', 'manual')
            source_context: Additional context information
            profile_id: Profile to add to
        """
        return self.database.add_to_wishlist(
            spotify_track_data=spotify_track_data,
            failure_reason=failure_reason,
            source_type=source_type,
            source_info=source_context or {},
            profile_id=profile_id,
        )

    def get_wishlist_tracks_for_download(
        self,
        limit: Optional[int] = None,
        profile_id: int = 1,
    ) -> List[Dict[str, Any]]:
        """
        Get wishlist tracks formatted for the download modal.
        Returns tracks in a format similar to playlist tracks for compatibility.
        """
        try:
            wishlist_tracks = self.database.get_wishlist_tracks(limit=limit, profile_id=profile_id)
            formatted_tracks = []

            for wishlist_track in wishlist_tracks:
                spotify_data = wishlist_track["spotify_data"]

                # Create a track object similar to what download modals expect
                formatted_track = {
                    "wishlist_id": wishlist_track["id"],
                    "spotify_track_id": wishlist_track["spotify_track_id"],
                    "spotify_data": spotify_data,
                    "failure_reason": wishlist_track["failure_reason"],
                    "retry_count": wishlist_track["retry_count"],
                    "date_added": wishlist_track["date_added"],
                    "last_attempted": wishlist_track["last_attempted"],
                    "source_type": wishlist_track["source_type"],
                    "source_info": wishlist_track["source_info"],
                    # Format for modal compatibility (similar to Spotify Track objects)
                    "id": spotify_data.get("id"),
                    "name": spotify_data.get("name", "Unknown Track"),
                    "artists": spotify_data.get("artists", []),
                    "album": spotify_data.get("album") or {},
                    "duration_ms": spotify_data.get("duration_ms", 0),
                    "preview_url": spotify_data.get("preview_url"),
                    "external_urls": spotify_data.get("external_urls", {}),
                    "popularity": spotify_data.get("popularity", 0),
                    "track_number": spotify_data.get("track_number", 1),
                    "disc_number": spotify_data.get("disc_number", 1),
                }

                formatted_tracks.append(formatted_track)

            return formatted_tracks

        except Exception as e:
            logger.error(f"Error getting wishlist tracks for download: {e}")
            return []

    def mark_track_download_result(
        self,
        spotify_track_id: str,
        success: bool,
        error_message: str = None,
        profile_id: int = 1,
    ) -> bool:
        """
        Mark the result of a download attempt for a wishlist track.

        Args:
            spotify_track_id: Spotify track ID
            success: Whether the download was successful
            error_message: Error message if failed
            profile_id: Profile to scope the operation to
        """
        return self.database.update_wishlist_retry(spotify_track_id, success, error_message, profile_id=profile_id)

    def remove_track_from_wishlist(self, spotify_track_id: str, profile_id: int = 1) -> bool:
        """Remove a track from the wishlist (typically after successful download)"""
        return self.database.remove_from_wishlist(spotify_track_id, profile_id=profile_id)

    def get_wishlist_count(self, profile_id: int = 1) -> int:
        """Get the total number of tracks in the wishlist"""
        return self.database.get_wishlist_count(profile_id=profile_id)

    def clear_wishlist(self, profile_id: int = 1) -> bool:
        """Clear all tracks from the wishlist"""
        return self.database.clear_wishlist(profile_id=profile_id)

    def check_track_in_wishlist(self, spotify_track_id: str) -> bool:
        """Check if a track exists in the wishlist by Spotify track ID"""
        try:
            wishlist_tracks = self.get_wishlist_tracks_for_download()
            for track in wishlist_tracks:
                if track.get("spotify_track_id") == spotify_track_id or track.get("id") == spotify_track_id:
                    return True
            return False
        except Exception as e:
            logger.error(f"Error checking track in wishlist: {e}")
            return False

    def find_matching_wishlist_track(self, track_name: str, artist_name: str) -> Optional[Dict[str, Any]]:
        """
        Find a matching track in the wishlist using fuzzy matching on name and artist.
        Returns the first matching wishlist track or None if no match found.
        """
        try:
            wishlist_tracks = self.get_wishlist_tracks_for_download()

            # Normalize input for comparison
            normalized_track_name = track_name.lower().strip()
            normalized_artist_name = artist_name.lower().strip()

            for wl_track in wishlist_tracks:
                wl_name = wl_track.get("name", "").lower().strip()
                wl_artists = wl_track.get("artists", [])

                # Extract artist name from wishlist track
                wl_artist_name = ""
                if wl_artists:
                    if isinstance(wl_artists[0], dict):
                        wl_artist_name = wl_artists[0].get("name", "").lower().strip()
                    else:
                        wl_artist_name = str(wl_artists[0]).lower().strip()

                # Simple exact matching (could be enhanced with fuzzy matching algorithms)
                if wl_name == normalized_track_name and wl_artist_name == normalized_artist_name:
                    return wl_track

            return None

        except Exception as e:
            logger.error(f"Error finding matching wishlist track: {e}")
            return None

    def get_wishlist_summary(self, profile_id: int = 1) -> Dict[str, Any]:
        """Get a summary of the wishlist for dashboard display"""
        try:
            total_tracks = self.get_wishlist_count(profile_id=profile_id)

            if total_tracks == 0:
                return {
                    "total_tracks": 0,
                    "by_source_type": {},
                    "recent_failures": [],
                }

            # Get detailed breakdown
            wishlist_tracks = self.database.get_wishlist_tracks(profile_id=profile_id)

            # Group by source type
            by_source_type = {}
            recent_failures = []

            for track in wishlist_tracks:
                source_type = track["source_type"]
                by_source_type[source_type] = by_source_type.get(source_type, 0) + 1

                # Keep track of recent failures (last 5)
                if len(recent_failures) < 5:
                    spotify_data = track["spotify_data"]
                    recent_failures.append(
                        {
                            "name": spotify_data.get("name", "Unknown Track"),
                            "artist": (
                                spotify_data.get("artists", [{}])[0].get("name", "Unknown Artist")
                                if isinstance(spotify_data.get("artists", [{}])[0], dict)
                                else spotify_data.get("artists", ["Unknown Artist"])[0]
                            )
                            if spotify_data.get("artists")
                            else "Unknown Artist",
                            "failure_reason": track["failure_reason"],
                            "retry_count": track["retry_count"],
                            "date_added": track["date_added"],
                        }
                    )

            return {
                "total_tracks": total_tracks,
                "by_source_type": by_source_type,
                "recent_failures": recent_failures,
            }

        except Exception as e:
            logger.error(f"Error getting wishlist summary: {e}")
            return {"total_tracks": 0, "by_source_type": {}, "recent_failures": []}


_wishlist_service = None


def get_wishlist_service() -> WishlistService:
    """Get the global wishlist service instance"""
    global _wishlist_service
    if _wishlist_service is None:
        _wishlist_service = WishlistService()
    return _wishlist_service


__all__ = ["WishlistService", "get_wishlist_service"]
