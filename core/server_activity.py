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
    """The live activity payload. Never raises — an unconfigured / down server
    is a normal state the UI shows, not an error."""
    srv = _plex_server(db)
    if srv is None:
        return {"ok": False, "reason": "no_server",
                "message": "No Plex server configured (or it's unreachable).",
                "sessions": [], "summary": {"streams": 0}}
    try:
        raw = srv.sessions()
    except Exception:   # noqa: BLE001
        logger.debug("plex sessions() failed", exc_info=True)
        return {"ok": False, "reason": "unreachable",
                "message": "Couldn't reach the Plex server.",
                "sessions": [], "summary": {"streams": 0}}
    sessions = []
    for item in raw or []:
        try:
            sessions.append(normalize_session(item))
        except Exception:   # noqa: BLE001 - one odd session never blanks the view
            logger.debug("session normalize failed", exc_info=True)
    sessions.sort(key=lambda s: (s["state"] != "playing", s["user"].lower()))
    return _summarize(sessions, str(_g(srv, "friendlyName", "Plex") or "Plex"),
                      str(_g(srv, "version", "") or ""))


def fetch_image(path: str, db=None) -> Optional[tuple]:
    """(bytes, content_type) for a Plex image path — proxied server-side so the
    token never reaches the browser. None on any failure."""
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
