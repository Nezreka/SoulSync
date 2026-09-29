"""Wishlist track selection helpers."""

from __future__ import annotations

from typing import Any, Callable, Iterable

from core.wishlist.classification import classify_wishlist_track
from core.wishlist.payloads import sanitize_track_data_for_processing


def sanitize_and_dedupe_wishlist_tracks(
    raw_tracks: Iterable[dict[str, Any]],
    *,
    sanitizer: Callable[[dict[str, Any]], dict[str, Any]] = sanitize_track_data_for_processing,
) -> tuple[list[dict[str, Any]], int]:
    """Sanitize wishlist tracks and drop duplicate track IDs.

    Duplicates are per (track, owner): the same track wishlisted by two
    profiles is two owned requests, not a duplicate (H10) — each profile's
    entry must survive so it can be batched under its own profile and
    downloaded into its own library.
    """
    sanitized_tracks: list[dict[str, Any]] = []
    seen_keys: set[tuple[Any, Any]] = set()
    duplicates_found = 0

    for track in raw_tracks:
        sanitized_track = sanitizer(track)
        spotify_track_id = (
            sanitized_track.get('track_id')
            or sanitized_track.get('spotify_track_id')
            or sanitized_track.get('id')
        )
        dedupe_key = (spotify_track_id, sanitized_track.get('profile_id'))

        if spotify_track_id and dedupe_key in seen_keys:
            duplicates_found += 1
            continue

        sanitized_tracks.append(sanitized_track)
        if spotify_track_id:
            seen_keys.add(dedupe_key)

    return sanitized_tracks, duplicates_found


def filter_wishlist_tracks_by_category(
    tracks: Iterable[dict[str, Any]],
    category: str,
    *,
    classifier: Callable[[dict[str, Any]], str] = classify_wishlist_track,
) -> tuple[list[dict[str, Any]], int]:
    """Filter wishlist tracks by category and return the matches plus total count.

    Dedupes per (track, owner) like :func:`sanitize_and_dedupe_wishlist_tracks`
    (H10): the same track in this category for two profiles is two owned
    requests and both must reach their owner's batch.
    """
    filtered_tracks: list[dict[str, Any]] = []
    seen_keys: set[tuple[Any, Any]] = set()

    for track in tracks:
        track_category = classifier(track)
        spotify_track_id = track.get('track_id') or track.get('spotify_track_id') or track.get('id')
        if category != track_category:
            continue

        if spotify_track_id:
            dedupe_key = (spotify_track_id, track.get('profile_id'))
            if dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)

        filtered_tracks.append(track)

    total_in_category = sum(1 for track in tracks if classifier(track) == category)
    return filtered_tracks, total_in_category


def prepare_wishlist_tracks_for_display(
    raw_tracks: Iterable[dict[str, Any]],
    *,
    category: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Sanitize, dedupe, and optionally filter wishlist tracks for API output."""
    sanitized_tracks, duplicates_found = sanitize_and_dedupe_wishlist_tracks(raw_tracks)

    result_tracks = sanitized_tracks
    total = len(sanitized_tracks)

    if category:
        result_tracks, total = filter_wishlist_tracks_by_category(sanitized_tracks, category)

    if limit is not None:
        result_tracks = result_tracks[:limit]

    return {
        'tracks': result_tracks,
        'total': total,
        'duplicates_found': duplicates_found,
        'category': category,
    }


__all__ = [
    "sanitize_and_dedupe_wishlist_tracks",
    "filter_wishlist_tracks_by_category",
    "prepare_wishlist_tracks_for_display",
]
