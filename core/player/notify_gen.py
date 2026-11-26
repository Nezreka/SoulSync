"""what happened since the last look, turned into notifications.

runs from the automation engine every few minutes (player_notify_library).
each source keeps a high-water mark in player.db and reads only what is past
it, on indexed columns; nothing here calls a provider. the first run of a
source only sets its mark, so an upgrade never floods anyone with the whole
library.

  * media_files (video db): a new file is a movie or an episode arriving.
    from it come new_episode (one note per show per day, merged),
    season_premiere (an E01 past season 1), watchlist_available and
    featured_movie (well-rated, at most 3 a week)
  * episodes airing soon: season_premiere ahead of the day, once a season
  * shelf life findings: leaving_soon, for titles a profile started
  * lib2_albums (music db): new_music, for artists a profile plays a lot
  * home videos on this day: home_memories, once a day at the chosen time

who may see a title is the profile's own view: its rating caps, the kinds
it may see, and the libraries hidden from it (api/video/kids.py).
"""

from __future__ import annotations

import datetime as _dt
import json
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Set

from core.player import notifications as N
from utils.logging_config import get_logger

logger = get_logger("player.notify_gen")

MARKS = "player_notification_marks"
ARRIVED = "player_notification_arrived"

WATCHING_DAYS = 120
FRESH_DAYS = 14           # a file for a title older than this is an upgrade, not news
FEATURED_PER_WEEK = 3
PREMIERE_AHEAD_DAYS = 3
BATCH = 2000
LISTENED_DAYS = 90
LISTENED_PLAYS = 10
EVERY_HOUR = 3600
EVERY_6H = 6 * 3600


# ── marks ────────────────────────────────────────────────────────────────

def _ensure(db: Any) -> None:
    N.ensure_schema(db)
    with db._get_connection() as conn:
        conn.execute(f"CREATE TABLE IF NOT EXISTS {MARKS} (source TEXT PRIMARY KEY, value REAL NOT NULL, "
                     "updated_at REAL NOT NULL)")
        conn.execute(f"CREATE TABLE IF NOT EXISTS {ARRIVED} (kind TEXT NOT NULL, item_id INTEGER NOT NULL, "
                     "at REAL NOT NULL, PRIMARY KEY (kind, item_id))")
        conn.commit()


def get_mark(db: Any, source: str) -> Optional[float]:
    with db._get_connection() as conn:
        row = conn.execute(f"SELECT value FROM {MARKS} WHERE source = ?", (source,)).fetchone()
    return None if row is None else row[0]


def set_mark(db: Any, source: str, value: float, now: float) -> None:
    with db._get_connection() as conn:
        conn.execute(f"INSERT INTO {MARKS} (source, value, updated_at) VALUES (?, ?, ?) "
                     "ON CONFLICT(source) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                     (source, value, now))
        conn.commit()


def _due(db: Any, source: str, every: float, now: float) -> bool:
    """a timed source's turn: once per ``every`` seconds"""
    last = get_mark(db, source)
    if last is not None and now - last < every:
        return False
    set_mark(db, source, now, now)
    return True


# ── the world, as the generator sees it ──────────────────────────────────

class Ctx:
    """everything a run reads. tests hand in their own pieces"""

    def __init__(self, player_db: Any, video_db: Any = None, music_db: Any = None, *,
                 profiles: Optional[List[Dict[str, Any]]] = None,
                 allow: Optional[Callable[[Dict[str, Any], str, Iterable[int]], Set[int]]] = None,
                 music_ok: Optional[Callable[[Dict[str, Any]], bool]] = None,
                 home_ok: Optional[Callable[[Dict[str, Any]], bool]] = None,
                 unowned_ok: Optional[Callable[[Dict[str, Any]], bool]] = None,
                 released: Optional[Callable[[], List[Dict[str, Any]]]] = None,
                 now: Optional[float] = None):
        self.pdb, self.vdb, self.mdb = player_db, video_db, music_db
        self.now = time.time() if now is None else now
        self._profiles = profiles
        self.allow = allow or _real_allow
        self.music_ok = music_ok or (lambda p: _perm(p, "music.access"))
        self.unowned_ok = unowned_ok or _may_see_unowned_movie
        self.home_ok = home_ok or (lambda p: _perm(p, "movies.access") or _perm(p, "tv.access"))
        self.released = released or _real_released
        self._prefs: Optional[Dict[int, Dict[str, Any]]] = None

    @property
    def profiles(self) -> List[Dict[str, Any]]:
        if self._profiles is None:
            self._profiles = _real_profiles()
        return self._profiles

    def prefs(self, pid: int) -> Dict[str, Any]:
        if self._prefs is None:
            self._prefs = N.all_prefs(self.pdb)
        return self._prefs.get(int(pid)) or N.defaults()

    def wanting(self, kind: str) -> List[Dict[str, Any]]:
        return [p for p in self.profiles if N.wants(self.prefs(p["id"]), kind)]


def _real_profiles() -> List[Dict[str, Any]]:
    from core.content_filter import _load_profile
    from database.music_database import get_database
    out = []
    for p in get_database().get_all_profiles():
        try:
            row = _load_profile(int(p["id"])) or p
        except Exception:  # noqa: BLE001 - an unreadable profile hears nothing
            logger.debug("notify: profile %s unreadable", p.get("id"), exc_info=True)
            continue
        if row.get("disabled"):
            continue
        out.append({**row, "id": int(p["id"]), "is_admin": bool(p.get("is_admin"))})
    return out


def _cap(profile: Dict[str, Any]):
    if "_cap" not in profile:
        from api.video.kids import _with_hidden_libraries, feed_cap
        cap, sees = feed_cap(profile)
        profile["_cap"] = (_with_hidden_libraries(cap, {"is_admin": profile.get("is_admin"),
                                                         "profile_id": profile["id"]}), sees)
    return profile["_cap"]


def _real_allow(profile: Dict[str, Any], kind: str, ids: Iterable[int]) -> Set[int]:
    """the titles of these the profile may see: its caps and hidden libraries"""
    from api.video import get_video_db
    from api.video.kids import filter_library_items
    cap, sees = _cap(profile)
    if not sees:
        return set()
    items = [{"id": int(i)} for i in ids]
    return {it["id"] for it in filter_library_items(get_video_db(), items, cap, kind)}


def _real_released() -> List[Dict[str, Any]]:
    """tmdb's now-playing list: one chart call, cached by the engine"""
    from core.video.enrichment.engine import get_video_enrichment_engine
    return get_video_enrichment_engine().discover_curated("now_playing") or []


def _may_see_unowned_movie(profile: Dict[str, Any]) -> bool:
    """a film not in the library has no library rating to check: only a
    profile without a movie rating cap (and that sees movies) hears of one"""
    from api.video.kids import kind_visible, rating_capped
    cap, sees = _cap(profile)
    return bool(sees) and kind_visible(cap, "movie") and not rating_capped(cap, "movie")


def _perm(profile: Dict[str, Any], perm: str) -> bool:
    if profile.get("is_admin"):
        return True
    if "_perms" not in profile:
        try:
            from core.video.quality_access import effective_for_row
            profile["_perms"] = effective_for_row(profile)
        except Exception:  # noqa: BLE001 - can't vouch: nothing
            profile["_perms"] = {}
    return bool(profile["_perms"].get(perm))


def _fresh(added_at: Any, now: float) -> bool:
    """added recently enough to be news (unknown: yes)"""
    if not added_at:
        return True
    day = str(added_at)[:10]
    cutoff = _dt.datetime.fromtimestamp(now - FRESH_DAYS * 86400, tz=_dt.timezone.utc).date().isoformat()
    return day >= cutoff


def _chunks(seq: List[Any], n: int = 500):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _marks(n: int) -> str:
    return ",".join("?" * n)


def _poster(kind: str, item_id: int) -> str:
    return f"/api/video/poster/{kind}/{int(item_id)}?w=342"


# ── what profiles watch ──────────────────────────────────────────────────

def _has(conn, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?", (table,)).fetchone() is not None


def _watching(ctx: Ctx, show_ids: Set[int]) -> Dict[int, Dict[int, float]]:
    """{profile: {show: the last time it played an episode}} for these shows"""
    out: Dict[int, Dict[int, float]] = {}
    if not show_ids:
        return out
    with ctx.pdb._get_connection() as conn:
        if not _has(conn, "watch_progress"):
            return out
        rows = conn.execute(
            "SELECT profile_id, item_id, updated_at, json_extract(meta_json, '$.parent_id') "
            "FROM watch_progress WHERE item_type = 'episode'").fetchall()
    unknown: Dict[int, List[Any]] = {}
    for pid, eid, at, parent in rows:
        try:
            sid = int(parent) if parent not in (None, "") else None
        except (TypeError, ValueError):
            sid = None
        if sid is None:
            try:
                unknown.setdefault(int(eid), []).append((pid, at))
            except (TypeError, ValueError):
                continue
            continue
        if sid in show_ids:
            mine = out.setdefault(int(pid), {})
            mine[sid] = max(mine.get(sid, 0.0), float(at or 0))
    if unknown and ctx.vdb is not None:
        conn = ctx.vdb._get_connection()
        try:
            for chunk in _chunks(list(unknown)):
                for eid, sid in conn.execute(
                        f"SELECT id, show_id FROM episodes WHERE id IN ({_marks(len(chunk))})", chunk):
                    if int(sid) in show_ids:
                        for pid, at in unknown.get(int(eid), []):
                            mine = out.setdefault(int(pid), {})
                            mine[int(sid)] = max(mine.get(int(sid), 0.0), float(at or 0))
        finally:
            conn.close()
    return out


def _watchlisted(ctx: Ctx, kind: str, ids: Iterable[int]) -> Dict[int, Set[int]]:
    """{profile: {ids on its watchlist}}"""
    wanted = [str(int(i)) for i in ids]
    out: Dict[int, Set[int]] = {}
    if not wanted:
        return out
    with ctx.pdb._get_connection() as conn:
        if not _has(conn, "player_watchlist"):
            return out
        for chunk in _chunks(wanted):
            for pid, iid in conn.execute(
                    f"SELECT profile_id, item_id FROM player_watchlist WHERE item_type = ? "
                    f"AND item_id IN ({_marks(len(chunk))})", [kind, *chunk]):
                out.setdefault(int(pid), set()).add(int(iid))
    return out


def _started(ctx: Ctx, kind: str, ids: Iterable[int]) -> Dict[int, Set[int]]:
    """{profile: {ids it has a place in, unfinished}}"""
    wanted = [str(int(i)) for i in ids]
    out: Dict[int, Set[int]] = {}
    if not wanted:
        return out
    with ctx.pdb._get_connection() as conn:
        if not _has(conn, "watch_progress"):
            return out
        for chunk in _chunks(wanted):
            for pid, iid, done in conn.execute(
                    f"SELECT profile_id, item_id, finished FROM watch_progress WHERE item_type = ? "
                    f"AND item_id IN ({_marks(len(chunk))})", [kind, *chunk]):
                if kind != "movie" or not done:
                    out.setdefault(int(pid), set()).add(int(iid))
    return out


# ── featured: a film worth a ping ────────────────────────────────────────

def notable(movie: Dict[str, Any]) -> bool:
    """a film people rate well, not a B movie. the stored tmdb rating has to
    be 7.0 or better, and the other scores enrichment found have to agree
    (imdb, rotten tomatoes, metacritic, critics). with no other score to
    lean on, a film has to clear a higher bar on tmdb alone."""
    try:
        tmdb = float(movie.get("rating") or 0)
    except (TypeError, ValueError):
        return False
    if tmdb < 7.0:
        return False
    checks = []
    for key, bar in (("imdb_rating", 6.8), ("rt_rating", 70), ("metacritic", 60), ("rating_critic", 65)):
        v = movie.get(key)
        if v not in (None, ""):
            try:
                checks.append(float(v) >= bar)
            except (TypeError, ValueError):
                continue
    if not checks:
        return tmdb >= 7.8
    return sum(checks) * 2 >= len(checks) and any(checks)


# ── new files: movies and episodes arriving ──────────────────────────────

def _new_files(ctx: Ctx, out: Dict[str, int]) -> None:
    if ctx.vdb is None:
        return
    mark = get_mark(ctx.pdb, "media_files")
    conn = ctx.vdb._get_connection()
    try:
        if mark is None:
            top = conn.execute("SELECT MAX(id) FROM media_files").fetchone()[0] or 0
            set_mark(ctx.pdb, "media_files", top, ctx.now)
            return
        rows = conn.execute("SELECT id, movie_id, episode_id FROM media_files WHERE id > ? "
                            "AND video_id IS NULL ORDER BY id LIMIT ?", (int(mark), BATCH)).fetchall()
        if not rows:
            return
        movie_ids = sorted({int(r[1]) for r in rows if r[1] is not None})
        ep_ids = sorted({int(r[2]) for r in rows if r[2] is not None})
        movies, episodes = {}, {}
        for chunk in _chunks(movie_ids):
            for r in conn.execute(
                    "SELECT id, title, year, rating, imdb_rating, rt_rating, metacritic, rating_critic, added_at "
                    f"FROM movies WHERE id IN ({_marks(len(chunk))})", chunk):
                movies[int(r[0])] = dict(r)
        for chunk in _chunks(ep_ids):
            for r in conn.execute(
                    "SELECT e.id, e.show_id, e.season_number, e.episode_number, e.title, e.added_at, "
                    "s.title AS show_title FROM episodes e JOIN shows s ON s.id = e.show_id "
                    f"WHERE e.id IN ({_marks(len(chunk))})", chunk):
                episodes[int(r[0])] = dict(r)
    finally:
        conn.close()
    with ctx.pdb._get_connection() as pconn:
        seen = set()
        for kind, ids in (("movie", movie_ids), ("episode", ep_ids)):
            for chunk in _chunks(ids):
                seen |= {(kind, int(r[0])) for r in pconn.execute(
                    f"SELECT item_id FROM {ARRIVED} WHERE kind = ? AND item_id IN ({_marks(len(chunk))})",
                    [kind, *chunk])}
        news_movies = [m for i, m in movies.items() if ("movie", i) not in seen and _fresh(m["added_at"], ctx.now)]
        news_eps = [e for i, e in episodes.items() if ("episode", i) not in seen and _fresh(e["added_at"], ctx.now)]
        pconn.executemany(f"INSERT OR IGNORE INTO {ARRIVED} (kind, item_id, at) VALUES (?, ?, ?)",
                          [("movie", i, ctx.now) for i in movies] + [("episode", i, ctx.now) for i in episodes])
        pconn.commit()
    if news_movies:
        _movies_arrived(ctx, news_movies, out)
    if news_eps:
        _episodes_arrived(ctx, news_eps, out)
    set_mark(ctx.pdb, "media_files", int(rows[-1][0]), ctx.now)


def _sent(out: Dict[str, int], kind: str, result: Optional[str]) -> None:
    if result:
        out[kind] = out.get(kind, 0) + 1


def _movies_arrived(ctx: Ctx, movies: List[Dict[str, Any]], out: Dict[str, int]) -> None:
    ids = [m["id"] for m in movies]
    wl = _watchlisted(ctx, "movie", ids)
    seen = _started(ctx, "movie", ids)
    featured = sorted((m for m in movies if notable(m)), key=lambda m: -float(m.get("rating") or 0))
    for p in ctx.profiles:
        pid, prefs = p["id"], ctx.prefs(p["id"])
        wants_wl, wants_ft = N.wants(prefs, "watchlist_available"), N.wants(prefs, "featured_movie")
        if not (wants_wl or wants_ft):
            continue
        visible = ctx.allow(p, "movie", ids)
        mine = wl.get(pid, set())
        told_wl = set()
        for m in movies:
            if m["id"] in mine and m["id"] in visible and wants_wl:
                name = f"{m['title']} ({m['year']})" if m.get("year") else m["title"]
                _sent(out, "watchlist_available", N.deliver(
                    ctx.pdb, pid, "watchlist_available", f"{name} is here",
                    "It was on your watchlist. Ready to play.", image=_poster("movie", m["id"]),
                    link=f"/player/movies/{m['id']}", group_key=f"watchlist:movie:{m['id']}",
                    prefs=prefs, now=ctx.now))
                told_wl.add(m["id"])
        if not wants_ft:
            continue
        room = FEATURED_PER_WEEK - N.count_since(ctx.pdb, pid, "featured_movie", ctx.now - 7 * 86400)
        for m in featured:
            if room <= 0:
                break
            if m["id"] in told_wl or m["id"] not in visible or m["id"] in seen.get(pid, set()):
                continue
            stars = f"{float(m['rating']):.1f}"
            name = f"{m['title']} ({m['year']})" if m.get("year") else m["title"]
            r = N.deliver(ctx.pdb, pid, "featured_movie", f"New film: {name}",
                          f"Rated {stars} on TMDB. Now in the library.", image=_poster("movie", m["id"]),
                          link=f"/player/movies/{m['id']}", group_key=f"featured:movie:{m['id']}",
                          prefs=prefs, now=ctx.now)
            if r:
                room -= 1
            _sent(out, "featured_movie", r)


def _ep_label(e: Dict[str, Any]) -> str:
    return f"S{int(e.get('season_number') or 0)} E{int(e.get('episode_number') or 0)}"


def _earlier(ctx: Ctx, pid: int, group_key: str) -> Dict[int, str]:
    """the episodes an earlier note today already named: {id: label}"""
    with ctx.pdb._get_connection() as conn:
        for table in (N.TABLE, N.PENDING):
            row = conn.execute(f"SELECT data_json FROM {table} WHERE profile_id = ? AND group_key = ?",  # noqa: S608
                               (pid, group_key)).fetchone()
            if row and row[0]:
                try:
                    data = json.loads(row[0])
                    return {int(i): str(lab) for i, lab in zip(data.get("episodes") or [], data.get("labels") or [], strict=False)}
                except (TypeError, ValueError, AttributeError):
                    return {}
    return {}


def _episodes_arrived(ctx: Ctx, eps: List[Dict[str, Any]], out: Dict[str, int]) -> None:
    by_show: Dict[int, List[Dict[str, Any]]] = {}
    for e in sorted(eps, key=lambda e: (e["season_number"] or 0, e["episode_number"] or 0)):
        by_show.setdefault(int(e["show_id"]), []).append(e)
    shows = set(by_show)
    watching = _watching(ctx, shows)
    wl = _watchlisted(ctx, "show", shows)
    recent = ctx.now - WATCHING_DAYS * 86400
    for p in ctx.profiles:
        pid, prefs = p["id"], ctx.prefs(p["id"])
        mine_w, mine_wl = watching.get(pid, {}), wl.get(pid, set())
        candidates = [s for s in shows if s in mine_w or s in mine_wl]
        if not candidates:
            continue
        visible = ctx.allow(p, "show", candidates)
        for sid in candidates:
            if sid not in visible:
                continue
            new = by_show[sid]
            name = new[0]["show_title"] or "A show"
            premieres = sorted({int(e["season_number"]) for e in new
                                if e.get("episode_number") == 1 and (e.get("season_number") or 0) >= 2})
            if premieres and sid in mine_w and N.wants(prefs, "season_premiere"):
                season = premieres[-1]
                first = next(e for e in new if e["season_number"] == season and e["episode_number"] == 1)
                _sent(out, "season_premiere", N.deliver(
                    ctx.pdb, pid, "season_premiere", f"Season {season} of {name} is here",
                    f"Episode 1{': ' + first['title'] if first.get('title') else ''} is ready to play.",
                    image=_poster("show", sid), link=f"/player/tv/{sid}/episode/{first['id']}",
                    group_key=f"premiere:{sid}:{season}", prefs=prefs, now=ctx.now))
                continue
            if sid not in mine_w and sid in mine_wl:
                if N.wants(prefs, "watchlist_available") and not N.told(ctx.pdb, pid, f"watchlist:show:{sid}"):
                    _sent(out, "watchlist_available", N.deliver(
                        ctx.pdb, pid, "watchlist_available", f"{name} is here",
                        "It was on your watchlist. Ready to play.", image=_poster("show", sid),
                        link=f"/player/tv/{sid}", group_key=f"watchlist:show:{sid}",
                        prefs=prefs, now=ctx.now))
                    continue
            elif mine_w.get(sid, 0) < recent and sid not in mine_wl:
                continue
            if not N.wants(prefs, "new_episode"):
                continue
            key = f"new_episode:{sid}:{N.local_day(prefs, ctx.now)}"
            named = _earlier(ctx, pid, key)
            named.update({e["id"]: _ep_label(e) for e in new})
            ids, labels = list(named), list(named.values())
            if len(ids) == 1:
                e = new[0]
                title = f"New episode of {name}"
                body = f"{_ep_label(e)}{': ' + e['title'] if e.get('title') else ''}"
                link = f"/player/tv/{sid}/episode/{e['id']}"
            else:
                title = f"{len(ids)} new episodes of {name}"
                body = ", ".join(labels[:6]) + (" and more" if len(labels) > 6 else "")
                link = f"/player/tv/{sid}"
            _sent(out, "new_episode", N.deliver(
                ctx.pdb, pid, "new_episode", title, body, image=_poster("show", sid), link=link,
                data={"episodes": ids, "labels": labels}, group_key=key, replace=True, prefs=prefs, now=ctx.now))


# ── season premieres ahead of the day ────────────────────────────────────

def _premieres_soon(ctx: Ctx, out: Dict[str, int]) -> None:
    if ctx.vdb is None or not ctx.wanting("season_premiere"):
        return
    if not _due(ctx.pdb, "premieres_soon", EVERY_HOUR, ctx.now):
        return
    today = _dt.date.fromtimestamp(ctx.now)
    last = today + _dt.timedelta(days=PREMIERE_AHEAD_DAYS)
    conn = ctx.vdb._get_connection()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT e.id, e.show_id, e.season_number, e.air_date, s.title AS show_title FROM episodes e "
            "JOIN shows s ON s.id = e.show_id WHERE e.air_date >= ? AND e.air_date <= ? "
            "AND e.episode_number = 1 AND e.season_number >= 2 AND COALESCE(e.has_file, 0) = 0",
            (today.isoformat(), last.isoformat() + "T99"))]
    finally:
        conn.close()
    if not rows:
        return
    watching = _watching(ctx, {int(r["show_id"]) for r in rows})
    for p in ctx.wanting("season_premiere"):
        pid, prefs = p["id"], ctx.prefs(p["id"])
        mine = [r for r in rows if int(r["show_id"]) in watching.get(pid, {})]
        if not mine:
            continue
        visible = ctx.allow(p, "show", {int(r["show_id"]) for r in mine})
        for r in mine:
            sid, season = int(r["show_id"]), int(r["season_number"])
            if sid not in visible or N.told(ctx.pdb, pid, f"premiere:{sid}:{season}"):
                continue
            try:
                day = _dt.date.fromisoformat(str(r["air_date"])[:10])
            except ValueError:
                continue
            when = "today" if day == today else ("tomorrow" if day == today + _dt.timedelta(days=1)
                                                 else day.strftime("%A"))
            _sent(out, "season_premiere", N.deliver(
                ctx.pdb, pid, "season_premiere", f"Season {season} of {r['show_title']} starts {when}",
                "You watched the last one. It lands here as soon as it's out.", image=_poster("show", sid),
                link=f"/player/tv/{sid}", group_key=f"premiere_soon:{sid}:{season}", prefs=prefs, now=ctx.now))


# ── leaving soon ─────────────────────────────────────────────────────────

def _leaving_soon(ctx: Ctx, out: Dict[str, int], findings: Optional[List[Dict[str, Any]]] = None) -> None:
    if ctx.vdb is None or not ctx.wanting("leaving_soon"):
        return
    if findings is None:
        if not _due(ctx.pdb, "leaving_soon", EVERY_6H, ctx.now):
            return
        try:
            from core.video.repair import shelf_life
            if not shelf_life._config(ctx.vdb).get("enabled"):
                return
            findings = shelf_life._cleanup_findings(ctx.vdb)
        except Exception:  # noqa: BLE001 - no shelf life here: nothing leaving
            logger.debug("notify: shelf life unreadable", exc_info=True)
            return
    titles = []
    for f in findings or []:
        d = f.get("details") or {}
        try:
            titles.append((str(d.get("kind")), int(d["item_id"]), str(d.get("title") or "A title")))
        except (KeyError, TypeError, ValueError):
            continue
    if not titles:
        return
    movies = {i for k, i, _ in titles if k == "movie"}
    shows = {i for k, i, _ in titles if k == "show"}
    started = _started(ctx, "movie", movies)
    watching = _watching(ctx, shows)
    for p in ctx.wanting("leaving_soon"):
        pid, prefs = p["id"], ctx.prefs(p["id"])
        for kind, iid, name in titles:
            mine = iid in started.get(pid, set()) if kind == "movie" else iid in watching.get(pid, {})
            if not mine or iid not in ctx.allow(p, kind, [iid]):
                continue
            link = f"/player/movies/{iid}" if kind == "movie" else f"/player/tv/{iid}"
            _sent(out, "leaving_soon", N.deliver(
                ctx.pdb, pid, "leaving_soon", f"{name} may be leaving soon",
                "It's on the list to make room on the server. Finish it while you can.",
                image=_poster(kind, iid), link=link, group_key=f"leaving:{kind}:{iid}", prefs=prefs, now=ctx.now))


# ── new music ────────────────────────────────────────────────────────────

def _new_music(ctx: Ctx, out: Dict[str, int]) -> None:
    if ctx.mdb is None:
        return
    mark = get_mark(ctx.pdb, "lib2_albums")
    with ctx.mdb._get_connection() as conn:
        if not _has(conn, "lib2_albums"):
            return
        if mark is None:
            top = conn.execute("SELECT MAX(id) FROM lib2_albums").fetchone()[0] or 0
            set_mark(ctx.pdb, "lib2_albums", top, ctx.now)
            return
        albums = [dict(r) for r in conn.execute(
            "SELECT a.id, a.title, a.primary_artist_id, a.added_at, a.explicit, a.album_type, "
            "ar.name AS artist FROM lib2_albums a LEFT JOIN lib2_artists ar ON ar.id = a.primary_artist_id "
            "WHERE a.id > ? ORDER BY a.id LIMIT ?", (int(mark), BATCH))]
        if not albums:
            return
        set_mark(ctx.pdb, "lib2_albums", albums[-1]["id"], ctx.now)
        fresh = [a for a in albums if _fresh(a["added_at"], ctx.now) and a.get("album_type") != "single"]
        wanting = [p for p in ctx.wanting("new_music") if ctx.music_ok(p)]
        if not fresh or not wanting:
            return
        artists = sorted({int(a["primary_artist_id"]) for a in fresh})
        since = _dt.datetime.fromtimestamp(ctx.now - LISTENED_DAYS * 86400).strftime("%Y-%m-%d %H:%M:%S")
        fans: Dict[int, Set[int]] = {}
        for chunk in _chunks(artists):
            for pid, aid in conn.execute(
                    "SELECT lh.profile_id, al.primary_artist_id FROM listening_history lh "
                    "JOIN lib2_tracks t ON t.id = lh.lib2_track_id JOIN lib2_albums al ON al.id = t.album_id "
                    f"WHERE lh.played_at >= ? AND al.primary_artist_id IN ({_marks(len(chunk))}) "
                    "GROUP BY lh.profile_id, al.primary_artist_id HAVING COUNT(*) >= ?",
                    [since, *chunk, LISTENED_PLAYS]):
                if pid is not None:
                    fans.setdefault(int(pid), set()).add(int(aid))
    from core.content_filter import restrictions_for
    for p in wanting:
        pid, prefs = p["id"], ctx.prefs(p["id"])
        try:
            clean = bool(restrictions_for(p).get("hide_explicit"))
        except Exception:  # noqa: BLE001 - can't tell: keep explicit out
            clean = True
        mine = [a for a in fresh if int(a["primary_artist_id"]) in fans.get(pid, set())
                and not (clean and a.get("explicit"))]
        if not mine:
            continue
        a = mine[0]
        key = f"new_music:{N.local_day(prefs, ctx.now)}"
        if len(mine) == 1:
            title = f"New from {a['artist'] or 'an artist you love'}"
            body = f"{a['title']} is in the library."
            link = f"/player/music/album/{a['id']}"
        else:
            title = f"{len(mine)} new albums from artists you play"
            body = " · ".join(f"{x['title']} by {x['artist']}" for x in mine[:3])
            link = "/player/music"
        _sent(out, "new_music", N.deliver(
            ctx.pdb, pid, "new_music", title, body, image=f"/api/library/v2/artwork/album/{a['id']}",
            link=link, group_key=key, replace=True, prefs=prefs, now=ctx.now))


# ── home memories ────────────────────────────────────────────────────────

def _memories(ctx: Ctx, out: Dict[str, int]) -> None:
    if ctx.vdb is None:
        return
    wanting = ctx.wanting("home_memories")
    if not wanting:
        return
    from core.player import home_access
    from core.player.libraries import hidden_for
    from core.video import home_videos as H
    for p in wanting:
        pid, prefs = p["id"], ctx.prefs(p["id"])
        if not N.is_time(prefs, ctx.now, prefs["memories_time"]):
            continue
        local = N.local_now(prefs, ctx.now).date()
        key = f"memories:{local.isoformat()}"
        if N.told(ctx.pdb, pid, key) or not ctx.home_ok(p):
            continue
        hidden = frozenset() if p.get("is_admin") else hidden_for(ctx.pdb, pid)
        roots = home_access.visible_roots(ctx.pdb, ctx.vdb, hidden)
        if not roots:
            continue
        conn = ctx.vdb._get_connection()
        try:
            rows = H.on_this_day_rows(conn, roots, today=local, limit=12)
        finally:
            conn.close()
        if not rows:
            continue
        years = sorted({str(r.get("sort_at") or "")[:4] for r in rows if r.get("sort_at")})
        span = years[0] if len(years) == 1 else (f"{years[0]} to {years[-1]}" if years else "")
        n = len(rows)
        _sent(out, "home_memories", N.deliver(
            ctx.pdb, pid, "home_memories", "On this day",
            f"{n} home video{'s' if n != 1 else ''} from this day{' in ' + span if span else ''}.",
            link="/player/home-videos", group_key=key, prefs=prefs, now=ctx.now))


# ── films released, not in the library ───────────────────────────────────

RELEASED_DAYS = 21


def _released_films(ctx: Ctx, out: Dict[str, int]) -> None:
    """for profiles that asked ("also films not in your library"): a film
    now in cinemas that is well rated, or among the most popular of what's
    out. the same weekly cap as featured films"""
    wanting = [p for p in ctx.wanting("featured_movie") if ctx.prefs(p["id"]).get("featured_not_in_library")]
    if not wanting or not _due(ctx.pdb, "released_films", EVERY_6H, ctx.now):
        return
    items = [r for r in (ctx.released() or []) if r.get("kind", "movie") == "movie" and r.get("tmdb_id")
             and not r.get("library_id")]
    cutoff = _dt.date.fromtimestamp(ctx.now - RELEASED_DAYS * 86400).isoformat()
    today = _dt.date.fromtimestamp(ctx.now).isoformat()
    items = [r for r in items if cutoff <= str(r.get("date") or "")[:10] <= today]
    pops = sorted((float(r.get("popularity") or 0) for r in items), reverse=True)
    top = pops[max(0, len(pops) // 5 - 1)] if pops else 0.0
    picks = [r for r in items if float(r.get("rating") or 0) >= 7.0
             or (float(r.get("popularity") or 0) >= top > 0 and float(r.get("rating") or 0) >= 6.0)]
    picks.sort(key=lambda r: (-float(r.get("rating") or 0), -float(r.get("popularity") or 0)))
    for p in wanting:
        pid, prefs = p["id"], ctx.prefs(p["id"])
        if not ctx.unowned_ok(p):
            continue
        room = FEATURED_PER_WEEK - N.count_since(ctx.pdb, pid, "featured_movie", ctx.now - 7 * 86400)
        for r in picks:
            if room <= 0:
                break
            name = f"{r['title']} ({r['year']})" if r.get("year") else r.get("title") or "A new film"
            res = N.deliver(ctx.pdb, pid, "featured_movie", f"Out now: {name}",
                            f"Rated {float(r.get('rating') or 0):.1f} on TMDB. Not in the library yet.",
                            image=r.get("poster"), link=f"/player/title/movie/{int(r['tmdb_id'])}",
                            group_key=f"featured:tmdb:{int(r['tmdb_id'])}", prefs=prefs, now=ctx.now)
            if res:
                room -= 1
            _sent(out, "featured_movie", res)


# ── the run ──────────────────────────────────────────────────────────────

STEPS = (("files", _new_files), ("released", _released_films), ("premieres", _premieres_soon), ("leaving", _leaving_soon),
         ("music", _new_music), ("memories", _memories))


def run(ctx: Ctx) -> Dict[str, int]:
    """one pass: let out what was held, then look for news. a step that
    fails is logged and the rest still run"""
    _ensure(ctx.pdb)
    out: Dict[str, int] = {}
    try:
        out["released"] = N.release_due(ctx.pdb, now=ctx.now)
    except Exception:  # noqa: BLE001
        logger.exception("notify: release failed")
    for name, step in STEPS:
        try:
            step(ctx, out)
        except Exception:  # noqa: BLE001 - one source down never stops the rest
            logger.exception("notify: %s step failed", name)
    try:
        if _due(ctx.pdb, "prune", 86400, ctx.now):
            N.prune(ctx.pdb, now=ctx.now)
    except Exception:  # noqa: BLE001
        logger.exception("notify: prune failed")
    return out


def run_live() -> Dict[str, int]:
    """the automation's entry: the real databases"""
    from database.player_database import get_player_database
    vdb = mdb = None
    try:
        from api.video import get_video_db
        vdb = get_video_db()
    except Exception:  # noqa: BLE001 - no video side
        logger.debug("notify: no video db", exc_info=True)
    try:
        from database.music_database import get_database
        mdb = get_database()
    except Exception:  # noqa: BLE001
        logger.debug("notify: no music db", exc_info=True)
    return run(Ctx(get_player_database(), vdb, mdb))
