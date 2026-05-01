"""Automatic wishlist cleanup after database updates.

Runs as a background task after the library DB refresh completes — walks
every profile's wishlist, fuzzy-matches each track against the freshly
scanned library, and removes hits. Best-effort: logs and continues on
per-track failure, swallows top-level exceptions so the executor doesn't
get a propagated failure.

Lifted verbatim from web_server.py's `_automatic_wishlist_cleanup_after_db_update`.
The single global dep (`config_manager`) is passed in to keep this module
free of web_server imports.
"""

from __future__ import annotations

import logging
import traceback

logger = logging.getLogger(__name__)


def cleanup_wishlist_after_db_update(config_manager) -> None:
    """Walk all profiles' wishlists and remove tracks now present in the library."""
    try:
        from core.wishlist_service import get_wishlist_service
        from database.music_database import MusicDatabase, get_database

        wishlist_service = get_wishlist_service()
        db = MusicDatabase()
        active_server = config_manager.get_active_media_server()

        logger.info("[Auto Cleanup] Starting automatic wishlist cleanup after database update...")

        # Get all wishlist tracks (across all profiles - cleanup is global)
        database = get_database()
        all_profiles = database.get_all_profiles()
        wishlist_tracks = []
        for p in all_profiles:
            wishlist_tracks.extend(wishlist_service.get_wishlist_tracks_for_download(profile_id=p['id']))
        if not wishlist_tracks:
            logger.warning("[Auto Cleanup] No tracks in wishlist to clean up")
            return

        logger.info(f"[Auto Cleanup] Found {len(wishlist_tracks)} tracks in wishlist")

        removed_count = 0

        for track in wishlist_tracks:
            track_name = track.get('name', '')
            artists = track.get('artists', [])
            spotify_track_id = track.get('spotify_track_id') or track.get('id')
            track_album = track.get('album', {}).get('name') if isinstance(track.get('album'), dict) else track.get('album')

            # Skip if no essential data
            if not track_name or not artists or not spotify_track_id:
                continue

            # Check each artist
            found_in_db = False
            for artist in artists:
                # Handle both string format and dict format
                if isinstance(artist, str):
                    artist_name = artist
                elif isinstance(artist, dict) and 'name' in artist:
                    artist_name = artist['name']
                else:
                    artist_name = str(artist)

                try:
                    db_track, confidence = db.check_track_exists(
                        track_name, artist_name,
                        confidence_threshold=0.7,
                        server_source=active_server,
                        album=track_album,
                    )

                    if db_track and confidence >= 0.7:
                        found_in_db = True
                        logger.info(f"[Auto Cleanup] Track found in database: '{track_name}' by {artist_name} (confidence: {confidence:.2f})")
                        break

                except Exception as db_error:
                    logger.error(f"[Auto Cleanup] Error checking database for track '{track_name}': {db_error}")
                    continue

            # If found in database, remove from wishlist
            if found_in_db:
                try:
                    removed = wishlist_service.mark_track_download_result(spotify_track_id, success=True)
                    if removed:
                        removed_count += 1
                        logger.info(f"[Auto Cleanup] Removed track from wishlist: '{track_name}' ({spotify_track_id})")
                except Exception as remove_error:
                    logger.error(f"[Auto Cleanup] Error removing track from wishlist: {remove_error}")

        logger.info(f"[Auto Cleanup] Completed automatic cleanup: {removed_count} tracks removed from wishlist")

    except Exception as e:
        logger.error(f"[Auto Cleanup] Error in automatic wishlist cleanup: {e}")
        traceback.print_exc()
