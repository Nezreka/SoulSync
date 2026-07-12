"""Import ytdl-sub / Kometa subscription files into SoulSync watchlist follows.

A ytdl-sub subscription file is YAML: each top-level key is a subscription whose
``overrides.url`` is a YouTube channel or playlist and ``overrides.tv_show_name``
is the show name. That maps 1:1 onto SoulSync's YouTube follows — so importing
one is: parse → resolve each URL → follow it (channel or playlist) → apply the
show name as the channel's custom name, and ``best_video_quality`` as a quality
override.

The parser is deliberately NOT a full YAML load (PyYAML isn't a dependency, and
a strict load throws on the messy real-world files users actually have). It's a
tolerant line scanner that pulls the three fields it needs per block and skips
anything it doesn't understand — a malformed block is dropped, never fatal.
"""

from __future__ import annotations

import re
import threading
from typing import Any, Callable, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("video.subscriptions")

_KEY_RE = re.compile(r"^([^\s#][^:]*):\s*(.*)$")     # top-level `name:` (col 0)


def _strip_comment(line: str) -> str:
    """Drop a YAML inline comment (``#`` at line start or after whitespace).
    A ``#`` glued to non-space — e.g. inside a URL fragment — is kept."""
    out = []
    prev_space = True                 # start-of-line counts as "after whitespace"
    in_q = ""
    for ch in line:
        if ch in ("'", '"'):
            in_q = "" if in_q == ch else (in_q or ch)
        if ch == "#" and not in_q and prev_space:
            break
        out.append(ch)
        prev_space = ch in (" ", "\t")
    return "".join(out).rstrip()


def _unquote(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] in ("'", '"') and v[-1] == v[0]:
        return v[1:-1]
    return v


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def parse_subscriptions(text: str) -> List[Dict[str, Any]]:
    """Return ``[{name, url, show_name, presets}]`` — one per subscription block
    that has a URL. ``presets`` is the list of ACTIVE (uncommented) preset names.
    Tolerant: blocks without a URL are skipped, never raised on."""
    subs: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None
    section: Optional[str] = None      # 'preset' | 'overrides' | None
    section_indent = 0

    for raw in (text or "").splitlines():
        line = _strip_comment(raw)
        if not line.strip():
            continue
        indent = _indent(line)
        body = line.strip()

        top = _KEY_RE.match(line) if indent == 0 else None
        if top:
            # a new top-level subscription key
            if cur and cur.get("url"):
                subs.append(cur)
            cur = {"name": top.group(1).strip(), "url": None,
                   "show_name": None, "presets": []}
            section = None
            continue
        if cur is None:
            continue

        # a nested mapping key ("preset:" / "overrides:") opens a section
        if body.endswith(":") and ":" in body:
            k = body[:-1].strip()
            if k in ("preset", "presets", "overrides"):
                section = "preset" if k.startswith("preset") else "overrides"
                section_indent = indent
                continue

        if section == "preset" and body.startswith("-"):
            p = _unquote(body[1:].strip())
            if p:
                cur["presets"].append(p)
            continue

        # any `key: value` under the block (overrides live here)
        m = re.match(r"^([A-Za-z_][\w]*):\s*(.*)$", body)
        if m:
            key, val = m.group(1), _unquote(m.group(2))
            if key == "url" and val:
                cur["url"] = val
            elif key == "tv_show_name" and val:
                cur["show_name"] = val
            # leaving a section when indent drops back is implicit; we don't need it
            if indent <= section_indent and key not in ("url", "tv_show_name",
                                                        "tv_show_directory"):
                section = None

    if cur and cur.get("url"):
        subs.append(cur)
    return subs


def wants_best_quality(presets: List[str]) -> bool:
    return any("best_video_quality" in str(p) for p in (presets or []))


# ── the background import runner (pure; all I/O injected) ─────────────────────

def import_subscriptions(subs: List[Dict[str, Any]], *,
                         resolve_channel: Callable[[str], Optional[dict]],
                         resolve_playlist: Callable[[str], Optional[dict]],
                         is_playlist: Callable[[str], bool],
                         follow_channel: Callable[[dict], bool],
                         follow_playlist: Callable[[dict], bool],
                         apply_channel_settings: Callable[[str, dict], Any],
                         on_progress: Optional[Callable[[int, dict], None]] = None,
                         should_stop: Optional[Callable[[], bool]] = None) -> Dict[str, Any]:
    """Resolve + follow every subscription. Pure orchestration — every network /
    DB touch is an injected seam, so the whole flow is unit-tested without yt-dlp.
    Returns a summary ``{followed, skipped, failed, items:[...]}``."""
    results = []
    followed = skipped = failed = 0
    for i, sub in enumerate(subs):
        if should_stop and should_stop():
            break
        url, show = sub.get("url"), sub.get("show_name")
        name = sub.get("name") or show or url
        item = {"name": name, "url": url, "show_name": show, "status": "failed", "kind": None}
        try:
            if is_playlist(url):
                pl = resolve_playlist(url)
                if pl and pl.get("playlist_id"):
                    item["kind"] = "playlist"
                    item["title"] = pl.get("title")
                    if follow_playlist(pl):
                        item["status"] = "followed"; followed += 1
                    else:
                        item["status"] = "skipped"; skipped += 1   # already following
                else:
                    failed += 1
            else:
                ch = resolve_channel(url)
                if ch and ch.get("youtube_id"):
                    item["kind"] = "channel"
                    item["title"] = ch.get("title")
                    item["youtube_id"] = ch.get("youtube_id")
                    was_followed = follow_channel(ch)
                    item["status"] = "followed" if was_followed else "skipped"
                    followed += 1 if was_followed else 0
                    skipped += 0 if was_followed else 1
                    # carry the show name + quality intent onto the channel
                    cs: Dict[str, Any] = {}
                    if show and show != ch.get("title"):
                        cs["custom_name"] = show
                    if wants_best_quality(sub.get("presets")):
                        cs["quality"] = {"max_resolution": "best"}
                    if cs:
                        apply_channel_settings(ch["youtube_id"], cs)
                else:
                    failed += 1
        except Exception:   # noqa: BLE001 - one bad subscription never aborts the batch
            logger.exception("subscription import failed for %r", url)
            failed += 1
        results.append(item)
        if on_progress:
            try:
                on_progress(i + 1, item)
            except Exception:   # noqa: BLE001
                pass
    return {"followed": followed, "skipped": skipped, "failed": failed,
            "total": len(subs), "items": results}


# ── background import job (a one-shot singleton the UI polls) ─────────────────

class _ImportJob:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.state: Dict[str, Any] = _idle_state()


def _idle_state() -> Dict[str, Any]:
    return {"running": False, "finished": False, "total": 0, "done": 0,
            "current": None, "followed": 0, "skipped": 0, "failed": 0, "items": []}


_JOB = _ImportJob()


def import_status() -> Dict[str, Any]:
    with _JOB.lock:
        return {**_JOB.state, "items": list(_JOB.state["items"])}


def start_import_job(subs: List[Dict[str, Any]], seams: Dict[str, Callable]) -> bool:
    """Run the resolve+follow loop on a daemon thread. Returns False if one's
    already running. ``seams`` are the same injected callables import_subscriptions
    takes (bound to the real yt-dlp/DB calls by the API layer)."""
    with _JOB.lock:
        if _JOB.state["running"]:
            return False
        _JOB.stop.clear()
        _JOB.state = {**_idle_state(), "running": True, "total": len(subs)}

    def _on_progress(n: int, item: Dict[str, Any]) -> None:
        with _JOB.lock:
            st = _JOB.state
            st["done"] = n
            st["current"] = item.get("name")
            st["items"].append(item)
            st[item.get("status", "failed")] = st.get(item.get("status", "failed"), 0) + 1

    def _run() -> None:
        try:
            import_subscriptions(subs, on_progress=_on_progress,
                                 should_stop=_JOB.stop.is_set, **seams)
        except Exception:   # noqa: BLE001 - a crash still finishes the job cleanly
            logger.exception("subscription import job crashed")
        finally:
            with _JOB.lock:
                _JOB.state["running"] = False
                _JOB.state["finished"] = True
                _JOB.state["current"] = None

    t = threading.Thread(target=_run, name="video-sub-import", daemon=True)
    _JOB.thread = t
    t.start()
    return True


def stop_import_job() -> None:
    _JOB.stop.set()


def _reset_for_tests() -> None:
    _JOB.stop.set()
    _JOB.state = _idle_state()
