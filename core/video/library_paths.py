"""Organize a finished video download into a Radarr/Sonarr-standard library path.

Given the SCOPE/intent of a grab (movie vs episode, plus title/year/season/episode)
and the PARSED release quality, build the canonical folder + filename the file should
land at:

    <movies_root>/The Matrix (1999)/The Matrix (1999) Bluray-1080p.mkv
    <tv_root>/Breaking Bad/Season 01/Breaking Bad - S01E01 - Pilot WEBDL-1080p.mkv

These are the community-standard templates and also exactly what Plex/Jellyfin want
for reliable identification (we organise on disk; the server just refreshes).

Pure (no filesystem, no DB) so it's unit-tested in isolation. Isolated — stdlib +
the sibling ``download_pipeline.basename_of`` only; nothing from the music side.
"""

from __future__ import annotations

import os
import re
from typing import Any

from core.video.download_pipeline import basename_of

# Characters illegal on Windows / awkward on most filesystems, plus control chars.
_ILLEGAL = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_TRAILING_DOTSPACE = re.compile(r"[ .]+$")

# Parsed source token → the label used in the Radarr ``{Quality Full}`` tag.
_SRC_LABEL = {
    "remux": "Remux", "bluray": "Bluray", "web-dl": "WEBDL",
    "webrip": "WEBRip", "hdtv": "HDTV", "dvd": "DVD",
}


def sanitize(name: Any) -> str:
    """Filesystem-safe path COMPONENT: strip illegal chars, collapse whitespace, and
    trim trailing dots/spaces (Windows rejects those). Never contains a separator."""
    s = _ILLEGAL.sub("", str(name or ""))
    s = re.sub(r"\s+", " ", s).strip()
    s = _TRAILING_DOTSPACE.sub("", s)
    return s


def _year_suffix(year: Any) -> str:
    """' (1999)' for a plausible release year, else '' (keeps titles clean)."""
    try:
        y = int(year)
    except (TypeError, ValueError):
        return ""
    return " (%d)" % y if 1870 <= y <= 2999 else ""


def source_label(source: Any) -> str:
    """The display label for a parsed source token ('bluray' → 'Bluray', 'web-dl' →
    'WEBDL'), '' when unknown. Used for the $source template token."""
    return _SRC_LABEL.get(str(source or "").strip().lower(), "")


def quality_full(parsed: Any) -> str:
    """The Radarr-style ``{Quality Full}`` tag, e.g. 'Bluray-1080p' / 'WEBDL-1080p' /
    'Remux-2160p'. '' when neither source nor resolution can be determined. A
    proper/repack is appended ('… Proper') so an upgrade reads at a glance."""
    parsed = parsed if isinstance(parsed, dict) else {}
    src = _SRC_LABEL.get(parsed.get("source"))
    res = parsed.get("resolution")
    if src and res:
        tag = src + "-" + res
    elif res:
        tag = res
    elif src:
        tag = src
    else:
        return ""
    if parsed.get("proper") or parsed.get("repack"):
        tag += " Proper"
    return tag


def movie_folder(title: Any, year: Any) -> str:
    """'The Matrix (1999)' — the per-movie folder name."""
    return (sanitize(title) or "Unknown") + _year_suffix(year)


def movie_filename(title: Any, year: Any, quality: Any, ext: Any) -> str:
    """'The Matrix (1999) Bluray-1080p.mkv' (quality tag omitted when unknown)."""
    stem = (sanitize(title) or "Unknown") + _year_suffix(year)
    q = sanitize(quality)
    if q:
        stem += " " + q
    return stem + _ext(ext)


def show_folder(series: Any) -> str:
    """'Breaking Bad' — the per-series folder name."""
    return sanitize(series) or "Unknown"


def season_folder(season: Any) -> str:
    """'Season 01' (or 'Specials' for season 0, Sonarr's convention)."""
    try:
        n = int(season)
    except (TypeError, ValueError):
        n = None
    if n == 0:
        return "Specials"
    return "Season %02d" % (n if n is not None else 0)


def episode_filename(series: Any, season: Any, episode: Any, ep_title: Any,
                     quality: Any, ext: Any) -> str:
    """'Breaking Bad - S01E01 - Pilot WEBDL-1080p.mkv'. The ' - {title}' segment and
    the trailing quality tag are each omitted when unknown."""
    try:
        s = int(season)
    except (TypeError, ValueError):
        s = 0
    try:
        e = int(episode)
    except (TypeError, ValueError):
        e = 0
    stem = "%s - S%02dE%02d" % (sanitize(series) or "Unknown", s, e)
    t = sanitize(ep_title)
    if t:
        stem += " - " + t
    q = sanitize(quality)
    if q:
        stem += " " + q
    return stem + _ext(ext)


def _ext(ext: Any) -> str:
    e = str(ext or "").strip().lower()
    if not e:
        return ""
    return e if e.startswith(".") else "." + e


def plan_path(scope: Any, root: Any, ctx: dict, quality: Any, ext: Any) -> dict:
    """Resolve the canonical destination for a finished download.

    ``ctx`` = {title, year, season, episode, episode_title}. Returns
    ``{"dir": <abs folder>, "filename": <name>, "path": <abs file path>}``. For an
    unsupported scope it falls back to a flat drop (root + sanitized basename) so a
    file is never lost — the caller gates scope before relying on this."""
    ctx = ctx if isinstance(ctx, dict) else {}
    root = str(root or "")
    sc = str(scope or "").lower()
    if sc == "movie":
        d = os.path.join(root, movie_folder(ctx.get("title"), ctx.get("year")))
        fn = movie_filename(ctx.get("title"), ctx.get("year"), quality, ext)
    elif sc == "episode":
        d = os.path.join(root, show_folder(ctx.get("title")), season_folder(ctx.get("season")))
        fn = episode_filename(ctx.get("title"), ctx.get("season"), ctx.get("episode"),
                              ctx.get("episode_title"), quality, ext)
    else:
        d = root
        fn = sanitize(basename_of(ctx.get("src") or "")) or "download"
    return {"dir": d, "filename": fn, "path": os.path.join(d, fn)}


__all__ = [
    "sanitize", "source_label", "quality_full", "movie_folder", "movie_filename",
    "show_folder", "season_folder", "episode_filename", "plan_path",
]
