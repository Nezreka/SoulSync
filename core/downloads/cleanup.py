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

from utils.logging_config import get_logger
import os
import time
import traceback

logger = get_logger("downloads.cleanup")

# Landed audio a cancelled streaming download leaves behind (yt-dlp can't be
# interrupted mid-stream; the file arrives after its record is gone). These
# are the extensions the streaming clients actually write.
_ORPHAN_AUDIO_EXTS = frozenset({'.mp3', '.flac', '.m4a', '.ogg', '.opus', '.wav', '.aac', '.wma'})


def sweep_orphaned_download_audio(download_dir, *, min_age_seconds: int = 3600,
                                  referenced_basenames=frozenset(), now=None) -> list:
    """Delete ROOT-LEVEL audio files in the download dir that are older than
    ``min_age_seconds`` and referenced by no live download record.

    This is the reaper for the downloads-folder bleed: the monitor cancels a
    streaming download, the cancel can't interrupt yt-dlp, and the finished
    file lands with no record to claim it — nothing ever post-processes or
    deletes it. Streaming clients write flat into the download root, so ONLY
    root-level files are considered: Soulseek's per-share folders, the
    quarantine and staging trees all live in subdirectories and are never
    touched. The caller must only invoke this while no batch is active and
    nothing is post-processing (the automation's existing guard).

    Returns the list of removed paths (best-effort; unremovable files are
    logged and skipped).
    """
    removed = []
    now = time.time() if now is None else now
    refs = {str(b).casefold() for b in (referenced_basenames or ())}
    try:
        names = os.listdir(download_dir)
    except OSError:
        return removed
    for name in names:
        path = os.path.join(download_dir, name)
        try:
            if not os.path.isfile(path):
                continue
            if os.path.splitext(name)[1].lower() not in _ORPHAN_AUDIO_EXTS:
                continue
            if name.casefold() in refs:
                continue
            if (now - os.path.getmtime(path)) < min_age_seconds:
                continue
            os.remove(path)
            removed.append(path)
            logger.info("[Orphan Reaper] Removed abandoned download: %s", path)
        except OSError as e:
            logger.warning("[Orphan Reaper] Could not remove %s: %s", path, e)
    return removed


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

            # Check each artist. A match whose library row still points into
            # atomic-publish staging is not ownership (#1289) — the file is
            # quarantined and may never publish, so the request has to stand.
            from core.wishlist.library_match import find_owned_match
            from core.wishlist.removal_guard import REASON_ALREADY_OWNED

            match = find_owned_match(
                db, track_name, artists, track_album, active_server,
                log=logger, log_prefix="[Auto Cleanup]")

            # If found in database, remove from wishlist
            if match:
                db_track, _confidence, _artist = match
                try:
                    removed = wishlist_service.mark_track_download_result(
                        spotify_track_id, success=True,
                        audit={'reason': REASON_ALREADY_OWNED,
                               'final_path': getattr(db_track, 'file_path', '') or ''})
                    if removed:
                        removed_count += 1
                        logger.info(f"[Auto Cleanup] Removed track from wishlist: '{track_name}' ({spotify_track_id})")
                except Exception as remove_error:
                    logger.error(f"[Auto Cleanup] Error removing track from wishlist: {remove_error}")

        logger.info(f"[Auto Cleanup] Completed automatic cleanup: {removed_count} tracks removed from wishlist")

    except Exception as e:
        logger.error(f"[Auto Cleanup] Error in automatic wishlist cleanup: {e}")
        traceback.print_exc()
