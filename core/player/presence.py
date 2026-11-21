"""who is watching or listening to what, right now, on this server.

a screen that plays something says so every few seconds (a heartbeat:
what, where it is, playing or paused); it's forgotten a little after the
last one, or at once when the screen stops. kept in memory: it's about
now, so a restart forgetting it is right.

a profile shares by default and can turn it off (player settings,
share_activity): its heartbeats are then never kept. what a viewer sees of
others follows what the viewer may see: a kind of media it has no access
to, or a film over its rating cap, isn't shown.
"""

from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.player.progress import clean_meta

# a screen that hasn't said anything for this long has stopped
STALE_SECONDS = 75
MAX_SESSIONS = 500

KIND_PERMISSIONS: Dict[str, Tuple[str, ...]] = {
    "track": ("music.access",),
    "movie": ("movies.access",),
    "episode": ("tv.access",),
    "youtube": ("movies.access", "tv.access"),
    "audiobook": ("books.access",),
    "podcast_episode": ("podcasts.access",),
}


@dataclass
class Session:
    profile_id: int
    session: str
    item_type: str
    item_id: str
    position: float
    duration: Optional[float]
    playing: bool
    live: bool
    channel: Optional[Dict[str, Any]]
    meta: Dict[str, Any] = field(default_factory=dict)
    started: float = 0.0
    seen: float = 0.0


_sessions: Dict[Tuple[int, str], Session] = {}
_lock = threading.Lock()


class PresenceError(ValueError):
    pass


def _num(v: Any, name: str, *, allow_none: bool = False) -> Optional[float]:
    if v is None and allow_none:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or v < 0:
        raise PresenceError(f"{name} must be a number")
    return float(v)


def _channel(v: Any) -> Optional[Dict[str, Any]]:
    """a guide channel the screen tuned to: its id, number and name"""
    if not isinstance(v, dict):
        return None
    cid = str(v.get("id") or "")[:80]
    if not cid:
        return None
    num = v.get("number")
    return {"id": cid, "name": str(v.get("name") or "")[:80],
            "number": int(num) if isinstance(num, int) and not isinstance(num, bool) else None}


def beat(profile_id: int, body: Any, *, now: Optional[float] = None) -> Session:
    """a screen is playing something (or paused on it)"""
    if not isinstance(body, dict):
        raise PresenceError("bad heartbeat")
    session = str(body.get("session") or "")[:64]
    item_type = str(body.get("item_type") or "")
    item_id = str(body.get("item_id") or "")[:200]
    if not session or not item_id or item_type not in KIND_PERMISSIONS:
        raise PresenceError("a heartbeat needs a session, an item_type and an item_id")
    t = time.time() if now is None else now
    key = (int(profile_id), session)
    with _lock:
        old = _sessions.get(key)
        same = old is not None and (old.item_type, old.item_id) == (item_type, item_id)
        s = Session(
            profile_id=int(profile_id), session=session, item_type=item_type, item_id=item_id,
            position=_num(body.get("position"), "position") or 0.0,
            duration=_num(body.get("duration"), "duration", allow_none=True),
            playing=bool(body.get("playing")), live=bool(body.get("live")),
            channel=_channel(body.get("channel")),
            meta=clean_meta(body.get("meta")) or {},
            started=old.started if same and old else t, seen=t)
        _sessions[key] = s
        if len(_sessions) > MAX_SESSIONS:
            _sweep(t)
    return s


def stop(profile_id: int, session: str) -> bool:
    with _lock:
        return _sessions.pop((int(profile_id), str(session)), None) is not None


def forget_profile(profile_id: int) -> None:
    """a profile turned sharing off (or was deleted): it's gone from now"""
    with _lock:
        for k in [k for k in _sessions if k[0] == int(profile_id)]:
            _sessions.pop(k, None)


def _sweep(now: float) -> None:
    for k in [k for k, s in _sessions.items() if now - s.seen > STALE_SECONDS]:
        _sessions.pop(k, None)


def everyone(*, now: Optional[float] = None) -> List[Session]:
    t = time.time() if now is None else now
    with _lock:
        _sweep(t)
        return list(_sessions.values())


def visible_to(viewer_id: int, sessions: List[Session], *,
               shares: Callable[[int], bool],
               may_see: Callable[[Session], bool]) -> List[Dict[str, Any]]:
    """what a viewer sees: others' screens, from profiles that share, of
    things the viewer may see. a profile on two screens shows twice."""
    out = []
    for s in sessions:
        if s.profile_id == int(viewer_id) or not shares(s.profile_id) or not may_see(s):
            continue
        row = asdict(s)
        row.pop("session", None)
        out.append(row)
    out.sort(key=lambda r: (not r["playing"], -r["seen"]))
    return out


# ── what you and others both have going ──────────────────────────────────

# kinds worth comparing: what people watch or listen to over days, not songs
TOGETHER_KINDS = ("movie", "episode", "youtube", "audiobook", "podcast_episode")
RECENT_DAYS = 60


def _title_key(item_type: str, item_id: str, meta: Dict[str, Any]) -> Tuple[str, str]:
    """a show counts as one thing, whichever episode each of you is on"""
    if item_type == "episode" and meta.get("parent_id"):
        return ("show", str(meta["parent_id"]))
    if item_type == "podcast_episode" and meta.get("parent_id"):
        return ("podcast", str(meta["parent_id"]))
    return (item_type, item_id)


def together(db: Any, viewer_id: int, *, shares: Callable[[int], bool],
             may_see: Callable[[Session], bool], now: Optional[float] = None,
             limit: int = 30) -> List[Dict[str, Any]]:
    """titles the viewer and other profiles (that share) have both started
    lately, the viewer's place and theirs, newest first. a show is one title"""
    import json as _json

    from core.player.progress import TABLE, ensure_schema
    ensure_schema(db)
    t = time.time() if now is None else now
    since = t - RECENT_DAYS * 86400
    marks = ",".join("?" * len(TOGETHER_KINDS))
    with db._get_connection() as conn:
        rows = conn.execute(
            f"SELECT profile_id, item_type, item_id, position_seconds, duration_seconds, finished, "
            f"meta_json, updated_at FROM {TABLE} WHERE item_type IN ({marks}) AND updated_at >= ? "
            f"ORDER BY updated_at DESC LIMIT 5000",
            (*TOGETHER_KINDS, since)).fetchall()
    mine: Dict[Tuple[str, str], Dict[str, Any]] = {}
    theirs: Dict[Tuple[str, str], Dict[int, Dict[str, Any]]] = {}
    for r in rows:
        try:
            meta = _json.loads(r[6]) if r[6] else {}
        except (TypeError, ValueError):
            meta = {}
        meta = meta if isinstance(meta, dict) else {}
        key = _title_key(r[1], r[2], meta)
        place = {"item_type": r[1], "item_id": r[2], "position": r[3], "duration": r[4],
                 "finished": bool(r[5]), "meta": meta, "updated": r[7]}
        if int(r[0]) == int(viewer_id):
            mine.setdefault(key, place)  # newest first: the first is the latest
        else:
            theirs.setdefault(key, {}).setdefault(int(r[0]), place)
    out = []
    for key, me in mine.items():
        others = {pid: p for pid, p in theirs.get(key, {}).items() if shares(pid)}
        if not others:
            continue
        probe = Session(profile_id=int(viewer_id), session="", item_type=me["item_type"],
                        item_id=me["item_id"], position=0, duration=None, playing=False,
                        live=False, channel=None, meta=me["meta"])
        if not may_see(probe):
            continue
        out.append({"key": f"{key[0]}:{key[1]}", "mine": me,
                    "others": [{"profile_id": pid, **p} for pid, p in
                               sorted(others.items(), key=lambda x: -x[1]["updated"])]})
    out.sort(key=lambda x: -max([x["mine"]["updated"], *[o["updated"] for o in x["others"]]]))
    return out[:limit]
