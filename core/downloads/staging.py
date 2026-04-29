"""Staging-folder match shortcut for downloads.

`try_staging_match(task_id, batch_id, track, deps)` is the per-track
shortcut the task worker calls before kicking off a Soulseek search.
If the user has dropped audio files matching the track into the
configured staging folder, we copy directly to the transfer dir and
hand off to post-processing — skipping the network round-trip entirely.

1. Pull the staging-file cache for the batch (one scan per batch).
2. Compute title + artist similarity (SequenceMatcher) against each
   staging entry; require title >= 0.80 and combined score >= 0.75.
   Score weighting flips based on whether artist info is available on
   both sides:
   - both have artist: 0.55*title + 0.45*artist
   - either side missing artist: 0.80*title + 0.20*artist (lean on title)
3. Copy the matched file to the transfer dir (suffix "_staging" if a
   file with that name already exists).
4. Mark the task as 'post_processing' with username='staging'.
5. Build a synthetic spotify_artist / spotify_album context (mirrors
   the modal-worker's logic so the path template applies cleanly) and
   store it in matched_downloads_context under "staging_<task_id>".
6. Hand off to `_post_process_matched_download_with_verification` which
   does tagging, path building, AcoustID verification, and DB insertion.

Returns True if the staging shortcut won; False to fall through to the
normal Soulseek search path.

Lifted verbatim from web_server.py. Wide dependency surface
(matching_engine, post-processing helper, file-system helpers, staging
cache, runtime state) all injected via `StagingDeps`.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Callable

# `shutil` and `SequenceMatcher` are imported inline inside try_staging_match()
# to keep the lift byte-identical with the original web_server.py function body.

from core.runtime_state import (
    download_tasks,
    matched_context_lock,
    matched_downloads_context,
    tasks_lock,
)

logger = logging.getLogger(__name__)


@dataclass
class StagingDeps:
    """Bundle of cross-cutting deps the staging-match helper needs."""
    config_manager: Any
    matching_engine: Any
    get_staging_file_cache: Callable[[str], list]
    docker_resolve_path: Callable[[str], str]
    post_process_matched_download_with_verification: Callable


def try_staging_match(task_id, batch_id, track, deps: StagingDeps):
    """Check if a matching file exists in the staging folder before downloading.

    Returns True if a match was found and the file was moved to the transfer folder.
    Returns False to fall through to normal download.
    """
    staging_files = deps.get_staging_file_cache(batch_id or task_id)
    if not staging_files:
        return False

    track_title = track.name or ''
    track_artist = track.artists[0] if track.artists else ''

    if not track_title:
        return False

    from difflib import SequenceMatcher
    normalize = deps.matching_engine.normalize_string
    norm_title = normalize(track_title)
    norm_artist = normalize(track_artist)

    best_match = None
    best_score = 0.0

    for sf in staging_files:
        sf_norm_title = normalize(sf['title'])
        sf_norm_artist = normalize(sf['artist'])

        if not sf_norm_title:
            continue

        # Title similarity (primary)
        title_sim = SequenceMatcher(None, norm_title, sf_norm_title).ratio()
        if title_sim < 0.80:
            continue

        # Artist similarity (secondary)
        artist_sim = 0.0
        if norm_artist and sf_norm_artist:
            artist_sim = SequenceMatcher(None, norm_artist, sf_norm_artist).ratio()
        elif not norm_artist and not sf_norm_artist:
            artist_sim = 0.5  # Both unknown — neutral
        elif norm_artist and not sf_norm_artist:
            artist_sim = 0.3  # Staging file lacks artist — partial credit if title is strong
        elif sf_norm_artist and not norm_artist:
            artist_sim = 0.3  # Track lacks artist — same partial credit

        # Combined score: title-weighted (these are user-curated staging files)
        # If artist info is available, require it to match. If not, lean on title.
        if norm_artist and sf_norm_artist:
            combined = (title_sim * 0.55) + (artist_sim * 0.45)
        else:
            combined = (title_sim * 0.80) + (artist_sim * 0.20)

        if combined > best_score:
            best_score = combined
            best_match = sf

    # Require high confidence to avoid false positives
    if not best_match or best_score < 0.75:
        return False

    logger.info(f"[Staging] Match found for '{track_title}' by '{track_artist}': "
          f"{os.path.basename(best_match['full_path'])} (score: {best_score:.2f})")

    # Copy the file to the transfer folder
    try:
        transfer_dir = deps.docker_resolve_path(deps.config_manager.get('soulseek.transfer_path', './Transfer'))
        dest_filename = os.path.basename(best_match['full_path'])
        dest_path = os.path.join(transfer_dir, dest_filename)
        os.makedirs(transfer_dir, exist_ok=True)

        # Don't overwrite existing files
        if os.path.exists(dest_path):
            base, ext = os.path.splitext(dest_filename)
            dest_path = os.path.join(transfer_dir, f"{base}_staging{ext}")

        import shutil
        shutil.copy2(best_match['full_path'], dest_path)
        logger.info(f"[Staging] Copied to transfer: {dest_path}")

        # Mark task as completed with staging context
        with tasks_lock:
            if task_id in download_tasks:
                download_tasks[task_id]['status'] = 'post_processing'
                download_tasks[task_id]['filename'] = dest_path
                download_tasks[task_id]['username'] = 'staging'
                download_tasks[task_id]['staging_match'] = True

        # Run post-processing (tagging, AcoustID verification, path building)
        context_key = f"staging_{task_id}"
        with tasks_lock:
            track_info = download_tasks.get(task_id, {}).get('track_info', {})
        if not isinstance(track_info, dict):
            track_info = {}

        # Build spotify_artist / spotify_album context so post-processing can apply
        # the path template. Without these, _post_process_matched_download returns
        # early and the file stays at the transfer root with its original filename.
        # Mirror the context-building logic from the sync modal worker.
        has_explicit_context = track_info.get('_is_explicit_album_download', False)

        if has_explicit_context:
            explicit_artist = track_info.get('_explicit_artist_context', {})
            if isinstance(explicit_artist, str):
                explicit_artist = {'name': explicit_artist}
            elif not isinstance(explicit_artist, dict):
                explicit_artist = {}
            spotify_artist_ctx = {
                'id': explicit_artist.get('id', 'staging'),
                'name': explicit_artist.get('name', track_artist),
                'genres': explicit_artist.get('genres', [])
            }
            explicit_album = track_info.get('_explicit_album_context', {})
            if not isinstance(explicit_album, dict):
                explicit_album = {}
            _album_image_url = explicit_album.get('image_url')
            if not _album_image_url and explicit_album.get('images'):
                _imgs = explicit_album['images']
                if isinstance(_imgs, list) and _imgs:
                    _album_image_url = _imgs[0].get('url') if isinstance(_imgs[0], dict) else None
            spotify_album_ctx = {
                'id': explicit_album.get('id', 'staging'),
                'name': explicit_album.get('name', getattr(track, 'album', '') or ''),
                'release_date': explicit_album.get('release_date', ''),
                'image_url': _album_image_url,
                'album_type': explicit_album.get('album_type', 'album'),
                'total_tracks': explicit_album.get('total_tracks', 0),
                'total_discs': explicit_album.get('total_discs', 1),
                'artists': explicit_album.get('artists', [{'name': spotify_artist_ctx.get('name', '')}])
            }
            is_album_ctx = True
            has_clean_data = True
        else:
            fallback_album = track_info.get('album', {})
            if isinstance(fallback_album, str):
                fallback_album = {'name': fallback_album}
            elif not isinstance(fallback_album, dict):
                fallback_album = {}
            track_album_name = getattr(track, 'album', '') or fallback_album.get('name', '') or ''
            spotify_artist_ctx = {
                'id': 'staging',
                'name': track_artist or 'Unknown',
                'genres': []
            }
            spotify_album_ctx = {
                'id': 'staging',
                'name': track_album_name,
                'release_date': fallback_album.get('release_date', ''),
                'image_url': fallback_album.get('image_url'),
                'album_type': fallback_album.get('album_type', 'album'),
                'total_tracks': fallback_album.get('total_tracks', 0),
                'total_discs': fallback_album.get('total_discs', 1),
                'artists': [{'name': track_artist}] if track_artist else []
            }
            is_album_ctx = bool(
                track_album_name and
                track_album_name.strip() and
                track_album_name.lower() not in ('unknown album', '') and
                track_album_name.lower() != track_title.lower()
            )
            has_clean_data = bool(track_title and track_artist and track_album_name)

        track_number = (
            track_info.get('track_number', 0) or
            getattr(track, 'track_number', 0) or 0
        )
        disc_number = (
            track_info.get('disc_number', 1) or
            getattr(track, 'disc_number', 1) or 1
        )

        context = {
            'track_info': track_info,
            'spotify_artist': spotify_artist_ctx,
            'spotify_album': spotify_album_ctx,
            'original_search_result': {
                'title': track_title,
                'artist': track_artist,
                'spotify_clean_title': track_title,
                'spotify_clean_album': spotify_album_ctx.get('name', ''),
                'spotify_clean_artist': track_artist,
                'track_number': track_number,
                'disc_number': disc_number,
            },
            'is_album_download': is_album_ctx,
            'has_clean_spotify_data': has_clean_data,
            'staging_source': True,
        }

        # Store context in the matched downloads context store (used by post-processing)
        with matched_context_lock:
            matched_downloads_context[context_key] = context

        # Trigger post-processing which handles tagging, path building, and DB insertion
        deps.post_process_matched_download_with_verification(context_key, context, dest_path, task_id, batch_id)
        return True

    except Exception as e:
        logger.error(f"[Staging] Failed to use staging file: {e}")
        return False
