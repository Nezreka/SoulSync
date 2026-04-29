"""Wishlist reporting helpers."""

from __future__ import annotations

from typing import Any, Iterable

from core.wishlist.classification import classify_wishlist_track
from core.wishlist.selection import sanitize_and_dedupe_wishlist_tracks


def count_wishlist_tracks_by_category(raw_tracks: Iterable[dict[str, Any]]) -> dict[str, int]:
    """Count deduped wishlist tracks by category."""
    sanitized_tracks, _ = sanitize_and_dedupe_wishlist_tracks(raw_tracks)

    singles_count = 0
    albums_count = 0

    for track in sanitized_tracks:
        if classify_wishlist_track(track) == 'singles':
            singles_count += 1
        else:
            albums_count += 1

    total = singles_count + albums_count
    return {
        'singles': singles_count,
        'albums': albums_count,
        'total': total,
    }


def build_wishlist_stats_payload(
    raw_tracks: Iterable[dict[str, Any]],
    *,
    next_run_in_seconds: int,
    is_auto_processing: bool,
    current_cycle: str,
) -> dict[str, Any]:
    """Build the API payload used by the wishlist stats endpoint."""
    counts = count_wishlist_tracks_by_category(raw_tracks)
    return {
        "singles": counts["singles"],
        "albums": counts["albums"],
        "total": counts["total"],
        "next_run_in_seconds": next_run_in_seconds,
        "is_auto_processing": is_auto_processing,
        "current_cycle": current_cycle,
    }


__all__ = ["count_wishlist_tracks_by_category", "build_wishlist_stats_payload"]
