"""Real Soulseek (slskd) search for the video Download view.

Replaces the mock for the Soulseek source: it POSTs a search to the slskd instance
(the shared ``soulseek.*`` config used by the music side too), polls for responses,
keeps the video files, and GROUPS them by release folder so each card is one release
(with a peer count) rather than one row per user. The parse → evaluate → rank pipeline
downstream is unchanged — this just returns the same ``{title, size_bytes, …}`` shape.

Isolated: imports only stdlib + requests + the shared ``config_manager`` (app config,
not music code). The pure helpers (build_query / group_video_files) are unit-tested;
the HTTP poll is thin I/O glue.
"""

from __future__ import annotations

import time
from typing import Any

import requests

# Container extensions we treat as the actual video (everything else — subs, nfo,
# art, samples — is ignored for quality purposes).
VIDEO_EXTS = frozenset((
    "mkv", "mp4", "avi", "m4v", "mov", "wmv", "ts", "m2ts", "mpg", "mpeg",
    "webm", "flv", "vob", "divx", "mk3d",
))


def _conn():
    from config.settings import config_manager
    base = str(config_manager.get("soulseek.slskd_url", "") or "").rstrip("/")
    key = config_manager.get("soulseek.api_key", "") or ""
    return base, ({"X-API-Key": key} if key else {})


def build_query(scope: str, title: Any, *, year: Any = None, season: Any = None,
                episode: Any = None) -> str:
    """The text we hand slskd for a given scope (movie / episode / season / series)."""
    t = str(title or "").strip()
    scope = (scope or "movie").lower()
    try:
        s_i = int(season) if season is not None else None
        e_i = int(episode) if episode is not None else None
    except (TypeError, ValueError):
        s_i = e_i = None
    if scope == "episode" and s_i is not None and e_i is not None:
        return "%s S%02dE%02d" % (t, s_i, e_i)
    if scope == "season" and s_i is not None:
        return "%s S%02d" % (t, s_i)
    if scope == "movie" and year:
        return ("%s %s" % (t, year)).strip()
    return t   # series / fallback


def _is_video(filename: str) -> bool:
    fn = str(filename or "").replace("\\", "/")
    base = fn.rsplit("/", 1)[-1]
    if "sample" in base.lower():
        return False
    ext = base.rsplit(".", 1)[-1].lower() if "." in base else ""
    return ext in VIDEO_EXTS


def _release_name(filename: str) -> str:
    """The release a file belongs to — its parent folder (where scene/p2p put the
    quality), falling back to the bare filename (sans extension)."""
    fn = str(filename or "").replace("\\", "/").strip("/")
    parts = [p for p in fn.split("/") if p]
    if len(parts) >= 2:
        return parts[-2]
    base = parts[-1] if parts else ""
    return base.rsplit(".", 1)[0] if "." in base else base


def group_video_files(responses: Any) -> list:
    """Flatten slskd responses → one hit per release folder, with a peer count and the
    best (fastest, most-available) source. Pure — drives the unit tests."""
    groups: dict = {}
    for resp in (responses if isinstance(responses, list) else []):
        if not isinstance(resp, dict):
            continue
        user = resp.get("username")
        speed = resp.get("uploadSpeed", 0) or 0
        slots = resp.get("freeUploadSlots", 0) or 0
        for f in (resp.get("files") or []):
            fn = f.get("filename", "")
            if not _is_video(fn):
                continue
            rel = _release_name(fn)
            g = groups.get(rel)
            if g is None:
                g = groups[rel] = {"title": rel, "size_bytes": 0, "users": set(),
                                   "best_speed": -1, "username": None, "slots": 0, "filename": fn}
            g["size_bytes"] = max(g["size_bytes"], f.get("size", 0) or 0)
            if user:
                g["users"].add(user)
            if speed > g["best_speed"]:
                g["best_speed"] = speed
                g["username"] = user
                g["slots"] = slots
                g["filename"] = fn
    out = []
    for g in groups.values():
        out.append({"title": g["title"], "size_bytes": g["size_bytes"], "peers": len(g["users"]),
                    "username": g["username"], "slots": g["slots"], "filename": g["filename"]})
    out.sort(key=lambda h: (h["peers"], h["size_bytes"]), reverse=True)
    return out


def _min_speed_bytes() -> int:
    from config.settings import config_manager
    try:
        return int(config_manager.get("soulseek.min_peer_upload_speed", 0) or 0) * 125000
    except (TypeError, ValueError):
        return 0


def search_timeout_ms() -> int:
    """How long to ask slskd to keep searching — the SAME ``soulseek.search_timeout``
    the music side uses (default 60s), so results have time to arrive."""
    from config.settings import config_manager
    try:
        secs = int(config_manager.get("soulseek.search_timeout", 60) or 60)
    except (TypeError, ValueError):
        secs = 60
    return max(10, min(120, secs)) * 1000


def start_search(query: str) -> dict:
    """Kick off a slskd search (don't wait). Returns {configured[, id][, error]}.
    The caller polls ``poll_responses(id)`` until satisfied (like the music side)."""
    base, headers = _conn()
    if not base:
        return {"configured": False}
    payload = {"searchText": query, "timeout": search_timeout_ms(), "filterResponses": True,
               "minimumResponseFileCount": 1, "minimumPeerUploadSpeed": _min_speed_bytes()}
    try:
        r = requests.post(base + "/api/v0/searches", json=payload, headers=headers, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception as e:   # noqa: BLE001 - surface any slskd/network failure to the UI
        return {"configured": True, "error": str(e)}
    sid = data.get("id") if isinstance(data, dict) else (
        data[0].get("id") if isinstance(data, list) and data and isinstance(data[0], dict) else None)
    if not sid:
        return {"configured": True, "error": "slskd returned no search id"}
    return {"configured": True, "id": sid}


def stop_search(search_id) -> None:
    """Stop + forget a search on slskd once we're done polling it. slskd otherwise keeps
    every search running its full ``search_timeout`` (default 60s), so the bounded automation
    searches — which finish fast when results come in — pile up dozens-deep and swamp slskd.
    Best-effort; a failed cleanup never matters."""
    base, headers = _conn()
    if not base or not search_id:
        return
    try:
        requests.delete(base + "/api/v0/searches/%s" % search_id, headers=headers, timeout=10)
    except Exception:   # noqa: BLE001, S110 - cleanup is best-effort
        pass


def poll_responses(search_id: str) -> list:
    """Current grouped video hits for an in-flight search (cheap; call repeatedly)."""
    return poll_search(search_id)["hits"]


def poll_search(search_id: str) -> dict:
    """Poll an in-flight search → {hits (grouped video releases), total_files (every
    file slskd returned, incl. non-video)}. total_files lets the UI distinguish
    'nothing back yet' from 'plenty back but it's all audio/junk, no video'."""
    base, headers = _conn()
    if not base or not search_id:
        return {"hits": [], "total_files": 0}
    try:
        r = requests.get(base + "/api/v0/searches/%s/responses" % search_id, headers=headers, timeout=15)
        if not r.ok:
            return {"hits": [], "total_files": 0}
        data = r.json()
    except Exception:   # noqa: BLE001, S110 - transient error → no new hits this poll
        return {"hits": [], "total_files": 0}
    total = 0
    for u in (data if isinstance(data, list) else []):
        if isinstance(u, dict):
            for d in (u.get("directories") or []):
                total += len(d.get("files") or [])
    return {"hits": group_video_files(data), "total_files": total}


def slskd_search(query: str, *, max_seconds: int = 8, slskd_timeout_ms: int = 4500) -> dict:
    """POST a search to slskd, poll responses, return {configured, hits[, error]}.
    Thin I/O glue around ``group_video_files``."""
    from config.settings import config_manager
    base = str(config_manager.get("soulseek.slskd_url", "") or "").rstrip("/")
    if not base:
        return {"configured": False, "hits": []}
    key = config_manager.get("soulseek.api_key", "") or ""
    headers = {"X-API-Key": key} if key else {}
    try:
        min_mbps = int(config_manager.get("soulseek.min_peer_upload_speed", 0) or 0)
    except (TypeError, ValueError):
        min_mbps = 0
    payload = {"searchText": query, "timeout": slskd_timeout_ms, "filterResponses": True,
               "minimumResponseFileCount": 1, "minimumPeerUploadSpeed": min_mbps * 125000}
    try:
        r = requests.post(base + "/api/v0/searches", json=payload, headers=headers, timeout=10)
        r.raise_for_status()
        data = r.json()
    except Exception as e:   # noqa: BLE001 - surface any slskd/network failure to the UI
        return {"configured": True, "hits": [], "error": str(e)}
    sid = data.get("id") if isinstance(data, dict) else (
        data[0].get("id") if isinstance(data, list) and data and isinstance(data[0], dict) else None)
    if not sid:
        return {"configured": True, "hits": [], "error": "slskd returned no search id"}

    responses: list = []
    deadline = time.monotonic() + max_seconds
    while time.monotonic() < deadline:
        try:
            rr = requests.get(base + "/api/v0/searches/%s/responses" % sid, headers=headers, timeout=10)
            if rr.ok:
                body = rr.json()
                if isinstance(body, list):
                    responses = body
        except Exception:   # noqa: BLE001, S110 - keep polling through transient errors
            pass
        if len(responses) >= 25:
            break
        time.sleep(1)
    return {"configured": True, "hits": group_video_files(responses)}


__all__ = ["VIDEO_EXTS", "build_query", "group_video_files", "slskd_search",
           "start_search", "poll_responses", "poll_search", "search_timeout_ms"]
