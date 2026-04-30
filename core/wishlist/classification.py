"""Wishlist track classification helpers."""

from __future__ import annotations

import json
from typing import Any, Dict


def _extract_track_data(track: Dict[str, Any]) -> Dict[str, Any]:
    for key in ("track_data", "spotify_data", "metadata", "track"):
        data = track.get(key)
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:
                data = {}
        if isinstance(data, dict) and data:
            nested = data.get("track_data") or data.get("spotify_data") or data.get("metadata") or data.get("track")
            if isinstance(nested, str):
                try:
                    nested = json.loads(nested)
                except Exception:
                    nested = {}
            if isinstance(nested, dict) and nested:
                return nested
            return data
    return {}


def classify_wishlist_track(track: Dict[str, Any]) -> str:
    """Classify a wishlist track as `singles` or `albums`."""
    track_data = _extract_track_data(track)

    album_data = track_data.get('album') or {}
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
