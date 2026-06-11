"""Playlist-folder layout helpers for download analysis and existence checks."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple

from core.downloads.file_finder import AUDIO_EXTENSIONS
from core.imports.paths import (
    _get_config_manager,
    docker_resolve_path,
    get_file_path_from_template,
    sanitize_filename,
)


def _first_artist_name(artists: Any) -> str:
    if not artists:
        return ''
    first = artists[0]
    if isinstance(first, dict):
        return str(first.get('name', '') or '').strip()
    return str(first).strip()


def candidate_playlist_folder_paths(
    playlist_name: str,
    artist: str,
    title: str,
) -> List[str]:
    """Return absolute candidate paths for a track in playlist-folder layout."""
    if not playlist_name or not title:
        return []

    artist_name = (artist or 'Unknown Artist').strip()
    track_name = title.strip()
    transfer_dir = docker_resolve_path(
        _get_config_manager().get('soulseek.transfer_path', './Transfer')
    )

    template_context = {
        'artist': artist_name,
        'albumartist': artist_name,
        'album': track_name,
        'title': track_name,
        'playlist_name': playlist_name,
        'track_number': 1,
        'disc_number': 1,
        'year': '',
        'quality': '',
        'albumtype': '',
        '_artists_list': [{'name': artist_name}],
    }

    candidates: List[str] = []
    folder_path, filename_base = get_file_path_from_template(template_context, 'playlist_path')
    if folder_path and filename_base:
        base = os.path.join(transfer_dir, folder_path, filename_base)
        for ext in AUDIO_EXTENSIONS:
            candidates.append(base + ext)
    else:
        playlist_name_sanitized = sanitize_filename(playlist_name)
        playlist_dir = os.path.join(transfer_dir, playlist_name_sanitized)
        artist_name_sanitized = sanitize_filename(artist_name)
        track_name_sanitized = sanitize_filename(track_name)
        stem = f'{artist_name_sanitized} - {track_name_sanitized}'
        for ext in AUDIO_EXTENSIONS:
            candidates.append(os.path.join(playlist_dir, stem + ext))

    return candidates


def track_exists_in_playlist_folder(
    playlist_name: str,
    artist: str,
    title: str,
) -> bool:
    """Return True if any audio file exists at the playlist-folder path for this track.

    Uses a case-insensitive fallback scan of the playlist directory so that
    provider-casing differences (e.g. "HUGEL" vs "hugel") don't cause the same
    file to be downloaded twice under a differently-cased filename.
    """
    candidates = candidate_playlist_folder_paths(playlist_name, artist, title)
    for path in candidates:
        if os.path.isfile(path):
            return True

    # Case-insensitive fallback: list the playlist directory and compare basenames
    # after lowercasing.  On Linux (case-sensitive fs) a file written as
    # "HUGEL - Song.flac" is invisible to an exact match for "hugel - Song.flac".
    checked_dirs: set = set()
    for path in candidates:
        parent = os.path.dirname(path)
        if parent in checked_dirs or not os.path.isdir(parent):
            continue
        checked_dirs.add(parent)
        target_lower = os.path.basename(path).lower()
        try:
            for fname in os.listdir(parent):
                if fname.lower() == target_lower:
                    return True
        except OSError:
            pass

    return False


def is_soulsync_standalone_server(active_server: str) -> bool:
    return (active_server or '').strip().lower() == 'soulsync'


def effective_keep_playlist_folder_copies(
    mirrored: Optional[Dict[str, Any]],
    active_server: str,
    *,
    batch_keep: bool = False,
) -> bool:
    """True when per-playlist folder copies should be kept for this batch.

    In SoulSync standalone mode, mirrored playlists with organize-by-playlist
    default to keeping copies unless the user explicitly opted out.
    """
    if batch_keep:
        return True
    if not mirrored:
        return False
    if mirrored.get('keep_playlist_folder_copies'):
        return True
    if mirrored.get('keep_playlist_folder_copies_opt_out'):
        return False
    return (
        is_soulsync_standalone_server(active_server)
        and bool(mirrored.get('organize_by_playlist'))
    )


def track_exists_in_playlist_folder_from_track_data(
    playlist_name: str,
    track_data: Dict[str, Any],
) -> bool:
    """Check playlist-folder existence using Spotify-style track payload."""
    title = track_data.get('name', '') or track_data.get('track_name', '')
    artist = _first_artist_name(track_data.get('artists', []))
    if not artist:
        artist = str(track_data.get('artist_name', '') or '').strip()
    return track_exists_in_playlist_folder(playlist_name, artist, title)


def resolve_wishlist_track_playlist_folder_mode(
    wl_source: Any,
    db: Any,
    *,
    profile_id: int = 1,
    default_playlist_name: str = 'Unknown Playlist',
) -> Tuple[bool, str]:
    """Resolve playlist-folder layout for a single wishlist track.

    Honors ``organize_by_playlist`` stored when the row was added from a download
    modal, then falls back to the mirrored-playlist row (using ``playlist_source``
    or ``source`` for upstream lookup).
    """
    if isinstance(wl_source, str):
        try:
            wl_source = json.loads(wl_source)
        except (json.JSONDecodeError, TypeError):
            wl_source = {}
    if not isinstance(wl_source, dict):
        return False, default_playlist_name

    playlist_name = wl_source.get('playlist_name') or default_playlist_name

    if wl_source.get('organize_by_playlist'):
        return True, playlist_name

    wl_pl_ref = wl_source.get('playlist_id')
    wl_pl_source = (
        wl_source.get('source')
        or wl_source.get('playlist_source')
        or 'spotify'
    )
    if not wl_pl_ref or not hasattr(db, 'resolve_mirrored_playlist'):
        return False, playlist_name

    refs_to_try: List[Tuple[str, str]] = [(str(wl_pl_ref).strip(), wl_pl_source)]
    ui_ref = wl_source.get('ui_playlist_ref')
    if ui_ref and str(ui_ref).strip() != str(wl_pl_ref).strip():
        refs_to_try.append((str(ui_ref).strip(), wl_pl_source))

    for ref, src in refs_to_try:
        if not ref:
            continue
        mirrored = db.resolve_mirrored_playlist(
            ref, profile_id=profile_id, default_source=src or 'spotify'
        )
        if mirrored and mirrored.get('organize_by_playlist'):
            return True, playlist_name or mirrored.get('name') or default_playlist_name

    return False, playlist_name


def resolve_playlist_folder_mode_for_batch(
    db: Any,
    *,
    playlist_id: str,
    playlist_name: str,
    batch_playlist_folder_mode: bool,
    batch_keep_playlist_folder_copies: bool = False,
    profile_id: int = 1,
    source: str = 'spotify',
    active_server: str = '',
) -> tuple[bool, str, bool]:
    """Merge batch flags with persisted mirrored-playlist preferences.

    Returns ``(folder_mode, effective_playlist_name, keep_folder_copies)``.
    """
    mirrored = None
    if hasattr(db, 'resolve_mirrored_playlist'):
        mirrored = db.resolve_mirrored_playlist(
            playlist_id, profile_id=profile_id, default_source=source or 'spotify'
        )

    keep = effective_keep_playlist_folder_copies(
        mirrored,
        active_server,
        batch_keep=batch_keep_playlist_folder_copies,
    )

    if batch_playlist_folder_mode:
        name = (mirrored.get('name') if mirrored else None) or playlist_name
        return True, name, keep

    if mirrored and mirrored.get('organize_by_playlist'):
        return True, mirrored.get('name') or playlist_name, keep

    return False, playlist_name, False


__all__ = [
    'candidate_playlist_folder_paths',
    'effective_keep_playlist_folder_copies',
    'is_soulsync_standalone_server',
    'track_exists_in_playlist_folder',
    'track_exists_in_playlist_folder_from_track_data',
    'resolve_wishlist_track_playlist_folder_mode',
    'resolve_playlist_folder_mode_for_batch',
]
