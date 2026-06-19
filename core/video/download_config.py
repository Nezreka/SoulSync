"""Video download SOURCE config — which source(s) to download from.

Video only ever uses three sources: **soulseek / torrent / usenet** (no streaming
APIs — those are music-only). ``download_mode`` is one of those three, or
``hybrid``; in hybrid mode ``hybrid_order`` is the ordered chain of enabled sources
the (later-phase) engine tries in turn.

Pure normalize here (no DB, no network) so it's unit-tested in isolation. Stored in
video.db's ``video_settings`` (``download_mode`` + ``hybrid_order`` JSON). Isolated
from the music side — imports only json/typing.
"""

from __future__ import annotations

import json
from typing import Any

SOURCES = ("soulseek", "torrent", "usenet")
MODES = SOURCES + ("hybrid",)


def normalize_mode(value: Any) -> str:
    v = str(value or "").strip().lower()
    return v if v in MODES else "soulseek"


def normalize_hybrid_order(value: Any) -> list:
    """Ordered, de-duped list of valid sources; defaults to ['soulseek']. Accepts a
    JSON string (as stored) or a list (as posted)."""
    arr = value
    if isinstance(arr, str):
        try:
            arr = json.loads(arr)
        except (ValueError, TypeError):
            arr = None
    out = []
    if isinstance(arr, list):
        for s in arr:
            s = str(s or "").strip().lower()
            if s in SOURCES and s not in out:
                out.append(s)
    return out or ["soulseek"]


def load(db) -> dict:
    return {
        "download_mode": normalize_mode(db.get_setting("download_mode")),
        "hybrid_order": normalize_hybrid_order(db.get_setting("hybrid_order")),
    }


def save(db, body: Any) -> dict:
    """Persist whichever of mode/hybrid_order is present in ``body``."""
    body = body if isinstance(body, dict) else {}
    if "download_mode" in body:
        db.set_setting("download_mode", normalize_mode(body.get("download_mode")))
    if "hybrid_order" in body:
        db.set_setting("hybrid_order", json.dumps(normalize_hybrid_order(body.get("hybrid_order"))))
    return load(db)


__all__ = ["SOURCES", "MODES", "normalize_mode", "normalize_hybrid_order", "load", "save"]
