"""Sync match overrides — user-confirmed source→server track pairings.

When a user picks a local file via "Find & Add" on the Server Playlist
compare view, that selection should persist as a hard match across
future syncs — bypassing the fuzzy/exact title-match algorithm
entirely. This module provides pure helpers that the web layer calls
to resolve and persist those overrides through the existing
`sync_match_cache` table.

Override semantics:
    - One mapping per (source_track_id, server_source). UNIQUE
      constraint on the table enforces single mapping per pair.
    - Stored with confidence=1.0 to distinguish from auto-discovered
      matches (which use the actual title-similarity score).
    - Read at the START of the matching algorithm — before pass-1
      exact and pass-2 fuzzy. Skipped sources don't re-enter the
      normal matching pool.
    - Stale-cache safe: if the cached server_track_id doesn't exist
      in the current server_tracks list (track removed from server),
      the override is silently skipped and normal matching runs.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("sync.match_overrides")


def resolve_match_overrides(
    source_tracks: List[Dict[str, Any]],
    server_tracks: List[Dict[str, Any]],
    cache_lookup: Callable[[str], Optional[Any]],
) -> Dict[int, int]:
    """Map source-track indexes to server-track indexes for cached overrides.

    Pure function. `cache_lookup(source_track_id) -> server_track_id or
    None` is injected by the caller (web layer wraps the DB call).

    Returns ``{source_idx: server_idx}``. Only includes pairs where:
        - source_track has a non-empty `source_track_id`
        - cache_lookup returns a server_track_id
        - that server_track_id exists in server_tracks (no stale cache
          entries pointing at deleted tracks)
        - the server_track hasn't already been claimed by an earlier
          override (defensive — UNIQUE on the cache table prevents this
          in practice)

    Caller uses the returned dict to short-circuit the per-source
    matching loop: indices in the dict skip the exact/fuzzy passes.
    """
    if not source_tracks or not server_tracks:
        return {}

    server_id_to_idx: Dict[str, int] = {}
    for j, svr in enumerate(server_tracks):
        sid = svr.get("id") if isinstance(svr, dict) else None
        if sid is not None:
            key = str(sid)
            if key not in server_id_to_idx:
                server_id_to_idx[key] = j

    overrides: Dict[int, int] = {}
    used_server: set[int] = set()

    for i, src in enumerate(source_tracks):
        if not isinstance(src, dict):
            continue
        src_id = src.get("source_track_id")
        if not src_id:
            continue
        try:
            cached_server_id = cache_lookup(str(src_id))
        except Exception:
            cached_server_id = None
        if not cached_server_id:
            continue
        j = server_id_to_idx.get(str(cached_server_id))
        if j is None or j in used_server:
            continue
        overrides[i] = j
        used_server.add(j)

    return overrides


def record_manual_match(
    db: Any,
    source_track_id: str,
    server_source: str,
    server_track_id: Any,
    server_track_title: str = "",
    source_title: str = "",
    source_artist: str = "",
) -> bool:
    """Persist a user-confirmed source→server pairing as a hard override.

    Wraps `db.save_sync_match_cache` with confidence=1.0 (the manual
    match marker). Normalized title/artist fields are informational
    only — the cache is keyed by `(spotify_track_id, server_source)`,
    so the normalization is just for inspection and future debugging.

    Returns True on persist success, False on any failure (DB, missing
    args, etc). Never raises.
    """
    if not source_track_id or not server_source or server_track_id is None:
        return False
    if not hasattr(db, "save_sync_match_cache"):
        return False
    try:
        return bool(db.save_sync_match_cache(
            spotify_track_id=str(source_track_id),
            normalized_title=(source_title or "").lower().strip(),
            normalized_artist=(source_artist or "").lower().strip(),
            server_source=server_source,
            server_track_id=server_track_id,
            server_track_title=server_track_title or "",
            confidence=1.0,
        ))
    except Exception:
        return False


def resolve_durable_match_server_id(
    db: Any,
    profile_id: int,
    source_track_id: str,
    server_source: str,
    valid_server_ids: set,
) -> Optional[str]:
    """Current server track id for a DURABLE manual library match, or None.

    Unlike ``sync_match_cache`` (wiped on every rescan), the
    ``manual_library_track_matches`` table survives a scan — so consulting it
    here is what makes a user's Find & Add / manual match persist across a
    library rescan (#787). If the stored ``library_track_id`` went stale
    (a rescan re-keyed the track — common on Jellyfin/Navidrome), re-resolve
    it from the stored file path and self-heal the row so the next lookup is
    a direct hit.

    Pure helper: ``db`` is injected. ``valid_server_ids`` is the set of
    string ids that currently exist in the server playlist's track list —
    a re-resolved id is only returned if it's actually present. Never raises.
    """
    if not source_track_id:
        return None
    finder = getattr(db, "find_manual_library_match_by_source_track_id", None)
    if finder is None:
        return None
    try:
        match = finder(profile_id, str(source_track_id), server_source or "")
    except Exception:
        return None
    if not match:
        return None

    lib_id = match.get("library_track_id")
    if lib_id is not None and str(lib_id) in valid_server_ids:
        return str(lib_id)

    # Stale pointer — re-resolve via the stored file path and self-heal.
    file_path = match.get("library_file_path")
    resolver = getattr(db, "find_track_id_by_file_path", None)
    if file_path and resolver is not None:
        try:
            new_id = resolver(file_path)
        except Exception:
            new_id = None
        if new_id and str(new_id) in valid_server_ids:
            _self_heal_match_id(db, match, str(new_id))
            return str(new_id)
    return None


def _self_heal_match_id(db: Any, match: Dict[str, Any], new_library_track_id: str) -> None:
    """Rewrite a manual match's library_track_id after re-resolution. Best-effort."""
    saver = getattr(db, "save_manual_library_match", None)
    if saver is None:
        return
    try:
        saver(
            match.get("profile_id", 1),
            match.get("source", ""),
            match.get("source_track_id", ""),
            new_library_track_id,
            source_title=match.get("source_title"),
            source_artist=match.get("source_artist"),
            source_album=match.get("source_album"),
            source_context_json=match.get("source_context_json"),
            server_source=match.get("server_source", ""),
            library_file_path=match.get("library_file_path"),
        )
    except Exception as e:
        logger.debug("manual match self-heal failed: %s", e)
