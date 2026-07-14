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


# Resolution → pixel-height ceiling for yt-dlp's ``height<=`` filter. "best" = no cap.
_RES_HEIGHT = {"best": None, "4320p": 4320, "2160p": 2160, "1440p": 1440,
               "1080p": 1080, "720p": 720, "480p": 480, "360p": 360}


def format_selection(profile: Any) -> dict:
    """Map a profile to the yt-dlp options the (download) engine passes through:
    ``{format, format_sort, merge_output_format}``. Pure — no DB, no network.

    * ``format`` takes the best video+audio capped to the resolution ceiling, falling
      back to an uncapped best so a video that only exists above the cap still grabs.
    * ``format_sort`` is an ordered soft-preference list (codec → resolution → fps →
      SDR) — yt-dlp picks the top match, so these never *exclude* a stream, they rank it.
    * ``merge_output_format`` is the container yt-dlp muxes into.

    NB: the exact yt-dlp tokens are tunable against the live yt-dlp version when the
    downloader is wired; the shape (one capped format expr + a ranked sort list) is the
    contract the tests pin.
    """
    p = normalize(profile)
    height = _RES_HEIGHT.get(p["max_resolution"])
    if height:
        fmt = "bv*[height<=%d]+ba/b[height<=%d]/bv*+ba/b" % (height, height)
    else:
        fmt = "bv*+ba/b"

    sort: list[str] = []
    if p["video_codec"] != "any":
        sort.append("vcodec:%s" % p["video_codec"])     # soft codec preference
    sort.append("res:%d" % height if height else "res")  # prefer the ceiling, else highest
    if p["prefer_60fps"]:
        sort.append("fps")                               # prefer higher frame rate
    if not p["allow_hdr"]:
        sort.append("hdr:SDR")                           # rank SDR above HDR (don't exclude)

    return {"format": fmt, "format_sort": sort, "merge_output_format": p["container"]}


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
    "default_profile", "normalize", "format_selection", "load", "save",
]
