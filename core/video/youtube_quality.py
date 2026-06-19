"""YouTube download quality profile — deliberately SEPARATE from the main video
quality profile (``core/video/quality_profile.py``).

YouTube is fetched with yt-dlp, not from scene/p2p releases, so the Radarr-style
ladder (Remux / BluRay / WEB-DL, HDR/audio tiers, scene rejects) is meaningless
here. yt-dlp just picks a stream by **resolution + codec + container**, so this
profile is small: a resolution ceiling, a codec preference, an output container,
and two flags (60fps / HDR). The (later-phase) downloader maps these to a yt-dlp
``format`` / ``format_sort`` selection.

Pure normalize/load/save (no DB, no network) so it's unit-tested in isolation.
Persisted as a JSON blob in video.db's ``video_settings['youtube_quality_profile']``.
Isolated — imports only json/typing; the music side never imports it.
"""

from __future__ import annotations

import json
from typing import Any

# Resolution ceiling (yt-dlp height filter). "best" = no cap (take the top stream).
RESOLUTIONS = ("best", "4320p", "2160p", "1440p", "1080p", "720p", "480p", "360p")
CODECS = ("any", "av1", "vp9", "h264")       # SOFT preference; "any" = let yt-dlp pick best
CONTAINERS = ("mp4", "mkv", "webm")          # yt-dlp --merge-output-format


def default_profile() -> dict:
    """Sensible default: 1080p ceiling, yt-dlp's best codec, mp4 (most compatible),
    prefer 60fps, SDR (HDR off — washes out on non-HDR displays)."""
    return {
        "version": 1,
        "max_resolution": "1080p",
        "video_codec": "any",
        "container": "mp4",
        "prefer_60fps": True,
        "allow_hdr": False,
    }


def normalize(raw: Any) -> dict:
    """Coerce a stored/posted profile to a valid shape, filling gaps from the
    default. Unknown keys dropped; invalid values fall back. Never raises."""
    d = default_profile()
    if not isinstance(raw, dict):
        return d
    if raw.get("max_resolution") in RESOLUTIONS:
        d["max_resolution"] = raw["max_resolution"]
    if raw.get("video_codec") in CODECS:
        d["video_codec"] = raw["video_codec"]
    if raw.get("container") in CONTAINERS:
        d["container"] = raw["container"]
    d["prefer_60fps"] = bool(raw.get("prefer_60fps", d["prefer_60fps"]))
    d["allow_hdr"] = bool(raw.get("allow_hdr", d["allow_hdr"]))
    return d


def load(db) -> dict:
    """Read + normalize the stored profile, or the default if none/garbage."""
    raw = db.get_setting("youtube_quality_profile")
    if raw:
        try:
            return normalize(json.loads(raw))
        except (ValueError, TypeError):
            pass
    return default_profile()


def save(db, raw: Any) -> dict:
    """Normalize + persist; returns the normalized profile that was stored."""
    prof = normalize(raw)
    db.set_setting("youtube_quality_profile", json.dumps(prof))
    return prof


__all__ = [
    "RESOLUTIONS", "CODECS", "CONTAINERS",
    "default_profile", "normalize", "load", "save",
]
