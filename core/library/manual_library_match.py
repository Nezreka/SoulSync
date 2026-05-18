"""Manual library match service.

Lets users explicitly link a source track (wishlist/sync-history candidate) to
an existing library track so SoulSync stops trying to re-download it.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from utils.logging_config import get_logger

logger = get_logger("library.manual_library_match")


def save_match(
    db,
    profile_id: int,
    source: str,
    source_track_id: str,
    library_track_id: int,
    **meta,
) -> bool:
    """Save (insert or replace) a manual match."""
    return db.save_manual_library_match(
        profile_id, source, source_track_id, library_track_id, **meta
    )


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


def delete_match(db, match_id: int, profile_id: int) -> bool:
    """Delete match by PK id, scoped to profile."""
    return db.delete_manual_library_match(match_id, profile_id)


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
