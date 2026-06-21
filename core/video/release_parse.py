"""Parse a scene/p2p release title into structured quality + scope fields.

This is the video equivalent of Sonarr/Radarr's release parser: given a raw name
like ``The Wire S02 1080p BluRay x265 DDP5.1-GROUP`` it pulls out resolution,
source, codec, HDR, audio, group, repack/proper, and the SEASON/EPISODE scope
(single episode vs season pack vs complete-series pack). The download engine and
the search UI both rely on this to validate that a hit actually matches what was
searched (a season search must return the *whole* season, etc.).

Pure + regex-only (no DB, no network) so it's unit-tested in isolation. Isolated —
imports only re/typing; the music side never imports it.
"""

from __future__ import annotations

import re
from typing import Any

# Resolution — first match wins (most specific first).
_RES = [
    (re.compile(r"\b(2160p|4k|uhd)\b", re.I), "2160p"),
    (re.compile(r"\b1080[pi]\b", re.I), "1080p"),
    (re.compile(r"\b720[pi]\b", re.I), "720p"),
    (re.compile(r"\b(480[pi]|576[pi])\b", re.I), "480p"),
]
# Source — order matters (remux before bluray; web-dl before webrip).
_SOURCE = [
    (re.compile(r"\bremux\b", re.I), "remux"),
    (re.compile(r"\b(blu-?ray|bdrip|brrip|bd25|bd50)\b", re.I), "bluray"),
    (re.compile(r"\b(web-?dl|web\.?dl|amzn|nf|dsnp|hmax|atvp)\b", re.I), "web-dl"),
    (re.compile(r"\bweb-?rip\b", re.I), "webrip"),
    (re.compile(r"\bweb\b", re.I), "web-dl"),   # plain "WEB" (very common) — treat as WEB-DL
    (re.compile(r"\bhdtv\b", re.I), "hdtv"),
    (re.compile(r"\b(dvdrip|dvd)\b", re.I), "dvd"),
    (re.compile(r"\b(cam|hdcam|ts|telesync|hdts)\b", re.I), "cam"),
    (re.compile(r"\b(scr|screener|dvdscr|bdscr)\b", re.I), "screener"),
    (re.compile(r"\bworkprint\b", re.I), "workprint"),
]
_CODEC = [
    (re.compile(r"\b(x265|h\.?265|hevc)\b", re.I), "hevc"),
    (re.compile(r"\b(x264|h\.?264|avc)\b", re.I), "x264"),
    (re.compile(r"\bav1\b", re.I), "av1"),
]
_HDR = [
    (re.compile(r"\b(dolby\s?vision|do?vi|\bdv\b)\b", re.I), "dv"),
    (re.compile(r"\bhdr10\+\b", re.I), "hdr10"),
    (re.compile(r"\bhdr10\b", re.I), "hdr10"),
    (re.compile(r"\bhdr\b", re.I), "hdr"),
]
_AUDIO = [
    (re.compile(r"\batmos\b", re.I), "atmos"),
    (re.compile(r"\b(truehd|true-hd)\b", re.I), "truehd"),
    (re.compile(r"\b(dts-?hd|dts-?x)\b", re.I), "dts-hd"),
    (re.compile(r"\bdts\b", re.I), "dts"),
    (re.compile(r"\b(ddp|eac3|dd\+)\b", re.I), "eac3"),
    (re.compile(r"\b(ac3|dd5\.?1|dd2\.?0)\b", re.I), "ac3"),
    (re.compile(r"\baac\b", re.I), "aac"),
]

_RANGE = re.compile(r"\bS(\d{1,2})\s*[-–]\s*S?(\d{1,2})\b", re.I)   # S01-S05
_SXXEXX = re.compile(r"\bS(\d{1,2})[\s.]?E(\d{1,3})\b", re.I)        # S02E03
_SXX = re.compile(r"\bS(\d{1,2})\b", re.I)                          # S02 (pack)
_SEASON_WORD = re.compile(r"\bseason[\s.]?(\d{1,2})\b", re.I)        # Season 2
_COMPLETE = re.compile(r"\b(complete|collection|all\s?seasons)\b", re.I)
_GROUP = re.compile(r"-([A-Za-z0-9]{2,})\s*$")


def _first(table, text) -> Any:
    for rx, val in table:
        if rx.search(text):
            return val
    return None


def parse_release(title: Any) -> dict:
    """Parse a release name into quality + scope fields. Never raises."""
    t = str(title or "")
    out = {
        "title": t,
        "resolution": _first(_RES, t),
        "source": _first(_SOURCE, t),
        "codec": _first(_CODEC, t),
        "hdr": _first(_HDR, t),
        "audio": _first(_AUDIO, t),
        "group": None,
        "repack": bool(re.search(r"\brepack\b", t, re.I)),
        "proper": bool(re.search(r"\bproper\b", t, re.I)),
        "three_d": bool(re.search(r"\b3d\b", t, re.I)),
        "season": None,
        "season_end": None,
        "episode": None,
        "is_season_pack": False,
        "is_series_pack": False,
    }
    g = _GROUP.search(t)
    if g:
        out["group"] = g.group(1)

    rng = _RANGE.search(t)
    m = _SXXEXX.search(t)
    if rng:
        out["season"] = int(rng.group(1))
        out["season_end"] = int(rng.group(2))
        out["is_series_pack"] = True
    elif m:
        out["season"] = int(m.group(1))
        out["episode"] = int(m.group(2))
    else:
        sm = _SXX.search(t) or _SEASON_WORD.search(t)
        if sm:
            out["season"] = int(sm.group(1))
            out["is_season_pack"] = True
        if _COMPLETE.search(t):
            out["is_series_pack"] = True
            out["is_season_pack"] = False
    return out


__all__ = ["parse_release"]
