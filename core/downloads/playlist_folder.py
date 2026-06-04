"""Playlist-folder layout helpers for download analysis and existence checks."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

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
    """Return True if any audio file exists at the playlist-folder path for this track."""
    for path in candidate_playlist_folder_paths(playlist_name, artist, title):
        if os.path.isfile(path):
            return True
    return False


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


def resolve_playlist_folder_mode_for_batch(
    db: Any,
    *,
    playlist_id: str,
    playlist_name: str,
    batch_playlist_folder_mode: bool,
    profile_id: int = 1,
    source: str = 'spotify',
) -> tuple[bool, str]:
    """Merge batch flag with persisted mirrored-playlist preference."""
    if batch_playlist_folder_mode:
        return True, playlist_name

    if not hasattr(db, 'resolve_mirrored_playlist'):
        return False, playlist_name

    # Pass the batch's source so numeric upstream ids (e.g. Deezer) resolve by
    # source instead of colliding with the mirrored-playlists primary key.
    mirrored = db.resolve_mirrored_playlist(
        playlist_id, profile_id=profile_id, default_source=source or 'spotify'
    )
    if mirrored and mirrored.get('organize_by_playlist'):
        return True, mirrored.get('name') or playlist_name
    return False, playlist_name


__all__ = [
    'candidate_playlist_folder_paths',
    'track_exists_in_playlist_folder',
    'track_exists_in_playlist_folder_from_track_data',
    'resolve_playlist_folder_mode_for_batch',
]
