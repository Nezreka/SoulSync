"""Video quality profile — ONE unified, Radarr/Sonarr-class profile applied to
every video download source (slskd / torrent / usenet).

Unlike the music side (where quality is bitrate density), video quality is a
**source×resolution tier** parsed from a release title, refined by codec / HDR /
audio preferences. The (later-phase) download engine uses this profile to pick the
best candidate and to decide when a library item is "good enough" (the cutoff).

The model (rich-curated — Radarr-competitive without a full custom-formats engine):

  - ``tiers``         : the source×resolution quality ladder (Remux-2160p … SDTV)
                        as ONE ranked list; each tier enabled + ordered best→worst.
  - ``cutoff``        : once the library holds a tier at/above this rank, stop
                        upgrading (prevents endless re-grabbing).
  - ``rejects``       : hard blocks — never grab these (cam / screener / workprint /
                        3d / optionally x264).
  - preferences (SOFT — they score/tie-break, they never reject on their own):
      ``prefer_codec`` (any|hevc|av1), ``prefer_hdr`` (off|prefer|require),
      ``prefer_audio`` (any|surround|lossless|atmos), ``prefer_repack`` (bool).
  - ``min_size_gb`` / ``max_size_gb`` : size guard per item (0 = no limit).

Pure data + normalize/validate here (no DB, no network) so it's unit-tested in
isolation. Persisted as a JSON blob in video.db's ``video_settings['quality_profile']``.
This module is isolated — it imports nothing from the music side.
"""

from __future__ import annotations

import json
from typing import Any

# The quality ladder, ordered best→worst. ``key`` = ``<source>-<resolution>`` (plus
# the two resolution-less SD tiers). This is the default ranking the UI renders.
TIERS = (
    "remux-2160p", "bluray-2160p", "web-2160p",
    "remux-1080p", "bluray-1080p", "web-1080p", "webrip-1080p", "hdtv-1080p",
    "bluray-720p", "web-720p", "hdtv-720p",
    "dvd", "sdtv",
)

# Default-enabled tiers: solid 1080p + 720p coverage. 4K tiers off (size) and the
# SD tiers (dvd/sdtv) off — users opt into those deliberately.
_DEFAULT_ON = frozenset({
    "remux-1080p", "bluray-1080p", "web-1080p", "webrip-1080p", "hdtv-1080p",
    "bluray-720p", "web-720p", "hdtv-720p",
})

# Hard rejects (never grabbed). x264 is offered but OFF by default (rejecting it
# would drop most releases) — power users who only want HEVC/AV1 can enable it.
REJECTS = ("cam", "screener", "workprint", "3d", "x264")

CODECS = ("any", "hevc", "av1")              # SOFT codec preference (tie-breaker)
HDR_MODES = ("off", "prefer", "require")     # require = HDR-only (a real filter)
AUDIO_MODES = ("any", "surround", "lossless", "atmos")
MAX_SIZE_CAP_GB = 200                        # slider ceiling; 0 means "no limit"

# The cutoff is a LOOSE resolution target (Radarr-style "upgrade until"): once the
# library holds an item at this resolution or better, stop chasing upgrades. ""
# (empty) means "best available — always upgrade". Always offered in full, regardless
# of which specific tiers are toggled on.
RESOLUTIONS = ("2160p", "1080p", "720p", "480p")

_TIER_SET = frozenset(TIERS)


def default_profile() -> dict:
    """A sensible best-in-class default: full 1080p/720p ladder, loose cutoff at
    1080p, junk rejected, HEVC + HDR preferred (soft)."""
    return {
        "version": 2,
        "tiers": [{"key": k, "enabled": k in _DEFAULT_ON} for k in TIERS],
        "cutoff_resolution": "1080p",
        "rejects": ["cam", "screener", "workprint", "3d"],
        "prefer_codec": "hevc",
        "prefer_hdr": "prefer",
        "prefer_audio": "any",
        "prefer_repack": True,
        "max_movie_gb": 0,      # per-item size guard, split by runtime (0 = no limit)
        "max_episode_gb": 0,
    }


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clamp_size(value: Any) -> int:
    return min(MAX_SIZE_CAP_GB, max(0, _coerce_int(value, 0)))


def normalize_tiers(value: Any) -> list:
    """Rebuild the ranked tier ladder: keep the caller's order for known tiers,
    coerce each ``enabled``, drop junk/dupes, then append any missing tiers in
    canonical order so the ladder is always complete. Defaults from ``_DEFAULT_ON``."""
    enabled = {k: (k in _DEFAULT_ON) for k in TIERS}
    order: list = []
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                k = str(item.get("key") or "").strip().lower()
            else:
                k = str(item or "").strip().lower()
            if k in _TIER_SET and k not in order:
                order.append(k)
                if isinstance(item, dict) and "enabled" in item:
                    enabled[k] = bool(item.get("enabled"))
    for k in TIERS:                      # complete the ladder, canonical order
        if k not in order:
            order.append(k)
    return [{"key": k, "enabled": enabled[k]} for k in order]


def normalize(raw: Any) -> dict:
    """Coerce a stored/posted profile to a valid shape, filling gaps from the
    default. Unknown keys dropped; invalid values fall back. Never raises."""
    d = default_profile()
    if not isinstance(raw, dict):
        return d

    d["tiers"] = normalize_tiers(raw.get("tiers"))

    if "cutoff_resolution" in raw:
        cr = str(raw.get("cutoff_resolution") or "").strip().lower()
        if cr in RESOLUTIONS or cr == "":   # "" = best available / always upgrade
            d["cutoff_resolution"] = cr

    rj = raw.get("rejects")
    if isinstance(rj, list):
        chosen = {str(x or "").strip().lower() for x in rj}
        d["rejects"] = [r for r in REJECTS if r in chosen]   # canonical order, valid only

    if raw.get("prefer_codec") in CODECS:
        d["prefer_codec"] = raw["prefer_codec"]
    if raw.get("prefer_hdr") in HDR_MODES:
        d["prefer_hdr"] = raw["prefer_hdr"]
    if raw.get("prefer_audio") in AUDIO_MODES:
        d["prefer_audio"] = raw["prefer_audio"]
    d["prefer_repack"] = bool(raw.get("prefer_repack", d["prefer_repack"]))

    d["max_movie_gb"] = _clamp_size(raw.get("max_movie_gb"))
    d["max_episode_gb"] = _clamp_size(raw.get("max_episode_gb"))
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
    "TIERS", "REJECTS", "CODECS", "HDR_MODES", "AUDIO_MODES", "RESOLUTIONS",
    "MAX_SIZE_CAP_GB", "default_profile", "normalize", "normalize_tiers", "load", "save",
]
