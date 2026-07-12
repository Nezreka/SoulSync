"""Live server activity — a Tautulli-style view of what's playing on the server.

App-wide (music AND video): Plex's ``sessions()`` returns every active stream
regardless of library type, so one connection powers the whole live view. The
normalization (raw plexapi session objects → a clean JSON payload) is pure and
defensive — plexapi attributes vary by media type and version, so every access
is guarded and a weird session degrades gracefully instead of blanking the view.

Plex is first-class; Jellyfin support is a best-effort follow-on (its /Sessions
shape differs and is added in :func:`_jellyfin_sessions`).
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("server_activity")

_PLEX_TIMEOUT = 8          # a live view must never hang the UI on a slow server


def _g(obj: Any, attr: str, default: Any = None) -> Any:
    """getattr that never raises (plexapi lazy-loads + attrs vary by type)."""
    try:
        v = getattr(obj, attr, default)
        return v if v is not None else default
    except Exception:   # noqa: BLE001
        return default


def _first(seq: Any) -> Any:
    try:
        return seq[0] if seq else None
    except Exception:   # noqa: BLE001
        return None


def _plex_config(db=None) -> Dict[str, str]:
    """Any working Plex connection config — the music config first (SoulSync's
    origin), then the video side's effective creds. Both usually point at the
    same server, and sessions() returns everything, so either works."""
    try:
        from config.settings import config_manager
        cfg = config_manager.get_plex_config() or {}
        if cfg.get("base_url") and cfg.get("token"):
            return {"base_url": cfg["base_url"], "token": cfg["token"]}
    except Exception:   # noqa: BLE001, S110 - music config missing is normal
        pass
    try:
        from core.video.sources import video_plex_config
        cfg = video_plex_config(db)
        if cfg.get("base_url") and cfg.get("token"):
            return {"base_url": cfg["base_url"], "token": cfg["token"]}
    except Exception:   # noqa: BLE001, S110 - no config at all is a valid state
        pass
    return {"base_url": "", "token": ""}


_server_cache: Dict[str, Any] = {"srv": None, "at": 0.0, "key": ""}


def _plex_server(db=None):
    """A connected PlexServer (cached ~60s so a 3s poll doesn't reconnect each
    time). Returns None when Plex isn't configured or is unreachable."""
    cfg = _plex_config(db)
    if not cfg["base_url"] or not cfg["token"]:
        return None
    key = cfg["base_url"] + "|" + cfg["token"][:6]
    now = time.time()
    if _server_cache["srv"] is not None and _server_cache["key"] == key and now - _server_cache["at"] < 60:
        return _server_cache["srv"]
    try:
        from plexapi.server import PlexServer
        srv = PlexServer(cfg["base_url"], cfg["token"], timeout=_PLEX_TIMEOUT)
        _server_cache.update(srv=srv, at=now, key=key)
        return srv
    except Exception:   # noqa: BLE001 - unreachable server is an expected state
        logger.debug("plex connect failed for activity", exc_info=True)
        _server_cache.update(srv=None, at=now, key=key)
        return None


# ── normalization (pure — unit-tested with fakes) ────────────────────────────

def _pct(offset: Any, duration: Any) -> int:
    try:
        o, d = float(offset or 0), float(duration or 0)
        return max(0, min(100, round(100 * o / d))) if d > 0 else 0
    except (TypeError, ValueError):
        return 0


def _stream_of(item: Any) -> Dict[str, Any]:
    """Play method + source stream detail (Tautulli's transcode line)."""
    media = _first(_g(item, "media", []))
    src_res = str(_g(media, "videoResolution", "") or "").upper()
    if src_res.isdigit():
        src_res += "P"
    src_vcodec = str(_g(media, "videoCodec", "") or "").upper()
    src_acodec = str(_g(media, "audioCodec", "") or "").upper()
    bitrate = _g(media, "bitrate", 0)

    tc = _first(_g(item, "transcodeSessions", []))
    if tc is not None:
        v_dec = str(_g(tc, "videoDecision", "") or "").lower()
        a_dec = str(_g(tc, "audioDecision", "") or "").lower()
        # 'transcode' anywhere → Transcode; else 'copy' → Direct Stream
        if v_dec == "transcode" or a_dec == "transcode":
            method = "Transcode"
        else:
            method = "Direct Stream"
        tgt_vcodec = str(_g(tc, "videoCodec", "") or "").upper()
        video = src_vcodec + ((" → " + tgt_vcodec) if (v_dec == "transcode" and tgt_vcodec) else "")
        tgt_acodec = str(_g(tc, "audioCodec", "") or "").upper()
        audio = src_acodec + ((" → " + tgt_acodec) if (a_dec == "transcode" and tgt_acodec) else "")
        return {"method": method, "video": video.strip(" →"), "audio": audio.strip(" →"),
                "resolution": src_res, "bitrate_kbps": bitrate,
                "transcode_progress": round(float(_g(tc, "progress", 0) or 0)),
                "throttled": bool(_g(tc, "throttled", False)),
                "hw": bool(_g(tc, "transcodeHwEncoding", False) or _g(tc, "transcodeHwRequested", False)),
                "container": str(_g(tc, "container", "") or "")}
    return {"method": "Direct Play", "video": src_vcodec, "audio": src_acodec,
            "resolution": src_res, "bitrate_kbps": bitrate, "transcode_progress": 0,
            "throttled": False, "hw": False, "container": str(_g(media, "container", "") or "")}


def _title_of(item: Any, mtype: str) -> Dict[str, str]:
    """(title, subtitle, grandparent) shaped per media type."""
    title = str(_g(item, "title", "") or "")
    gp = str(_g(item, "grandparentTitle", "") or "")     # show / artist
    parent = str(_g(item, "parentTitle", "") or "")      # season / album
    if mtype == "episode":
        s, e = _g(item, "parentIndex"), _g(item, "index")
        code = ""
        if s is not None and e is not None:
            code = "S%02dE%02d" % (int(s), int(e))
        return {"title": title, "subtitle": (gp + (" · " + code if code else "")).strip(" ·"),
                "grandparent": gp}
    if mtype == "track":
        return {"title": title, "subtitle": (gp + (" · " + parent if parent else "")).strip(" ·"),
                "grandparent": gp}
    year = _g(item, "year")
    return {"title": title, "subtitle": str(year) if year else "", "grandparent": gp}


def normalize_session(item: Any) -> Dict[str, Any]:
    """One raw plexapi session → the clean activity card payload. Defensive:
    any attribute may be missing on a given media type / server version."""
    mtype = str(_g(item, "type", "") or "").lower()
    player = _first(_g(item, "players", []))
    sess = _g(item, "session")
    names = _title_of(item, mtype)
    # USERNAME ONLY from the local XML list — never touch `.user`/`.users`, which
    # lazy-loads a MyPlexAccount over the network (a plex.tv round-trip PER session,
    # per poll). The frontend renders an initials avatar instead of a user thumb.
    username = str(_first(_g(item, "usernames", [])) or _g(item, "_username", "") or "Someone")

    state = str(_g(player, "state", "") or "playing").lower()
    thumb = _g(item, "grandparentThumb") or _g(item, "parentThumb") or _g(item, "thumb")
    art = _g(item, "art") or _g(item, "grandparentArt")

    return {
        "session_key": str(_g(item, "sessionKey", "") or _g(sess, "id", "") or ""),
        "media_type": mtype,
        "title": names["title"],
        "subtitle": names["subtitle"],
        "grandparent": names["grandparent"],
        "thumb": str(thumb or ""),
        "art": str(art or ""),
        "duration_ms": int(_g(item, "duration", 0) or 0),
        "offset_ms": int(_g(item, "viewOffset", 0) or 0),
        "progress_pct": _pct(_g(item, "viewOffset"), _g(item, "duration")),
        "state": state if state in ("playing", "paused", "buffering") else "playing",
        "user": username,
        "user_id": str(_g(item, "_userId", "") or ""),
        "player": {
            "product": str(_g(player, "product", "") or ""),
            "device": str(_g(player, "device", "") or _g(player, "title", "") or ""),
            "platform": str(_g(player, "platform", "") or ""),
            "title": str(_g(player, "title", "") or ""),
        },
        "location": str(_g(sess, "location", "") or "").lower(),
        "bandwidth_kbps": int(_g(sess, "bandwidth", 0) or 0),
        "stream": _stream_of(item),
    }


# ── Jellyfin activity (best-effort; UNVERIFIED against a live Jellyfin) ───────

def _jellyfin_config(db=None) -> Dict[str, str]:
    """Any working Jellyfin config — music first, then video's effective creds."""
    try:
        from config.settings import config_manager
        cfg = config_manager.get_jellyfin_config() or {}
        if cfg.get("base_url") and cfg.get("api_key"):
            return {"base_url": cfg["base_url"], "api_key": cfg["api_key"]}
    except Exception:   # noqa: BLE001, S110
        pass
    try:
        from core.video.sources import video_jellyfin_config
        cfg = video_jellyfin_config(db)
        if cfg.get("base_url") and cfg.get("api_key"):
            return {"base_url": cfg["base_url"], "api_key": cfg["api_key"]}
    except Exception:   # noqa: BLE001, S110
        pass
    return {"base_url": "", "api_key": ""}


_JF_METHOD = {"transcode": "Transcode", "directstream": "Direct Stream", "directplay": "Direct Play"}
_JF_TYPE = {"movie": "movie", "episode": "episode", "audio": "track", "musicvideo": "clip"}


def normalize_jellyfin(s: Dict[str, Any], npi: Dict[str, Any]) -> Dict[str, Any]:
    """One Jellyfin /Sessions entry (with NowPlayingItem) → the shared card
    shape. Ticks are 100ns; images ride a 'jf:<itemId>' marker the proxy resolves."""
    ptype = str(npi.get("Type") or "").lower()
    mtype = _JF_TYPE.get(ptype, ptype)
    ps = s.get("PlayState") or {}
    ti = s.get("TranscodingInfo") or {}
    dur = int(npi.get("RunTimeTicks") or 0) // 10000
    off = int(ps.get("PositionTicks") or 0) // 10000
    method = _JF_METHOD.get(str(ps.get("PlayMethod") or "").lower().replace(" ", ""),
                            str(ps.get("PlayMethod") or "Direct Play"))
    if mtype == "episode":
        show = str(npi.get("SeriesName") or "")
        sn, en = npi.get("ParentIndexNumber"), npi.get("IndexNumber")
        code = ("S%02dE%02d" % (int(sn), int(en))) if (sn is not None and en is not None) else ""
        subtitle, gp = (show + (" · " + code if code else "")).strip(" ·"), show
    elif mtype == "track":
        artist = str(npi.get("AlbumArtist") or (npi.get("Artists") or [""])[0] or "")
        album = str(npi.get("Album") or "")
        subtitle, gp = (artist + (" · " + album if album else "")).strip(" ·"), artist
    else:
        subtitle, gp = str(npi.get("ProductionYear") or ""), ""
    item_id = str(npi.get("Id") or "")
    thumb = ("jf:" + item_id) if item_id else ""
    br = int(ti.get("Bitrate") or 0) // 1000
    return {
        "session_key": "",   # no stop button — our terminate path is Plex-only
        "media_type": mtype, "title": str(npi.get("Name") or ""),
        "subtitle": subtitle, "grandparent": gp, "thumb": thumb, "art": thumb,
        "duration_ms": dur, "offset_ms": off,
        "progress_pct": (max(0, min(100, round(100 * off / dur))) if dur else 0),
        "state": "paused" if ps.get("IsPaused") else "playing",
        "user": str(s.get("UserName") or "Someone"), "user_id": str(s.get("UserId") or ""),
        "player": {"product": str(s.get("Client") or ""), "device": str(s.get("DeviceName") or ""),
                   "platform": str(s.get("Client") or ""), "title": str(s.get("DeviceName") or "")},
        "location": "", "bandwidth_kbps": br,
        "stream": {"method": method, "video": str(ti.get("VideoCodec") or "").upper(),
                   "audio": str(ti.get("AudioCodec") or "").upper(), "resolution": "",
                   "bitrate_kbps": br, "transcode_progress": round(float(ti.get("CompletionPercentage") or 0)),
                   "throttled": False, "hw": False, "container": str(ti.get("Container") or "")},
    }


def _jellyfin_activity(db=None) -> tuple:
    """(sessions, server_name|None). name is None only when Jellyfin isn't
    configured. Best-effort + defensive — never raises into get_activity."""
    cfg = _jellyfin_config(db)
    if not cfg["base_url"] or not cfg["api_key"]:
        return [], None
    try:
        import requests
        r = requests.get(cfg["base_url"].rstrip("/") + "/Sessions",
                         headers={"X-Emby-Token": cfg["api_key"]}, timeout=_PLEX_TIMEOUT)
        raw = r.json() if r.status_code == 200 else []
    except Exception:   # noqa: BLE001
        logger.debug("jellyfin /Sessions failed", exc_info=True)
        return [], "Jellyfin"
    out = []
    for s in raw or []:
        try:
            npi = s.get("NowPlayingItem")
            if npi:
                out.append(normalize_jellyfin(s, npi))
        except Exception:   # noqa: BLE001
            logger.debug("jellyfin session normalize failed", exc_info=True)
    return out, "Jellyfin"


def _summarize(sessions: List[Dict[str, Any]], server_name: str, version: str) -> Dict[str, Any]:
    transcodes = sum(1 for s in sessions if s["stream"]["method"] == "Transcode")
    direct = sum(1 for s in sessions if s["stream"]["method"] == "Direct Play")
    return {
        "ok": True,
        "server": {"name": server_name, "version": version, "platform": "plex"},
        "summary": {
            "streams": len(sessions),
            "transcodes": transcodes,
            "direct_play": direct,
            "direct_stream": len(sessions) - transcodes - direct,
            "total_bandwidth_kbps": sum(s["bandwidth_kbps"] for s in sessions),
            "lan": sum(1 for s in sessions if s["location"] == "lan"),
            "wan": sum(1 for s in sessions if s["location"] == "wan"),
        },
        "sessions": sessions,
    }


def get_activity(db=None) -> Dict[str, Any]:
    """The live activity payload — Plex AND Jellyfin sessions merged. Never
    raises; an unconfigured / down server is a normal state the UI shows."""
    sessions: List[Dict[str, Any]] = []
    server_name, server_version, platform = "", "", None
    srv = _plex_server(db)
    plex_reachable = False
    if srv is not None:
        try:
            raw = srv.sessions() or []
            plex_reachable = True
            for item in raw:
                try:
                    sessions.append(normalize_session(item))
                except Exception:   # noqa: BLE001 - one odd session never blanks the view
                    logger.debug("session normalize failed", exc_info=True)
            server_name = str(_g(srv, "friendlyName", "Plex") or "Plex")
            server_version = str(_g(srv, "version", "") or "")
            platform = "plex"
        except Exception:   # noqa: BLE001 - configured but unreachable
            logger.debug("plex sessions() failed", exc_info=True)

    jf_sessions, jf_name = _jellyfin_activity(db)
    if jf_name is not None:                       # Jellyfin is configured
        sessions += jf_sessions
        if not server_name:
            server_name = jf_name
        platform = "jellyfin" if platform is None else "plex+jellyfin"

    if srv is None and jf_name is None:           # nothing configured at all
        return {"ok": False, "reason": "no_server",
                "message": "No Plex or Jellyfin server configured.",
                "sessions": [], "summary": {"streams": 0}}
    if not plex_reachable and jf_name is None:    # the one configured server is down
        return {"ok": False, "reason": "unreachable",
                "message": "Couldn't reach the media server.",
                "sessions": [], "summary": {"streams": 0}}

    sessions.sort(key=lambda s: (s["state"] != "playing", s["user"].lower()))
    out = _summarize(sessions, server_name or "Server", server_version)
    out["server"]["platform"] = platform or "plex"
    return out


# ── stream termination (Tautulli's kill-with-message) ────────────────────────

def stop_session(session_key: str, message: str = "", db=None) -> Dict[str, Any]:
    """End an active stream by its session key, with an optional message shown
    to the viewer (Plex supports this). Returns {ok, error?}."""
    if not session_key:
        return {"ok": False, "error": "no session"}
    srv = _plex_server(db)
    if srv is None:
        return {"ok": False, "error": "server unavailable"}
    reason = str(message or "").strip() or "The server administrator ended this stream."
    try:
        for item in srv.sessions() or []:
            if str(_g(item, "sessionKey", "")) == str(session_key):
                item.stop(reason=reason)
                return {"ok": True}
        return {"ok": False, "error": "that stream is no longer active"}
    except Exception as e:   # noqa: BLE001
        logger.debug("stop_session failed", exc_info=True)
        return {"ok": False, "error": str(e) or "couldn't stop the stream"}


# ── history (Phase 2) ────────────────────────────────────────────────────────

_lookup_cache: Dict[str, Any] = {"accounts": {}, "devices": {}, "at": 0.0, "key": ""}


def _lookups(srv, key: str) -> tuple:
    """(accountID→name, deviceID→name). Accounts/devices change rarely, so this
    is cached ~5min — history rows resolve their user/device without a per-row
    network call."""
    now = time.time()
    if _lookup_cache["key"] == key and now - _lookup_cache["at"] < 300 and _lookup_cache["accounts"]:
        return _lookup_cache["accounts"], _lookup_cache["devices"]
    accounts, devices = {}, {}
    try:
        for a in srv.systemAccounts():
            accounts[_g(a, "id")] = str(_g(a, "name", "") or _g(a, "title", "") or "")
    except Exception:   # noqa: BLE001
        logger.debug("systemAccounts failed", exc_info=True)
    try:
        for d in srv.systemDevices():
            devices[_g(d, "id")] = str(_g(d, "name", "") or _g(d, "clientIdentifier", "") or "")
    except Exception:   # noqa: BLE001
        logger.debug("systemDevices failed", exc_info=True)
    _lookup_cache.update(accounts=accounts, devices=devices, at=now, key=key)
    return accounts, devices


def normalize_history(item: Any, accounts: Dict, devices: Dict) -> Dict[str, Any]:
    """One raw plexapi history item → a clean history row. Defensive."""
    mtype = str(_g(item, "type", "") or "").lower()
    names = _title_of(item, mtype)
    viewed = _g(item, "viewedAt")
    epoch = 0
    try:
        epoch = int(viewed.timestamp()) if viewed else 0
    except Exception:   # noqa: BLE001
        epoch = 0
    return {
        "title": names["title"],
        "subtitle": names["subtitle"],
        "media_type": mtype,
        "user": accounts.get(_g(item, "accountID"), "") or "Someone",
        "device": devices.get(_g(item, "deviceID"), "") or "",
        "thumb": str(_g(item, "grandparentThumb") or _g(item, "parentThumb") or _g(item, "thumb") or ""),
        "viewed_epoch": epoch,
    }


def get_history(db=None, limit: int = 40) -> Dict[str, Any]:
    """Recent watch/listen history (Tautulli's History). Never raises."""
    srv = _plex_server(db)
    if srv is None:
        return {"ok": False, "reason": "no_server", "history": []}
    try:
        limit = max(1, min(200, int(limit)))
        items = srv.history(maxresults=limit)
    except Exception:   # noqa: BLE001
        logger.debug("plex history() failed", exc_info=True)
        return {"ok": False, "reason": "unreachable", "history": []}
    key = str(_g(srv, "machineIdentifier", "") or "plex")
    accounts, devices = _lookups(srv, key)
    rows = []
    for item in items or []:
        try:
            rows.append(normalize_history(item, accounts, devices))
        except Exception:   # noqa: BLE001
            logger.debug("history normalize failed", exc_info=True)
    rows.sort(key=lambda r: r["viewed_epoch"], reverse=True)   # newest first
    return {"ok": True, "history": rows}


# ── statistics (Phase 3 — beat the Tautulli/Plex dashboard glance) ───────────

_stats_cache: Dict[str, Any] = {"data": None, "at": 0.0, "key": ""}


def _content_key(item: Any, mtype: str) -> tuple:
    """(group key, display title, thumb) for the most-watched roll-up: movies by
    their own title, episodes by SHOW, tracks by ARTIST."""
    title = str(_g(item, "title", "") or "")
    gp = str(_g(item, "grandparentTitle", "") or "")
    if mtype == "movie" or not gp:
        return (title, title, str(_g(item, "thumb", "") or ""))
    return (gp, gp, str(_g(item, "grandparentThumb", "") or _g(item, "thumb", "") or ""))


def compute_stats(items: List[Any], accounts: Dict, devices: Dict, days: int) -> Dict[str, Any]:
    """Aggregate raw history into the dashboard stats. Pure — tested with fakes."""
    from datetime import datetime, timedelta
    content: Dict[str, Dict] = {}
    users: Dict[str, int] = {}
    device_counts: Dict[str, int] = {}
    day_counts: Dict[str, int] = {}
    total = 0
    for it in items or []:
        try:
            mtype = str(_g(it, "type", "") or "").lower()
            total += 1
            ck, ctitle, cthumb = _content_key(it, mtype)
            if ck:
                c = content.setdefault(ck, {"title": ctitle, "plays": 0, "thumb": cthumb, "media_type": mtype})
                c["plays"] += 1
            uname = accounts.get(_g(it, "accountID")) or "Someone"
            users[uname] = users.get(uname, 0) + 1
            dname = devices.get(_g(it, "deviceID")) or "Unknown device"
            device_counts[dname] = device_counts.get(dname, 0) + 1
            viewed = _g(it, "viewedAt")
            if viewed is not None:
                day_counts[viewed.date().isoformat()] = day_counts.get(viewed.date().isoformat(), 0) + 1
        except Exception:   # noqa: BLE001 - one odd row never skews the whole roll-up
            continue
    top_content = sorted(content.values(), key=lambda c: c["plays"], reverse=True)[:8]
    top_users = [{"user": k, "plays": v} for k, v in
                 sorted(users.items(), key=lambda kv: kv[1], reverse=True)[:6]]
    top_devices = [{"device": k, "plays": v} for k, v in
                   sorted(device_counts.items(), key=lambda kv: kv[1], reverse=True)[:6]]
    # plays-over-time: an ordered bucket per day for the last 14 days (graph x-axis)
    today = datetime.now().date()
    series = []
    for i in range(13, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        series.append({"date": d, "plays": day_counts.get(d, 0)})
    return {"ok": True, "days": days, "total_plays": total, "unique_users": len(users),
            "top_content": top_content, "top_users": top_users,
            "top_devices": top_devices, "series": series}


def get_stats(db=None, days: int = 30) -> Dict[str, Any]:
    """The stats dashboard. Cached ~10min — the history fetch it aggregates is
    the expensive call. Never raises."""
    srv = _plex_server(db)
    if srv is None:
        return {"ok": False, "reason": "no_server"}
    days = max(1, min(90, int(days)))
    key = str(_g(srv, "machineIdentifier", "") or "plex") + ":" + str(days)
    if _stats_cache["data"] is not None and _stats_cache["key"] == key and time.time() - _stats_cache["at"] < 600:
        return _stats_cache["data"]
    try:
        from datetime import datetime, timedelta
        items = srv.history(maxresults=1000, mindate=datetime.now() - timedelta(days=days))
    except Exception:   # noqa: BLE001
        logger.debug("plex history() for stats failed", exc_info=True)
        return {"ok": False, "reason": "unreachable"}
    accounts, devices = _lookups(srv, str(_g(srv, "machineIdentifier", "") or "plex"))
    data = compute_stats(items or [], accounts, devices, days)
    _stats_cache.update(data=data, at=time.time(), key=key)
    return data


def _fetch_jellyfin_image(item_id: str, db=None) -> Optional[tuple]:
    cfg = _jellyfin_config(db)
    if not item_id or not cfg["base_url"] or not cfg["api_key"]:
        return None
    try:
        import requests
        url = cfg["base_url"].rstrip("/") + "/Items/" + item_id + "/Images/Primary"
        r = requests.get(url, params={"api_key": cfg["api_key"], "maxHeight": 480}, timeout=_PLEX_TIMEOUT)
        if r.status_code == 200 and r.content:
            return r.content, r.headers.get("Content-Type", "image/jpeg")
    except Exception:   # noqa: BLE001
        logger.debug("jellyfin image proxy failed for %s", item_id, exc_info=True)
    return None


def fetch_image(path: str, db=None) -> Optional[tuple]:
    """(bytes, content_type) for a Plex or Jellyfin image — proxied server-side
    so the token never reaches the browser. None on any failure."""
    if path and str(path).startswith("jf:"):
        return _fetch_jellyfin_image(str(path)[3:], db)
    if not path or not str(path).startswith("/"):
        return None
    cfg = _plex_config(db)
    if not cfg["base_url"] or not cfg["token"]:
        return None
    try:
        import requests
        url = cfg["base_url"].rstrip("/") + path
        r = requests.get(url, params={"X-Plex-Token": cfg["token"]}, timeout=_PLEX_TIMEOUT)
        if r.status_code == 200 and r.content:
            return r.content, r.headers.get("Content-Type", "image/jpeg")
    except Exception:   # noqa: BLE001
        logger.debug("plex image proxy failed for %s", path, exc_info=True)
    return None
