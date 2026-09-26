"""signed-in devices: each sign-in gets an id stored in the session and a row
per profile, so one browser can be signed out without signing out the rest
(sign out everywhere is session_epoch).

a session from before this existed carries no device id and is left alone.
the row is read at most every few seconds per process, and last_seen is
written at most every few minutes, so this costs nothing per request.
"""

from __future__ import annotations

import re
import secrets
import threading
import time
from typing import Callable, Dict, Optional, Tuple

_LIVE_TTL = 5.0
_TOUCH_EVERY = 300.0
_live: Dict[str, Tuple[float, bool]] = {}
_touched: Dict[str, float] = {}
_lock = threading.Lock()


def new_device_id() -> str:
    return secrets.token_urlsafe(16)


def device_label(user_agent: str) -> str:
    """"Chrome on Windows"-ish, from a user agent string. good enough to
    tell your phone from your laptop."""
    ua = user_agent or ""
    browser = next((name for pat, name in (
        (r"Edg/", "Edge"), (r"OPR/|Opera", "Opera"), (r"Firefox/", "Firefox"),
        (r"Chrome/", "Chrome"), (r"Safari/", "Safari")) if re.search(pat, ua)), "Browser")
    system = next((name for pat, name in (
        (r"iPhone|iPad", "iOS"), (r"Android", "Android"), (r"Windows", "Windows"),
        (r"Mac OS X|Macintosh", "macOS"), (r"Linux", "Linux")) if re.search(pat, ua)), "")
    return f"{browser} on {system}" if system else browser


def start_device(session, add_row: Callable[[str, int, str, str], bool], owner_id: int,
                 user_agent: str, ip: str) -> Optional[str]:
    """a sign-in: new device id in the session and a row for it."""
    device_id = new_device_id()
    if add_row(device_id, int(owner_id), device_label(user_agent), ip or ""):
        session["device_id"] = device_id
        return device_id
    session.pop("device_id", None)
    return None


def device_is_live(device_id: Optional[str], load: Callable[[str], Optional[dict]]) -> bool:
    """False only for a device row that was revoked. no id, no row, or a read
    error all keep the session: a db hiccup must not sign people out."""
    if not device_id:
        return True
    now = time.monotonic()
    with _lock:
        hit = _live.get(device_id)
        if hit and now - hit[0] < _LIVE_TTL:
            return hit[1]
    try:
        row = load(device_id)
        live = not (row and row.get("revoked_at"))
    except Exception:  # noqa: BLE001
        return True
    with _lock:
        _live[device_id] = (now, live)
    return live


def touch(device_id: Optional[str], write: Callable[[str], None]) -> None:
    if not device_id:
        return
    now = time.monotonic()
    with _lock:
        if now - _touched.get(device_id, 0.0) < _TOUCH_EVERY:
            return
        _touched[device_id] = now
    try:
        write(device_id)
    except Exception:  # noqa: BLE001, S110 - last_seen is a nicety
        pass


def forget(device_id: str) -> None:
    with _lock:
        _live.pop(device_id, None)


def _reset_for_tests() -> None:
    with _lock:
        _live.clear()
        _touched.clear()


__all__ = ["new_device_id", "device_label", "start_device", "device_is_live", "touch", "forget"]
