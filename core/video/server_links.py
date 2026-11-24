"""plex and jellyfin as one library.

the video library's rows belong to the video server (the one picked in
Settings). with both plex and jellyfin connected, and linking switched on,
soulsync also reads the other one and records, for every movie, show and
episode, which item there is the same title (``video_server_links``). on
that it keeps the two in step:

* **watched follows.** a movie or episode marked watched or unwatched on
  either server (or in soulsync) is marked the same on the other. each
  compare remembers what both sides said, so a change on one side is told
  from no change at all; when both changed, watched wins.
* **switching servers keeps the library.** making the other server the video
  server re-points every linked title at it: the rows, with their monitored
  flags, fix-matches, quality profiles and history, stay.

matching is by the file itself first (same file name and size on both
servers), then by tmdb / tvdb / imdb id, then for shows an exact title and
year when only one show has it. nothing is ever matched on a guess, and an
unmatched title is simply not linked (it's counted, and named in the result).

off by default (``video_link_servers``); nothing reads the table otherwise.
the shared server account's watch state is what is synced: a profile linked
to its own plex account (#1941) keeps its own, as before.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Any, Callable, Dict, Iterable, List, Optional

from utils.logging_config import get_logger

logger = get_logger("video_server_links")

SETTING = "video_link_servers"
_SERVERS = ("plex", "jellyfin")

_lock = threading.Lock()
_status: Dict[str, Any] = {"state": "idle"}


def enabled(db) -> bool:
    try:
        return str(db.get_setting(SETTING) or "") == "1"
    except Exception:   # noqa: BLE001
        return False


def other_server(db, active: Optional[str] = None) -> Optional[str]:
    """the connected media server that is NOT the video server, or None"""
    from core.video.sources import configured_video_servers, resolve_video_server
    active = active or resolve_video_server(db)
    if active not in _SERVERS:
        return None
    others = [s for s in configured_video_servers(db) if s != active]
    return others[0] if others else None


def _name(path) -> str:
    return str(path or "").replace("\\", "/").rsplit("/", 1)[-1].lower()


def _watched(n) -> bool:
    try:
        return int(n or 0) > 0
    except (TypeError, ValueError):
        return False


# ── matching ────────────────────────────────────────────────────────────────

class _Index:
    """the library's rows for one server label, by everything a second
    server's item can be matched on. built once per pass."""

    def __init__(self, conn, label: str):
        from core.video.release_parse import normalize_title
        self._norm = normalize_title
        self.movie_by_file: Dict[tuple, int] = {}
        self.movie_by_id: Dict[tuple, List[int]] = {}
        self.show_by_id: Dict[tuple, List[int]] = {}
        self.show_by_title: Dict[tuple, List[int]] = {}
        self.episodes: Dict[tuple, int] = {}
        for r in conn.execute(
                "SELECT f.movie_id, f.relative_path, f.size_bytes FROM media_files f "
                "JOIN movies m ON m.id=f.movie_id WHERE m.server_source=?", (label,)):
            if r[2]:
                self.movie_by_file.setdefault((_name(r[1]), int(r[2])), r[0])
        for r in conn.execute("SELECT id, tmdb_id, imdb_id FROM movies WHERE server_source=? "
                              "ORDER BY has_file DESC, id", (label,)):
            for col, v in (("tmdb", r[1]), ("imdb", r[2])):
                if v:
                    self.movie_by_id.setdefault((col, str(v)), []).append(r[0])
        for r in conn.execute("SELECT id, tvdb_id, tmdb_id, imdb_id, title, year FROM shows "
                              "WHERE server_source=? ORDER BY id", (label,)):
            for col, v in (("tvdb", r[1]), ("tmdb", r[2]), ("imdb", r[3])):
                if v:
                    self.show_by_id.setdefault((col, str(v)), []).append(r[0])
            self.show_by_title.setdefault((self._norm(r[4]), r[5]), []).append(r[0])
        for r in conn.execute(
                "SELECT e.id, e.show_id, e.season_number, e.episode_number FROM episodes e "
                "JOIN shows s ON s.id=e.show_id WHERE s.server_source=?", (label,)):
            self.episodes[(r[1], r[2], r[3])] = r[0]

    def movie(self, item: dict, taken: set) -> Optional[int]:
        for f in item.get("files") or ([item["file"]] if item.get("file") else []):
            if f and f.get("size_bytes"):
                hit = self.movie_by_file.get((_name(f.get("relative_path")), int(f["size_bytes"])))
                if hit is not None:
                    return hit
        for col, v in (("tmdb", item.get("tmdb_id")), ("imdb", item.get("imdb_id"))):
            # the same film in two libraries is two rows: each takes one item
            for rid in self.movie_by_id.get((col, str(v)), []) if v else []:
                if rid not in taken:
                    return rid
        return None

    def show(self, item: dict, taken: set) -> Optional[int]:
        for col, v in (("tvdb", item.get("tvdb_id")), ("tmdb", item.get("tmdb_id")),
                       ("imdb", item.get("imdb_id"))):
            for rid in self.show_by_id.get((col, str(v)), []) if v else []:
                if rid not in taken:
                    return rid
        if not (item.get("tvdb_id") or item.get("tmdb_id") or item.get("imdb_id")):
            same = [r for r in self.show_by_title.get((self._norm(item.get("title")), item.get("year")), [])
                    if r not in taken]
            if len(same) == 1 and item.get("year"):
                return same[0]
        return None


def _upsert(conn, kind: str, item_id: int, server: str, server_id, item: dict, now: str) -> None:
    conn.execute(
        "INSERT INTO video_server_links (kind, item_id, server_source, server_id, seen_at) "
        "VALUES (?, ?, ?, ?, ?) ON CONFLICT(kind, item_id, server_source) DO UPDATE SET "
        "server_id=excluded.server_id, seen_at=excluded.seen_at",
        (kind, int(item_id), server, str(server_id), now))


def link_pass(db, other, *, active: Optional[str] = None, incremental: bool = False,
              since=None, on_progress: Optional[Callable] = None) -> Dict[str, Any]:
    """read the other server and record which of its items are the library's
    titles. returns ``{server, movies, shows, episodes, unmatched: [titles],
    watch: {kind: [(item_id, their state)]}}``; ``watch`` is what
    :func:`sync_watch` compares. a full pass forgets links to items the other
    server no longer has; an incremental one only adds."""
    from core.video.sources import resolve_video_server
    active = active or resolve_video_server(db)
    server = other.server_name
    # to the microsecond: two passes in one second must still tell this
    # pass's links from the last one's
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
    out: Dict[str, Any] = {"server": server, "movies": 0, "shows": 0, "episodes": 0,
                           "unmatched": [], "unmatched_count": 0, "watch": {"movie": [], "episode": []}}

    def miss(title):
        out["unmatched_count"] += 1
        if len(out["unmatched"]) < 50:
            out["unmatched"].append(str(title or "?"))

    conn = db._get_connection()
    try:
        idx = _Index(conn, active)
        taken_m: set = set()
        taken_s: set = set()
        for item in other.iter_movies(incremental=incremental, since=since):
            rid = idx.movie(item, taken_m)
            if rid is None:
                miss(item.get("title"))
                continue
            taken_m.add(rid)
            _upsert(conn, "movie", rid, server, item["server_id"], item, now)
            out["movies"] += 1
            out["watch"]["movie"].append((rid, item.get("play_count"), item.get("last_viewed_at")))
            if on_progress and out["movies"] % 200 == 0:
                on_progress(out)
        conn.commit()
        for show in other.iter_shows(incremental=incremental, since=since):
            rid = idx.show(show, taken_s)
            if rid is None:
                miss(show.get("title"))
                continue
            taken_s.add(rid)
            _upsert(conn, "show", rid, server, show["server_id"], show, now)
            out["shows"] += 1
            for season in show.get("seasons") or []:
                for ep in season.get("episodes") or []:
                    eid = idx.episodes.get((rid, season.get("season_number"), ep.get("episode_number")))
                    if eid is None or not ep.get("server_id"):
                        continue
                    _upsert(conn, "episode", eid, server, ep["server_id"], ep, now)
                    out["episodes"] += 1
                    out["watch"]["episode"].append((eid, ep.get("play_count"), ep.get("last_viewed_at")))
            if on_progress and out["shows"] % 50 == 0:
                on_progress(out)
        if not incremental and (out["movies"] or out["shows"]):
            # what a full read didn't see is gone from that server. never on a
            # read that matched nothing (an unreachable or empty answer)
            conn.execute("DELETE FROM video_server_links WHERE server_source=? AND seen_at < ?",
                         (server, now))
        # a link whose library row is gone
        for kind, table in (("movie", "movies"), ("show", "shows"), ("episode", "episodes")):
            conn.execute(f"DELETE FROM video_server_links WHERE kind=? AND item_id NOT IN "
                         f"(SELECT id FROM {table})", (kind,))
        conn.commit()
    finally:
        conn.close()
    return out


# ── watched, in step ────────────────────────────────────────────────────────

def decide(ours_now: bool, theirs_now: bool, ours_then, theirs_then) -> bool:
    """what both sides should say. ``*_then`` is what each said at the last
    compare (None: never compared). a first compare takes watched from
    either; after that the side that changed wins, and when both changed (or
    neither did, yet they differ) watched wins. pure."""
    if ours_then is None or theirs_then is None:
        return ours_now or theirs_now
    ours_changed, theirs_changed = ours_now != bool(ours_then), theirs_now != bool(theirs_then)
    if ours_changed and not theirs_changed:
        return ours_now
    if theirs_changed and not ours_changed:
        return theirs_now
    return ours_now or theirs_now


def sync_watch(db, watch: Dict[str, list], other, *, primary=None) -> Dict[str, int]:
    """carry watched / unwatched across the two servers for the items a link
    pass just read. ``primary`` is the video server's source (its rows are
    the library's); a push that fails leaves the remembered state alone, so
    the next compare tries again. returns counts."""
    server = other.server_name
    out = {"to_library": 0, "to_other": 0, "failed": 0}
    conn = db._get_connection()
    try:
        for kind, table in (("movie", "movies"), ("episode", "episodes")):
            for item_id, their_count, _viewed in watch.get(kind) or []:
                row = conn.execute(
                    f"SELECT t.play_count, t.server_id, l.server_id, l.their_play_count, l.our_play_count "
                    f"FROM {table} t JOIN video_server_links l ON l.kind=? AND l.item_id=t.id "
                    "AND l.server_source=? WHERE t.id=?", (kind, server, item_id)).fetchone()
                if not row:
                    continue
                ours_now, theirs_now = _watched(row[0]), _watched(their_count)
                target = decide(ours_now, theirs_now, row[4], row[3])
                ok = True
                if ours_now != target:
                    ok = _set_ours(db, conn, kind, item_id, target, primary, row[1])
                    out["to_library" if ok else "failed"] += 1
                if ok and theirs_now != target:
                    ok = _push(other, row[2], target, kind)
                    out["to_other" if ok else "failed"] += 1
                if ok:
                    conn.execute(
                        "UPDATE video_server_links SET their_play_count=?, our_play_count=? "
                        "WHERE kind=? AND item_id=? AND server_source=?",
                        (1 if target else 0, 1 if target else 0, kind, item_id, server))
        conn.commit()
    finally:
        conn.close()
    return out


def _push(source, server_id, watched: bool, kind: str) -> bool:
    from core.video.owned_ids import is_owned
    if source is None or not server_id or is_owned(server_id) or not hasattr(source, "set_watched"):
        return source is None        # no server to tell is not a failure
    try:
        return bool(source.set_watched(str(server_id), watched, kind=kind).get("ok"))
    except Exception:   # noqa: BLE001 - tried again at the next compare
        logger.debug("watch push failed for %s %s", kind, server_id, exc_info=True)
        return False


def _set_ours(db, conn, kind: str, item_id: int, watched: bool, primary, primary_id) -> bool:
    """the library row, then the video server it came from (or the next scan
    of that server would put its old answer back)"""
    if not _push(primary, primary_id, watched, kind):
        return False
    table = "movies" if kind == "movie" else "episodes"
    conn.execute(
        f"UPDATE {table} SET play_count=CASE WHEN ? THEN MAX(COALESCE(play_count, 0), 1) ELSE 0 END, "
        "view_offset_ms=0 WHERE id=?", (1 if watched else 0, item_id))
    return True


def push_toggle(db, kind: str, item_id: int, watched: bool) -> int:
    """a watched tick made in soulsync goes to the linked server too, now
    (core/video/metadata.set_watched already told the video server). a show
    or season tick reaches each of its episodes at the next compare. returns
    how many servers were told."""
    if kind not in ("movie", "episode") or not enabled(db):
        return 0
    conn = db._get_connection()
    try:
        links = conn.execute("SELECT server_source, server_id FROM video_server_links "
                             "WHERE kind=? AND item_id=?", (kind, int(item_id))).fetchall()
    finally:
        conn.close()
    told = 0
    for server, server_id in links:
        from core.video.sources import source_for_server
        if _push(source_for_server(server), server_id, watched, kind):
            told += 1
            conn = db._get_connection()
            try:
                conn.execute(
                    "UPDATE video_server_links SET their_play_count=?, our_play_count=? "
                    "WHERE kind=? AND item_id=? AND server_source=?",
                    (1 if watched else 0, 1 if watched else 0, kind, int(item_id), server))
                conn.commit()
            finally:
                conn.close()
    return told


# ── switching servers ───────────────────────────────────────────────────────

def rekey_library(db, old: str, new: str) -> Dict[str, int]:
    """the other server becomes the video server: every linked title is
    re-pointed at it and keeps its row. the row's id on the old server is
    kept as its link back. a title with no link stays filed under the old
    server, as a switch always left it. a title the new server already has a
    row for (it was scanned as the video server before) is left alone."""
    out = {"movies": 0, "shows": 0, "episodes": 0, "skipped": 0}
    if old not in _SERVERS or new not in _SERVERS or old == new:
        return out
    conn = db._get_connection()
    try:
        for kind, table in (("movie", "movies"), ("show", "shows"), ("episode", "episodes")):
            rows = conn.execute(
                f"SELECT t.id, t.server_id, l.server_id FROM {table} t JOIN video_server_links l "
                "ON l.kind=? AND l.item_id=t.id AND l.server_source=? WHERE t.server_source=?",
                (kind, new, old)).fetchall()
            for rid, old_id, new_id in rows:
                if kind != "episode" and conn.execute(
                        f"SELECT 1 FROM {table} WHERE server_source=? AND server_id=?",
                        (new, new_id)).fetchone():
                    out["skipped"] += 1
                    continue
                conn.execute(f"UPDATE {table} SET server_source=?, server_id=? WHERE id=?",
                             (new, new_id, rid))
                conn.execute("DELETE FROM video_server_links WHERE kind=? AND item_id=? AND server_source=?",
                             (kind, rid, new))
                if old_id and not str(old_id).startswith("ss:"):
                    conn.execute(
                        "INSERT OR REPLACE INTO video_server_links (kind, item_id, server_source, server_id) "
                        "VALUES (?, ?, ?, ?)", (kind, rid, old, str(old_id)))
                out[table] += 1
        conn.commit()
    finally:
        conn.close()
    if out["movies"] or out["shows"]:
        logger.info("video: library moved from %s to %s (%d movies, %d shows, %d episodes kept their rows)",
                    old, new, out["movies"], out["shows"], out["episodes"])
    return out


# ── running it ──────────────────────────────────────────────────────────────

def counts(db) -> Dict[str, Any]:
    """how much of the library is on the other server too, for Settings"""
    from core.video.sources import resolve_video_server
    active = resolve_video_server(db)
    other = other_server(db, active)
    out: Dict[str, Any] = {"enabled": enabled(db), "server": active, "other": other,
                           "movies": 0, "movies_total": 0, "shows": 0, "shows_total": 0}
    if not other:
        return out
    conn = db._get_connection()
    try:
        for kind, table in (("movie", "movies"), ("show", "shows")):
            out[table + "_total"] = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE server_source=?", (active,)).fetchone()[0]
            out[table] = conn.execute(
                f"SELECT COUNT(*) FROM video_server_links l JOIN {table} t ON t.id=l.item_id "
                "WHERE l.kind=? AND l.server_source=? AND t.server_source=?",
                (kind, other, active)).fetchone()[0]
    finally:
        conn.close()
    return out


def status() -> Dict[str, Any]:
    with _lock:
        return dict(_status)


def run(db, *, incremental: bool = False, since=None, source_for: Optional[Callable] = None,
        primary_source: Optional[Callable] = None) -> Dict[str, Any]:
    """one link pass and watch compare, when linking is on and a second
    server is connected. called after a library scan and by Link now. never
    raises; returns the result (``skipped`` says why nothing ran)."""
    global _status
    if not enabled(db):
        return {"skipped": "off"}
    other_name = other_server(db)
    if not other_name:
        return {"skipped": "one server"}
    with _lock:
        if _status.get("state") == "running":
            return {"skipped": "running"}
        _status = {"state": "running", "server": other_name, "started_at": time.time()}
    try:
        from core.video.sources import get_active_video_source, source_for_server
        other = (source_for or source_for_server)(other_name)
        if other is None:
            raise RuntimeError("Couldn't reach %s" % other_name.capitalize())
        res = link_pass(db, other, incremental=incremental, since=since)
        watch = res.pop("watch")
        primary = (primary_source or get_active_video_source)()
        res["watched"] = sync_watch(db, watch, other, primary=primary)
        with _lock:
            _status = {"state": "done", "finished_at": time.time(), **res}
        logger.info("video link pass (%s): %d movies, %d shows, %d episodes linked, %d not matched; "
                    "watched: %s", other_name, res["movies"], res["shows"], res["episodes"],
                    res["unmatched_count"], res["watched"])
        return res
    except Exception as e:   # noqa: BLE001 - linking never fails a scan
        logger.exception("video link pass failed")
        with _lock:
            _status = {"state": "error", "error": str(e), "finished_at": time.time()}
        return {"error": str(e)}


def run_async(db, **kw) -> bool:
    if not enabled(db) or status().get("state") == "running":
        return False
    threading.Thread(target=run, args=(db,), kwargs=kw, name="video-server-links", daemon=True).start()
    return True


__all__: Iterable[str] = (
    "SETTING", "enabled", "other_server", "link_pass", "decide", "sync_watch", "push_toggle",
    "rekey_library", "counts", "status", "run", "run_async",
)
