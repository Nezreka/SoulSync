"""what's playing -> the soulsync page for it, and which tracks are really
audiobook chapters or podcast episodes.

links (``session.link``):
  movie      {kind: 'movie', id, source: 'library'|'tmdb'}
  episode    {kind: 'show', id, source, season, episode}
  track      {kind: 'music', id: artist_id, artist_id, album_id, track_id, source: 'library'}
  audiobook  {kind: 'audiobook', id: asin, source: 'library'}
a hit is cached 10 min and a miss 2 min, so a poll asks the dbs about new
items only.
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any, Dict, List, Optional

import core.server_activity as sa

logger = sa.logger

_HIT_TTL, _MISS_TTL = 600, 120
_cache: Dict[tuple, tuple] = {}
_lock = threading.Lock()
_books: Dict[str, Any] = {"at": 0.0, "by_tail": {}}

_BOOK_SECTION = re.compile(r"audio\s*-?books?|h[oö]rb[uü]ch|livres?\s+audio|audiolibr", re.I)
_PODCAST_SECTION = re.compile(r"podcast", re.I)


def reset() -> None:
    with _lock:
        _cache.clear()
        _books.update(at=0.0, by_tail={})


def _segments(path: str) -> List[str]:
    return [p for p in re.split(r"[\\/]+", str(path or "").strip().lower()) if p]


def _tail(path: str, n: int = 2) -> str:
    return "/".join(_segments(path)[-n:])


def _root_names() -> Dict[str, str]:
    """the last folder name of soulsync's audiobook and podcast roots."""
    out = {}
    try:
        from core.settings import config_manager
        for kind, key in (("audiobook", "audiobooks.download_path"), ("podcast", "podcasts.download_path")):
            seg = _segments(config_manager.get(key, "") or "")
            if seg and seg[-1] not in (".", ".."):
                out[kind] = seg[-1]
    except Exception:   # noqa: BLE001
        logger.debug("audio roots unavailable", exc_info=True)
    return out


def _book_index() -> Dict[str, Dict[str, Any]]:
    """book folder tail -> {asin, title, author, narrator}. 5 min cache; only
    when this install has used audiobooks at all."""
    now = time.time()
    with _lock:
        if now - _books["at"] < 300:
            return _books["by_tail"]
    by_tail: Dict[str, Dict[str, Any]] = {}
    try:
        from core.audiobook_database import get_audiobook_db, subsystem_in_use
        if subsystem_in_use():
            for row in get_audiobook_db().get_library():
                meta = {"asin": row.get("asin"), "title": row.get("title") or "",
                        "author": row.get("author") or "", "narrator": row.get("narrator") or ""}
                if row.get("path"):
                    by_tail[_tail(row["path"])] = meta
    except Exception:   # noqa: BLE001
        logger.debug("audiobook index failed", exc_info=True)
    with _lock:
        _books.update(at=now, by_tail=by_tail)
    return by_tail


def book_meta(file_path: str) -> Optional[Dict[str, Any]]:
    """the soulsync audiobook a file belongs to, matched on its folder (server
    and soulsync mount the same folder under different roots)."""
    seg = _segments(file_path)
    if len(seg) < 2:
        return None
    idx = _book_index()
    if not idx:
        return None
    # the file's folder, then one up (books split into cd folders)
    for cut in (1, 2):
        if len(seg) > cut:
            hit = idx.get("/".join(seg[:-cut][-2:]))
            if hit:
                return hit
    return None


def audio_kind(section: str, file_path: str) -> Optional[str]:
    """'audiobook', 'podcast' or None for a server track."""
    if section and _PODCAST_SECTION.search(section):
        return "podcast"
    if section and _BOOK_SECTION.search(section):
        return "audiobook"
    seg = _segments(file_path)
    if seg:
        roots = _root_names()
        if roots.get("podcast") and roots["podcast"] in seg[:-1]:
            return "podcast"
        if roots.get("audiobook") and roots["audiobook"] in seg[:-1]:
            return "audiobook"
        if book_meta(file_path):
            return "audiobook"
    return None


# ── link resolution ─────────────────────────────────────────────────────────

def _cached(key: tuple, now: float):
    with _lock:
        hit = _cache.get(key)
    if hit and hit[0] > now:
        return True, hit[1]
    return False, None


def _put(key: tuple, link: Optional[Dict[str, Any]], now: float) -> None:
    with _lock:
        _cache[key] = (now + (_HIT_TTL if link else _MISS_TTL), link)
        if len(_cache) > 2000:
            for k in list(_cache)[:500]:
                _cache.pop(k, None)


def _video_links(items: List[Dict[str, Any]]) -> Dict[int, Optional[Dict[str, Any]]]:
    out: Dict[int, Optional[Dict[str, Any]]] = {}
    vdb = None
    try:
        from api.video import get_video_db
        vdb = get_video_db()
    except Exception:   # noqa: BLE001 - no video library, no links
        logger.debug("library link resolve: no video db", exc_info=True)
    by_sid: Dict[str, Dict[str, Any]] = {}
    if vdb is not None:
        sids = [it["sid"] for it in items if it["sid"]]
        if sids:
            try:
                for r in vdb.items_by_server_ids(sids):
                    by_sid.setdefault(str(r["server_id"]), r)
            except Exception:   # noqa: BLE001
                logger.debug("items_by_server_ids failed", exc_info=True)
    for it in items:
        want = it["want"]
        link = None
        r = by_sid.get(it["sid"])
        if r and r["kind"] == want:
            link = {"kind": want, "id": r["id"], "source": "library"}
        elif vdb is not None and it["title"]:
            try:
                rid = vdb.find_library_ref_by_title(want, it["title"], it["year"])
            except Exception:   # noqa: BLE001
                rid = None
            if rid:
                link = {"kind": want, "id": rid, "source": "library"}
        if link is None and it["tmdb"]:
            link = {"kind": want, "id": it["tmdb"], "source": "tmdb"}
        out[it["i"]] = link
    return out


def _music_links(server: str, keys: List[str]) -> Dict[str, Dict[str, Any]]:
    """server track ids -> lib2 ids, one query for the whole poll."""
    if not keys:
        return {}
    from core.server_activity import store
    marks = ", ".join("?" for _ in keys)
    out: Dict[str, Dict[str, Any]] = {}
    try:
        rows = store.query(
            "SELECT m.server_id AS sid, t.id AS track_id, t.album_id AS album_id, "
            "a.primary_artist_id AS artist_id FROM lib2_media_server_mappings m "
            "JOIN lib2_tracks t ON t.id = m.entity_id LEFT JOIN lib2_albums a ON a.id = t.album_id "
            f"WHERE m.entity_type = 'track' AND m.server_source = ? AND m.server_id IN ({marks})",
            [server, *keys])
        missing = [k for k in keys if k not in {str(r["sid"]) for r in rows}]
        if missing:
            marks2 = ", ".join("?" for _ in missing)
            rows += store.query(
                "SELECT t.server_id AS sid, t.id AS track_id, t.album_id AS album_id, "
                "a.primary_artist_id AS artist_id FROM lib2_tracks t LEFT JOIN lib2_albums a ON a.id = t.album_id "
                f"WHERE t.server_source = ? AND t.server_id IN ({marks2})", [server, *missing])
        for r in rows:
            out.setdefault(str(r["sid"]), {
                "kind": "music", "id": r["artist_id"], "artist_id": r["artist_id"], "album_id": r["album_id"],
                "track_id": r["track_id"], "source": "library"})
    except Exception:   # noqa: BLE001 - no catalogue yet means no music links
        logger.debug("music link resolve failed", exc_info=True)
    return out


def resolve(sessions: List[Dict[str, Any]], now: Optional[float] = None) -> None:
    """set ``link`` on each session. pops the private ``_link`` hints."""
    now = time.time() if now is None else now
    video: List[Dict[str, Any]] = []
    music: Dict[str, List[int]] = {}
    for i, s in enumerate(sessions):
        hint = s.pop("_link", None) or {}
        s.setdefault("link", None)
        kind = hint.get("kind") or s.get("kind")
        key = (hint.get("server"), kind, hint.get("rating_key"), hint.get("grandparent_key"))
        hit, link = _cached(key, now)
        if hit:
            s["link"] = dict(link) if link else None
            if link and link.get("kind") == "show" and s.get("show"):
                s["link"].update(season=s["show"].get("season"), episode=s["show"].get("episode"))
            continue
        s["_key"] = key
        if kind == "movie":
            video.append({"i": i, "want": "movie", "sid": hint.get("rating_key") or "",
                          "title": hint.get("title"), "year": hint.get("year"), "tmdb": hint.get("tmdb")})
        elif kind == "episode":
            video.append({"i": i, "want": "show", "sid": hint.get("grandparent_key") or "",
                          "title": hint.get("title"), "year": None, "tmdb": ""})
        elif kind == "track" and hint.get("rating_key"):
            music.setdefault(hint.get("server") or "", []).append(i)
        elif kind == "audiobook":
            asin = ((s.get("book") or {}).get("asin")) or s.pop("_asin", None)
            link = {"kind": "audiobook", "id": asin, "source": "library"} if asin else None
            s["link"] = link
            _put(key, link, now)
            s.pop("_key", None)
        else:
            _put(key, None, now)
            s.pop("_key", None)
    if video:
        for i, link in _video_links(video).items():
            sessions[i]["link"] = link
            _put(sessions[i].pop("_key"), link, now)
            if link and link["kind"] == "show" and sessions[i].get("show"):
                sessions[i]["link"] = dict(link, season=sessions[i]["show"].get("season"),
                                           episode=sessions[i]["show"].get("episode"))
    for server, idxs in music.items():
        keys = [sessions[i]["_key"][2] for i in idxs]
        found = _music_links(server, [str(k) for k in keys])
        for i in idxs:
            link = found.get(str(sessions[i]["_key"][2]))
            sessions[i]["link"] = link
            _put(sessions[i].pop("_key"), link, now)
    for s in sessions:
        s.pop("_key", None)
