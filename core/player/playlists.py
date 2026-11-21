"""music playlists in the player: the profile's own ("your playlists",
Liked Songs first) and the ones it mirrored from spotify, deezer and the
rest. one table holds both (mirrored_playlists; ``source='soulsync'`` is
one of yours).

a playlist row is a song by name. to play it the player needs the library
track: the id identify stamped on the row (``library_track_id``), else the
library's own match by title, artist and album (the same lookup subsonic
apps use, so every way of playing agrees). each song then says whether
this profile can play it (core.connect.may_play rules, in one query per
batch): a song you don't have, or one hidden from this profile, shows
greyed out instead of failing when it comes up.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional, Set

from utils.logging_config import get_logger

logger = get_logger("player.playlists")

BATCH = 500


def _extra(row: Dict[str, Any]) -> Dict[str, Any]:
    raw = row.get("extra_data")
    if isinstance(raw, dict):
        return raw
    try:
        out = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
    return out if isinstance(out, dict) else {}


def _tiles(raw: Any) -> List[str]:
    from core.playlists.user_playlists import _cover_tiles
    return _cover_tiles(raw)


def summary(p: Dict[str, Any]) -> Dict[str, Any]:
    """a playlist on a shelf"""
    from core.playlists.naming import effective_mirrored_name
    from core.playlists.user_playlists import is_liked_playlist, is_user_playlist
    yours = is_user_playlist(p)
    return {
        "id": int(p["id"]),
        "name": effective_mirrored_name(p),
        "description": p.get("description") or None,
        "kind": "yours" if yours else "mirrored",
        "liked": is_liked_playlist(p),
        "source": p.get("source"),
        "owner": None if yours else (p.get("owner") or None),
        "track_count": int(p.get("track_count") or 0),
        "image_url": p.get("image_url") or None,
        "cover_tiles": _tiles(p.get("cover_tiles")),
        "updated_at": p.get("updated_at") or p.get("mirrored_at"),
    }


def list_for(db: Any, profile_id: int) -> Dict[str, List[Dict[str, Any]]]:
    """the profile's playlists: yours (Liked Songs first, newest edit next),
    then mirrored (newest first). request lists are a download tool, not
    something to listen to, so they stay out."""
    rows = db.get_mirrored_playlists(profile_id=int(profile_id), is_request_list=False,
                                     backfill_covers=False) or []
    shelf = [summary(p) for p in rows]
    yours = sorted((p for p in shelf if p["kind"] == "yours"),
                   key=lambda p: str(p["updated_at"] or ""), reverse=True)
    yours.sort(key=lambda p: not p["liked"])
    mirrored = sorted((p for p in shelf if p["kind"] == "mirrored"),
                      key=lambda p: str(p["updated_at"] or ""), reverse=True)
    return {"yours": yours, "mirrored": mirrored}


def playable_ids(conn, ids: Iterable[int], *, scope: Any, hide_explicit: bool) -> Set[int]:
    """which of ``ids`` this profile may play: the may_play_track rule, one
    query per batch instead of one per song"""
    from core.library2.sql_util import owned_sql
    from core.library_scope import library_scope
    from core.subsonic.library import _has_overrides, track_explicit_sql
    wanted = sorted({int(i) for i in ids if i is not None})
    out: Set[int] = set()
    if not wanted:
        return out
    with library_scope(scope):
        hidden = f" AND NOT {track_explicit_sql('t', 'al', _has_overrides(conn))}" if hide_explicit else ""
        owned = owned_sql("track", "t")
        for i in range(0, len(wanted), BATCH):
            chunk = wanted[i:i + BATCH]
            marks = ",".join("?" * len(chunk))
            rows = conn.execute(
                f"SELECT t.id FROM lib2_tracks t JOIN lib2_albums al ON al.id = t.album_id "
                f"WHERE t.id IN ({marks}) AND {owned}{hidden}", chunk).fetchall()
            out.update(int(r[0]) for r in rows)
    return out


def _facts(conn, ids: Iterable[int]) -> Dict[int, Dict[str, Any]]:
    """title, artist, album and length of library tracks, by id"""
    wanted = sorted({int(i) for i in ids if i is not None})
    out: Dict[int, Dict[str, Any]] = {}
    for i in range(0, len(wanted), BATCH):
        chunk = wanted[i:i + BATCH]
        marks = ",".join("?" * len(chunk))
        for r in conn.execute(
                f"SELECT t.id, t.title, t.track_artist, t.duration, al.id, al.title, ar.id, ar.name "
                f"FROM lib2_tracks t JOIN lib2_albums al ON al.id = t.album_id "
                f"LEFT JOIN lib2_artists ar ON ar.id = al.primary_artist_id "
                f"WHERE t.id IN ({marks})", chunk).fetchall():
            out[int(r[0])] = {"title": r[1], "artist": r[2] or r[7], "duration_ms": int(r[3] or 0),
                              "album_id": r[4], "album": r[5], "artist_id": r[6]}
    return out


def _stamped_id(row: Dict[str, Any]) -> Optional[int]:
    extra = _extra(row)
    if extra.get("library_track_id") and (extra.get("library_track_id_kind") or "lib2") == "lib2":
        try:
            return int(extra["library_track_id"])
        except (TypeError, ValueError):
            return None
    return None


def detail(db: Any, profile_id: int, playlist_id: int, *, scope: Any,
           hide_explicit: bool) -> Optional[Dict[str, Any]]:
    """one of the profile's playlists with its songs, each with the library
    track it plays (or none) and whether this profile can play it. None:
    not this profile's playlist."""
    p = db.get_mirrored_playlist(int(playlist_id), profile_id=int(profile_id))
    if not p or p.get("is_request_list"):
        return None
    rows = db.get_mirrored_playlist_tracks(int(playlist_id), profile_id=int(profile_id)) or []
    from core.subsonic.library import Library
    with db._get_connection() as conn:
        lib = Library(conn, db_key=str(getattr(db, "database_path", id(db))), scope=scope,
                      hide_explicit=hide_explicit)
        ids: List[Optional[int]] = []
        for r in rows:
            tid = _stamped_id(r)
            if tid is None:
                try:
                    tid = lib.find_track(r.get("track_name") or "", r.get("artist_name") or "",
                                         r.get("album_name") or "")
                except Exception:  # noqa: BLE001 - one unmatched song never sinks the list
                    logger.debug("playlist %s: matching a row failed", playlist_id, exc_info=True)
                    tid = None
            ids.append(tid)
        ok = playable_ids(conn, ids, scope=scope, hide_explicit=hide_explicit)
        facts = _facts(conn, [i for i in ids if i in ok])
    songs = []
    total_ms = 0
    for n, (r, tid) in enumerate(zip(rows, ids), start=1):
        f = facts.get(tid) if tid in ok else None
        length = (f or {}).get("duration_ms") or int(r.get("duration_ms") or 0)
        total_ms += length
        songs.append({
            # 1-based, in order: what the edit routes (remove, reorder) take
            "position": n,
            "title": (f or {}).get("title") or r.get("track_name") or "",
            "artist": (f or {}).get("artist") or r.get("artist_name") or "",
            "album": (f or {}).get("album") or r.get("album_name") or "",
            "album_id": (f or {}).get("album_id"),
            "artist_id": (f or {}).get("artist_id"),
            "duration_ms": length,
            "image_url": r.get("image_url") or None,
            "added_at": r.get("added_at"),
            # playable: in this profile's library and not hidden from it
            "track_id": tid if tid in ok else None,
        })
    out = summary(p)
    out.update({"songs": songs, "duration_ms": total_ms,
                "playable_count": sum(1 for s in songs if s["track_id"] is not None)})
    return out
