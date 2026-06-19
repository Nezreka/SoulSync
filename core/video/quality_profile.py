"""Video quality profile — ONE unified profile applied to every video download
source (slskd / torrent / usenet).

Unlike the music side (where quality is bitrate density), video quality is
**resolution + source + codec**, parsed from a release title or filename. So the
profile is a small, source-agnostic ranking the (later-phase) download engine will
use to pick the best candidate:

  - resolution tiers (2160p / 1080p / 720p / 480p), each enabled + priority-ordered
  - a preferred source order (bluray > web-dl > webrip > hdtv)
  - a codec preference (any / x265 / x264) and an HDR preference
  - an optional max size cap per item, and a fallback toggle

Pure data + normalize/validate here (no DB, no network) so it's unit-tested in
isolation. Persisted as a JSON blob in video.db's ``video_settings['quality_profile']``.
This module is isolated — it imports nothing from the music side.
"""

from __future__ import annotations

import json
from typing import Any

# Ordered best→worst; the UI renders these as the default priority order.
RESOLUTIONS = ("2160p", "1080p", "720p", "480p")
SOURCES = ("bluray", "web-dl", "webrip", "hdtv")
CODECS = ("any", "x265", "x264")
MAX_SIZE_CAP_GB = 200   # slider ceiling; 0 means "no cap"


def default_profile() -> dict:
    """A sensible default: 1080p/720p on, 4K off (size), SD off."""
    return {
        "version": 1,
        "resolutions": {
            "2160p": {"enabled": False, "priority": 1},
            "1080p": {"enabled": True, "priority": 2},
            "720p": {"enabled": True, "priority": 3},
            "480p": {"enabled": False, "priority": 4},
        },
        "source_priority": list(SOURCES),
        "codec": "any",
        "prefer_hdr": False,
        "max_size_gb": 0,
        "fallback_enabled": True,
    }


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize(raw: Any) -> dict:
    """Coerce a stored/posted profile to a valid shape, filling gaps from the
    default. Unknown keys are dropped; invalid values fall back. Never raises."""
    d = default_profile()
    if not isinstance(raw, dict):
        return d

    res = raw.get("resolutions")
    if isinstance(res, dict):
        for i, key in enumerate(RESOLUTIONS):
            r = res.get(key)
            if isinstance(r, dict):
                d["resolutions"][key] = {
                    "enabled": bool(r.get("enabled", d["resolutions"][key]["enabled"])),
                    "priority": _coerce_int(r.get("priority"), i + 1),
                }

    sp = raw.get("source_priority")
    if isinstance(sp, list):
        clean = []
        for s in sp:                 # keep known sources, in order, no dupes/junk
            if s in SOURCES and s not in clean:
                clean.append(s)
        for s in SOURCES:            # append any the caller dropped, canonical order
            if s not in clean:
                clean.append(s)
        d["source_priority"] = clean

    if raw.get("codec") in CODECS:
        d["codec"] = raw["codec"]
    d["prefer_hdr"] = bool(raw.get("prefer_hdr", d["prefer_hdr"]))
    d["max_size_gb"] = min(MAX_SIZE_CAP_GB, max(0, _coerce_int(raw.get("max_size_gb"), 0)))
    d["fallback_enabled"] = bool(raw.get("fallback_enabled", True))
    return d


def load(db) -> dict:
    """Read + normalize the stored profile, or the default if none/garbage."""
    raw = db.get_setting("quality_profile")
    if raw:
        try:
            return normalize(json.loads(raw))
        except (ValueError, TypeError):
            pass
    return default_profile()


def save(db, raw: Any) -> dict:
    """Normalize + persist; returns the normalized profile that was stored."""
    prof = normalize(raw)
    db.set_setting("quality_profile", json.dumps(prof))
    return prof


__all__ = [
    "RESOLUTIONS", "SOURCES", "CODECS", "MAX_SIZE_CAP_GB",
    "default_profile", "normalize", "load", "save",
]
