"""history from soulsync's own play log, for plex and jellyfin alike.

rows are binge groups (back to back episodes, an album, a run of chapters),
newest first, with how long, how far and how it played. plex's own history is
imported once for the time before the log started, so day one isn't empty;
those rows say so (``source: 'plex_history'``) and carry no time or method.
"""

from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

import core.server_activity as sa
from core.server_activity import store
from core.server_activity.images import row_image
from core.server_activity.reasons import label as reason_label

logger = sa.logger

KIND_FILTERS = {
    "movie": ["movie"], "movies": ["movie"], "film": ["movie"], "films": ["movie"],
    "episode": ["episode"], "tv": ["episode"], "shows": ["episode"],
    "track": ["track"], "music": ["track"],
    "audiobook": ["audiobook"], "audiobooks": ["audiobook", "podcast"], "books": ["audiobook", "podcast"],
    "podcast": ["podcast"], "podcasts": ["podcast"],
    "other": ["clip", "live", "other"],
}
METHODS = ("direct_play", "direct_stream", "transcode")
_RANK = {"direct_play": 0, "direct_stream": 1, "transcode": 2}
_METHOD_LABEL = {"direct_play": "Direct Play", "direct_stream": "Direct Stream", "transcode": "Transcode"}


def _kids(root):
    """children of an xml reply. never `or` an Element: one with no children is falsy."""
    return list(root) if root is not None else []


def tzinfo(name: Optional[str]):
    """the viewer's zone for day buckets; the server's own when unknown."""
    if name:
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(str(name))
        except Exception:   # noqa: BLE001
            logger.debug("unknown time zone %r", name)
    return datetime.now().astimezone().tzinfo or timezone.utc


def local_date(ts: float, tz) -> str:
    return datetime.fromtimestamp(ts, tz).date().isoformat()


def _where(f: Dict[str, Any]) -> Tuple[str, List[Any]]:
    clauses, args = ["1=1"], []
    kinds = KIND_FILTERS.get(str(f.get("kind") or "").lower())
    if kinds:
        clauses.append("kind IN (" + ", ".join("?" for _ in kinds) + ")")
        args += kinds
    if f.get("user"):
        clauses.append("user = ?")
        args.append(str(f["user"]))
    if f.get("method") in METHODS:
        clauses.append("method = ?")
        args.append(f["method"])
    if f.get("server") in ("plex", "jellyfin"):
        clauses.append("server = ?")
        args.append(f["server"])
    if f.get("start") is not None:
        clauses.append("started_at >= ?")
        args.append(float(f["start"]))
    if f.get("end") is not None:
        clauses.append("started_at < ?")
        args.append(float(f["end"]))
    q = str(f.get("q") or "").strip()
    if q:
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        cols = ("title", "parent_title", "grandparent_title", "user", "player_product", "player_name",
                "location")
        clauses.append("(" + " OR ".join(f"{c} LIKE ? ESCAPE '\\'" for c in cols) + ")")
        args += [like] * len(cols)
    return " AND ".join(clauses), args


def _loads(v: Any, default: Any) -> Any:
    if isinstance(v, str) and v:
        try:
            return json.loads(v)
        except ValueError:
            return default
    return v if v is not None else default


def _ep_range(plays: List[Dict[str, Any]]) -> str:
    eps = [(p.get("season"), p.get("episode")) for p in plays if p.get("episode") is not None]
    if not eps:
        return f"{len(plays)} episodes"
    a, b = eps[0], eps[-1]
    if a == b:
        return f"S{a[0]} E{a[1]}" if a[0] is not None else f"E{a[1]}"
    if a[0] == b[0]:
        return f"S{a[0]} E{a[1]} – E{b[1]}"
    return f"S{a[0]} E{a[1]} – S{b[0]} E{b[1]}"


def _chapter_range(plays: List[Dict[str, Any]]) -> str:
    idx = [(_loads(p.get("chapter"), {}) or {}).get("index") for p in plays]
    idx = [i for i in idx if i]
    if not idx:
        return ""
    lo, hi = min(idx), max(idx)
    return f"Chapter {lo}" if lo == hi else f"Chapters {lo} – {hi}"


def play_row(p: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": p.get("id"), "play_key": p["play_key"], "kind": p["kind"], "title": p.get("title") or "",
        "parent_title": p.get("parent_title"), "grandparent_title": p.get("grandparent_title"),
        "season": p.get("season"), "episode": p.get("episode"), "track": p.get("track"),
        "chapter": _loads(p.get("chapter"), None),
        "started_at": p["started_at"], "stopped_at": p.get("stopped_at"), "live": p.get("stopped_at") is None,
        "played_ms": int(p.get("played_ms") or 0), "paused_ms": int(p.get("paused_ms") or 0),
        "buffering_ms": int(p.get("buffering_ms") or 0), "pause_count": int(p.get("pause_count") or 0),
        "duration_ms": p.get("duration_ms"), "percent": p.get("percent"),
        "book_percent": p.get("book_percent"), "method": p.get("method"),
        "method_label": _METHOD_LABEL.get(p.get("method") or ""), "quality": p.get("quality"),
        "change": p.get("change"), "source": p.get("source") or "live",
    }


def build_group(plays: List[Dict[str, Any]]) -> Dict[str, Any]:
    """one history row from the plays of a binge group (oldest first). pure."""
    plays = sorted(plays, key=lambda p: p["started_at"])
    first, last = plays[0], plays[-1]
    kind, n = first["kind"], len(plays)
    worst = max(plays, key=lambda p: _RANK.get(p.get("method") or "", -1))
    method = worst.get("method")
    if kind == "episode":
        title = first.get("grandparent_title") or first.get("title") or ""
        if n > 1:
            subtitle = _ep_range(plays) + " · back to back"
        else:
            subtitle = " · ".join(x for x in (_ep_range(plays), first.get("title")) if x)
        count_label = f"{n} episodes" if n > 1 else None
    elif kind == "track":
        if n > 1:
            title = first.get("parent_title") or first.get("title") or ""
            subtitle = first.get("grandparent_title") or ""
            count_label = f"{n} tracks"
        else:
            title = first.get("title") or ""
            subtitle = " · ".join(x for x in (first.get("grandparent_title"), first.get("parent_title")) if x)
            count_label = None
    elif kind == "audiobook":
        title = first.get("title") or ""
        subtitle = " · ".join(x for x in (_chapter_range(plays), first.get("grandparent_title")) if x)
        count_label = None
    elif kind == "podcast":
        title = first.get("parent_title") or first.get("title") or ""
        subtitle = (f"{n} episodes" if n > 1 else first.get("title") or "")
        count_label = f"{n} episodes" if n > 1 else None
    else:
        title = first.get("title") or ""
        subtitle = " · ".join(x for x in (str(first.get("year") or ""), "Movie" if kind == "movie" else "") if x)
        count_label = None
    pcts = [p.get("percent") for p in plays if p.get("percent") is not None]
    reasons: List[str] = []
    for p in plays:
        for c in _loads(p.get("reasons"), []) or []:
            if c not in reasons:
                reasons.append(c)
    link = next((_loads(p.get("link"), None) for p in plays if p.get("link")), None)
    return {
        "group_key": first["group_key"], "kind": kind, "title": title, "subtitle": subtitle,
        "count": n, "count_label": count_label,
        "started_at": first["started_at"],
        "stopped_at": None if any(p.get("stopped_at") is None for p in plays) else max(p["stopped_at"] for p in plays),
        "live": any(p.get("stopped_at") is None for p in plays),
        "played_ms": sum(int(p.get("played_ms") or 0) for p in plays),
        "paused_ms": sum(int(p.get("paused_ms") or 0) for p in plays),
        "buffering_ms": sum(int(p.get("buffering_ms") or 0) for p in plays),
        "pause_count": sum(int(p.get("pause_count") or 0) for p in plays),
        "percent": round(sum(pcts) / len(pcts)) if pcts else None,
        "finished": sum(1 for x in pcts if x >= 90),
        "book_percent": last.get("book_percent"),
        "method": method, "method_label": _METHOD_LABEL.get(method or ""),
        "quality": next((p.get("quality") for p in plays if p.get("quality")), None),
        "change": worst.get("change") or next((p.get("change") for p in plays if p.get("change")), None),
        "reasons": [{"code": c, "label": reason_label(c)} for c in reasons],
        "user": last.get("user") or "", "user_avatar": last.get("user_avatar"),
        "player": {"product": last.get("player_product"), "name": last.get("player_name"),
                   "device": last.get("player_device"), "platform": last.get("player_platform"),
                   "version": last.get("player_version")},
        "connection": {"remote": bool(last.get("remote")), "ip": last.get("ip"), "location": last.get("location")},
        "server": last.get("server"), "server_name": last.get("server_name"),
        "thumb": last.get("thumb"), "image": row_image(last), "link": link,
        "source": first.get("source") or "live",
        "plays": [play_row(p) for p in plays],
    }


def _range(f: Dict[str, Any], now: float) -> Dict[str, Any]:
    out = dict(f)
    if out.get("start") is None and out.get("days"):
        try:
            out["start"] = now - max(1, int(out["days"])) * 86400
        except (TypeError, ValueError):
            pass
    return out


# answers kept while the play log stands still (store.generation) and for a
# minute at most, so the "last 7 days" edge moves on. a tab switch or a filter
# put back reads from here.
CACHE_TTL = 60.0
_CACHE_MAX = 64
_cache: "OrderedDict[tuple, tuple]" = OrderedDict()
_cache_lock = threading.Lock()


def invalidate() -> None:
    with _cache_lock:
        _cache.clear()


def _cached(key: tuple, build):
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_TTL:
            _cache.move_to_end(key)
            return hit[1]
    val = build()
    if val is not None:
        with _cache_lock:
            _cache[key] = (now, val)
            _cache.move_to_end(key)
            while len(_cache) > _CACHE_MAX:
                _cache.popitem(last=False)
    return val


def get_history(*, kind: Optional[str] = None, user: Optional[str] = None, method: Optional[str] = None,
                server: Optional[str] = None, start: Optional[float] = None, end: Optional[float] = None,
                days: Optional[int] = None, q: Optional[str] = None, page: int = 0, per_page: int = 50,
                tz: Optional[str] = None, limit: Optional[int] = None, now: Optional[float] = None
                ) -> Dict[str, Any]:
    args = dict(kind=kind, user=user, method=method, server=server, start=start, end=end, days=days, q=q,
                page=page, per_page=per_page, tz=tz, limit=limit)
    if now is not None:     # a fixed clock (tests, exports) is never cached
        return _get_history(now=now, cacheable=False, **args)
    key = ("page", store.generation(), *sorted((k, str(v)) for k, v in args.items()))
    return _cached(key, lambda: (lambda r: r if r.get("ok") else None)(
        _get_history(now=time.time(), cacheable=True, **args))) or \
        {"ok": False, "reason": "error", "groups": [], "history": []}


def _facets(f: Dict[str, Any]) -> Dict[str, Any]:
    """the people and servers menus over the date range. both read from
    indexes only (user, started_at, user_avatar) and (server, started_at)."""
    fw, fa = _where({"start": f.get("start"), "end": f.get("end")})
    users = [{"user": r["user"], "avatar": r["avatar"], "plays": r["n"]} for r in store.query(
        f"SELECT user, MAX(user_avatar) AS avatar, COUNT(*) AS n FROM server_plays WHERE {fw} "
        "GROUP BY user ORDER BY n DESC", fa)]
    servers = [name for name in ("plex", "jellyfin") if store.query(
        f"SELECT 1 AS x FROM server_plays WHERE server = ? AND {fw} LIMIT 1", [name, *fa])]
    return {"users": users, "servers": servers}


def _overview(f: Dict[str, Any], where: str, args: List[Any], cache_key: Optional[tuple],
              facets: bool = True) -> tuple:
    """(summary row, facets, when the log began). the same for every page of
    one filter, so later pages reuse page one's. a cached overview always
    carries the facets (page one wants them); an uncached one only when asked."""
    with_facets = facets or cache_key is not None

    def build():
        s = store.query(
            "SELECT COUNT(*) AS plays, COALESCE(SUM(played_ms), 0) AS played_ms, COUNT(DISTINCT user) AS people, "
            "SUM(CASE WHEN method = 'transcode' THEN 1 ELSE 0 END) AS converted, "
            "SUM(CASE WHEN method IS NOT NULL THEN 1 ELSE 0 END) AS with_method, "
            "COUNT(DISTINCT CASE WHEN remote = 1 THEN COALESCE(location, ip) END) AS away_places, "
            f"MIN(started_at) AS first FROM server_plays WHERE {where}", args)[0]
        first_live = store.query("SELECT MIN(started_at) AS t FROM server_plays WHERE source = 'live'")[0]["t"]
        return s, _facets(f) if with_facets else None, first_live
    if cache_key is None:
        return build()
    return _cached(("overview", store.generation(), *cache_key), build)


def _get_history(*, kind, user, method, server, start, end, days, q, page, per_page, tz, limit, now,
                 cacheable) -> Dict[str, Any]:
    if limit and not per_page:
        per_page = limit
    per_page = max(1, min(200, int(per_page or 50)))
    page = max(0, int(page or 0))
    f = _range({"kind": kind, "user": user, "method": method, "server": server, "start": start, "end": end,
                "days": days, "q": q}, now)
    zone = tzinfo(tz)
    where, args = _where(f)
    try:
        keys = store.query(f"SELECT group_key, MAX(started_at) AS last FROM server_plays WHERE {where} "
                           "GROUP BY group_key ORDER BY last DESC LIMIT ? OFFSET ?",
                           [*args, per_page + 1, page * per_page])
        has_more = len(keys) > per_page
        keys = keys[:per_page]
        groups: List[Dict[str, Any]] = []
        if keys:
            gk = [k["group_key"] for k in keys]
            rows = store.query(f"SELECT * FROM server_plays WHERE {where} AND group_key IN ("
                               + ", ".join("?" for _ in gk) + ") ORDER BY started_at", [*args, *gk])
            by: Dict[str, List[Dict[str, Any]]] = {}
            for r in rows:
                by.setdefault(r["group_key"], []).append(r)
            groups = [build_group(by[k]) for k in gk if by.get(k)]
        # keyed by the filter as asked ("last 7 days"), not the moving start it becomes
        okey = (kind, user, method, server, start, end, days, q) if cacheable else None
        s, facets, first_live = _overview(f, where, args, okey, facets=page == 0)
        days_out: List[Dict[str, Any]] = []
        if groups:
            lo = min(g["started_at"] for g in groups)
            hi = max(g["started_at"] for g in groups)
            lo_day = datetime.fromtimestamp(lo, zone).replace(hour=0, minute=0, second=0, microsecond=0)
            hi_day = datetime.fromtimestamp(hi, zone).replace(hour=0, minute=0, second=0, microsecond=0) \
                + timedelta(days=1)
            dw, da = _where(dict(f, start=max(lo_day.timestamp(), f.get("start") or 0),
                                 end=min(hi_day.timestamp(), f.get("end") or hi_day.timestamp())))
            buckets: Dict[str, List[int]] = {}
            for r in store.query(f"SELECT started_at, played_ms FROM server_plays WHERE {dw}", da):
                b = buckets.setdefault(local_date(r["started_at"], zone), [0, 0])
                b[0] += 1
                b[1] += int(r["played_ms"] or 0)
            for d in sorted({local_date(g["started_at"], zone) for g in groups}, reverse=True):
                b = buckets.get(d, [0, 0])
                days_out.append({"date": d, "plays": b[0], "played_ms": b[1]})
        if page != 0:
            facets = None
    except Exception:   # noqa: BLE001
        logger.exception("server activity history failed")
        return {"ok": False, "reason": "error", "groups": [], "history": []}
    with_method = int(s["with_method"] or 0)
    return {
        "ok": True,
        "filters": {k: f.get(k) for k in ("kind", "user", "method", "server", "start", "end", "q")},
        "summary": {"plays": int(s["plays"] or 0), "played_ms": int(s["played_ms"] or 0),
                    "people": int(s["people"] or 0),
                    "converted_pct": round(100 * int(s["converted"] or 0) / with_method) if with_method else None,
                    "away_places": int(s["away_places"] or 0), "first_at": s["first"]},
        "days": days_out,
        "groups": groups,
        "page": page, "per_page": per_page, "has_more": has_more,
        "facets": facets,
        "log_started_at": first_live,
        # the pre-overhaul drawer reads this
        "history": [{"title": g["title"], "subtitle": g["subtitle"], "media_type": g["kind"], "user": g["user"],
                     "device": g["player"]["name"] or g["player"]["product"] or "", "thumb": g["thumb"] or "",
                     "viewed_epoch": int(g["started_at"])} for g in groups],
    }


def timeline(plays: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """playing / paused / buffering bands across a group, with the time between
    plays as 'between'. pure."""
    out: List[Dict[str, Any]] = []
    prev_end = None
    for p in sorted(plays, key=lambda p: p["started_at"]):
        segs = _loads(p.get("segments"), []) or []
        if not segs:
            end = p.get("stopped_at") or p["started_at"]
            segs = [["playing", p["started_at"], end]]
        if prev_end is not None and segs[0][1] - prev_end > 1:
            out.append({"state": "between", "start": prev_end, "end": segs[0][1]})
        for state, a, b in segs:
            if b <= a:
                continue
            if out and out[-1]["state"] == state and abs(out[-1]["end"] - a) <= 1:
                out[-1]["end"] = b
            else:
                out.append({"state": state, "start": a, "end": b})
        prev_end = max(prev_end or 0, segs[-1][2])
    return out


def get_history_group(group_key: str) -> Dict[str, Any]:
    """one history row opened: the timeline and the facts."""
    try:
        rows = store.query("SELECT * FROM server_plays WHERE group_key = ? ORDER BY started_at", (str(group_key),))
    except Exception:   # noqa: BLE001
        logger.exception("server activity history group failed")
        return {"ok": False, "reason": "error"}
    if not rows:
        return {"ok": False, "reason": "not_found"}
    g = build_group(rows)
    details = [_loads(r.get("detail"), {}) or {} for r in rows]
    changes = [c for d in details for c in (d.get("method_changes") or [])]
    g.update({
        "ok": True,
        "timeline": timeline(rows),
        "quality_detail": next((d.get("quality") for d in details if d.get("quality")), None),
        "method_changes": changes,
        "reasons_inferred": any(d.get("reasons_inferred") for d in details),
        "hw_engine": next((d.get("hw_engine") for d in details if d.get("hw_engine")), None),
        "gaps": sum(int(d.get("gaps") or 0) for d in details),
        "sent_kb": round(sum(float(r.get("sent_kb") or 0) for r in rows), 1),
        "peak_kbps": max(int(r.get("peak_kbps") or 0) for r in rows),
    })
    return g


# ── plex history import ─────────────────────────────────────────────────────

def _kind_for(ptype: str, section: str) -> str:
    from core.server_activity.links import _BOOK_SECTION, _PODCAST_SECTION
    if ptype == "track":
        if _PODCAST_SECTION.search(section or ""):
            return "podcast"
        if _BOOK_SECTION.search(section or ""):
            return "audiobook"
        return "track"
    return ptype if ptype in ("movie", "episode", "clip") else "other"


def _key_of(path: Optional[str]) -> Optional[str]:
    m = re.match(r"^/library/metadata/(\d+)$", str(path or ""))
    return m.group(1) if m else None


def plex_history_rows(elements: List[Any], *, machine: str, accounts: Dict[str, str],
                      devices: Dict[str, Dict[str, str]], sections: Dict[str, str], until: float,
                      server_name: str) -> List[Dict[str, Any]]:
    """plex history xml elements -> server_plays rows, binge-grouped. pure."""
    from core.server_activity.recorder import BINGE_GAP, _group_parent
    rows: List[Dict[str, Any]] = []
    for el in elements:
        a = el.attrib
        try:
            viewed = float(a.get("viewedAt") or 0)
        except ValueError:
            continue
        if not viewed or viewed >= until:
            continue
        ptype = (a.get("type") or "").lower()
        kind = _kind_for(ptype, sections.get(a.get("librarySectionID") or "", ""))
        dur = int(a.get("duration") or 0) if str(a.get("duration") or "").isdigit() else 0
        acct = a.get("accountID") or ""
        dev = devices.get(a.get("deviceID") or "", {})
        if kind in ("track", "audiobook", "podcast"):
            thumb = a.get("parentThumb") or a.get("thumb") or a.get("grandparentThumb")
        else:
            thumb = a.get("grandparentThumb") or a.get("parentThumb") or a.get("thumb")
        hk = a.get("historyKey") or f"{a.get('ratingKey')}:{int(viewed)}:{acct}"
        rows.append({
            "play_key": f"plexh:{machine}:{hk}", "group_key": "", "source": "plex_history",
            "server": "plex", "server_name": server_name, "session_key": None, "kind": kind,
            # history names the family as paths (parentKey="/library/metadata/12")
            "rating_key": a.get("ratingKey"), "parent_key": a.get("parentRatingKey") or _key_of(a.get("parentKey")),
            "grandparent_key": a.get("grandparentRatingKey") or _key_of(a.get("grandparentKey")),
            "title": a.get("title") or "", "parent_title": a.get("parentTitle"),
            "grandparent_title": a.get("grandparentTitle"), "subtitle": None,
            "year": int(a["year"]) if str(a.get("year") or "").isdigit() else None,
            "season": int(a["parentIndex"]) if kind == "episode" and str(a.get("parentIndex") or "").isdigit() else None,
            "episode": int(a["index"]) if kind == "episode" and str(a.get("index") or "").isdigit() else None,
            "track": int(a["index"]) if kind == "track" and str(a.get("index") or "").isdigit() else None,
            "thumb": thumb, "user": accounts.get(acct, "") or "Someone", "user_id": acct or None,
            "user_avatar": f"/api/server-activity/avatar?user=plex:{acct}" if acct else None,
            "player_product": None, "player_version": None, "player_platform": dev.get("platform"),
            "player_device": None, "player_name": dev.get("name"), "ip": None, "remote": 0, "location": None,
            # plex keeps when it was marked played, not when it started
            "started_at": viewed - dur / 1000.0 if dur else viewed, "stopped_at": viewed, "last_seen": viewed,
            "duration_ms": dur or None, "position_ms": None, "max_position_ms": None, "percent": None,
            "played_ms": 0, "paused_ms": 0, "buffering_ms": 0, "pause_count": 0, "buffer_count": 0,
            "method": None, "reasons": [], "hw": None, "quality": None, "change": None, "lossless": None,
            "detail": {"from_plex_history": True}, "segments": [], "sent_kb": 0, "peak_kbps": None,
            "book_percent": None, "book_left_ms": None, "chapter": None, "link": None,
        })
    rows.sort(key=lambda r: r["started_at"])
    last: Dict[tuple, tuple] = {}
    for r in rows:
        gp = _group_parent(r["kind"], r)
        key = (r["user"], r["kind"], gp)
        prev = last.get(key) if gp else None
        r["group_key"] = prev[0] if prev and r["started_at"] - prev[1] <= BINGE_GAP else r["play_key"]
        if gp:
            last[key] = (r["group_key"], r["stopped_at"])
    return rows


def _page(srv, path: str, params: Dict[str, Any], size: int = 500, cap: int = 20000) -> List[Any]:
    out: List[Any] = []
    start = 0
    while start < cap:
        p = dict(params, **{"X-Plex-Container-Start": start, "X-Plex-Container-Size": size})
        root = srv.query(path, params=p)
        items = list(root) if root is not None else []
        out += items
        if len(items) < size:
            break
        start += size
    return out


def backfill_plex_history(now: Optional[float] = None, days: int = 365) -> int:
    """import plex's history once per plex server, for the time before the log
    began. raw xml only: plexapi history objects reload themselves when a
    missing attribute is read."""
    now = time.time() if now is None else now
    srv = sa._plex_server()
    if srv is None:
        return 0
    machine = str(sa._g(srv, "machineIdentifier", "") or "plex")
    marker = f"plex_history_import:{machine}"
    if store.meta_get(marker):
        return 0
    first = store.query("SELECT MIN(started_at) AS t FROM server_plays WHERE server = 'plex' "
                        "AND source = 'live'")[0]["t"]
    until = float(first or now)
    accounts: Dict[str, str] = {}
    devices: Dict[str, Dict[str, str]] = {}
    sections: Dict[str, str] = {}
    try:
        for a in _kids(srv.query("/accounts")):
            accounts[a.get("id", "")] = a.get("name") or a.get("title") or ""
        for d in _kids(srv.query("/devices")):
            devices[d.get("id", "")] = {"name": d.get("name") or "", "platform": d.get("platform") or ""}
        for s in _kids(srv.query("/library/sections")):
            sections[s.get("key", "")] = s.get("title") or ""
        els = _page(srv, "/status/sessions/history/all",
                    {"sort": "viewedAt:desc", "viewedAt>": int(now - days * 86400)})
    except Exception:   # noqa: BLE001 - try again at the next maintenance round
        logger.debug("plex history import failed", exc_info=True)
        return 0
    rows = plex_history_rows(els, machine=machine, accounts=accounts, devices=devices, sections=sections,
                             until=until, server_name=str(sa._g(srv, "friendlyName", "") or "Plex"))
    for i in range(0, len(rows), 500):
        store.apply_ops([("play", r) for r in rows[i:i + 500]])
    store.meta_set(marker, json.dumps({"rows": len(rows), "at": now, "until": until}))
    logger.info("server activity: imported %d plays from plex history", len(rows))
    return len(rows)
