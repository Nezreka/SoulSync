"""Event notifications (arr-parity P11) — Discord / webhook / Telegram.

Radarr's "Connections": tell me when something happens. The video event bus
already publishes every moment worth telling (grab imported, upgrade landed,
import needs attention, download failed, wishlist/watchlist adds) — this
module is a second bus forwarder that fans those events out to configured
connections:

  · discord   — a Discord webhook URL (rich-ish embed)
  · webhook   — any URL; receives the raw {event, data} JSON (build your own)
  · telegram  — bot token + chat id via the Bot API

Connections live in video_settings['notify_connections']: {id, name, type,
url | token+chat_id, events: [...], enabled}. Dispatch runs on a small
daemon thread per event batch with short timeouts — a dead webhook can never
slow the download pipeline (publishers are synchronous-cheap by contract).
"""

from __future__ import annotations

import json
import threading
from typing import Any, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("video.notifications")

_KEY = "notify_connections"
TYPES = ("discord", "webhook", "telegram")
EVENTS = (
    "video_download_completed", "video_upgrade_completed",
    "video_import_failed", "video_download_failed",
    "video_wishlist_item_added", "video_watchlist_added",
)
_EVENT_LABEL = {
    "video_download_completed": "✅ Imported",
    "video_upgrade_completed": "⬆️ Upgraded",
    "video_import_failed": "⚠️ Needs manual import",
    "video_download_failed": "❌ Download failed",
    "video_wishlist_item_added": "⭐ Wishlisted",
    "video_watchlist_added": "👁 Following",
}
MAX_CONNECTIONS = 16


# ── config store ──────────────────────────────────────────────────────────────

def normalize_connection(raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    ctype = str(raw.get("type") or "").strip().lower()
    if ctype not in TYPES:
        return None
    url = str(raw.get("url") or "").strip()
    token = str(raw.get("token") or "").strip()
    chat_id = str(raw.get("chat_id") or "").strip()
    if ctype in ("discord", "webhook") and not url.startswith(("http://", "https://")):
        return None
    if ctype == "telegram" and (not token or not chat_id):
        return None
    events = [e for e in (raw.get("events") or []) if e in EVENTS]
    cid = raw.get("id")
    return {"id": int(cid) if isinstance(cid, (int, float)) and int(cid) >= 1 else None,
            "name": (str(raw.get("name") or "").strip() or ctype)[:80],
            "type": ctype, "url": url[:500], "token": token[:200], "chat_id": chat_id[:64],
            "events": events or list(EVENTS[:4]),   # default: the four download outcomes
            "enabled": raw.get("enabled", True) is not False}


def load_connections(db) -> List[Dict[str, Any]]:
    try:
        rows = json.loads(db.get_setting(_KEY) or "[]")
    except (ValueError, TypeError):
        return []
    out = []
    for r in rows if isinstance(rows, list) else []:
        n = normalize_connection(r)
        if n and n["id"]:
            out.append(n)
    return out


def save_connection(db, raw: Any) -> Optional[Dict[str, Any]]:
    n = normalize_connection(raw)
    if not n:
        return None
    rows = load_connections(db)
    if n["id"] is None:
        n["id"] = max([0] + [r["id"] for r in rows]) + 1
        rows.append(n)
    else:
        rows = [r for r in rows if r["id"] != n["id"]] + [n]
    db.set_setting(_KEY, json.dumps(rows[:MAX_CONNECTIONS]))
    return n


def delete_connection(db, conn_id: Any) -> bool:
    try:
        cid = int(conn_id)
    except (TypeError, ValueError):
        return False
    rows = load_connections(db)
    kept = [r for r in rows if r["id"] != cid]
    if len(kept) == len(rows):
        return False
    db.set_setting(_KEY, json.dumps(kept))
    return True


# ── message shaping (pure) ────────────────────────────────────────────────────

def format_message(event_type: str, data: Dict[str, Any]) -> str:
    label = _EVENT_LABEL.get(event_type, event_type)
    data = data or {}
    title = str(data.get("title") or "?")
    bits = []
    if data.get("season") not in (None, "") and data.get("episode") not in (None, ""):
        bits.append("S%02dE%02d" % (int(data["season"]), int(data["episode"])))
    if data.get("year"):
        bits.append(str(data["year"]))
    if data.get("quality"):
        bits.append(str(data["quality"]))
    if data.get("source"):
        bits.append(str(data["source"]))
    if data.get("error"):
        bits.append(str(data["error"])[:200])
    return "%s: %s%s" % (label, title, (" (" + " · ".join(bits) + ")") if bits else "")


# ── dispatch ──────────────────────────────────────────────────────────────────

def _send(conn: Dict[str, Any], event_type: str, data: Dict[str, Any]) -> bool:
    import requests
    msg = format_message(event_type, data)
    try:
        if conn["type"] == "discord":
            r = requests.post(conn["url"], json={"content": msg, "username": "SoulSync"},
                              timeout=6)
        elif conn["type"] == "telegram":
            r = requests.post("https://api.telegram.org/bot%s/sendMessage" % conn["token"],
                              json={"chat_id": conn["chat_id"], "text": msg}, timeout=6)
        else:   # generic webhook — the raw event, for people building their own
            r = requests.post(conn["url"], json={"event": event_type, "message": msg,
                                                 "data": data}, timeout=6)
        ok = 200 <= r.status_code < 300 or r.status_code == 204
        if not ok:
            logger.warning("notification %r: HTTP %s", conn.get("name"), r.status_code)
        return ok
    except Exception:   # noqa: BLE001 - a dead endpoint must never bubble up
        logger.warning("notification %r failed", conn.get("name"), exc_info=True)
        return False


def handle_event(event_type: str, data: Dict[str, Any]) -> None:
    """The bus forwarder: fan the event out to subscribed connections on a
    daemon thread (publishers must stay synchronous-cheap)."""
    if event_type not in EVENTS:
        return
    try:
        from api.video import get_video_db
        conns = [c for c in load_connections(get_video_db())
                 if c["enabled"] and event_type in c["events"]]
    except Exception:   # noqa: BLE001
        return
    if not conns:
        return

    def _fan_out():
        for c in conns:
            _send(c, event_type, data or {})

    threading.Thread(target=_fan_out, daemon=True, name="video-notify").start()


def test_connection(conn_raw: Any) -> bool:
    """Fire a test message at an (unsaved) connection config."""
    conn = normalize_connection(conn_raw)
    if not conn:
        return False
    return _send(conn, "video_download_completed",
                 {"title": "SoulSync test notification", "quality": "it works"})
