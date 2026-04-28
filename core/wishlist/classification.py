"""Wishlist track classification helpers."""

from __future__ import annotations

import json
from typing import Any, Dict


def classify_wishlist_track(track: Dict[str, Any]) -> str:
    """Classify a wishlist track as `singles` or `albums`."""
    spotify_data = track.get('spotify_data', {})
    if isinstance(spotify_data, str):
        try:
            spotify_data = json.loads(spotify_data)
        except Exception:
            spotify_data = {}

    album_data = spotify_data.get('album') or {}
    if not isinstance(album_data, dict):
        album_data = {}
    total_tracks = album_data.get('total_tracks')
    album_type = album_data.get('album_type', '').lower()

    # Prioritize Spotify's album_type classification (most accurate)
    if album_type in ('single', 'ep'):
        return 'singles'
    if album_type in ('album', 'compilation'):
        return 'albums'

    # Fallback: track count heuristic
    if total_tracks is not None and total_tracks > 0:
        return 'singles' if total_tracks < 6 else 'albums'

    # No classification data — default to albums
    return 'albums'


__all__ = ["classify_wishlist_track"]
