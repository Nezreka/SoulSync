"""Manual library match service.

Lets users explicitly link a source track (wishlist/sync-history candidate) to
an existing library track so SoulSync stops trying to re-download it.
"""

from __future__ import annotations

import json
import time
from typing import Any, Optional

from utils.logging_config import get_logger

logger = get_logger("library.manual_library_match")


def normalize_library_track_id(value: Any) -> Optional[str]:
    """Normalize an incoming library_track_id to the opaque string id we store.

    Library track ids are ``str(ratingKey)`` — numeric strings for Plex, but
    GUIDs/hashes for Jellyfin, Navidrome, and other Subsonic servers (see
    ``tracks.id`` which is TEXT). They must NOT be coerced to int: doing so
    rejected every non-numeric id with "Invalid library track id". We only
    reject empty/None here; the caller validates existence against the DB.
    """
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def save_match(
    db,
    profile_id: int,
    source: str,
    source_track_id: str,
    library_track_id: str,
    **meta,
) -> bool:
    """Save (insert or replace) a manual match."""
    # Normalize the library id the same way the sync cache expects it (#754).
    library_track_id = normalize_library_track_id(library_track_id)
    if not library_track_id:
        return False
    ok = db.save_manual_library_match(
        profile_id, source, source_track_id, library_track_id, **meta
    )
    if ok:
        # #1289: a new manual match must immediately refresh the mirrored
        # playlist "missing / in library" flags. Those flags are a stored
        # per-track cache (extra_data.in_library) written only during sync
        # (_record_library_membership), so without this the card kept showing
        # the track as missing until the next sync ran.
        try:
            refresh_mirrored_library_flags(
                db, profile_id, source_track_id, library_track_id
            )
        except Exception as exc:  # noqa: BLE001 - never fail the save over bookkeeping
            logger.debug("mirrored flag refresh failed: %s", exc)
    return ok


def _mirrored_tracks_for_source_id(db, profile_id, source_track_id):
    """Yield mirrored tracks (for this profile) matching a source ID.

    Matching is by ``source_track_id`` only: source labels legitimately differ
    between UI surfaces (``get_match_for_track`` documents the same), and the
    sync matcher honors the match via its ID fallback anyway, so the flag
    must agree with what the next sync would compute.
    """
    wanted = str(source_track_id or "")
    if not wanted:
        return
    try:
        playlists = db.get_mirrored_playlists(profile_id=profile_id) or []
    except Exception as exc:  # noqa: BLE001
        logger.debug("mirrored flag refresh: playlist list failed: %s", exc)
        return
    for pl in playlists:
        pid = pl.get("id") if isinstance(pl, dict) else None
        if pid is None:
            continue
        try:
            tracks = db.get_mirrored_playlist_tracks(pid) or []
        except Exception as exc:  # noqa: BLE001
            logger.debug("mirrored flag refresh: tracks for playlist %s failed: %s", pid, exc)
            continue
        for track in tracks:
            if not isinstance(track, dict):
                continue
            if str(track.get("source_track_id") or "") != wanted:
                continue
            yield track


def refresh_mirrored_library_flags(
    db,
    profile_id: int,
    source_track_id: str,
    library_track_id: str,
) -> int:
    """Mark mirrored tracks for a manual match as in-library, immediately.

    Stamps the same three ``extra_data`` fields the sync-time
    ``_record_library_membership`` writes (``in_library``,
    ``library_track_id``, ``library_checked_at``). The card counts
    (``get_all_mirrored_playlist_status_counts``) read that stored cache, so
    this is what makes the "missing / in library" flags accurate without
    waiting for a sync.

    A liveness check guards the stamp: if the library track no longer
    resolves (the #1138 lesson), the flag is left alone instead of asserting
    something the next sync would immediately contradict. When the db object
    cannot answer (a stub without the reader), the match is treated as live —
    same rule ``match_is_live`` uses, so a narrowed facade never mass-resets
    flags it cannot verify.

    Returns the number of mirrored tracks updated.
    """
    if not source_track_id or not library_track_id:
        return 0
    # Liveness: the sync durable path resolves the library id (and self-heals
    # via the stored file path) before reporting found. Mirror that here so
    # the flag agrees with what the next sync would compute.
    if not _library_track_is_live(db, library_track_id):
        logger.debug(
            "mirrored flag refresh: library track %s not live, leaving flags",
            library_track_id,
        )
        return 0
    checked_at = int(time.time())
    updated = 0
    for track in _mirrored_tracks_for_source_id(db, profile_id, source_track_id):
        try:
            if db.update_mirrored_track_extra_data(track["id"], {
                "in_library": True,
                "library_track_id": library_track_id,
                "library_checked_at": checked_at,
            }):
                updated += 1
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "mirrored flag refresh: update failed for track %s: %s",
                track.get("id"), exc,
            )
    if updated:
        logger.info(
            "Refreshed in_library flag for %d mirrored track(s) after manual match",
            updated,
        )
    else:
        logger.debug(
            "mirrored flag refresh: no mirrored tracks matched source_track_id %s",
            source_track_id,
        )
    return updated


def _library_track_is_live(db, library_track_id) -> bool:
    """Does the library track id still resolve to a library row?"""
    getter = getattr(db, "api_get_tracks_by_ids", None)
    if getter is None:
        # Cannot check is not the same as gone (same rule as match_is_live).
        return True
    try:
        return bool(getter([library_track_id]))
    except Exception as exc:  # noqa: BLE001
        logger.debug("mirrored flag refresh: liveness check failed: %s", exc)
        return True


def clear_mirrored_library_flags(
    db, profile_id: int, source_track_id: str
) -> int:
    """Reset mirrored in-library flags after a manual match was deleted.

    #1289 (delete half): the save path stamps ``in_library=True``; deleting
    the match must not strand that flag as ``True`` until the next sync. When
    no other live manual match still covers the ``source_track_id``, the flags
    are reset to what the next sync would compute for an unmatched track
    (``in_library=False``, no ``library_track_id``).
    """
    if not source_track_id:
        return 0
    # If another live match still covers this source track, leave the flags.
    # The check is server-agnostic and considers EVERY survivor: a single
    # dead survivor must not mask a live one (LIMIT 1 with no ORDER BY would
    # pick arbitrarily). Keep the flags if ANY survivor is live.
    try:
        any_live = False
        all_getter = getattr(
            db, "find_all_manual_library_matches_by_source_track_id", None
        )
        if all_getter is not None:
            for survivor in all_getter(profile_id, str(source_track_id)) or []:
                if _library_track_is_live(
                    db, survivor.get("library_track_id") if isinstance(survivor, dict) else None
                ):
                    any_live = True
                    break
        if any_live:
            logger.debug(
                "mirrored flag clear: another live match covers %s, keeping flags",
                source_track_id,
            )
            return 0
    except Exception as exc:  # noqa: BLE001
        logger.debug("mirrored flag clear: surviving-match check failed: %s", exc)
    checked_at = int(time.time())
    updated = 0
    for track in _mirrored_tracks_for_source_id(db, profile_id, source_track_id):
        try:
            if db.update_mirrored_track_extra_data(track["id"], {
                "in_library": False,
                "library_track_id": None,
                "library_checked_at": checked_at,
            }):
                updated += 1
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "mirrored flag clear: update failed for track %s: %s",
                track.get("id"), exc,
            )
    if updated:
        logger.info(
            "Cleared in_library flag for %d mirrored track(s) after manual match delete",
            updated,
        )
    return updated


def get_match(
    db,
    profile_id: int,
    source: str,
    source_track_id: str,
    server_source: str = "",
) -> Optional[dict]:
    """Return match row dict or None if not found."""
    getter = getattr(db, "get_manual_library_match", None)
    if getter is None:
        return None
    return getter(profile_id, source, source_track_id, server_source)


def match_is_live(db, match: Optional[dict]) -> bool:
    """Does this saved manual match still point at something that EXISTS?

    #1138 (carlosjfcasero): a user matched a track to a library file, deleted
    the file, then reprocessed the playlist. The download analysis asked only
    whether a match ROW existed — never whether it still resolved — so it
    marked the track found, removed it from the wishlist, and skipped the
    download. The track then vanished from every subsequent run: not synced,
    not downloaded, not wishlisted, with only a WARNING in the log.

    "Exists" is answered against the library DATABASE, not the filesystem, and
    that is deliberate: ``library_file_path`` is the path as the MEDIA SERVER
    sees it (``/music/library/...`` in the report), which SoulSync may mount
    elsewhere or not at all. An ``os.path.exists`` check would call live
    matches dead on every split-container install and trigger a storm of
    re-downloads — a far worse bug than the one being fixed.

    Mirrors the resolution order the sync compare view already uses
    (``match_overrides.build_bulk_override_lookup``): the stored library id
    first, then the stored path, so a rescan that merely re-keyed the row
    still counts as live. A match with no id and no path is not evidence of
    anything, so it is treated as dead.
    """
    if not isinstance(match, dict):
        return False

    lib_id = match.get("library_track_id")
    if lib_id:
        getter = getattr(db, "api_get_tracks_by_ids", None)
        if getter is None:
            # Cannot check is not the same as gone. A db object without the
            # readers (a stub, a narrowed facade) would otherwise have EVERY
            # saved match declared dead, and the guard would re-download the
            # user's whole library — the opposite failure, and a worse one.
            return True
        try:
            if getter([lib_id]):
                return True
        except Exception as exc:   # noqa: BLE001
            # Same reasoning for a transient DB error.
            logger.debug("match_is_live id lookup failed for %s: %s", lib_id, exc)
            return True

    file_path = match.get("library_file_path")
    if file_path:
        resolver = getattr(db, "find_track_id_by_file_path", None)
        if resolver is None:
            return True
        try:
            if resolver(file_path):
                return True
        except Exception as exc:   # noqa: BLE001
            logger.debug("match_is_live path lookup failed for %r: %s", file_path, exc)
            return True

    # Every check that COULD run, ran, and none of them found the track. A row
    # carrying neither an id nor a path lands here too: it is not evidence of
    # anything, so it cannot justify skipping a download.
    return False


def _first_artist_name(track: dict[str, Any]) -> str:
    artists = track.get("artists") or []
    if isinstance(artists, list) and artists:
        first = artists[0]
        if isinstance(first, dict):
            return (first.get("name") or "").strip()
        return str(first).strip()
    return (track.get("artist") or track.get("artist_name") or "").strip()


def _track_source_candidates(track: dict[str, Any], default_source: str = "") -> list[str]:
    candidates = [
        track.get("provider"),
        track.get("source"),
        default_source,
        "spotify",
    ]
    out = []
    for source in candidates:
        source = (source or "").strip()
        if source and source not in out:
            out.append(source)
    return out


def _track_id_candidates(track: dict[str, Any]) -> list[str]:
    candidates = [
        track.get("source_track_id"),
        track.get("spotify_track_id"),
        track.get("track_id"),
        track.get("id"),
        track.get("musicbrainz_recording_id"),
        track.get("deezer_id") or track.get("deezer_track_id"),
        track.get("itunes_track_id"),
        track.get("tidal_id") or track.get("tidal_track_id"),
        track.get("qobuz_id") or track.get("qobuz_track_id"),
        track.get("amazon_id") or track.get("amazon_track_id"),
    ]
    out = []
    for value in candidates:
        value = str(value).strip() if value is not None else ""
        if value and value not in out:
            out.append(value)
    return out


def get_match_for_track(
    db,
    profile_id: int,
    track: dict[str, Any],
    *,
    default_source: str = "",
    server_source: str = "",
) -> Optional[dict]:
    """Return a manual match for a wishlist/sync track.

    Exact source+ID matches are preferred, but source labels can legitimately
    change between UI surfaces (for example ``mirrored`` in sync history versus
    ``wishlist`` in the wishlist batch). Fall back to track ID and finally
    title/artist so saved manual matches are honored consistently.
    """
    if not isinstance(track, dict):
        return None

    sources = _track_source_candidates(track, default_source)
    track_ids = _track_id_candidates(track)
    for track_id in track_ids:
        for source in sources:
            match = get_match(db, profile_id, source, track_id, server_source)
            if match:
                return match

    id_getter = getattr(db, "find_manual_library_match_by_source_track_id", None)
    if id_getter is not None:
        for track_id in track_ids:
            match = id_getter(profile_id, track_id, server_source)
            if match:
                return match

    title = (track.get("name") or track.get("title") or track.get("track_name") or "").strip()
    artist = _first_artist_name(track)
    metadata_getter = getattr(db, "find_manual_library_match_by_metadata", None)
    if metadata_getter is not None and title and artist:
        return metadata_getter(profile_id, title, artist, server_source)
    return None


def delete_match(db, match_id: int, profile_id: int) -> bool:
    """Delete match by PK id, scoped to profile."""
    # #1289: capture the source_track_id BEFORE deleting so the mirrored
    # flags stamped at save time can be reset (they would otherwise stay True
    # until the next sync — the mirror-image stale flag). Fetch by PK: the
    # list is capped at 100 most-recently-updated, so an older match would
    # silently skip the flag reset.
    source_track_id = None
    try:
        get_by_id = getattr(db, "get_manual_library_match_by_id", None)
        row = get_by_id(match_id, profile_id) if get_by_id else None
        if isinstance(row, dict):
            source_track_id = row.get("source_track_id")
    except Exception as exc:  # noqa: BLE001
        logger.debug("mirrored flag clear: pre-delete lookup failed: %s", exc)
    ok = db.delete_manual_library_match(match_id, profile_id)
    if ok and source_track_id:
        try:
            clear_mirrored_library_flags(db, profile_id, source_track_id)
        except Exception as exc:  # noqa: BLE001 - never fail the delete over bookkeeping
            logger.debug("mirrored flag clear failed: %s", exc)
    return ok


def list_matches(db, profile_id: int, limit: int = 100) -> list[dict]:
    """Return all matches for profile, most-recently-updated first."""
    rows = db.list_manual_library_matches(profile_id, limit)
    return [_enrich_match(row, db) for row in rows]


def search_source_candidates(db, query: str, profile_id: int, limit: int = 15) -> list[dict]:
    """Search wishlist + sync history for source track candidates matching query."""
    if not query or not query.strip():
        return []

    q = query.strip()
    like = f"%{q}%"
    results: dict[tuple, dict] = {}

    # 1) Wishlist tracks
    try:
        with db._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    json_extract(spotify_data, '$.id')   AS track_id,
                    json_extract(spotify_data, '$.name') AS title,
                    json_extract(spotify_data, '$.artists[0].name') AS artist,
                    json_extract(spotify_data, '$.album.name')      AS album,
                    date_added AS added_at
                FROM wishlist_tracks
                WHERE profile_id = ?
                  AND (
                      json_extract(spotify_data, '$.name') LIKE ?
                      OR json_extract(spotify_data, '$.artists[0].name') LIKE ?
                  )
                ORDER BY date_added DESC
                LIMIT ?
            """, (profile_id, like, like, limit * 2))
            for row in cursor.fetchall():
                r = dict(row)
                if not r.get("track_id"):
                    continue
                key = ("spotify", r["track_id"])
                if key not in results:
                    results[key] = {
                        "source": "spotify",
                        "source_track_id": r["track_id"],
                        "title": r["title"] or "",
                        "artist": r["artist"] or "",
                        "album": r["album"] or "",
                        "context": "Wishlist",
                        "added_at": r["added_at"] or "",
                    }
    except Exception as exc:
        logger.debug("source_candidates wishlist query failed: %s", exc)

    # 2) Sync history — scan tracks_json blobs
    try:
        with db._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT playlist_name, source, tracks_json, started_at
                FROM sync_history
                ORDER BY started_at DESC
                LIMIT 50
            """)
            for row in cursor.fetchall():
                sh = dict(row)
                try:
                    tracks = json.loads(sh["tracks_json"] or "[]")
                except Exception:
                    continue
                for t in tracks:
                    title = t.get("name", "")
                    artist = ""
                    artists = t.get("artists", [])
                    if artists:
                        first = artists[0]
                        artist = first.get("name", "") if isinstance(first, dict) else str(first)
                    if q.lower() not in title.lower() and q.lower() not in artist.lower():
                        continue
                    src = sh["source"] or "spotify"
                    tid = t.get("id") or t.get("spotify_track_id") or ""
                    if not tid:
                        continue
                    key = (src, tid)
                    if key not in results:
                        album = ""
                        alb = t.get("album")
                        if isinstance(alb, dict):
                            album = alb.get("name", "")
                        elif isinstance(alb, str):
                            album = alb
                        results[key] = {
                            "source": src,
                            "source_track_id": tid,
                            "title": title,
                            "artist": artist,
                            "album": album,
                            "context": sh["playlist_name"] or "",
                            "added_at": sh["started_at"] or "",
                        }
                    if len(results) >= limit * 3:
                        break
    except Exception as exc:
        logger.debug("source_candidates sync_history query failed: %s", exc)

    # Sort by recency and cap
    sorted_results = sorted(results.values(), key=lambda r: r.get("added_at", ""), reverse=True)
    return sorted_results[:limit]


def _matched_source_track_ids(db, profile_id: int) -> set:
    """All source_track_ids covered by a manual match for this profile.

    Server-agnostic (any source label / server_source), matching the rule
    ``find_all_manual_library_matches_by_source_track_id`` documents: a
    track linked under one label must not reappear as unmatched under
    another.
    """
    try:
        with db._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT DISTINCT source_track_id FROM manual_library_track_matches"
                " WHERE profile_id = ?",
                (profile_id,),
            )
            return {str(r[0]) for r in cursor.fetchall() if r[0]}
    except Exception as exc:
        logger.debug("unmatched worklist matched-ids query failed: %s", exc)
        return set()


def _mirrored_extra_in_library(track: dict) -> bool:
    """Does this mirrored track's stored cache say it is in the library?

    Reads the same ``extra_data.in_library`` flag the sync-time
    ``_record_library_membership`` writes and ``save_match()`` stamps via
    ``refresh_mirrored_library_flags``.
    """
    raw = track.get("extra_data")
    if not raw:
        return False
    try:
        extra = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return False
    return bool(isinstance(extra, dict) and extra.get("in_library"))


def list_unmatched_wanted_tracks(db, profile_id: int, limit: int = 200) -> list[dict]:
    """Pre-populated worklist of wanted-but-unmatched tracks (#1289).

    The manual-match modal's source panel used to start empty ("Type to
    search"), so users had to already know which tracks were unmatched.
    This returns every wanted track with no library link and no manual
    match, most-recent first, in the same dict shape as
    ``search_source_candidates``:

    - wishlist rows (context "Wishlist"), skipping rows whose Spotify id is
      already a library track (``api_get_track_by_external_id`` — the same
      external-ID-first resolution ``api/library.py`` uses to map source
      tracks to library tracks) or is covered by a manual match;
    - mirrored playlist tracks (context = playlist name), skipping rows
      whose stored ``extra_data.in_library`` is true or covered by a manual
      match.

    The fuzzy strict-identity ownership check (``find_owned_match``) is
    deliberately NOT run here: it costs a library search per track and the
    background wishlist cleanup already removes those rows. A lingering
    owned-but-unmatched row simply shows up in the worklist, where the user
    can link it in one click.
    """
    try:
        limit = max(1, int(limit))
    except (TypeError, ValueError):
        limit = 200
    results: dict[tuple, dict] = {}
    matched_ids = _matched_source_track_ids(db, profile_id)

    # 1) Wishlist tracks
    try:
        with db._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    spotify_track_id AS track_id,
                    json_extract(spotify_data, '$.name') AS title,
                    json_extract(spotify_data, '$.artists[0].name') AS artist,
                    json_extract(spotify_data, '$.album.name')      AS album,
                    date_added AS added_at
                FROM wishlist_tracks
                WHERE profile_id = ?
                ORDER BY date_added DESC, id DESC
            """, (profile_id,))
            wishlist_rows = [dict(r) for r in cursor.fetchall()]
    except Exception as exc:
        logger.debug("unmatched worklist wishlist query failed: %s", exc)
        wishlist_rows = []
    ext_lookup = getattr(db, "api_get_track_by_external_id", None)
    for r in wishlist_rows:
        tid = r.get("track_id") or ""
        if not tid:
            continue
        key = ("spotify", tid)
        if key in results or tid in matched_ids:
            continue
        # Already in the library under its external id: nothing to link.
        try:
            if ext_lookup is not None and ext_lookup(tid):
                continue
        except Exception as exc:  # noqa: BLE001 - one bad lookup must not abort the list
            logger.debug("unmatched worklist external-id lookup failed: %s", exc)
        results[key] = {
            "source": "spotify",
            "source_track_id": tid,
            "title": r["title"] or "",
            "artist": r["artist"] or "",
            "album": r["album"] or "",
            "context": "Wishlist",
            "added_at": r["added_at"] or "",
        }

    # 2) Mirrored playlist tracks
    try:
        playlists = db.get_mirrored_playlists(profile_id=profile_id) or []
    except Exception as exc:  # noqa: BLE001
        logger.debug("unmatched worklist playlist list failed: %s", exc)
        playlists = []
    for pl in playlists:
        if not isinstance(pl, dict):
            continue
        pid = pl.get("id")
        if pid is None:
            continue
        pl_name = pl.get("name") or ""
        pl_source = (pl.get("source") or "spotify").strip() or "spotify"
        pl_updated = pl.get("updated_at") or ""
        try:
            tracks = db.get_mirrored_playlist_tracks(pid) or []
        except Exception as exc:  # noqa: BLE001
            logger.debug("unmatched worklist tracks failed for playlist %s: %s", pid, exc)
            continue
        for track in tracks:
            if not isinstance(track, dict):
                continue
            sid = str(track.get("source_track_id") or "")
            if not sid or sid in matched_ids:
                continue
            if _mirrored_extra_in_library(track):
                continue
            key = (pl_source, sid)
            if key in results:
                continue
            results[key] = {
                "source": pl_source,
                "source_track_id": sid,
                "title": track.get("track_name") or "",
                "artist": track.get("artist_name") or "",
                "album": track.get("album_name") or "",
                "context": pl_name,
                "added_at": pl_updated,
            }

    # Sort by recency and cap
    sorted_results = sorted(results.values(), key=lambda r: r.get("added_at", ""), reverse=True)
    return sorted_results[:limit]


def search_library_candidates(db, query: str, limit: int = 15) -> list[dict[str, Any]]:
    """Search library tracks using the existing api_search_tracks method."""
    if not query or not query.strip():
        return []
    q = query.strip()
    # Pass the full query as title (covers most single-field searches).
    # Also try as artist in parallel and merge, deduped by track id.
    title_rows = db.api_search_tracks(title=q, limit=limit)
    artist_rows = db.api_search_tracks(artist=q, limit=limit)
    seen: set[int] = set()
    merged = []
    for row in title_rows + artist_rows:
        rid = row.get('id')
        if rid not in seen:
            seen.add(rid)
            merged.append(row)
        if len(merged) >= limit:
            break
    return merged


def _enrich_match(match_row: dict, db) -> dict:
    """Add library track details to a match row."""
    out = dict(match_row)
    lib_id = match_row.get("library_track_id")
    if lib_id:
        try:
            tracks = db.api_get_tracks_by_ids([lib_id])
            if tracks:
                t = tracks[0]
                out["library_title"] = t.get("title", "")
                out["library_artist"] = t.get("artist_name", "")
                out["library_album"] = t.get("album_title", "")
                out["library_file_path"] = t.get("file_path", "")
                out["library_bitrate"] = t.get("bitrate")
        except Exception as exc:
            logger.debug("enrich_match track lookup failed for id=%s: %s", lib_id, exc)
    return out
