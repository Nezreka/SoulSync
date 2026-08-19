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

    TWO source rows MAY share one server track. The cache's UNIQUE
    constraint is per (source_track_id, server_source) — it stops one
    source having two servers, NOT two sources (a playlist carrying the
    same song twice under different provider ids, both hand-matched to
    the user's single library copy) sharing one server track. The old
    first-wins rule silently dropped the second pairing, so that row
    stayed "missing" forever no matter how many times the user re-ran
    Find & Add (wolf39us). Both pairings are user-confirmed — honor both.

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
        if j is None:
            continue
        overrides[i] = j

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


def _cache_row_is_user_confirmed(cached: Dict[str, Any]) -> bool:
    """Is this ``sync_match_cache`` row a USER-CONFIRMED pairing, or one the
    sync's auto-matcher wrote for its own reuse?

    The table holds both. ``record_manual_match`` writes confidence=1.0; the
    sync fast-path stores every auto-match it makes at its real score (>=0.7,
    ``services/sync_service.py``) so the next run can skip re-matching. The
    compare view's pass 0 documented itself as applying "user-confirmed match
    overrides ... persisted at confidence=1.0" but never actually checked, so a
    0.75 auto-match was pinned as if the user had confirmed it (#1159,
    AfonsoG6): the bad pairing came back on every load, and — because a cache
    hit that points into the playlist short-circuits the durable path — the
    user's SAVED manual match was never even consulted. His words: "almost as
    if the 'saved matches list' is not being consulted."

    A row with no readable confidence is trusted: only a numeric score below
    1.0 proves the row came from the auto-matcher, and the sync always writes
    one. (Stub DBs in tests return rows without the key.)"""
    raw = cached.get("confidence")
    if raw is None:
        return True
    try:
        return float(raw) >= 1.0
    except (TypeError, ValueError):
        return True


def resolve_override_server_id(
    db: Any,
    profile_id: int,
    source_track_id: str,
    server_source: str,
    valid_server_ids: set,
    read_cache: Callable[[str, str], Optional[Dict[str, Any]]],
) -> Optional[Any]:
    """Resolve a source track's user-confirmed server track id for the compare view.

    Prefers the fast ``sync_match_cache`` hit, then the durable manual library match
    (#787). THE CACHE HIT IS ONLY TRUSTED WHEN IT STILL POINTS AT A TRACK IN THIS
    PLAYLIST (``valid_server_ids``).

    Why (wolf39us / Plex): a cache hit was previously returned unconditionally, so a
    STALE entry — Plex reassigned the library ratingKey without a SoulSync rescan to
    wipe the cache — short-circuited the durable path and the manual match silently
    never applied (resolve_match_overrides drops a server id that isn't in the
    playlist). Falling through to the durable match lets it self-heal the stale id
    via the stored file path. Returns the server track id, or None. Never raises.
    """
    try:
        cached = read_cache(source_track_id, server_source) or {}
    except Exception:
        cached = {}
    cached_id = cached.get("server_track_id")
    if cached_id is not None and not _cache_row_is_user_confirmed(cached):
        # An auto-match the sync cached for its own reuse — not a user
        # confirmation, so it must not pin the pairing here (#1159).
        cached_id = None
    if cached_id is not None:
        if str(cached_id) in valid_server_ids:
            return cached_id
        logger.warning(
            "Stale match-cache for source %s (%s): cached server track %s is no "
            "longer in this playlist — falling back to the durable manual match.",
            source_track_id, server_source, cached_id,
        )
    return resolve_durable_match_server_id(
        db, profile_id, source_track_id, server_source, valid_server_ids
    )


def _playlist_server_id(db: Any, track_id: Any, valid_server_ids: set) -> Optional[str]:
    """The id THIS playlist knows for a matched library track, or None.

    ``library_track_id`` is a catalogue id. It used to be the media server's
    own id, because the two were one row — so a stored value that is already
    in the playlist is taken at face value, which keeps matches saved before
    the catalogue existed working.
    """
    if track_id is None:
        return None
    if str(track_id) in valid_server_ids:
        return str(track_id)
    reader = getattr(db, "server_track_id", None)
    if reader is None:
        return None
    try:
        server_id = reader(track_id)
    except Exception:
        return None
    if server_id and str(server_id) in valid_server_ids:
        return str(server_id)
    return None


def build_bulk_override_lookup(
    db: Any,
    profile_id: int,
    server_source: str,
    valid_server_ids: set,
    source_tracks: List[Dict[str, Any]],
) -> Callable[[str], Optional[Any]]:
    """A ``cache_lookup`` callable for :func:`resolve_match_overrides` backed by
    TWO bulk reads instead of 2-3 fresh-connection queries (plus a COMMIT per
    cache hit) for every source track — #1005: pass 0 alone took ~15s on a
    1500-track playlist before the columns could even render.

    Per-id semantics are IDENTICAL to :func:`resolve_override_server_id`:
    a cache hit is only trusted while it points into THIS playlist, else the
    durable manual match applies, with the per-row file-path self-heal kept
    for the (rare) stale rows. Falls back to the per-row resolver when the DB
    doesn't offer the bulk readers (stub DBs).

    The returned callable carries ``dead_match_source_ids``: source ids that
    HAVE a saved manual match which could not be applied by any route (#1138).
    Callers must not present those rows as "already matched" — the saved match
    points at something that no longer exists, and claiming otherwise costs the
    user the Find & Add and download affordances, so the track is silently
    neither synced nor wishlisted."""
    if not (hasattr(db, "read_sync_match_cache_bulk")
            and hasattr(db, "find_manual_library_matches_bulk")):
        def _per_row(src_id):
            return resolve_override_server_id(
                db, profile_id, src_id, server_source, valid_server_ids,
                getattr(db, "read_sync_match_cache", lambda *_a: None))
        _per_row.dead_match_source_ids = set()
        return _per_row

    ids = [str(s.get("source_track_id")) for s in (source_tracks or [])
           if isinstance(s, dict) and s.get("source_track_id")]
    try:
        cache_map = db.read_sync_match_cache_bulk(ids, server_source) or {}
    except Exception:
        cache_map = {}
    try:
        durable_map = db.find_manual_library_matches_bulk(profile_id, ids, server_source) or {}
    except Exception:
        durable_map = {}

    dead_ids: set = set()

    def _lookup(source_track_id):
        sid = str(source_track_id)
        cached = cache_map.get(sid) or {}
        cached_id = cached.get("server_track_id")
        if cached_id is not None and not _cache_row_is_user_confirmed(cached):
            # Auto-match reuse row, not a user confirmation (#1159) — fall
            # through to the durable manual match / normal matching.
            cached_id = None
        if cached_id is not None:
            if str(cached_id) in valid_server_ids:
                return cached_id
            logger.warning(
                "Stale match-cache for source %s (%s): cached server track %s is no "
                "longer in this playlist — falling back to the durable manual match.",
                sid, server_source, cached_id,
            )
        match = durable_map.get(sid)
        if not match:
            return None
        lib_id = match.get("library_track_id")
        resolved = _playlist_server_id(db, lib_id, valid_server_ids)
        if resolved is not None:
            return resolved
        # Stale pointer — re-resolve via the stored file path and self-heal.
        file_path = match.get("library_file_path")
        resolver = getattr(db, "find_track_id_by_file_path", None)
        if file_path and resolver is not None:
            try:
                new_id = resolver(file_path)
            except Exception:
                new_id = None
            resolved = _playlist_server_id(db, new_id, valid_server_ids)
            if resolved is not None:
                _self_heal_match_id(db, match, str(new_id))
                return resolved
        # Every path exhausted — the user's confirmed match CANNOT apply. Say
        # exactly why, or this renders as a bare "missing" row and looks like
        # the match was never saved (the wolf39us report shape).
        # Every path exhausted: this saved match is DEAD (its file was deleted,
        # or the library row went away). Remember it so the caller stops
        # advertising the row as "already matched" — see #1138, where a match
        # to a since-deleted file left the track with no Find & Add and no
        # download, so reprocessing the playlist silently skipped it forever.
        dead_ids.add(sid)
        logger.warning(
            "Manual match for source %s (%s) cannot apply: library track %s is not "
            "in this playlist and the file-path fallback %s. Treating the saved match "
            "as stale so the track can be re-matched or wishlisted; remove it from "
            "Tools → Manual Library Match to stop this warning.",
            sid, server_source, lib_id,
            ("resolved nothing for %r" % (file_path,)) if file_path else "is empty (no stored path)",
        )
        return None

    _lookup.dead_match_source_ids = dead_ids
    return _lookup


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
    resolved = _playlist_server_id(db, lib_id, valid_server_ids)
    if resolved is not None:
        return resolved

    # Stale pointer — re-resolve via the stored file path and self-heal.
    file_path = match.get("library_file_path")
    resolver = getattr(db, "find_track_id_by_file_path", None)
    if file_path and resolver is not None:
        try:
            new_id = resolver(file_path)
        except Exception:
            new_id = None
        resolved = _playlist_server_id(db, new_id, valid_server_ids)
        if resolved is not None:
            _self_heal_match_id(db, match, str(new_id))
            return resolved
    logger.warning(
        "Manual match for source %s (%s) cannot apply: library track %s is not "
        "in this playlist and the file-path fallback %s. The track likely needs "
        "re-adding to the playlist (Find & Add), or the library row was re-keyed.",
        source_track_id, server_source, lib_id,
        ("resolved nothing for %r" % (file_path,)) if file_path else "is empty (no stored path)",
    )
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
