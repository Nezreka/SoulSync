"""Library retag worker.

`execute_retag(group_id, album_id, deps)` rewrites tags + filenames for a
group of audio files when the user has matched them to a different
album. The worker:

1. Fetches album + track metadata for the new `album_id` (Spotify or
   iTunes — Spotify client transparently falls back).
2. Loads existing files in the retag group from the DB.
3. Matches each existing track to a new Spotify track:
   - Priority 1: same disc + track number.
   - Priority 2: title similarity >= 0.6 (SequenceMatcher).
4. For each matched pair:
   - Re-write metadata tags via `_enhance_file_metadata`.
   - Compute the new path via `_build_final_path_for_track` and move
     the audio file (plus .lrc / .txt sidecars) if the path changes.
   - Drop an orphaned cover.jpg if it's left in an empty directory.
   - Clean up empty parent directories left behind.
   - Download the new cover art into the new album dir.
5. Update the retag group record with the new artist / album / image /
   total_tracks / release_date and the appropriate Spotify-or-iTunes
   album ID.
6. Mark the retag state 'finished' (or 'error' on exception).

The original mutated `retag_state` as a module global. Here it's exposed
through the `RetagDeps` proxy as a Python property so the lifted body
keeps the same `name[key] = value` syntax. The property setter rebinds
the web_server.py reference if needed (currently the function only
mutates in place via .update() and key assignment, so the setter never
fires).
"""

from __future__ import annotations

import logging
import os
import traceback
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class RetagDeps:
    """Bundle of cross-cutting deps the retag worker needs.

    `retag_state` is exposed as a property so the lifted body keeps
    `name[key] = value` / `name.update(...)` syntax.
    """
    config_manager: Any
    retag_lock: Any  # threading.Lock
    spotify_client: Any
    get_audio_quality_string: Callable[[str], str]
    enhance_file_metadata: Callable
    build_final_path_for_track: Callable
    safe_move_file: Callable
    cleanup_empty_directories: Callable
    download_cover_art: Callable
    docker_resolve_path: Callable[[str], str]
    _get_retag_state: Callable[[], dict]
    _set_retag_state: Callable[[dict], None]
    get_database: Callable[[], Any]
    # Discord report (Netti93) — retag was clearing the LYRICS / USLT
    # tag without rewriting it, while the download pipeline calls
    # `generate_lrc_file` after enrichment to refetch + embed lyrics.
    # Injected here so retag mirrors the same post-enrichment step.
    # Optional for backward compat with any test caller that builds
    # RetagDeps without the new field — empty default no-ops the call.
    generate_lrc_file: Optional[Callable] = None

    @property
    def retag_state(self) -> dict:
        return self._get_retag_state()

    @retag_state.setter
    def retag_state(self, value: dict) -> None:
        self._set_retag_state(value)


def execute_retag(group_id, album_id, deps: RetagDeps):
    """Execute a retag operation: re-tag files in a group with metadata from a new album match."""
    try:
        with deps.retag_lock:
            deps.retag_state.update({
                "status": "running",
                "phase": "Fetching album metadata...",
                "progress": 0,
                "current_track": "",
                "total_tracks": 0,
                "processed": 0,
                "error_message": ""
            })

        # 1. Fetch new album metadata from Spotify/iTunes
        album_data = deps.spotify_client.get_album(album_id)
        if not album_data:
            raise ValueError(f"Could not fetch album data for ID: {album_id}")

        album_tracks_response = deps.spotify_client.get_album_tracks(album_id)
        if not album_tracks_response:
            raise ValueError(f"Could not fetch album tracks for ID: {album_id}")

        album_tracks_items = album_tracks_response.get('items', [])

        # Extract artist info
        album_artists = album_data.get('artists', [])
        new_artist = album_artists[0] if album_artists else {'name': 'Unknown Artist', 'id': ''}
        # Ensure artist is a dict with expected fields
        if not isinstance(new_artist, dict):
            new_artist = {'name': str(new_artist), 'id': ''}
        new_album_name = album_data.get('name', 'Unknown Album')
        new_images = album_data.get('images', [])
        new_image_url = new_images[0]['url'] if new_images else None
        new_release_date = album_data.get('release_date', '')
        total_tracks = album_data.get('total_tracks', len(album_tracks_items))

        # Build spotify track list
        spotify_tracks = []
        for item in album_tracks_items:
            track_artists = item.get('artists', [])
            spotify_tracks.append({
                'name': item.get('name', ''),
                'track_number': item.get('track_number', 1),
                'disc_number': item.get('disc_number', 1),
                'id': item.get('id', ''),
                'artists': track_artists,
                'duration_ms': item.get('duration_ms', 0)
            })

        total_discs = max((t['disc_number'] for t in spotify_tracks), default=1)

        # 2. Load existing tracks for this group
        db = deps.get_database()
        existing_tracks = db.get_retag_tracks(group_id)
        if not existing_tracks:
            raise ValueError(f"No tracks found for retag group {group_id}")

        with deps.retag_lock:
            deps.retag_state['total_tracks'] = len(existing_tracks)
            deps.retag_state['phase'] = "Matching tracks..."

        # 3. Match existing files to new tracklist
        matched_pairs = []
        for existing_track in existing_tracks:
            best_match = None
            best_score = 0

            # Priority 1: Match by track number
            for st in spotify_tracks:
                if (st['track_number'] == existing_track.get('track_number') and
                        st['disc_number'] == existing_track.get('disc_number', 1)):
                    best_match = st
                    best_score = 1.0
                    break

            # Priority 2: Match by title similarity
            if not best_match:
                from difflib import SequenceMatcher
                existing_title = (existing_track.get('title') or '').lower().strip()
                for st in spotify_tracks:
                    st_title = (st.get('name') or '').lower().strip()
                    score = SequenceMatcher(None, existing_title, st_title).ratio()
                    if score > best_score and score > 0.6:
                        best_score = score
                        best_match = st

            if best_match:
                matched_pairs.append((existing_track, best_match))
            else:
                logger.warning(f"[Retag] No match found for track: '{existing_track.get('title')}'")
                matched_pairs.append((existing_track, None))

        with deps.retag_lock:
            deps.retag_state['phase'] = "Retagging files..."

        # 4. Retag each matched track
        for existing_track, matched_spotify in matched_pairs:
            current_file_path = existing_track.get('file_path', '')
            track_title = matched_spotify['name'] if matched_spotify else existing_track.get('title', 'Unknown')

            with deps.retag_lock:
                deps.retag_state['current_track'] = track_title

            if not matched_spotify:
                with deps.retag_lock:
                    deps.retag_state['processed'] += 1
                    deps.retag_state['progress'] = int(deps.retag_state['processed'] / deps.retag_state['total_tracks'] * 100)
                continue

            # Verify file exists
            if not os.path.exists(current_file_path):
                logger.warning(f"[Retag] File not found, skipping: {current_file_path}")
                with deps.retag_lock:
                    deps.retag_state['processed'] += 1
                    deps.retag_state['progress'] = int(deps.retag_state['processed'] / deps.retag_state['total_tracks'] * 100)
                continue

            # Build synthetic context for _enhance_file_metadata
            track_artists = matched_spotify.get('artists', [])
            context = {
                'original_search_result': {
                    'spotify_clean_title': matched_spotify['name'],
                    'spotify_clean_album': new_album_name,
                    'track_number': matched_spotify['track_number'],
                    'disc_number': matched_spotify.get('disc_number', 1),
                    'artists': track_artists,
                    'title': matched_spotify['name']
                },
                'spotify_album': {
                    'id': album_id,
                    'name': new_album_name,
                    'release_date': new_release_date,
                    'total_tracks': total_tracks,
                    'image_url': new_image_url,
                    'total_discs': total_discs
                },
                'track_info': {'id': matched_spotify['id']},
                'spotify_artist': new_artist,
                '_audio_quality': deps.get_audio_quality_string(current_file_path) or ''
            }

            album_info = {
                'is_album': total_tracks > 1,
                'album_name': new_album_name,
                'track_number': matched_spotify['track_number'],
                'disc_number': matched_spotify.get('disc_number', 1),
                'clean_track_name': matched_spotify['name'],
                'album_image_url': new_image_url
            }

            # Re-write metadata tags
            try:
                deps.enhance_file_metadata(current_file_path, context, new_artist, album_info)
                logger.info(f"[Retag] Re-tagged: '{track_title}'")
            except Exception as meta_err:
                logger.error(f"[Retag] Metadata write failed for '{track_title}': {meta_err}")

            # Discord report (Netti93) — `enhance_file_metadata` clears
            # ALL tags (incl. USLT lyrics) and rewrites only the source
            # metadata. The download pipeline calls `generate_lrc_file`
            # after enrichment to refetch + embed lyrics — retag was
            # missing that step and dropped the LYRICS tag with no
            # rewrite. Mirroring the download path's post-enrichment
            # step. Same args, same `lrclib_enabled` config gate, same
            # idempotency (skip when sidecar already present).
            if deps.generate_lrc_file:
                try:
                    deps.generate_lrc_file(current_file_path, context, new_artist, album_info)
                except Exception as lrc_err:
                    logger.debug("[Retag] generate_lrc_file failed for '%s': %s", track_title, lrc_err)

            # Compute new path and move if different
            file_ext = os.path.splitext(current_file_path)[1]
            try:
                new_path, _ = deps.build_final_path_for_track(context, new_artist, album_info, file_ext)

                if os.path.normpath(current_file_path) != os.path.normpath(new_path):
                    logger.info(f"[Retag] Moving '{os.path.basename(current_file_path)}' -> '{new_path}'")
                    old_dir = os.path.dirname(current_file_path)
                    os.makedirs(os.path.dirname(new_path), exist_ok=True)
                    deps.safe_move_file(current_file_path, new_path)

                    # Move lyrics sidecar file alongside audio file if it exists
                    for lyrics_ext in ('.lrc', '.txt'):
                        old_lyrics = os.path.splitext(current_file_path)[0] + lyrics_ext
                        if os.path.exists(old_lyrics):
                            new_lyrics = os.path.splitext(new_path)[0] + lyrics_ext
                            try:
                                deps.safe_move_file(old_lyrics, new_lyrics)
                                logger.info(f"[Retag] Moved {lyrics_ext} file alongside audio")
                            except Exception as lrc_err:
                                logger.error(f"[Retag] Failed to move {lyrics_ext} file: {lrc_err}")

                    # Remove old cover.jpg if directory changed and old dir is now empty of audio
                    new_dir = os.path.dirname(new_path)
                    if os.path.normpath(old_dir) != os.path.normpath(new_dir):
                        old_cover = os.path.join(old_dir, 'cover.jpg')
                        if os.path.exists(old_cover):
                            # Check if any audio files remain in old directory
                            audio_exts = {'.flac', '.mp3', '.m4a', '.ogg', '.opus', '.wav', '.aac'}
                            remaining_audio = [f for f in os.listdir(old_dir)
                                               if os.path.splitext(f)[1].lower() in audio_exts]
                            if not remaining_audio:
                                try:
                                    os.remove(old_cover)
                                    logger.warning("[Retag] Removed orphaned cover.jpg from old directory")
                                except Exception as e:
                                    logger.debug("remove orphaned cover failed: %s", e)

                    # Cleanup old empty directories
                    transfer_dir = deps.docker_resolve_path(deps.config_manager.get('soulseek.transfer_path', './Transfer'))
                    deps.cleanup_empty_directories(transfer_dir, current_file_path)

                    # Update DB record
                    db.update_retag_track_path(existing_track['id'], str(new_path))
                    current_file_path = new_path
                else:
                    logger.warning(f"[Retag] Path unchanged for '{track_title}', no move needed")
            except Exception as move_err:
                logger.error(f"[Retag] Path/move failed for '{track_title}': {move_err}")

            # Download cover art to album directory
            try:
                deps.download_cover_art(album_info, os.path.dirname(current_file_path), context)
            except Exception as cover_err:
                logger.error(f"[Retag] Cover art download failed: {cover_err}")

            with deps.retag_lock:
                deps.retag_state['processed'] += 1
                deps.retag_state['progress'] = int(deps.retag_state['processed'] / deps.retag_state['total_tracks'] * 100)

        # 5. Update the retag group record with new metadata
        update_kwargs = {
            'artist_name': new_artist.get('name', 'Unknown Artist'),
            'album_name': new_album_name,
            'image_url': new_image_url,
            'total_tracks': total_tracks,
            'release_date': new_release_date
        }
        # Set the correct ID field based on Spotify vs iTunes
        if str(album_id).isdigit():
            update_kwargs['itunes_album_id'] = album_id
            update_kwargs['spotify_album_id'] = None
        else:
            update_kwargs['spotify_album_id'] = album_id
            update_kwargs['itunes_album_id'] = None

        db.update_retag_group(group_id, **update_kwargs)

        with deps.retag_lock:
            deps.retag_state.update({
                "status": "finished",
                "phase": "Retag complete!",
                "progress": 100,
                "current_track": ""
            })
        logger.info(f"[Retag] Retag operation complete for group {group_id}")

    except Exception as e:
        import traceback
        logger.error(f"[Retag] Error during retag: {e}")
        logger.error(traceback.format_exc())
        with deps.retag_lock:
            deps.retag_state.update({
                "status": "error",
                "phase": "Error",
                "error_message": str(e)
            })
