"""the live view: one poll of every configured server, merged, with the pulse
(streams, bandwidth, transcoders, cpu), a 10 minute bandwidth series kept
server side, and the play recorder fed from the same poll.

one poll serves everyone: the drawer's socket push, an http fallback and the
background sampler all read the same snapshot, so N open drawers cost one
upstream call.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Callable, Dict, List, Optional

import core.server_activity as sa
from core.server_activity import geo, images, links, store
from core.server_activity import jellyfin as jf
from core.server_activity import plex as px
from core.server_activity.quality import summarize_playback
from core.server_activity.recorder import Recorder, Writer

logger = sa.logger

LIVE_INTERVAL = 3.0
IDLE_INTERVAL = 10.0
SERIES_WINDOW = 600.0

_poll_lock = threading.Lock()
_snapshot: Dict[str, Any] = {"at": 0.0, "data": None}
_series: "deque[tuple]" = deque()
_series_lock = threading.Lock()
# what the browser must never see but the server needs later: avatar urls and
# the plex Session id a stop is sent to, keyed by what the payload exposes
_private_lock = threading.Lock()
_avatars: Dict[str, str] = {}
_session_ids: Dict[str, str] = {}

writer = Writer(store.apply_ops)
recorder = Recorder(sink=writer.put)


def reset_for_tests(threaded: bool = False) -> None:
    global writer, recorder
    with _poll_lock:
        _snapshot.update(at=0.0, data=None)
    with _series_lock:
        _series.clear()
    with _private_lock:
        _avatars.clear()
        _session_ids.clear()
    writer = Writer(store.apply_ops, threaded=threaded)
    recorder = Recorder(sink=writer.put)
    _maint.update(at=0.0, running=False)


def avatar_url(user_key: str) -> Optional[str]:
    with _private_lock:
        return _avatars.get(user_key)


# ── per server ──────────────────────────────────────────────────────────────

def _plex_sessions(db=None) -> tuple:
    """(sessions, server info | None). info is None when plex isn't configured."""
    cfg = sa._plex_config(db)
    if not cfg["base_url"] or not cfg["token"]:
        return [], None
    srv = sa._plex_server(db)
    info = {"server": "plex", "name": "Plex", "version": None, "reachable": False, "plex_pass": None}
    if srv is None:
        return [], info
    out: List[Dict[str, Any]] = []
    try:
        els = px.session_elements(srv)
    except Exception:   # noqa: BLE001 - configured but unreachable
        logger.debug("plex sessions failed", exc_info=True)
        sa._mark_plex_unreachable(srv)
        return [], info
    name = str(sa._g(srv, "friendlyName", "") or "Plex")
    sub = sa._g(srv, "myPlexSubscription", None)
    plex_pass = None if sub is None else bool(sub)
    info.update(name=name, version=str(sa._g(srv, "version", "") or "") or None, reachable=True, plex_pass=plex_pass)
    budget = [px.SOURCE_FETCH_BUDGET]
    # the library items behind new streams, asked side by side instead of one after another
    px.prefetch_sources(srv, [el.get("ratingKey") or "" for el in els], budget)
    for el in els:
        try:
            f = px.facts(el)
            src = px.source_item(srv, f["rating_key"], budget)
            file_path = f["file"]
            section = f["section"] or (src.get("librarySectionTitle", "") if src is not None else "")
            if not file_path and src is not None:
                _m, part = px._media_part(src)
                file_path = part.get("file", "") if part is not None else ""
            kind = meta = book = None
            if f["type"] == "track":
                kind = links.audio_kind(section, file_path)
                if kind:
                    meta = links.book_meta(file_path)
                if kind == "audiobook":
                    offset = px._n(el.get("viewOffset")) or 0
                    duration = px._n(el.get("duration")) or 0
                    chs = px.chapters_of(src)
                    if len(chs) > 1:
                        book = px.book_progress(offset_ms=offset, duration_ms=duration, chapters=chs)
                    else:
                        tracks = px.book_tracks(srv, f["parent_key"], budget)
                        book = px.book_progress(offset_ms=offset, duration_ms=duration, tracks=tracks,
                                                current_key=f["rating_key"])
            s = px.normalize(el, server_name=name, source=src, audio_kind=kind, book=book, book_meta=meta,
                             plex_pass=plex_pass)
            if meta and s.get("book") is not None:
                s["book"]["asin"] = meta.get("asin")
            out.append(s)
        except Exception:   # noqa: BLE001 - one odd session never blanks the view
            logger.debug("plex session normalize failed", exc_info=True)
    return out, info


def _jellyfin_sessions(db=None) -> tuple:
    cfg = sa._jellyfin_config(db)
    if not cfg["base_url"] or not cfg["api_key"]:
        return [], None
    info = {"server": "jellyfin", "name": "Jellyfin", "version": None, "reachable": False, "plex_pass": None}
    try:
        raw = jf.sessions(cfg)
    except Exception:   # noqa: BLE001
        logger.debug("jellyfin sessions failed", exc_info=True)
        return [], info
    info["reachable"] = True
    budget = [4]
    out: List[Dict[str, Any]] = []
    for s in raw:
        try:
            npi = s.get("NowPlayingItem") or {}
            ptype = str(npi.get("Type") or "").lower()
            kind = meta = book = None
            path = str(npi.get("Path") or "")
            if ptype == "audiobook":
                kind = "audiobook"
            elif ptype == "audio":
                kind = links.audio_kind("", path)
            if kind:
                meta = links.book_meta(path)
            if kind == "audiobook":
                off = jf._ms((s.get("PlayState") or {}).get("PositionTicks"))
                dur = jf._ms(npi.get("RunTimeTicks"))
                chs = jf.chapters_of(npi)
                if len(chs) > 1:
                    book = px.book_progress(offset_ms=off, duration_ms=dur, chapters=chs)
                else:
                    tracks = jf.book_tracks(cfg, str(npi.get("ParentId") or ""), budget)
                    book = px.book_progress(offset_ms=off, duration_ms=dur, tracks=tracks,
                                            current_key=str(npi.get("Id") or ""))
            n = jf.normalize(s, server_name="Jellyfin", audio_kind=kind, book=book, book_meta=meta)
            if meta and n.get("book") is not None:
                n["book"]["asin"] = meta.get("asin")
            out.append(n)
        except Exception:   # noqa: BLE001
            logger.debug("jellyfin session normalize failed", exc_info=True)
    return out, info


# ── the pulse ───────────────────────────────────────────────────────────────

_LEGACY_TYPE = {"audiobook": "track", "podcast": "track", "live": "clip", "other": "clip"}


def _legacy(s: Dict[str, Any]) -> None:
    """the fields the drawer read before the overhaul, so it keeps working
    until the new one replaces it."""
    pb = s.get("playback") or {}
    v = pb.get("video") or {}
    a = pb.get("audio") or {}
    vs, vo = v.get("source") or {}, v.get("stream") or {}
    as_, ao = a.get("source") or {}, a.get("stream") or {}
    tr = s.get("transcoder") or {}
    s["media_type"] = _LEGACY_TYPE.get(s["kind"], s["kind"])
    s["grandparent"] = (s.get("show") or {}).get("title") or (s.get("music") or {}).get("artist") or \
        (s.get("book") or {}).get("author") or ""
    s["location"] = "wan" if (s.get("connection") or {}).get("remote") else "lan"
    s["stream"] = {
        "method": pb.get("method_label"),
        "video": (vs.get("codec") or "") + (f" → {vo['codec']}" if v.get("changed") and vo.get("codec") else ""),
        "audio": (as_.get("codec") or "") + (f" → {ao['codec']}" if a.get("changed") and ao.get("codec") else ""),
        "resolution": vs.get("resolution") or "",
        "bitrate_kbps": (pb.get("bitrate_kbps") or {}).get("source") or 0,
        "transcode_progress": tr.get("progress_pct") or 0,
        "throttled": bool(tr.get("throttled")), "hw": bool(tr.get("hw")),
        "container": (pb.get("container") or {}).get("stream") or "",
    }


def _summary(sessions: List[Dict[str, Any]], cpu: Optional[Dict[str, Any]], upload_mbps) -> Dict[str, Any]:
    trans = [s for s in sessions if s["playback"]["method"] == "transcode"]
    total = sum(int(s.get("bandwidth_kbps") or 0) for s in sessions)
    remote = sum(int(s.get("bandwidth_kbps") or 0) for s in sessions if s["connection"]["remote"])
    direct = sum(1 for s in sessions if s["playback"]["method"] == "direct_play")
    hw = sum(1 for s in trans if (s.get("transcoder") or {}).get("hw"))
    return {
        "streams": len(sessions),
        "watching": sum(1 for s in sessions if s["activity"] == "watching"),
        "listening": sum(1 for s in sessions if s["activity"] == "listening"),
        "playing": sum(1 for s in sessions if s["state"] == "playing"),
        "paused": sum(1 for s in sessions if s["state"] == "paused"),
        "direct_play": direct,
        "direct_stream": sum(1 for s in sessions if s["playback"]["method"] == "direct_stream"),
        "transcodes": len(trans),
        "transcoders": {"running": len(trans), "hw": hw, "sw": len(trans) - hw},
        "total_bandwidth_kbps": total,
        "remote_bandwidth_kbps": remote,
        "local_bandwidth_kbps": total - remote,
        "upload_cap_kbps": int(upload_mbps * 1000) if upload_mbps else None,
        "lan": sum(1 for s in sessions if not s["connection"]["remote"]),
        "wan": sum(1 for s in sessions if s["connection"]["remote"]),
        "cpu": cpu,
    }


def _sample(now: float, summary: Dict[str, Any]) -> None:
    with _series_lock:
        _series.append((round(now, 1), summary["total_bandwidth_kbps"], summary["remote_bandwidth_kbps"],
                        summary["streams"]))
        while _series and _series[0][0] < now - SERIES_WINDOW:
            _series.popleft()


def bandwidth_series(now: Optional[float] = None) -> Dict[str, Any]:
    now = time.time() if now is None else now
    with _series_lock:
        pts = [p for p in _series if p[0] >= now - SERIES_WINDOW]
    return {"window_s": int(SERIES_WINDOW),
            "points": [{"t": p[0], "total_kbps": p[1], "remote_kbps": p[2], "streams": p[3]} for p in pts],
            "peak_kbps": max((p[1] for p in pts), default=0),
            "peak_remote_kbps": max((p[2] for p in pts), default=0)}


def _ensure_recorder_loaded(now: float) -> None:
    if recorder.loaded:
        return
    try:
        recorder.load(store.load_open_plays(), store.recent_closed(now - 3600))
    except Exception:   # noqa: BLE001 - no db yet; start fresh rather than never record
        logger.debug("play log load failed", exc_info=True)
        recorder.loaded = True


# ── the poll ────────────────────────────────────────────────────────────────

def _both_servers(db=None) -> tuple:
    """plex and jellyfin asked at the same time; a poll takes the slower of
    the two, not the sum."""
    jf_cfg = sa._jellyfin_config(db)
    if not (jf_cfg["base_url"] and jf_cfg["api_key"]):
        return _plex_sessions(db), ([], None)
    box: Dict[str, Any] = {}

    def run_jf():
        try:
            box["jf"] = _jellyfin_sessions(db)
        except Exception:   # noqa: BLE001
            logger.debug("jellyfin poll failed", exc_info=True)
    t = threading.Thread(target=run_jf, name="server-activity-jf", daemon=True)
    t.start()
    try:
        plex = _plex_sessions(db)
    finally:
        t.join()
    return plex, box.get("jf") or ([], {"server": "jellyfin", "name": "Jellyfin", "version": None,
                                        "reachable": False, "plex_pass": None})


def poll(db=None, now: Optional[float] = None) -> Dict[str, Any]:
    now = time.time() if now is None else now
    (plex_sessions, plex_info), (jf_sessions, jf_info) = _both_servers(db)
    servers = [i for i in (plex_info, jf_info) if i is not None]
    sessions = plex_sessions + jf_sessions
    from core.server_activity.settings import get_settings
    settings = get_settings()

    for s in sessions:
        hint = s.get("_link") or {}
        s["keys"] = {"rating_key": hint.get("rating_key") or None, "parent_key": hint.get("parent_key") or None,
                     "grandparent_key": hint.get("grandparent_key") or None}
        # the proxy queries for the poster and the backdrop, with the family to fall back on
        s["image"] = images.row_image(s)
        s["backdrop"] = images.row_image(dict(s, thumb=s.get("art") or s.get("thumb")), role="art")
        conn = s["connection"]
        if conn.get("remote"):
            conn["location"] = geo.lookup(conn.get("public_ip") or conn.get("ip"), now)
        s["playback"]["summary"] = summarize_playback(s["playback"], s["kind"])
        with _private_lock:
            if s.get("_user_thumb") and s.get("user_id"):
                _avatars[f"{s['server']}:{s['user_id']}"] = s["_user_thumb"]
            if s.get("_session_id"):
                _session_ids[f"{s['server']}:{s['session_key']}"] = s["_session_id"]
        s.pop("_user_thumb", None)
        s.pop("_session_id", None)
    links.resolve(sessions, now)

    _ensure_recorder_loaded(now)
    for info in servers:
        recorder.observe(info["server"], info["reachable"], sessions, now)
    for s in sessions:
        facts = recorder.live_facts(s["server"], str(s.get("session_key") or "")) or {}
        s["started_at"] = facts.get("started_at")
        s["paused_ms"] = facts.get("paused_ms", 0)
        s["pause_count"] = facts.get("pause_count", 0)
        s["buffering_ms"] = facts.get("buffering_ms", 0)
        s["play_key"] = facts.get("play_key")
        s["group_key"] = facts.get("group_key")
        _legacy(s)

    cpu = None
    if plex_info and plex_info.get("reachable"):
        srv = sa._plex_server(db)
        if srv is not None:
            cpu = px.cpu(srv)
    summary = _summary(sessions, cpu, settings.get("upload_mbps"))
    if servers:
        _sample(now, summary)
        if summary["streams"]:
            writer.put([("minute", int(now // 60), summary["streams"], summary["transcodes"],
                         summary["total_bandwidth_kbps"], summary["remote_bandwidth_kbps"])])

    sessions.sort(key=lambda s: (s["state"] != "playing", s["activity"] != "watching", s["user"].lower()))
    names = [i["name"] for i in servers]
    out: Dict[str, Any] = {
        "ok": True,
        "at": now,
        "server": {"name": names[0] if names else "Server",
                   "version": (servers[0].get("version") or "") if servers else "",
                   "platform": "+".join(i["server"] for i in servers) or "none"},
        "servers": servers,
        "summary": summary,
        "bandwidth": bandwidth_series(now),
        "sessions": sessions,
    }
    if not servers:
        out.update(ok=False, reason="no_server", message="No Plex or Jellyfin server configured.")
    elif not any(i["reachable"] for i in servers):
        out.update(ok=False, reason="unreachable", message="Couldn't reach the media server.")
    return out


STALE_OK = 15.0
_refresh_lock = threading.Lock()
_refreshing = {"on": False}


def _spawn(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, name="server-activity-refresh", daemon=True).start()


def _refresh_soon(db, max_age: float) -> None:
    """one background poll at a time."""
    with _refresh_lock:
        if _refreshing["on"]:
            return
        _refreshing["on"] = True

    def run():
        try:
            get_activity(db, max_age=max_age, stale_ok=0)
        finally:
            with _refresh_lock:
                _refreshing["on"] = False
    _spawn(run)


def get_activity(db=None, max_age: float = 2.5, stale_ok: float = STALE_OK) -> Dict[str, Any]:
    """the live payload. a poll younger than ``max_age`` is shared; never raises.

    with ``stale_ok`` (the http route; the drawer opening) a snapshot up to that
    old answers at once and a fresh poll starts behind it; the socket push
    brings the fresh one. ``age_ms`` says how old the answer is so the
    progress bars start from the right place. the sampler and the controls
    pass 0 (and ``max_age=0`` is always a fresh poll): they want the server
    as it is now."""
    try:
        data, at = _snapshot["data"], _snapshot["at"]
        age = time.time() - at
        if stale_ok and max_age > 0 and data is not None and 0 <= age < stale_ok:
            if age >= max_age:
                _refresh_soon(db, max_age)
            return dict(data, age_ms=int(age * 1000))
        with _poll_lock:
            if _snapshot["data"] is not None and time.time() - _snapshot["at"] < max_age:
                return _snapshot["data"]
            data = poll(db)
            _snapshot.update(at=time.time(), data=data)
            return data
    except Exception:   # noqa: BLE001
        logger.exception("server activity poll failed")
        return {"ok": False, "reason": "error", "message": "Server activity failed.", "servers": [],
                "sessions": [], "summary": {"streams": 0}, "bandwidth": bandwidth_series()}


# ── controls ────────────────────────────────────────────────────────────────

def _find(session_key: str) -> Optional[Dict[str, Any]]:
    data = get_activity(max_age=0, stale_ok=0)
    for s in data.get("sessions") or []:
        if str(s.get("session_key")) == str(session_key) or str(s.get("id")) == str(session_key):
            return s
    return None


def stop_session(session_key: str, message: str = "", db=None) -> Dict[str, Any]:
    """end a stream with a message the viewer sees. ``session_key`` is the
    session's ``session_key`` (plex) or ``jellyfin:<id>``."""
    key = str(session_key or "").strip()
    if not key:
        return {"ok": False, "error": "no session"}
    text = str(message or "").strip()[:500] or "The server administrator ended this stream."
    if key.startswith("plex:"):
        key = key[5:]
    if key.startswith("jellyfin:"):
        cfg = sa._jellyfin_config(db)
        if not cfg["base_url"] or not cfg["api_key"]:
            return {"ok": False, "error": "Jellyfin isn't configured."}
        res = jf.stop(cfg, key[len("jellyfin:"):], text)
    else:
        srv = sa._plex_server(db)
        if srv is None:
            return {"ok": False, "error": "server unavailable"}
        with _private_lock:
            sid = _session_ids.get(f"plex:{key}")
        if not sid:
            s = _find(key)
            with _private_lock:
                sid = _session_ids.get(f"plex:{key}") if s else None
        if not sid:
            return {"ok": False, "error": "that stream is no longer active"}
        res = px.stop(srv, sid, text)
    if res.get("ok"):
        with _poll_lock:
            _snapshot.update(at=0.0)       # the next push shows it gone
    return res


def message_session(session_key: str, message: str, header: str = "", db=None) -> Dict[str, Any]:
    """a message without stopping. jellyfin only: plex has no such call."""
    key = str(session_key or "").strip()
    if not key.startswith("jellyfin:"):
        return {"ok": False, "error": "Plex can't show a message without stopping the stream."}
    cfg = sa._jellyfin_config(db)
    if not cfg["base_url"] or not cfg["api_key"]:
        return {"ok": False, "error": "Jellyfin isn't configured."}
    return jf.message(cfg, key[len("jellyfin:"):], message, header=header)


# ── the background sampler ──────────────────────────────────────────────────

_maint: Dict[str, Any] = {"at": 0.0, "running": False}


def _maintenance() -> None:
    try:
        from core.server_activity.settings import get_settings
        days = int(get_settings().get("retention_days") or 0)
        if days:
            n = store.prune(days, time.time())
            if n:
                logger.info("server activity: pruned %d plays older than %d days", n, days)
        from core.server_activity import history
        history.backfill_plex_history()
        history.repair_plex_history()
    except Exception:   # noqa: BLE001
        logger.debug("server activity maintenance failed", exc_info=True)
    finally:
        _maint["running"] = False


def _maybe_maintain(now: float) -> None:
    if _maint["running"] or now - _maint["at"] < 3600:
        return
    _maint.update(at=now, running=True)
    threading.Thread(target=_maintenance, name="server-activity-maint", daemon=True).start()


def sampler_tick(has_subscribers: bool, emit: Optional[Callable[[Dict[str, Any]], None]] = None,
                 now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """one round: poll when a server is configured, feed the recorder (the poll
    does), push to open drawers. returns the payload it used."""
    if not sa.any_server_configured():
        return None
    data = get_activity(max_age=LIVE_INTERVAL - 0.5 if has_subscribers else IDLE_INTERVAL - 0.5, stale_ok=0)
    if has_subscribers and emit is not None:
        emit(data)
    _maybe_maintain(time.time() if now is None else now)
    return data


def run_sampler_forever(sleep: Callable[[float], None], should_stop: Callable[[], bool],
                        has_subscribers: Callable[[], bool],
                        emit: Callable[[Dict[str, Any]], None]) -> None:
    """the loop web_server starts once. 3 s while a drawer is open, 10 s while
    nobody looks (so plays are still recorded), nothing without a server."""
    while not should_stop():
        try:
            subs = bool(has_subscribers())
        except Exception:   # noqa: BLE001
            subs = False
        # sleep in slices so a drawer opening mid-wait gets its first push quickly
        waited, interval = 0.0, (LIVE_INTERVAL if subs else IDLE_INTERVAL)
        while waited < interval and not should_stop():
            sleep(1.0)
            waited += 1.0
            if not subs and has_subscribers():
                break
        try:
            sampler_tick(bool(has_subscribers()), emit)
        except Exception:   # noqa: BLE001
            logger.debug("server activity sampler tick failed", exc_info=True)
    try:
        writer.flush()
    except Exception:   # noqa: BLE001
        logger.debug("final play log flush failed", exc_info=True)
