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


def _coerce_positive_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            try:
                return int(float(text))
            except ValueError:
                return None
    return None


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
    total_tracks_value = _coerce_positive_int(total_tracks)
    if total_tracks_value is not None and total_tracks_value > 0:
        return 'singles' if total_tracks_value < 6 else 'albums'

    # No classification data — default to albums
    return 'albums'


__all__ = ["classify_wishlist_track"]
