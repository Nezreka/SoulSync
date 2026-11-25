"""turns live sessions into plays.

every poll (the drawer's 3 s push, or the 10 s background sampler when nobody
has it open) hands the sessions to :meth:`Recorder.observe`. a play starts the
first time a session is seen, keeps a timeline of playing / paused / buffering,
and closes when a good poll of its server no longer has it.

honest about time:
  * time between two sightings counts toward the state it was in, unless the
    sightings are more than MAX_GAP apart: then nobody knows what happened, and
    the gap counts toward nothing.
  * a play that vanishes closes at its last sighting.
  * a failed poll closes nothing; a server down for STALE closes its plays at
    their last sighting.
  * after a restart the open plays are read back and either carry on (same
    session, same item) or close at their last sighting.

binge runs: back to back episodes of one show, tracks of one album, chapters of
one book by one person within BINGE_GAP share a ``group_key``.

the state machine is pure (clock and sink are passed in); db writes go to a
writer thread so a busy database never stalls the live view.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from core.server_activity.quality import summarize_playback
from utils.logging_config import get_logger

logger = get_logger("server_activity")

MAX_GAP = 90.0
STALE = 15 * 60.0
BINGE_GAP = 30 * 60.0
MIN_PLAY_MS = 10_000
CHECKPOINT = 30.0
_RANK = {"direct_play": 0, "direct_stream": 1, "transcode": 2}


def _group_parent(kind: str, keys: Dict[str, Any]) -> str:
    if kind == "episode":
        return str(keys.get("grandparent_key") or "")
    if kind in ("track", "audiobook", "podcast"):
        return str(keys.get("parent_key") or "")
    return ""


class Recorder:
    def __init__(self, sink: Callable[[List[tuple]], None], clock: Callable[[], float] = time.time):
        self.sink = sink
        self.clock = clock
        self.open: Dict[tuple, Dict[str, Any]] = {}
        self.recent: Dict[tuple, tuple] = {}
        self.loaded = False
        self.lock = threading.RLock()

    # ── restart ────────────────────────────────────────────────────────────
    def load(self, open_rows: List[Dict[str, Any]], recent_rows: List[Dict[str, Any]]) -> None:
        """read back plays a previous run left open, and recent ends for binge
        grouping. the next observe decides whether they carry on."""
        with self.lock:
            for r in recent_rows or []:
                gp = _group_parent(r.get("kind") or "", r)
                if gp:
                    key = (r.get("server"), r.get("user"), r.get("kind"), gp)
                    prev = self.recent.get(key)
                    if not prev or (r.get("stopped_at") or 0) > prev[1]:
                        self.recent[key] = (r.get("group_key"), float(r.get("stopped_at") or 0))
            for r in open_rows or []:
                p = dict(r)
                for f in ("reasons", "detail", "segments", "link"):
                    try:
                        p[f] = json.loads(p[f]) if isinstance(p.get(f), str) and p.get(f) else p.get(f)
                    except ValueError:
                        p[f] = None
                p["segments"] = p.get("segments") or []
                p["reasons"] = p.get("reasons") or []
                p["detail"] = p.get("detail") or {}
                p["_state"] = p["segments"][-1][0] if p["segments"] else "playing"
                p["_item"] = p.get("rating_key") or p.get("title")
                p["_last_flush"] = 0.0
                p["_group_parent"] = _group_parent(p.get("kind") or "", p)
                self.open[(p.get("server"), p.get("session_key"))] = p
            self.loaded = True

    # ── the tick ───────────────────────────────────────────────────────────
    def observe(self, server: str, ok: bool, sessions: List[Dict[str, Any]], now: Optional[float] = None) -> None:
        now = self.clock() if now is None else now
        ops: List[tuple] = []
        with self.lock:
            if not ok:
                for k, p in list(self.open.items()):
                    if k[0] == server and now - float(p["last_seen"]) > STALE:
                        ops += self._close(k, float(p["last_seen"]))
                self._emit(ops)
                return
            seen = set()
            for s in sessions:
                if s.get("server") != server:
                    continue
                k = (server, str(s.get("session_key") or s.get("id") or ""))
                seen.add(k)
                item = (s.get("keys") or {}).get("rating_key") or s.get("title")
                p = self.open.get(k)
                if p is not None and p["_item"] != item:
                    ops += self._close(k, now)       # same player, next item
                    p = None
                if p is None:
                    p = self._start(s, now)
                    self.open[k] = p
                    ops.append(("play", self.row(p)))
                    continue
                changed = self._advance(p, s, now)
                if changed or now - p["_last_flush"] >= CHECKPOINT:
                    p["_last_flush"] = now
                    ops.append(("play", self.row(p)))
            for k, p in list(self.open.items()):
                if k[0] == server and k not in seen:
                    ops += self._close(k, float(p["last_seen"]))
        self._emit(ops)

    def live_facts(self, server: str, session_key: str) -> Optional[Dict[str, Any]]:
        with self.lock:
            p = self.open.get((server, str(session_key)))
            if not p:
                return None
            return {"started_at": p["started_at"], "paused_ms": p["paused_ms"], "pause_count": p["pause_count"],
                    "buffering_ms": p["buffering_ms"], "buffer_count": p["buffer_count"],
                    "played_ms": p["played_ms"], "play_key": p["play_key"], "group_key": p["group_key"]}

    def close_all(self, now: Optional[float] = None) -> None:
        """shutdown: nothing closes here, the plays stay open and the next start
        decides. only flush what we know."""
        with self.lock:
            ops = [("play", self.row(p)) for p in self.open.values()]
        self._emit(ops)

    # ── internals ──────────────────────────────────────────────────────────
    def _emit(self, ops: List[tuple]) -> None:
        if ops:
            try:
                self.sink(ops)
            except Exception:   # noqa: BLE001 - the live view never fails on the log
                logger.debug("play log sink failed", exc_info=True)

    def _static(self, s: Dict[str, Any]) -> Dict[str, Any]:
        kind = s.get("kind") or "other"
        keys = s.get("keys") or {}
        show, music, book = s.get("show") or {}, s.get("music") or {}, s.get("book") or {}
        if kind == "episode":
            title, parent, gp = s.get("title"), show.get("season_title"), show.get("title")
        elif kind == "track":
            title, parent, gp = s.get("title"), music.get("album"), music.get("artist")
        elif kind == "audiobook":
            title, parent, gp = book.get("title") or s.get("title"), None, book.get("author")
        elif kind == "podcast":
            title, parent, gp = s.get("title"), book.get("title"), book.get("author")
        else:
            title, parent, gp = s.get("title"), None, None
        player = s.get("player") or {}
        conn = s.get("connection") or {}
        return {
            "server": s.get("server"), "server_name": s.get("server_name"),
            "session_key": str(s.get("session_key") or s.get("id") or ""), "kind": kind,
            "rating_key": keys.get("rating_key") or None, "parent_key": keys.get("parent_key") or None,
            "grandparent_key": keys.get("grandparent_key") or None,
            "title": title or "", "parent_title": parent or None, "grandparent_title": gp or None,
            "subtitle": s.get("subtitle") or None, "year": s.get("year"),
            "season": show.get("season"), "episode": show.get("episode"), "track": music.get("track"),
            "thumb": s.get("thumb") or None,
            "user": s.get("user") or "", "user_id": s.get("user_id") or None,
            "user_avatar": s.get("user_avatar"),
            "player_product": player.get("product") or None, "player_version": player.get("version"),
            "player_platform": player.get("platform"), "player_device": player.get("device"),
            "player_name": player.get("name"),
            "ip": conn.get("public_ip") or conn.get("ip"), "remote": 1 if conn.get("remote") else 0,
        }

    def _start(self, s: Dict[str, Any], now: float) -> Dict[str, Any]:
        p = self._static(s)
        p["_group_parent"] = _group_parent(p["kind"], p)
        group_key = None
        if p["_group_parent"]:
            gk = (p["server"], p["user"], p["kind"], p["_group_parent"])
            for q in self.open.values():
                if (q["server"], q["user"], q["kind"], q["_group_parent"]) == gk:
                    group_key = q["group_key"]
                    break
            if group_key is None:
                prev = self.recent.get(gk)
                if prev and now - prev[1] <= BINGE_GAP:
                    group_key = prev[0]
        key = uuid.uuid4().hex
        state = s.get("state") or "playing"
        p.update({
            "play_key": key, "group_key": group_key or key, "source": "live",
            "started_at": now, "stopped_at": None, "last_seen": now,
            "played_ms": 0, "paused_ms": 0, "buffering_ms": 0,
            "pause_count": 1 if state == "paused" else 0, "buffer_count": 1 if state == "buffering" else 0,
            "segments": [[state, now, now]], "reasons": [], "detail": {}, "method": None,
            "sent_kb": 0.0, "peak_kbps": 0, "location": None, "link": None,
            "_state": state, "_item": (s.get("keys") or {}).get("rating_key") or s.get("title"),
            "_last_flush": now,
        })
        self._take(p, s, now)
        return p

    def _take(self, p: Dict[str, Any], s: Dict[str, Any], now: float) -> bool:
        """copy what can change during a play. True when something worth a write did."""
        changed = False
        dur = int(s.get("duration_ms") or 0)
        pos = int(s.get("offset_ms") or 0)
        p["duration_ms"] = dur or p.get("duration_ms")
        p["position_ms"] = pos
        p["max_position_ms"] = max(int(p.get("max_position_ms") or 0), pos)
        if p["duration_ms"]:
            p["percent"] = max(0, min(100, round(100 * p["max_position_ms"] / p["duration_ms"])))
        pb = s.get("playback") or {}
        method = pb.get("method")
        if method:
            if p.get("method") and _RANK.get(method, 0) != _RANK.get(p.get("_last_method") or p["method"], 0):
                p["detail"].setdefault("method_changes", []).append(
                    {"at": now, "from": p.get("_last_method") or p["method"], "to": method})
                changed = True
            p["_last_method"] = method
            if not p.get("method") or _RANK.get(method, 0) > _RANK.get(p["method"], 0):
                p["method"] = method
                changed = True
        codes = ((s.get("reason") or {}).get("codes")) or []
        for c in codes:
            if c not in p["reasons"]:
                p["reasons"].append(c)
                changed = True
        if (s.get("reason") or {}).get("inferred"):
            p["detail"]["reasons_inferred"] = True
        tr = s.get("transcoder") or {}
        if tr.get("hw"):
            p["hw"] = 1
        elif p.get("hw") is None and method == "transcode":
            p["hw"] = 0
        if tr.get("hw_decode") or tr.get("hw_encode"):
            p["detail"]["hw_engine"] = tr.get("hw_encode") or tr.get("hw_decode")
        labels = summarize_playback(pb, p["kind"])
        if p.get("quality") is None:
            p["quality"] = labels["quality"]
        if labels["change"] and labels["change"] != p.get("change"):
            p["change"] = labels["change"]
            changed = True
        p["lossless"] = 1 if ((pb.get("audio") or {}).get("source") or {}).get("lossless") else 0
        if not p["detail"].get("quality"):
            p["detail"]["quality"] = _quality_detail(pb)
        bw = int(s.get("bandwidth_kbps") or 0)
        p["peak_kbps"] = max(int(p.get("peak_kbps") or 0), bw)
        loc = ((s.get("connection") or {}).get("location") or {})
        label = loc.get("short") or loc.get("label")
        if label and label != p.get("location"):
            p["location"] = label
            changed = True
        book = (s.get("book") or {}).get("progress") or {}
        if book:
            p["book_percent"] = book.get("percent")
            p["book_left_ms"] = book.get("left_ms")
            ch = book.get("chapter") or {}
            if ch:
                p["chapter"] = json.dumps({"index": ch.get("index"), "count": ch.get("count"),
                                           "title": ch.get("title")})
        if s.get("link") and not p.get("link"):
            p["link"] = s["link"]
            changed = True
        p["_bw"] = bw
        return changed

    def _advance(self, p: Dict[str, Any], s: Dict[str, Any], now: float) -> bool:
        dt = max(0.0, now - float(p["last_seen"]))
        prev = p["_state"]
        new = s.get("state") or "playing"
        changed = False
        if dt > MAX_GAP:
            p["segments"].append([new, now, now])
            p["detail"]["gaps"] = int(p["detail"].get("gaps") or 0) + 1
            changed = True
        else:
            p["segments"][-1][2] = now
            ms = int(dt * 1000)
            if prev == "playing":
                p["played_ms"] += ms
            elif prev == "paused":
                p["paused_ms"] += ms
            elif prev == "buffering":
                p["buffering_ms"] += ms
            if prev in ("playing", "buffering"):
                p["sent_kb"] = float(p.get("sent_kb") or 0) + p.get("_bw", 0) * dt / 8.0
            if new != prev:
                p["segments"].append([new, now, now])
                changed = True
        if new != prev:
            if new == "paused":
                p["pause_count"] += 1
            elif new == "buffering":
                p["buffer_count"] += 1
            changed = True
        p["_state"] = new
        p["last_seen"] = now
        return self._take(p, s, now) or changed

    def _close(self, k: tuple, at: float) -> List[tuple]:
        p = self.open.pop(k, None)
        if p is None:
            return []
        p["stopped_at"] = max(float(p["started_at"]), at)
        if p["segments"] and p["segments"][-1][2] < p["stopped_at"]:
            p["segments"][-1][2] = p["stopped_at"]
        if p["played_ms"] < MIN_PLAY_MS and p["paused_ms"] < MIN_PLAY_MS:
            return [("drop", p["play_key"])]       # a skip, not a play
        if p["_group_parent"]:
            self.recent[(p["server"], p["user"], p["kind"], p["_group_parent"])] = (p["group_key"], p["stopped_at"])
        return [("play", self.row(p))]

    @staticmethod
    def row(p: Dict[str, Any]) -> Dict[str, Any]:
        out = {k: v for k, v in p.items() if not k.startswith("_")}
        out["segments"] = [[a, round(b, 1), round(c, 1)] for a, b, c in p.get("segments") or []]
        out["sent_kb"] = round(float(p.get("sent_kb") or 0), 1)
        return out


def _quality_detail(pb: Dict[str, Any]) -> Dict[str, Any]:
    def side(block, which):
        return ((pb.get(block) or {}).get(which) or {}).get("label")
    return {"video": [side("video", "source"), side("video", "stream")],
            "audio": [side("audio", "source"), side("audio", "stream")],
            "subtitles": [((pb.get("subtitles") or {}).get("source") or {}).get("label"),
                          ((pb.get("subtitles") or {}).get("stream") or {}).get("label")],
            "container": [(pb.get("container") or {}).get("source"), (pb.get("container") or {}).get("stream")],
            "bitrate_kbps": [(pb.get("bitrate_kbps") or {}).get("source"), (pb.get("bitrate_kbps") or {}).get("stream")]}


# ── the writer ──────────────────────────────────────────────────────────────

class Writer:
    """coalesces ops and applies them every couple of seconds on its own thread."""

    def __init__(self, apply: Callable[[List[tuple]], None], interval: float = 2.0, threaded: bool = True):
        self.apply = apply
        self.interval = interval
        self.threaded = threaded      # tests flush by hand
        self.lock = threading.Lock()
        self.plays: Dict[str, tuple] = {}
        self.minutes: Dict[int, list] = {}
        self.thread: Optional[threading.Thread] = None
        self.wake = threading.Event()

    def put(self, ops: List[tuple]) -> None:
        with self.lock:
            for op in ops:
                if op[0] in ("play", "drop"):
                    key = op[1]["play_key"] if op[0] == "play" else op[1]
                    self.plays[key] = op
                elif op[0] == "minute":
                    cur = self.minutes.get(op[1])
                    vals = list(op[2:6])
                    self.minutes[op[1]] = [max(a, b) for a, b in zip(cur, vals, strict=True)] if cur else vals
            if len(self.plays) > 5000:     # db gone for a long time: keep the newest
                for k in list(self.plays)[:1000]:
                    self.plays.pop(k, None)
        self._ensure_thread()

    def flush(self) -> None:
        with self.lock:
            ops = list(self.plays.values()) + [("minute", m, *v) for m, v in sorted(self.minutes.items())]
            self.plays, self.minutes = {}, {}
        if not ops:
            return
        try:
            self.apply(ops)
        except Exception:   # noqa: BLE001 - put them back, try again next round
            logger.debug("play log write failed, will retry", exc_info=True)
            self._requeue(ops)

    def _requeue(self, ops: List[tuple]) -> None:
        """failed ops go back behind anything newer that arrived meanwhile."""
        with self.lock:
            for op in ops:
                if op[0] in ("play", "drop"):
                    key = op[1]["play_key"] if op[0] == "play" else op[1]
                    self.plays.setdefault(key, op)
                elif op[0] == "minute":
                    cur = self.minutes.get(op[1])
                    vals = list(op[2:6])
                    self.minutes[op[1]] = [max(a, b) for a, b in zip(cur, vals, strict=True)] if cur else vals

    def _ensure_thread(self) -> None:
        if not self.threaded or (self.thread is not None and self.thread.is_alive()):
            return
        self.thread = threading.Thread(target=self._run, name="server-activity-writer", daemon=True)
        self.thread.start()

    def _run(self) -> None:
        while True:
            self.wake.wait(self.interval)
            self.wake.clear()
            self.flush()
