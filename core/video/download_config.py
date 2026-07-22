"""Video download SOURCE config — which source(s) to download from.

Video only ever uses three sources: **soulseek / torrent / usenet** (no streaming
APIs — those are music-only). ``download_mode`` is one of those three, or
``hybrid``; in hybrid mode ``hybrid_order`` is the ordered chain of enabled sources
the (later-phase) engine tries in turn.

Pure normalize here (no DB, no network) so it's unit-tested in isolation. Stored in
video.db's ``video_settings`` (``download_mode`` + ``hybrid_order`` JSON). Isolated:
imports only json/typing, and the music side never imports it.
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


def _norm_ratio(value: Any) -> float:
    try:
        return max(0.0, min(100.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _norm_hours(value: Any) -> int:
    try:
        return max(0, min(24 * 365, int(value)))
    except (TypeError, ValueError):
        return 0


def _norm_seed_mode(value: Any) -> str:
    return "client" if str(value or "").strip().lower() == "client" else "soulsync"


def load(db) -> dict:
    return {
        "download_mode": normalize_mode(db.get_setting("download_mode")),
        "hybrid_order": normalize_hybrid_order(db.get_setting("hybrid_order")),
        # Seeding lifecycle (arr-parity P5). BOTH goals default 0 = the sweep
        # is OFF and torrents behave exactly as before — managing (and deleting
        # from) someone's torrent client is strictly opt-in.
        "seed_ratio_goal": _norm_ratio(db.get_setting("seed_ratio_goal")),
        "seed_time_goal_hours": _norm_hours(db.get_setting("seed_time_goal_hours")),
        "seed_remove_data": (db.get_setting("seed_remove_data") or "1") != "0",
        # Who enforces the goal: "soulsync" (sweep polls + removes) or "client"
        # (write the ratio/time limit into the torrent client, arr-style).
        "seed_mode": _norm_seed_mode(db.get_setting("seed_mode")),
    }


def save(db, body: Any) -> dict:
    """Persist whichever known keys are present in ``body``."""
    body = body if isinstance(body, dict) else {}
    if "download_mode" in body:
        db.set_setting("download_mode", normalize_mode(body.get("download_mode")))
    if "hybrid_order" in body:
        db.set_setting("hybrid_order", json.dumps(normalize_hybrid_order(body.get("hybrid_order"))))
    if "seed_ratio_goal" in body:
        db.set_setting("seed_ratio_goal", str(_norm_ratio(body.get("seed_ratio_goal"))))
    if "seed_time_goal_hours" in body:
        db.set_setting("seed_time_goal_hours", str(_norm_hours(body.get("seed_time_goal_hours"))))
    if "seed_remove_data" in body:
        db.set_setting("seed_remove_data", "1" if body.get("seed_remove_data") else "0")
    if "seed_mode" in body:
        db.set_setting("seed_mode", _norm_seed_mode(body.get("seed_mode")))
    return load(db)


__all__ = ["SOURCES", "MODES", "normalize_mode", "normalize_hybrid_order", "load", "save"]
