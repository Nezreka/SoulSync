"""Video library-organisation settings: naming templates + post-process toggles.

Mirrors the MUSIC side's file-organisation standard — editable ``$token`` path
templates with per-component sanitisation and dangling-separator cleanup — but for
video's movie/episode shape, plus the optional post-process behaviours the user can
turn on and off.

Settings (persisted as JSON in video.db ``video_settings['organization']``):
  - ``movie_template``      : path template for movies   (folders via '/', last = file)
  - ``episode_template``    : path template for episodes
  - ``verify_with_ffprobe`` : probe the real file (true quality + reject junk)
  - ``replace_existing``    : upgrade-replace a worse copy already in the library
  - ``transfer_mode``       : 'copy' (reclaim source unless torrent) | 'move'
  - ``carry_subtitles``     : bring sibling .srt/.ass alongside the video

Template tokens
  Movies:   $title $titlefirst $year $quality $resolution $source $codec $edition
            $tmdbid $imdbid
  Episodes: $series $season $seasonraw $episode $episodetitle $year $quality
            $resolution $source $codec $tvdbid
  ($season/$episode are zero-padded to 2; $seasonraw is the bare number.)

Pure data + a pure renderer (no DB, no FS) so it's unit-tested in isolation. Isolated —
stdlib + sibling video ``library_paths`` only; nothing from the music side.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from core.video.library_paths import sanitize, source_label

DEFAULTS = {
    "version": 1,
    "movie_template": "$title ($year)/$title ($year) $quality",
    "episode_template": "$series/Season $season/$series - S$seasonE$episode - $episodetitle $quality",
    # YouTube channels organise as a Plex "TV by date" show: channel = series,
    # season = upload YEAR, episode named by upload DATE + title.
    "youtube_template": "$channel/Season $year/$channel - $date - $title",
    "verify_with_ffprobe": True,
    "replace_existing": True,
    "transfer_mode": "copy",
    "carry_subtitles": True,
    "save_artwork": True,    # nfo + artwork sidecars on by default (cheap, local) — best-in-class
    "write_nfo": True,
    "download_subtitles": False,   # opt-in: fetches from OpenSubtitles (external, rate-limited)
    "subtitle_langs": "en",
}

_TRANSFER_MODES = ("copy", "move")


def default_settings() -> dict:
    return dict(DEFAULTS)


def normalize(raw: Any) -> dict:
    """Coerce stored/posted settings to a valid shape, filling gaps from the default.
    Blank templates fall back to the default; never raises."""
    d = default_settings()
    if not isinstance(raw, dict):
        return d
    for key in ("movie_template", "episode_template", "youtube_template"):
        v = raw.get(key)
        if isinstance(v, str) and v.strip():
            d[key] = v.strip()
    for key in ("verify_with_ffprobe", "replace_existing", "carry_subtitles",
                "save_artwork", "write_nfo", "download_subtitles"):
        if key in raw:
            d[key] = bool(raw.get(key))
    tm = str(raw.get("transfer_mode") or "").strip().lower()
    if tm in _TRANSFER_MODES:
        d["transfer_mode"] = tm
    if "subtitle_langs" in raw:
        from core.video.subtitles import parse_langs
        d["subtitle_langs"] = ",".join(parse_langs(raw.get("subtitle_langs")))
    return d


def load(db) -> dict:
    raw = db.get_setting("organization")
    if raw:
        try:
            return normalize(json.loads(raw))
        except (ValueError, TypeError):
            pass
    return default_settings()


def save(db, raw: Any) -> dict:
    s = normalize(raw)
    db.set_setting("organization", json.dumps(s))
    return s


# ── the template engine (the music $token standard, video tokens) ─────────────
def _str(v: Any) -> str:
    if v is None:
        return ""
    return str(v)


def _pad2(v: Any) -> str:
    try:
        return "%02d" % int(v)
    except (TypeError, ValueError):
        return _str(v)


def _plausible_year(v: Any) -> bool:
    try:
        return 1870 <= int(v) <= 2999
    except (TypeError, ValueError):
        return False


def _ext(ext: Any) -> str:
    e = str(ext or "").strip().lower()
    if not e:
        return ""
    return e if e.startswith(".") else "." + e


def _movie_values(f: dict) -> dict:
    title = f.get("title") or "Unknown"
    return {
        "title": title,
        "titlefirst": (str(title)[:1] or "U").upper(),
        "year": _str(f.get("year")) if _plausible_year(f.get("year")) else "",
        "quality": _str(f.get("quality")),
        "resolution": _str(f.get("resolution")),
        "source": source_label(f.get("source")),
        "codec": _str(f.get("codec")).upper(),
        "edition": _str(f.get("edition")),
        "tmdbid": _str(f.get("tmdbid")),
        "imdbid": _str(f.get("imdbid")),
    }


def _episode_values(f: dict) -> dict:
    series = f.get("series") or f.get("title") or "Unknown"
    return {
        "series": series,
        "season": _pad2(f.get("season")),
        "seasonraw": _str(f.get("season")),
        "episode": _pad2(f.get("episode")),
        "episodetitle": _str(f.get("episode_title")),
        "year": _str(f.get("year")) if _plausible_year(f.get("year")) else "",
        "quality": _str(f.get("quality")),
        "resolution": _str(f.get("resolution")),
        "source": source_label(f.get("source")),
        "codec": _str(f.get("codec")).upper(),
        "tvdbid": _str(f.get("tvdbid")),
    }


def _youtube_values(f: dict) -> dict:
    """Template values for a YouTube upload — channel-as-show, season=year, date-named
    episode (Plex 'TV by date'). ``published_at``/``date`` is 'YYYY-MM-DD'."""
    channel = f.get("channel") or f.get("series") or f.get("title") or "Unknown"
    pub = str(f.get("published_at") or f.get("date") or "")[:10]
    y = m = d = ""
    if len(pub) == 10 and pub[4] == "-" and pub[7] == "-":
        y, m, d = pub[0:4], pub[5:7], pub[8:10]
    has_year = _plausible_year(y)
    return {
        "channel": channel,
        "title": _str(f.get("title")) or "Unknown",
        "year": y if has_year else "",
        "date": pub if has_year else "",     # only a trustworthy full date
        "month": m if has_year else "",
        "day": d if has_year else "",
        "videoid": _str(f.get("youtube_id")),
    }


def render_template(template: Any, values: dict) -> str:
    """Substitute ``$token`` / ``${token}`` from ``values`` into ``template``. Each
    value is path-sanitised first (so a title with '/' can't spawn a folder), and
    tokens are replaced longest-name-first ($episodetitle before $episode)."""
    clean = {k: sanitize(v) for k, v in (values or {}).items()}
    out = str(template or "")
    for tok in sorted(clean, key=len, reverse=True):
        out = out.replace("${" + tok + "}", clean[tok])
    for tok in sorted(clean, key=len, reverse=True):
        out = out.replace("$" + tok, clean[tok])
    return out


def _tidy_component(part: str) -> str:
    """Clean one path segment: drop a ' - ' left dangling by an empty token, remove
    empty ()/[] left by an empty $year, collapse whitespace, trim stray dashes and
    Windows-hostile trailing dots/spaces."""
    p = re.sub(r"\s+-\s+(?=(\s|$))", " ", part)   # ' - ' before an empty token
    p = re.sub(r"\(\s*\)", "", p)                 # empty ( ) from a missing $year
    p = re.sub(r"\[\s*\]", "", p)
    p = re.sub(r"\s+", " ", p).strip()
    p = p.strip("-").strip()
    return p.rstrip(". ")


def render_path(scope: Any, root: Any, fields: dict, settings: Any, ext: Any) -> dict:
    """Render the destination for a finished download from the user's templates.
    Returns ``{"dir", "filename", "path"}`` (same shape as ``library_paths.plan_path``).
    An unsupported scope falls back to a flat drop so a file is never lost."""
    settings = settings if isinstance(settings, dict) else {}
    fields = fields if isinstance(fields, dict) else {}
    root = str(root or "")
    sc = str(scope or "").lower()

    if sc == "movie":
        tmpl = settings.get("movie_template") or DEFAULTS["movie_template"]
        values = _movie_values(fields)
    elif sc == "episode":
        tmpl = settings.get("episode_template") or DEFAULTS["episode_template"]
        values = _episode_values(fields)
    elif sc == "youtube":
        tmpl = settings.get("youtube_template") or DEFAULTS["youtube_template"]
        values = _youtube_values(fields)
    else:
        base = (sanitize(fields.get("title")) or "download") + _ext(ext)
        return {"dir": root, "filename": base, "path": os.path.join(root, base)}

    rendered = render_template(tmpl, values)
    parts = [p for p in (_tidy_component(seg) for seg in rendered.split("/")) if p]
    if not parts:
        parts = ["download"]
    d = os.path.join(root, *parts[:-1]) if len(parts) > 1 else root
    filename = parts[-1] + _ext(ext)
    return {"dir": d, "filename": filename, "path": os.path.join(d, filename)}


__all__ = [
    "DEFAULTS", "default_settings", "normalize", "load", "save",
    "render_template", "render_path",
]
