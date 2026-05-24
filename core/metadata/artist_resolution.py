"""Pure artist-list resolution for tag-write paths.

Single source of truth for "what is the canonical multi-value artists
list for this track?" Different download paths populate `context` with
different keys — Deezer-direct downloads stamp `original_search.artists`
as a proper list, but Soulseek matched downloads only carry `artist`
(singular string) in `original_search_result` while the full list lives
on `track_info` (the full Spotify track object).

Resolution order:
    1. `context.original_search_result.artists` (preferred — already-
       curated by the source path that constructed the context)
    2. `context.track_info.artists` (Spotify/Deezer/Tidal full track
       object — always carries the artists array when matched)
    3. `[artist_dict.name]` as a single-element fallback when neither
       carries a list (primary-artist-only)

Each list item may be a dict with a `name` key (Spotify shape), a bare
string, or any other object — the helper normalizes all three to
strings and drops empty entries.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def _normalize_artists_iterable(items: Any) -> List[str]:
    if not isinstance(items, list):
        return []
    result: List[str] = []
    for item in items:
        if isinstance(item, dict):
            name = item.get("name")
            if isinstance(name, str) and name.strip():
                result.append(name.strip())
        elif isinstance(item, str):
            stripped = item.strip()
            if stripped:
                result.append(stripped)
        elif item is not None:
            text = str(item).strip()
            if text:
                result.append(text)
    return result


def resolve_track_artists(
    original_search: Optional[Dict[str, Any]],
    track_info: Optional[Dict[str, Any]],
    artist_dict: Optional[Dict[str, Any]],
) -> List[str]:
    """Return the canonical multi-value artists list for tag-write.

    Falls through preferred → track_info → primary-artist fallback. Each
    candidate is normalized to a list of stripped non-empty strings.
    Empty list returned only when every candidate is empty/invalid.
    """
    if isinstance(original_search, dict):
        primary = _normalize_artists_iterable(original_search.get("artists"))
        if primary:
            return primary

    if isinstance(track_info, dict):
        secondary = _normalize_artists_iterable(track_info.get("artists"))
        if secondary:
            return secondary

    if isinstance(artist_dict, dict):
        name = artist_dict.get("name")
        if isinstance(name, str) and name.strip():
            return [name.strip()]

    return []
