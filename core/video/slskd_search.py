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

import re
import time
from typing import Any

import requests

# slskd rate-limits search CREATION and returns 429 when exceeded. The budget lives in
# core.slskd_throttle and is SHARED with the music side — one slskd instance, one window.
# Video adds a small min-gap between creations (the auto-grab fires from a thread pool,
# so without it a burst — or a 429 that returns instantly — storms slskd).
from core.slskd_throttle import note_rate_limited as _note_rate_limited  # noqa: F401 (re-export for callers/tests)
from core.slskd_throttle import reserve_search_slot

_MIN_GAP_SECONDS = 2.0
# How long an HTTP request handler may wait on the shared budget before giving
# up: a user's manual search shouldn't hang for minutes (and trip gunicorn's
# 120s worker timeout) because background sync has the window drained.
_INTERACTIVE_MAX_WAIT_SECONDS = 20.0


def _throttle_search(max_wait: float | None = None) -> bool:
    """Block until the shared budget allows the next slskd search creation.
    With ``max_wait``, give up (returning False, consuming nothing) instead of
    blocking longer — for interactive callers."""
    slot = reserve_search_slot(_MIN_GAP_SECONDS, max_wait_seconds=max_wait)
    if slot is None:
        return False
    wait = slot - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    return True

_RATE_LIMIT_BUSY_MSG = "search rate limit reached (shared slskd budget) — try again in a moment"

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
    headers = {"Accept": "application/json"}   # make slskd answer in JSON, not whatever default
    if key:
        headers["X-API-Key"] = key
    return base, headers


def build_query(scope: str, title: Any, *, year: Any = None, season: Any = None,
                episode: Any = None, air_date: Any = None, absolute: Any = None,
                series_type: Any = None) -> str:
    """The text we hand slskd for a given scope (movie / episode / season / series).
    Series type (P8) picks the episode identity the SCENE actually names releases
    by: daily shows release by air date ('Title 2026.07.08'), anime by absolute
    number ('Title 1071') — an SxxExx query would simply never find those."""
    t = str(title or "").strip()
    scope = (scope or "movie").lower()
    st = str(series_type or "").lower()
    try:
        s_i = int(season) if season is not None else None
        e_i = int(episode) if episode is not None else None
    except (TypeError, ValueError):
        s_i = e_i = None
    if scope == "episode":
        ad = str(air_date or "")[:10]
        if st == "daily" and len(ad) == 10:
            return "%s %s" % (t, ad.replace("-", "."))
        if st == "anime" and absolute:
            return "%s %s" % (t, absolute)
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


# A parent folder that is just a CONTAINER, not a release name — library-style
# Soulseek shares nest 'Show Name/Season 12/episode.mkv', and keying the hit on
# 'Season 12' alone loses the show (the title gate then rejects every candidate)
# and collides different shows' same-named season folders into one hit.
_GENERIC_DIR = re.compile(
    r"^(?:season[ ._-]?\d{1,4}|s\d{1,3}|series[ ._-]?\d{1,3}|specials?|extras?"
    r"|disc[ ._-]?\d+|disk[ ._-]?\d+|cd[ ._-]?\d+|subs?|subtitles|samples?|\d{1,4})$", re.I)


def _release_name(filename: str) -> str:
    """The release a file belongs to — its parent folder (where scene/p2p put the
    quality). When the parent is a generic container ('Season 12', 'Specials'),
    the grandparent (the show folder) is prepended so the hit keeps its identity:
    'TV/90 Day Fiancé/Season 12/ep.mkv' → '90 Day Fiancé/Season 12'. Falls back
    to the bare filename (sans extension) for root-level files."""
    fn = str(filename or "").replace("\\", "/").strip("/")
    parts = [p for p in fn.split("/") if p]
    if len(parts) >= 2:
        parent = parts[-2]
        if len(parts) >= 3 and _GENERIC_DIR.match(parent.strip()):
            gp = parts[-3]
            # slskd share roots look like '@@abcdef' — never a show name.
            if gp and not gp.startswith("@@"):
                return gp + "/" + parent
        return parent
    base = parts[-1] if parts else ""
    return base.rsplit(".", 1)[0] if "." in base else base


def peer_availability(free_slots: Any, upload_speed: Any, queue_length: Any) -> float:
    """How DOWNLOADABLE a peer is right now (higher = grabs sooner). Mirrors the music
    side's availability scoring: a free upload slot, the upload speed (tiered), and the
    queue length (graduated penalty). A fast peer with a free slot and an empty queue beats
    a faster one stuck behind a 1500-deep queue. Pure."""
    try:
        slots, speed, queue = int(free_slots or 0), int(upload_speed or 0), int(queue_length or 0)
    except (TypeError, ValueError):
        slots, speed, queue = 0, 0, 0
    s = 0.05 if slots > 0 else -0.15
    if speed >= 5_000_000:
        s += 0.15
    elif speed >= 1_000_000:
        s += 0.10
    elif speed >= 500_000:
        s += 0.05
    elif speed < 100_000:
        s -= 0.05
    if queue > 50:
        s -= 0.25
    elif queue > 20:
        s -= 0.15
    elif queue > 10:
        s -= 0.10
    return round(s, 4)


def group_video_files(responses: Any) -> list:
    """Flatten slskd responses → one hit per release folder. The chosen source per release
    is the most DOWNLOADABLE peer (free slot → low queue → speed), not just the fastest, and
    hits are ranked by that availability — so we grab a free-slot/empty-queue release over a
    high-spec one stuck behind a huge queue. Pure — drives the unit tests."""
    groups: dict = {}
    for resp in (responses if isinstance(responses, list) else []):
        if not isinstance(resp, dict):
            continue
        user = resp.get("username")
        speed = resp.get("uploadSpeed", 0) or 0
        slots = resp.get("freeUploadSlots", 0) or 0
        queue = resp.get("queueLength", 0) or 0
        avail = peer_availability(slots, speed, queue)
        for f in (resp.get("files") or []):
            fn = f.get("filename", "")
            if not _is_video(fn):
                continue
            rel = _release_name(fn)
            g = groups.get(rel)
            if g is None:
                g = groups[rel] = {"title": rel, "size_bytes": 0, "users": set(),
                                   "best": (-99.0, -1), "username": None, "slots": 0,
                                   "queue": 0, "speed": 0, "availability": -99.0, "filename": fn,
                                   "peer_files": {}}
            g["size_bytes"] = max(g["size_bytes"], f.get("size", 0) or 0)
            if user:
                g["users"].add(user)
                # Remember each peer's video files in this folder so a pack grab can
                # pull the WHOLE folder from one source (mirrors the music album flow).
                g["peer_files"].setdefault(user, []).append(
                    {"filename": fn, "size_bytes": f.get("size", 0) or 0})
            if (avail, speed) > g["best"]:          # most available, then fastest, peer wins
                g["best"] = (avail, speed)
                g["username"], g["slots"], g["queue"] = user, slots, queue
                g["speed"], g["availability"], g["filename"] = speed, avail, fn
    out = []
    for g in groups.values():
        # The chosen peer's files in this folder — what a pack grab would actually pull.
        files = g["peer_files"].get(g["username"], [])
        folder_size = sum((x.get("size_bytes") or 0) for x in files) or g["size_bytes"]
        out.append({"title": g["title"], "size_bytes": g["size_bytes"], "peers": len(g["users"]),
                    "username": g["username"], "slots": g["slots"], "queue": g["queue"],
                    "speed": g["speed"], "availability": g["availability"], "filename": g["filename"],
                    "files": files, "file_count": len(files), "folder_size_bytes": folder_size})
    # availability first, then more peers, then bigger; the quality profile still gates the
    # final pick downstream (_evaluate_hits), this just orders within a quality tier.
    out.sort(key=lambda h: (h["availability"], h["peers"], h["size_bytes"]), reverse=True)
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


def start_search(query: str, *, max_throttle_wait: float | None = None) -> dict:
    """Kick off a slskd search (don't wait). Returns {configured[, id][, error]}.
    The caller polls ``poll_responses(id)`` until satisfied (like the music side).
    ``max_throttle_wait`` bounds the shared-rate-limit wait for interactive
    callers (HTTP handlers); background callers leave it None and wait their turn.

    We generate the search id OURSELVES and pass it to slskd — it honors a client-supplied
    ``id`` — so we never depend on parsing it back out of the POST response. Some slskd builds
    return the id in a Location header / a non-dict body, which made us think the search
    'didn't run' even though slskd created it (the bug behind the fast 'no results')."""
    import uuid
    base, headers = _conn()
    if not base:
        return {"configured": False}
    if not _throttle_search(max_throttle_wait):   # stay under slskd's search-creation rate limit
        return {"configured": True, "error": _RATE_LIMIT_BUSY_MSG}
    search_id = str(uuid.uuid4())
    payload = {"id": search_id, "searchText": query, "timeout": search_timeout_ms(),
               "filterResponses": True, "minimumResponseFileCount": 1,
               "minimumPeerUploadSpeed": _min_speed_bytes()}
    try:
        r = requests.post(base + "/api/v0/searches", json=payload, headers=headers, timeout=15)
        if r.status_code == 429:           # rate limited — back off, don't cascade
            _note_rate_limited(r.headers.get("Retry-After"))
            return {"configured": True, "error": "429 rate limited (backing off)"}
        r.raise_for_status()
    except Exception as e:   # noqa: BLE001 - surface any slskd/network failure to the UI
        return {"configured": True, "error": str(e)}
    # Prefer the id slskd echoes back if present; otherwise the one we supplied (it's honored).
    sid = None
    try:
        data = r.json()
        if isinstance(data, dict):
            sid = data.get("id")
        elif isinstance(data, str) and data.strip():
            sid = data.strip().strip('"')
        elif isinstance(data, list) and data and isinstance(data[0], dict):
            sid = data[0].get("id")
    except Exception:   # noqa: BLE001, S110 - non-JSON / empty body → fall back to our id
        pass
    return {"configured": True, "id": sid or search_id}


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
    # Same shared search-creation budget as start_search. This function only
    # serves the synchronous search endpoint (a user is waiting) → bounded wait.
    if not _throttle_search(_INTERACTIVE_MAX_WAIT_SECONDS):
        return {"configured": True, "hits": [], "error": _RATE_LIMIT_BUSY_MSG}
    try:
        r = requests.post(base + "/api/v0/searches", json=payload, headers=headers, timeout=10)
        if r.status_code == 429:           # rate limited — back off, don't cascade
            _note_rate_limited(r.headers.get("Retry-After"))
            return {"configured": True, "hits": [], "error": "429 rate limited (backing off)"}
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
