"""Resolve an album's canonical tracklist so missing tracks show real titles.

Lidarr shows the full tracklist of an album (from metadata) and marks which tracks
are present vs missing. We fetch the canonical tracklist from a metadata provider
(Spotify by id, else Deezer by search — both reusing SoulSync's existing clients)
and cache it on ``lib2_albums.tracklist_json``. The read path (``queries.get_album``)
then fills missing-track placeholders with the real title instead of "Track N".

Resolution is best-effort and never raises — when no provider yields a tracklist,
the UI falls back to numbered missing slots.
"""

from __future__ import annotations

import json
from typing import Any, List, Optional

from utils.logging_config import get_logger

logger = get_logger("library2.completeness")


def _extract_tracks(payload: Any) -> List[dict]:
    """Pull ``[{track_number, title}]`` out of a provider get_album_tracks payload,
    tolerant of the various container shapes (items / tracks / data)."""
    if not payload:
        return []
    items: Optional[list] = None
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        for key in ("items", "tracks", "data"):
            v = payload.get(key)
            if isinstance(v, dict):
                v = v.get("items") or v.get("data")
            if isinstance(v, list):
                items = v
                break
    out: List[dict] = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        title = it.get("name") or it.get("title")
        num = it.get("track_number") or it.get("track_position") or it.get("position")
        if title:
            out.append({"track_number": int(num) if num else None, "title": str(title)})
    return out


def resolve_tracklist(config_manager, conn, album_id: int) -> Optional[List[dict]]:
    """Return + cache the album's canonical tracklist. None when unavailable."""
    al = conn.execute(
        "SELECT title, spotify_id, primary_artist_id, tracklist_json FROM lib2_albums WHERE id=?",
        (album_id,),
    ).fetchone()
    if not al:
        return None
    if al["tracklist_json"]:
        try:
            cached = json.loads(al["tracklist_json"])
            if cached:
                return cached
        except (ValueError, TypeError):
            pass

    artist = conn.execute(
        "SELECT name FROM lib2_artists WHERE id=?", (al["primary_artist_id"],)
    ).fetchone()
    artist_name = artist["name"] if artist else ""
    tracks: List[dict] = []

    # 1) Spotify by stored album id (works when Spotify is authenticated).
    if al["spotify_id"]:
        try:
            from core.metadata.registry import get_spotify_client
            sp = get_spotify_client()
            if sp:
                tracks = _extract_tracks(sp.get_album_tracks(al["spotify_id"]))
        except Exception as e:  # noqa: BLE001
            logger.debug("spotify tracklist failed (%s): %s", album_id, e)

    # 2) Deezer by search (free, no auth) as a fallback.
    if not tracks and artist_name and al["title"]:
        try:
            from core.metadata.registry import get_deezer_client
            dz = get_deezer_client()
            if dz:
                album = dz.search_album(artist_name, al["title"])
                aid = album.get("id") if isinstance(album, dict) else None
                if aid:
                    tracks = _extract_tracks(dz.get_album_tracks(str(aid)))
        except Exception as e:  # noqa: BLE001
            logger.debug("deezer tracklist failed (%s): %s", album_id, e)

    if tracks:
        try:
            conn.execute(
                "UPDATE lib2_albums SET tracklist_json=? WHERE id=?",
                (json.dumps(tracks), album_id),
            )
            conn.commit()
        except Exception as e:  # noqa: BLE001
            logger.debug("tracklist cache write failed (%s): %s", album_id, e)
    return tracks or None


def precache_tracklists(database, config_manager, *, progress=None) -> int:
    """Resolve tracklists for every partial album (expected > present). Background."""
    resolved = 0
    try:
        conn = database._get_connection()
    except Exception:  # noqa: BLE001
        return 0
    try:
        rows = conn.execute(
            """SELECT al.id FROM lib2_albums al
               WHERE al.tracklist_json IS NULL
                 AND al.expected_track_count >
                     (SELECT COUNT(*) FROM lib2_tracks t WHERE t.album_id = al.id)"""
        ).fetchall()
        for i, r in enumerate(rows):
            if resolve_tracklist(config_manager, conn, r[0]):
                resolved += 1
            if progress and i % 20 == 0:
                progress("tracklists", i, len(rows))
    except Exception as e:  # noqa: BLE001
        logger.debug("tracklist precache error: %s", e)
    finally:
        conn.close()
    logger.info("Library v2 tracklist precache: %d resolved", resolved)
    return resolved


__all__ = ["resolve_tracklist", "precache_tracklists"]
