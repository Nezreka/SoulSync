"""the stats sheet, from the play log: one pure function over the plays of a
range (7 / 30 / 90 / 365 days), so every number on it is tested math.

time counts what the recorder saw play. rows imported from plex's history have
no played time; they count as plays, and when plex gave a length their time is
added as an estimate and reported apart (``estimated_hours``).
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import core.server_activity as sa
from core.server_activity import store
from core.server_activity.history import tzinfo
from core.server_activity.images import row_image
from core.server_activity.reasons import label as reason_label

logger = sa.logger

RANGES = (7, 30, 90, 365)
_KIND_GROUP = {"episode": "tv", "movie": "films", "track": "music", "audiobook": "books", "podcast": "books"}
# a sheet is kept while the play log stands still (store.generation). once a
# play lands it is still served, up to STALE_TTL old, while a fresh one is
# worked out behind it; the four ranges are worked out together so switching
# range is instant. MAX_AGE bounds even an unchanged log (the range's edge
# moves on).
_cache: Dict[tuple, tuple] = {}
_cache_lock = threading.Lock()
CACHE_TTL = 60          # kept for callers that read it; MAX_AGE is the bound now
MAX_AGE = 15 * 60
STALE_TTL = 10 * 60
_building: set = set()


def invalidate() -> None:
    with _cache_lock:
        _cache.clear()


def reset_for_tests() -> None:
    """drop the sheets and any refresh marked as running (a test that
    collects background work without running it leaves its marks behind)."""
    with _cache_lock:
        _cache.clear()
        _building.clear()


def _spawn(fn) -> None:
    threading.Thread(target=fn, name="server-activity-stats", daemon=True).start()


def _ms(p: Dict[str, Any]) -> int:
    if (p.get("source") or "live") == "live":
        return int(p.get("played_ms") or 0)
    return int(p.get("duration_ms") or 0)


def _spread(start: float, ms: int, tz, cb) -> None:
    """hand a play's time to the local hours it covered."""
    t, left = start, ms / 1000.0
    guard = 0
    while left > 0 and guard < 24 * 14:
        guard += 1
        dt = datetime.fromtimestamp(t, tz)
        nxt = (dt.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)).timestamp()
        take = min(left, max(1.0, nxt - t))
        cb(dt, take)
        t += take
        left -= take


def _daypart(h: int) -> str:
    if 5 <= h < 12:
        return "morning"
    if 12 <= h < 17:
        return "afternoon"
    if 17 <= h < 22:
        return "evening"
    return "night"


def _loads(v: Any, default: Any) -> Any:
    if isinstance(v, str) and v:
        try:
            return json.loads(v)
        except ValueError:
            return default
    return v if v is not None else default


def _hours(ms: float) -> float:
    return round(ms / 3_600_000, 1)


def compute(plays: List[Dict[str, Any]], *, start: float, end: float, days: int, tz,
            prev_plays: int = 0, minutes: Optional[List[Dict[str, Any]]] = None,
            live_users: Optional[List[str]] = None) -> Dict[str, Any]:
    """every number on the stats sheet. pure."""
    minutes = minutes or []
    live_users = set(live_users or [])
    total_ms = sum(_ms(p) for p in plays)
    est_ms = sum(_ms(p) for p in plays if (p.get("source") or "live") != "live")
    by_kind_ms: Dict[str, int] = {"tv": 0, "films": 0, "music": 0, "books": 0, "other": 0}
    for p in plays:
        by_kind_ms[_KIND_GROUP.get(p.get("kind") or "", "other")] += _ms(p)

    # days and hours
    start_day = datetime.fromtimestamp(start, tz).replace(hour=0, minute=0, second=0, microsecond=0)
    day_keys: List[str] = []
    d = start_day
    end_dt = datetime.fromtimestamp(end, tz)
    while d <= end_dt and len(day_keys) < 400:
        day_keys.append(d.date().isoformat())
        d = (d + timedelta(days=1, hours=2)).replace(hour=0)      # dst safe
    per_day = {k: {"ms": 0.0, "plays": 0} for k in day_keys}
    heat = [[0.0] * 24 for _ in range(7)]
    for p in plays:
        key = datetime.fromtimestamp(p["started_at"], tz).date().isoformat()
        if key in per_day:
            per_day[key]["plays"] += 1

        def add(dt, secs):
            k = dt.date().isoformat()
            if k in per_day:
                per_day[k]["ms"] += secs * 1000
            heat[dt.weekday()][dt.hour] += secs * 1000
        _spread(p["started_at"], _ms(p), tz, add)
    per_day_out = []
    for k in day_keys:
        dd = datetime.fromisoformat(k)
        per_day_out.append({"date": k, "weekday": dd.weekday(), "weekend": dd.weekday() >= 5,
                            "hours": _hours(per_day[k]["ms"]), "plays": per_day[k]["plays"]})

    cells = [[_hours(v) for v in row] for row in heat]
    best, best_v = None, 0.0
    for wd in range(7):
        for h in range(24):
            v = heat[wd][h] + heat[wd][(h + 1) % 24]
            if v > best_v:
                best, best_v = (wd, h), v
    dayparts = {"morning": 0.0, "afternoon": 0.0, "evening": 0.0, "night": 0.0}
    for wd in range(7):
        for h in range(24):
            dayparts[_daypart(h)] += heat[wd][h]
    quiet = min(((wd, h) for wd in range(7) for h in range(24)), key=lambda c: (heat[c[0]][c[1]], c))
    heatmap = {"cells": cells, "max": max((v for row in cells for v in row), default=0),
               "busiest": {"weekday": best[0], "hour_start": best[1], "hour_end": (best[1] + 2) % 24}
               if best else None,
               "quietest": {"weekday": quiet[0], "hour": quiet[1]},
               "dayparts": {k: _hours(v) for k, v in dayparts.items()},
               "busiest_daypart": max(dayparts, key=dayparts.get) if total_ms else None}

    # methods and reasons
    known = [p for p in plays if p.get("method")]
    methods = {}
    for m in ("direct_play", "direct_stream", "transcode"):
        n = sum(1 for p in known if p["method"] == m)
        methods[m] = {"plays": n, "pct": round(100 * n / len(known)) if known else None}
    converted = [p for p in known if p["method"] == "transcode"]
    reason_counts: Dict[str, int] = {}
    for p in known:
        if p["method"] == "direct_play":
            continue
        for c in _loads(p.get("reasons"), []) or []:
            reason_counts[c] = reason_counts.get(c, 0) + 1
    reasons = [{"code": c, "label": reason_label(c), "plays": n}
               for c, n in sorted(reason_counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    hw = {"hw": sum(1 for p in converted if p.get("hw")), "total": len(converted)}

    # most at once
    peak = max(minutes, key=lambda m: (m.get("streams") or 0, -(m.get("minute") or 0)), default=None)
    most = {"value": int(peak["streams"]), "at": int(peak["minute"]) * 60} if peak and peak.get("streams") else None
    if most is None:
        ev = []
        for p in plays:
            if (p.get("source") or "live") == "live":
                ev.append((p["started_at"], 1))
                ev.append((p.get("stopped_at") or p.get("last_seen") or p["started_at"], -1))
        cur = best_n = 0
        best_at = None
        for t, dlt in sorted(ev, key=lambda e: (e[0], e[1])):
            cur += dlt
            if cur > best_n:
                best_n, best_at = cur, t
        most = {"value": best_n, "at": best_at} if best_n else None
    rpeak = max(minutes, key=lambda m: m.get("remote_kbps") or 0, default=None)
    sent_kb = sum(float(p.get("sent_kb") or 0) for p in plays if p.get("remote"))

    # players and places
    players: Dict[str, Dict[str, float]] = {}
    places: Dict[str, Dict[str, Any]] = {}
    for p in plays:
        name = p.get("player_product") or p.get("player_name") or "Unknown player"
        e = players.setdefault(name, {"plays": 0, "ms": 0})
        e["plays"] += 1
        e["ms"] += _ms(p)
        if p.get("remote"):
            lab = p.get("location") or "Away, place unknown"
            home = False
        else:
            lab, home = "Home network", True
        if (p.get("source") or "live") != "live" and not p.get("remote") and not p.get("ip"):
            lab, home = "Unknown (from Plex history)", False
        e2 = places.setdefault(lab, {"label": lab, "home": home, "plays": 0, "ms": 0})
        e2["plays"] += 1
        e2["ms"] += _ms(p)
    players_out = [{"product": k, "plays": int(v["plays"]), "hours": _hours(v["ms"])}
                   for k, v in sorted(players.items(), key=lambda kv: (-kv[1]["plays"], kv[0]))][:10]
    places_out = [{"label": v["label"], "home": v["home"], "plays": v["plays"], "hours": _hours(v["ms"])}
                  for v in sorted(places.values(), key=lambda v: (-v["plays"], v["label"]))][:10]

    # people
    spark_start = datetime.fromtimestamp(end, tz).replace(hour=0, minute=0, second=0, microsecond=0) \
        - timedelta(days=13)
    spark_keys = [(spark_start + timedelta(days=i, hours=2)).date().isoformat() for i in range(14)]
    spark_floor = spark_start.timestamp() - 86400
    people: Dict[str, Dict[str, Any]] = {}
    for p in plays:
        u = p.get("user") or "Someone"
        e = people.setdefault(u, {"user": u, "avatar": None, "ms": 0, "plays": 0, "away_ms": 0,
                                  "titles": {}, "devices": {}, "places": {}, "music": 0, "lossless": 0,
                                  "last": 0.0, "spark": {k: 0.0 for k in spark_keys}})
        ms = _ms(p)
        e["avatar"] = e["avatar"] or p.get("user_avatar")
        e["ms"] += ms
        e["plays"] += 1
        if p.get("remote"):
            e["away_ms"] += ms
            if p.get("location"):
                e["places"][p["location"]] = e["places"].get(p["location"], 0) + ms
        t = p.get("grandparent_title") if p.get("kind") in ("episode", "track") else \
            (p.get("parent_title") if p.get("kind") == "podcast" else p.get("title"))
        if t:
            e["titles"][(t, p.get("kind"))] = e["titles"].get((t, p.get("kind")), 0) + ms + 1
        dev = p.get("player_product") or p.get("player_name")
        if dev:
            e["devices"][dev] = e["devices"].get(dev, 0) + ms + 1
        if p.get("kind") == "track":
            e["music"] += 1
            e["lossless"] += 1 if p.get("lossless") else 0
        e["last"] = max(e["last"], float(p.get("stopped_at") or p.get("last_seen") or p["started_at"]))

        def spark(dt, secs, e=e):
            k = dt.date().isoformat()
            if k in e["spark"]:
                e["spark"][k] += secs * 1000
        # the sparkline is the last 14 days: older plays can't land in it
        if p["started_at"] + ms / 1000.0 >= spark_floor:
            _spread(p["started_at"], ms, tz, spark)
    people_out = []
    for e in sorted(people.values(), key=lambda e: (-e["ms"], -e["plays"], e["user"])):
        top = max(e["titles"].items(), key=lambda kv: kv[1])[0] if e["titles"] else None
        people_out.append({
            "user": e["user"], "avatar": e["avatar"], "hours": _hours(e["ms"]), "plays": e["plays"],
            "spark": [_hours(e["spark"][k]) for k in spark_keys],
            "mostly": {"title": top[0], "kind": top[1]} if top else None,
            "devices": [k for k, _ in sorted(e["devices"].items(), key=lambda kv: -kv[1])][:3],
            "away_pct": round(100 * e["away_ms"] / e["ms"]) if e["ms"] else None,
            "top_location": max(e["places"], key=e["places"].get) if e["places"] else None,
            "lossless_pct": round(100 * e["lossless"] / e["music"]) if e["music"] else None,
            "last_seen": e["last"] or None, "live": e["user"] in live_users,
        })

    # shelves
    def shelf(kinds, key_fn, extra, role=None):
        acc: Dict[Any, Dict[str, Any]] = {}
        for p in plays:
            if p.get("kind") not in kinds:
                continue
            k = key_fn(p)
            if not k or not k[0]:
                continue
            e = acc.setdefault(k, {"ms": 0, "plays": 0, "users": {}, "items": set(), "rows": []})
            e["ms"] += _ms(p)
            e["plays"] += 1
            e["users"][p.get("user")] = e["users"].get(p.get("user"), 0) + _ms(p) + 1
            e["items"].add(p.get("rating_key") or p.get("title"))
            e["rows"].append(p)
        out = []
        for k, e in sorted(acc.items(), key=lambda kv: (-kv[1]["ms"], -kv[1]["plays"], str(kv[0]))):
            last = max(e["rows"], key=lambda r: r["started_at"])
            link = next((_loads(r.get("link"), None) for r in e["rows"] if r.get("link")), None)
            item = {"title": k[0], "thumb": last.get("thumb"), "image": row_image(last, role),
                    "hours": _hours(e["ms"]), "plays": e["plays"], "people": len(e["users"]), "link": link}
            item.update(extra(k, e, last))
            out.append(item)
        return out[:8]

    films = shelf({"movie"}, lambda p: (p.get("title"), p.get("year")), lambda k, e, last: {"year": k[1]})
    shows = shelf({"episode"}, lambda p: (p.get("grandparent_title"),),
                  lambda k, e, last: {"episodes": len(e["items"])})
    artists = shelf({"track"}, lambda p: (p.get("grandparent_title"),),
                    lambda k, e, last: {"tracks": e["plays"]}, role="artist")

    def book_extra(k, e, last):
        top_user = max(e["users"], key=e["users"].get) if e["users"] else None
        pct = last.get("book_percent")
        if k[1] == "podcast":
            return {"kind": "podcast", "user": top_user, "episodes": len(e["items"]), "percent": None,
                    "finished": None}
        return {"kind": "audiobook", "user": top_user, "episodes": None, "percent": pct,
                "finished": bool(pct is not None and pct >= 98)}
    books = shelf({"audiobook", "podcast"},
                  lambda p: ((p.get("title") if p.get("kind") == "audiobook" else p.get("parent_title")),
                             p.get("kind")),
                  book_extra)

    top_kind = max(by_kind_ms, key=by_kind_ms.get) if total_ms else None
    conv_users: Dict[str, int] = {}
    for p in converted:
        conv_users[p.get("user") or "Someone"] = conv_users.get(p.get("user") or "Someone", 0) + 1
    top_conv = max(conv_users, key=conv_users.get) if conv_users else None
    top_conv_remote = None
    if top_conv:
        mine = [p for p in converted if (p.get("user") or "Someone") == top_conv]
        top_conv_remote = round(100 * sum(1 for p in mine if p.get("remote")) / len(mine))
    as_is = methods["direct_play"]["plays"] + methods["direct_stream"]["plays"]
    return {
        "ok": True, "days": days, "range": {"start": start, "end": end},
        "headline": {
            "people": len(people), "hours": _hours(total_ms), "top_kind": top_kind,
            "busiest_daypart": heatmap["busiest_daypart"],
            "converted_pct": round(100 * len(converted) / len(known)) if known else None,
            "converted_one_in": round(len(known) / len(converted)) if converted else None,
            "top_converted_user": top_conv, "top_converted_user_away_pct": top_conv_remote,
            "servers": sorted({p.get("server") for p in plays if p.get("server")}),
        },
        "tiles": {
            "plays": {"value": len(plays), "prev": prev_plays,
                      "change_pct": round(100 * (len(plays) - prev_plays) / prev_plays) if prev_plays else None},
            "hours": {"value": _hours(total_ms), "by_kind": {k: _hours(v) for k, v in by_kind_ms.items()},
                      "estimated_hours": _hours(est_ms)},
            "most_at_once": most,
            "played_as_is": {"pct": round(100 * as_is / len(known)) if known else None, "plays": as_is,
                             "known": len(known)},
            "sent_remotely": {"gb": round(sent_kb / 1_000_000, 2),
                              "peak_kbps": int(rpeak["remote_kbps"]) if rpeak and rpeak.get("remote_kbps") else None,
                              "peak_at": int(rpeak["minute"]) * 60 if rpeak and rpeak.get("remote_kbps") else None},
        },
        "per_day": per_day_out,
        "methods": methods, "reasons": reasons, "hardware": hw,
        "heatmap": heatmap,
        "players": players_out, "locations": places_out,
        "people": people_out,
        "top": {"films": films, "shows": shows, "artists": artists, "books": books},
        "unknown_method_plays": len(plays) - len(known),
    }


_COLS = ("started_at, stopped_at, last_seen, played_ms, duration_ms, kind, user, user_avatar, method, reasons, hw, "
         "player_product, player_name, location, remote, ip, sent_kb, title, parent_title, grandparent_title, "
         "rating_key, parent_key, grandparent_key, thumb, lossless, book_percent, percent, source, server, link, "
         "year")


def get_stats(days: int = 30, tz: Optional[str] = None, now: Optional[float] = None) -> Dict[str, Any]:
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = 30
    days = min(RANGES, key=lambda r: abs(r - days))
    fixed = now is not None
    now = time.time() if now is None else now
    key = (days, tz or "")
    gen = store.generation()
    with _cache_lock:
        hit = _cache.get(key)
    if hit:
        at, data, hit_gen = hit
        age = now - at
        if hit_gen == gen and 0 <= age < MAX_AGE:
            return data
        if 0 <= age < STALE_TTL:
            _refresh([days], tz, None if not fixed else now)
            return data
    data = _build(days, tz, now, gen)
    if data.get("ok") and not fixed:
        # the other ranges next, so the range buttons answer at once
        _refresh([r for r in RANGES if r != days], tz, None, only_missing=True)
    return data


def _refresh(ranges: List[int], tz: Optional[str], now: Optional[float], only_missing: bool = False) -> None:
    todo = []
    with _cache_lock:
        for d in ranges:
            k = (d, tz or "")
            if k in _building or (only_missing and k in _cache):
                continue
            _building.add(k)
            todo.append(d)
    if not todo:
        return

    def run():
        try:
            for d in todo:
                try:
                    _build(d, tz, time.time() if now is None else now, store.generation())
                except Exception:   # noqa: BLE001
                    logger.debug("stats refresh failed", exc_info=True)
        finally:
            with _cache_lock:
                for d in todo:
                    _building.discard((d, tz or ""))
    _spawn(run)


def _build(days: int, tz: Optional[str], now: float, gen: int) -> Dict[str, Any]:
    zone = tzinfo(tz)
    start = now - days * 86400
    try:
        plays = store.query(f"SELECT {_COLS} FROM server_plays WHERE started_at >= ? AND started_at < ?",
                            (start, now))
        prev = store.query("SELECT COUNT(*) AS n FROM server_plays WHERE started_at >= ? AND started_at < ?",
                           (start - days * 86400, start))[0]["n"]
        minutes = store.query("SELECT minute, streams, remote_kbps FROM server_activity_minutes "
                              "WHERE minute >= ? AND minute < ?", (int(start // 60), int(now // 60) + 1))
        live = store.query("SELECT DISTINCT user FROM server_plays WHERE stopped_at IS NULL")
    except Exception:   # noqa: BLE001
        logger.exception("server activity stats failed")
        return {"ok": False, "reason": "error"}
    data = compute(plays, start=start, end=now, days=days, tz=zone, prev_plays=int(prev or 0), minutes=minutes,
                   live_users=[r["user"] for r in live])
    data["tz"] = str(tz or "")
    with _cache_lock:
        _cache[(days, tz or "")] = (now, data, gen)
    return data
