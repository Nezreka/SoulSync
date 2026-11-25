"""who is watching or listening to what, right now, on this server.

a screen that plays something says so every few seconds (a heartbeat:
what, where it is, playing or paused); it's forgotten a little after the
last one, or at once when the screen stops. kept in memory: it's about
now, so a restart forgetting it is right.

a profile shares by default and can turn it off (player settings,
share_activity). every heartbeat is kept either way, marked with whether
the profile shares: an admin's Server Activity sees every stream, the way
plex shows its admin everything, while other members never see a profile
that doesn't share. what a viewer sees of others follows what the viewer
may see: a kind of media it has no access to, or a film over its rating
cap, isn't shown.

an admin can stop a screen (with a message) or send it a message. the
order waits here and rides back on that screen's next heartbeat; a stopped
screen's beats aren't kept for a short while, and its item isn't served to
that profile again in that time.
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
# after an admin stops a screen: its beats aren't kept and its item isn't
# served to that profile again for this long
STOP_HOLD_SECONDS = 30
MAX_MESSAGE = 500
MAX_HEADER = 120

# how the screen plays it, as the screen says (all optional)
_VIA = ("disk", "cache", "plex", "jellyfin", "navidrome", "emby", "media server", "youtube", "feed")
_TRANSCODE_VIDEO = ("copy", "h264")
_TRANSCODE_AUDIO = ("copy", "aac")
_QUALITIES = ("original", "1080p", "720p", "480p")

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
    # whether the profile shares its activity with other members
    shared: bool = True
    # for the admin only, never shown to members: where from, on what, how
    ip: Optional[str] = None
    client: Dict[str, Any] = field(default_factory=dict)
    stream: Dict[str, Any] = field(default_factory=dict)


# what visible_to never hands a member
PRIVATE_FIELDS = ("session", "shared", "ip", "client", "stream")

_sessions: Dict[Tuple[int, str], Session] = {}
# admin orders waiting for a screen's next beat
_stops: Dict[Tuple[int, str], Dict[str, Any]] = {}
_notes: Dict[Tuple[int, str], Dict[str, str]] = {}
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


def _text(v: Any, limit: int) -> Optional[str]:
    if v is None or isinstance(v, (dict, list, bool)):
        return None
    t = " ".join(str(v).split())[:limit]
    return t or None


def clean_stream(v: Any) -> Dict[str, Any]:
    """how the screen plays it: where the bytes come from, which file, and
    what the converter does to it. anything odd is dropped, never guessed."""
    if not isinstance(v, dict):
        return {}
    out: Dict[str, Any] = {}
    via = _text(v.get("via"), 20)
    if via and via.lower() in _VIA:
        out["via"] = via.lower()
    fid = v.get("file_id")
    if isinstance(fid, int) and not isinstance(fid, bool) and fid > 0:
        out["file_id"] = fid
    mime = _text(v.get("mime"), 60)
    if mime:
        out["mime"] = mime
    if "transcode" in v:
        t = v.get("transcode")
        if t is None:
            out["transcode"] = None
        elif isinstance(t, dict) and t.get("video") in _TRANSCODE_VIDEO and t.get("audio") in _TRANSCODE_AUDIO:
            reasons = t.get("reasons") if isinstance(t.get("reasons"), list) else []
            out["transcode"] = {
                "video": t["video"], "audio": t["audio"],
                "quality": t.get("quality") if t.get("quality") in _QUALITIES else "original",
                "reasons": [r for r in (_text(x, 120) for x in reasons[:6]) if r],
                "encoder": _text(t.get("encoder"), 40),
            }
    return out


def clean_client(v: Any) -> Dict[str, Any]:
    """what the screen is: product, its name, platform, version"""
    if not isinstance(v, dict):
        return {}
    out = {k: _text(v.get(k), 80) for k in ("product", "name", "platform", "version", "device")}
    return {k: x for k, x in out.items() if x}


def beat(profile_id: int, body: Any, *, now: Optional[float] = None, shared: bool = True,
         ip: Optional[str] = None, client: Optional[Dict[str, Any]] = None) -> Optional[Session]:
    """a screen is playing something (or paused on it). None when an admin
    just stopped this screen: its beats aren't kept for a little while."""
    if not isinstance(body, dict):
        raise PresenceError("bad heartbeat")
    session = str(body.get("session") or "")[:64]
    item_type = str(body.get("item_type") or "")
    item_id = str(body.get("item_id") or "")[:200]
    if not session or not item_id or item_type not in KIND_PERMISSIONS:
        raise PresenceError("a heartbeat needs a session, an item_type and an item_id")
    t = time.time() if now is None else now
    key = (int(profile_id), session)
    position = _num(body.get("position"), "position") or 0.0
    duration = _num(body.get("duration"), "duration", allow_none=True)
    meta = clean_meta(body.get("meta")) or {}
    stream = clean_stream(body.get("stream"))
    told = clean_client(body.get("client"))
    with _lock:
        held = _stops.get(key)
        if held is not None and held["until"] > t:
            return None
        old = _sessions.get(key)
        same = old is not None and (old.item_type, old.item_id) == (item_type, item_id)
        s = Session(
            profile_id=int(profile_id), session=session, item_type=item_type, item_id=item_id,
            position=position, duration=duration,
            playing=bool(body.get("playing")), live=bool(body.get("live")),
            channel=_channel(body.get("channel")),
            meta=meta,
            started=old.started if same and old else t, seen=t,
            shared=bool(shared), ip=_text(ip, 64),
            # what the screen says it is wins over what the request looks like
            client={**(client or {}), **told},
            # a beat that doesn't say how it plays keeps what the last one said
            stream=stream or (old.stream if same and old else {}))
        _sessions[key] = s
        if len(_sessions) > MAX_SESSIONS:
            _sweep(t)
        # still over (many fresh session ids at once): the longest quiet go
        while len(_sessions) > MAX_SESSIONS:
            _sessions.pop(min(_sessions, key=lambda k: _sessions[k].seen), None)
    return s


def stop(profile_id: int, session: str) -> bool:
    key = (int(profile_id), str(session))
    with _lock:
        _notes.pop(key, None)
        return _sessions.pop(key, None) is not None


def forget_profile(profile_id: int) -> None:
    """a profile was deleted: it's gone from now"""
    with _lock:
        for k in [k for k in _sessions if k[0] == int(profile_id)]:
            _sessions.pop(k, None)


def set_shared(profile_id: int, shared: bool) -> None:
    """a profile turned sharing on or off: its screens follow at once"""
    with _lock:
        for k, s in _sessions.items():
            if k[0] == int(profile_id):
                s.shared = bool(shared)


def _sweep(now: float) -> None:
    for k in [k for k, s in _sessions.items() if now - s.seen > STALE_SECONDS]:
        _sessions.pop(k, None)
        _notes.pop(k, None)
    for k in [k for k, o in _stops.items() if o["until"] <= now]:
        _stops.pop(k, None)


# ── the admin's orders ───────────────────────────────────────────────────

def order_stop(profile_id: int, session: str, message: str, *,
               now: Optional[float] = None) -> Optional[Session]:
    """an admin stops this screen. it's off the list at once; the message
    rides back on its next beat, which also tells it to stop. None when
    there's no such screen."""
    t = time.time() if now is None else now
    key = (int(profile_id), str(session))
    with _lock:
        s = _sessions.pop(key, None)
        if s is None:
            return None
        _notes.pop(key, None)
        _stops[key] = {"until": t + STOP_HOLD_SECONDS, "message": str(message or "")[:MAX_MESSAGE],
                       "item": (s.item_type, s.item_id)}
        return s


def order_message(profile_id: int, session: str, text: str, header: str = "") -> bool:
    """a message for this screen, shown once on its next beat"""
    key = (int(profile_id), str(session))
    with _lock:
        if key not in _sessions:
            return False
        _notes[key] = {"header": str(header or "")[:MAX_HEADER], "text": str(text or "")[:MAX_MESSAGE]}
        return True


def orders(profile_id: int, session: str, *, now: Optional[float] = None) -> Dict[str, Any]:
    """what the beat's answer carries: {"stop": {"message"}} while this
    screen is stopped (every beat, so a lost answer can't miss it), and
    {"message": {"header", "text"}} once."""
    t = time.time() if now is None else now
    key = (int(profile_id), str(session or ""))
    out: Dict[str, Any] = {}
    with _lock:
        held = _stops.get(key)
        if held is not None and held["until"] > t:
            out["stop"] = {"message": held["message"]}
        note = _notes.pop(key, None)
        if note is not None and "stop" not in out:
            out["message"] = note
    return out


def refused(profile_id: Any, item_type: str, item_id: Any, *, now: Optional[float] = None) -> Optional[str]:
    """the admin's message when this profile's screen was just stopped on
    this item (a new stream of it is refused for a little while), else None"""
    if profile_id is None:
        return None
    t = time.time() if now is None else now
    want = (int(profile_id), (str(item_type), str(item_id)))
    with _lock:
        for k, o in _stops.items():
            if (k[0], o["item"]) == want and o["until"] > t:
                return o["message"] or "The server administrator stopped this stream."
    return None


def everyone(*, now: Optional[float] = None) -> List[Session]:
    t = time.time() if now is None else now
    with _lock:
        _sweep(t)
        return list(_sessions.values())


def visible_to(viewer_id: int, sessions: List[Session], *,
               shares: Callable[[int], bool],
               may_see: Callable[[Session], bool]) -> List[Dict[str, Any]]:
    """what a viewer sees: its own screens (marked you, first) and others',
    from profiles that share, of things the viewer may see. a profile on
    two screens shows twice. where a screen is and how it plays is the
    admin's to see (Server Activity), never a member's."""
    out = []
    for s in sessions:
        you = s.profile_id == int(viewer_id)
        if not you and (not s.shared or not shares(s.profile_id) or not may_see(s)):
            continue
        row = asdict(s)
        for k in PRIVATE_FIELDS:
            row.pop(k, None)
        row["you"] = you
        out.append(row)
    out.sort(key=lambda r: (not r["you"], not r["playing"], -r["seen"]))
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
