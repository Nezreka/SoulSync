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


# a playlist's matching, kept a while: the same songs (by content, so an edit
# is new) for the same profile settings skip the lookups. on a big library a
# 1,500-song playlist is seconds of disk reads, every open and every play
_MATCH_TTL = 600.0
_MATCH_MAX = 32
_matches: Dict[Any, Any] = {}


def _forget_matches() -> None:
    _matches.clear()


def _matched(db: Any, rows: List[Dict[str, Any]], *, scope: Any, hide_explicit: bool, playlist_id: int):
    import time
    key = (str(getattr(db, "database_path", id(db))), str(scope), bool(hide_explicit),
           tuple((r.get("track_name"), r.get("artist_name"), r.get("album_name"),
                       r.get("extra_data") if isinstance(r.get("extra_data"), str) else json.dumps(
                           r.get("extra_data"), sort_keys=True, default=str)) for r in rows))
    from core.subsonic.library import Library
    with db._get_connection() as conn:
        # a new file in the library (a download landing) is a new answer
        newest = conn.execute("SELECT MAX(id) FROM lib2_track_files").fetchone()[0]
        key = key + (newest,)
        hit = _matches.get(key)
        if hit and time.monotonic() - hit[0] < _MATCH_TTL:
            return hit[1]
        lib = Library(conn, db_key=str(getattr(db, "database_path", id(db))), scope=scope,
                      hide_explicit=hide_explicit)
        from core.subsonic.library import stamped_tracks
        ids: List[Optional[int]] = stamped_tracks(conn, rows)
        # the rest by name, in a few batched lookups
        todo = [n for n, tid in enumerate(ids) if tid is None]
        try:
            found = lib.find_tracks([(rows[n].get("track_name") or "", rows[n].get("artist_name") or "",
                                      rows[n].get("album_name") or "") for n in todo])
        except Exception:  # noqa: BLE001 - unmatched songs never sink the list
            logger.warning("playlist %s: matching songs by name failed", playlist_id, exc_info=True)
            found = [None] * len(todo)
        for n, tid in zip(todo, found, strict=True):
            ids[n] = tid
        ok = playable_ids(conn, ids, scope=scope, hide_explicit=hide_explicit)
        # why a song can't play: hidden as explicit for this profile, or not
        # in the library. only asked when explicit songs are hidden
        explicit: Set[int] = set()
        if hide_explicit:
            open_lib = Library(conn, db_key=str(getattr(db, "database_path", id(db))), scope=scope,
                               hide_explicit=False)
            off = [n for n, tid in enumerate(ids) if tid is None or tid not in ok]
            try:
                again = open_lib.find_tracks([(rows[n].get("track_name") or "", rows[n].get("artist_name") or "",
                                               rows[n].get("album_name") or "") for n in off])
            except Exception:  # noqa: BLE001
                again = [None] * len(off)
            maybe = {n: ids[n] or tid for n, tid in zip(off, again, strict=True)}
            open_ok = playable_ids(conn, [t for t in maybe.values() if t], scope=scope, hide_explicit=False)
            explicit = {n for n, t in maybe.items() if t and t in open_ok}
        facts = _facts(conn, [i for i in ids if i in ok])
    out = (ids, ok, explicit, facts)
    if len(_matches) >= _MATCH_MAX:
        _matches.pop(next(iter(_matches)))
    _matches[key] = (time.monotonic(), out)
    return out


def detail(db: Any, profile_id: int, playlist_id: int, *, scope: Any,
           hide_explicit: bool) -> Optional[Dict[str, Any]]:
    """one of the profile's playlists with its songs, each with the library
    track it plays (or none) and whether this profile can play it. None:
    not this profile's playlist."""
    p = db.get_mirrored_playlist(int(playlist_id), profile_id=int(profile_id))
    if not p or p.get("is_request_list"):
        return None
    rows = db.get_mirrored_playlist_tracks(int(playlist_id), profile_id=int(profile_id)) or []
    ids, ok, explicit, facts = _matched(db, rows, scope=scope, hide_explicit=hide_explicit,
                                        playlist_id=playlist_id)
    songs = []
    total_ms = 0
    for n, (r, tid) in enumerate(zip(rows, ids, strict=True), start=1):
        playable = tid is not None and tid in ok
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
            "track_id": tid if playable else None,
            # why it can't play: "explicit" (hidden for this profile) or "missing"
            "unplayable": None if playable else ("explicit" if (n - 1) in explicit else "missing"),
        })
    out = summary(p)
    out.update({"songs": songs, "duration_ms": total_ms, "hides_explicit": bool(hide_explicit),
                "playable_count": sum(1 for s in songs if s["track_id"] is not None)})
    return out


def top_tracks(conn, artist_id: int, *, scope: Any, hide_explicit: bool,
               limit: int = 10) -> List[Dict[str, Any]]:
    """an artist's most played songs this profile can play: by play count,
    then newest, so an artist nobody has played yet still shows their
    latest. songs on their own albums and the ones they're credited on"""
    from core.library2.sql_util import owned_sql
    from core.library_scope import library_scope
    from core.subsonic.library import _has_overrides, track_explicit_sql
    with library_scope(scope):
        hidden = f" AND NOT {track_explicit_sql('t', 'al', _has_overrides(conn))}" if hide_explicit else ""
        rows = conn.execute(
            f"SELECT t.id, t.title, COALESCE(NULLIF(t.track_artist, ''), ar.name), t.duration, "
            f"al.id, al.title, t.play_count, t.explicit FROM lib2_tracks t "
            f"JOIN lib2_albums al ON al.id = t.album_id "
            f"LEFT JOIN lib2_artists ar ON ar.id = al.primary_artist_id "
            # their own albums, and the songs they're credited on (track credits,
            # indexed). a LIKE on the track artist string scanned every track
            # and matched "U2" inside other names
            f"WHERE (al.primary_artist_id = ? OR t.id IN "
            f"(SELECT ta.track_id FROM lib2_track_artists ta WHERE ta.artist_id = ?)) "
            f"AND {owned_sql('track', 't')}{hidden} "
            f"ORDER BY t.play_count DESC, COALESCE(al.year, 0) DESC, t.id DESC LIMIT ?",
            (int(artist_id), int(artist_id), max(1, min(int(limit), 50)) * 3)).fetchall()
    seen, out = set(), []
    for r in rows:
        # the same song on two releases (an album and its deluxe) shows once, its most played copy
        key = " ".join(str(r[1]).casefold().split())
        if key in seen:
            continue
        seen.add(key)
        out.append({"track_id": int(r[0]), "title": r[1], "artist": r[2], "duration_ms": int(r[3] or 0),
                    "album_id": r[4], "album": r[5], "plays": int(r[6] or 0), "explicit": bool(r[7])})
    return out[:max(1, min(int(limit), 50))]


