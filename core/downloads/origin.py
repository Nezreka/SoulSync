"""Download-origin provenance: what TRIGGERED a download.

The library history records which SERVICE a file came from (Soulseek,
YouTube, ...) but not WHY it was downloaded — a watchlist scan, a playlist
sync, or a manual click. The origin-history modal (watchlist page / sync
page) answers that, so the trigger must be derived once, at the history
chokepoint (``record_library_history_download``), from the post-process
context.

Signals, in priority order:
  1. explicit ``track_info._dl_origin`` / ``_dl_origin_context`` stamps
     (set at batch-task creation in core/downloads/master.py)
  2. wishlist provenance riding in ``track_info.source_info`` — watchlist
     items carry ``watchlist_artist_name``, playlist items ``playlist_name``
  3. the playlist-folder-mode ``_playlist_name`` thread

Anything unmatched derives ``(None, '')`` — manual/other downloads are
intentionally not classified.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional, Tuple

ORIGIN_WATCHLIST = "watchlist"
ORIGIN_PLAYLIST = "playlist"
VALID_ORIGINS = (ORIGIN_WATCHLIST, ORIGIN_PLAYLIST)

# an album or a song picked by hand downloads under a made-up playlist id (the
# search page, an artist page, a pasted link). a cancel or a failed track goes
# back to the wishlist carrying that id and the album's name as its "playlist",
# and the retry then counted as a playlist download (#1932).
HAND_PICKED_ID_PREFIXES = (
    'enhanced_search_', 'gsearch_', 'artist_album_', 'discover_album_',
    'seasonal_album_', 'library_redownload_', 'top_tracks_', 'itunes_link_',
)
_PLACEHOLDER_NAMES = ('', 'unknown playlist')


def is_hand_picked(playlist_id: Any, playlist_name: Any = None, is_album_download: Any = False) -> bool:
    """Whether a batch (or the wishlist row it left behind) was a download
    someone picked by hand rather than a playlist."""
    if is_album_download:
        return True
    if str(playlist_id or '').startswith(HAND_PICKED_ID_PREFIXES):
        return True
    return playlist_name is not None and str(playlist_name).strip().casefold() in _PLACEHOLDER_NAMES


def _parse_source_info(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def derive_download_origin(context: Dict[str, Any]) -> Tuple[Optional[str], str]:
    """Return ``(origin, origin_context)`` for a completed download.

    ``origin`` is 'watchlist' / 'playlist' / None; ``origin_context`` is the
    human label (watchlist artist name / playlist name). Never raises."""
    try:
        ti = context.get("track_info") or {}
        if not isinstance(ti, dict):
            return None, ""
        si = _parse_source_info(ti.get("source_info"))

        # 1. Explicit stamp wins.
        origin = ti.get("_dl_origin")
        if origin in VALID_ORIGINS:
            return origin, str(ti.get("_dl_origin_context") or "")

        # 2. Wishlist provenance riding in source_info.
        if si.get("watchlist_artist_name"):
            return ORIGIN_WATCHLIST, str(si["watchlist_artist_name"])
        if si.get("playlist_name") and not is_hand_picked(
                si.get("playlist_id"), si.get("playlist_name"), si.get("is_album_download")):
            return ORIGIN_PLAYLIST, str(si["playlist_name"])

        # 3. Playlist-folder-mode thread.
        if ti.get("_playlist_name"):
            return ORIGIN_PLAYLIST, str(ti["_playlist_name"])

        return None, ""
    except Exception:
        return None, ""
