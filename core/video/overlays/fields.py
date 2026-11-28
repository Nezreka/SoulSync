"""Badge value formatters — value → display text.

These MUST mirror the editor's JS field formatters (video-overlay-editor.js FIELDS)
so a badge renders at apply time exactly as it previewed in the Studio. Each
returns None when there's no value, which the compositor treats as "don't render
this badge" (the apply-time equivalent of the editor's placeholder).
"""

from __future__ import annotations


def _up(v) -> str:
    return str(v).upper()


def _round1(v):
    try:
        return round(float(v) * 10) / 10
    except (TypeError, ValueError):
        return v


def _resolution(v):
    if not v:
        return None
    s = str(v).lower()
    if "2160" in s or s == "4k":
        return "4K"
    if "1080" in s:
        return "1080p"
    if "720" in s:
        return "720p"
    if "480" in s or "576" in s:
        return "SD"
    return _up(v)


def _video_codec(v):
    if not v:
        return None
    s = str(v).lower()
    if "hevc" in s or "265" in s:
        return "HEVC"
    if "264" in s or s == "avc":
        return "H.264"
    if "av1" in s:
        return "AV1"
    if "vp9" in s:
        return "VP9"
    return _up(v)


def _audio_codec(v):
    if not v:
        return None
    s = str(v).lower()
    if "atmos" in s:
        return "ATMOS"
    if "truehd" in s:
        return "TrueHD"
    return _up(v)


_SOURCE_MAP = {"bluray": "BluRay", "web-dl": "WEB-DL", "webdl": "WEB-DL",
               "webrip": "WEBRip", "hdtv": "HDTV", "remux": "REMUX", "dvd": "DVD"}


def _source(v):
    if not v:
        return None
    return _SOURCE_MAP.get(str(v).lower(), _up(v))


def _aspect(v):
    return str(v) if v else None   # stored canonical already (e.g. '16:9', '2.40:1')


def _status(v):
    if not v:
        return None
    s = str(v).lower()
    if "cancel" in s:
        return "Canceled"
    if "end" in s:
        return "Ended"
    if "continu" in s or "return" in s:
        return "Returning"
    if "releas" in s:
        return "Released"
    if "upcom" in s or "announc" in s or "production" in s:
        return "Upcoming"
    return _up(v)


def _runtime(v):
    if v is None or v == "":
        return None
    try:
        v = int(v)
    except (TypeError, ValueError):
        return None
    h, m = divmod(v, 60)
    return (f"{h}h {m}m" if m else f"{h}h") if h else f"{m}m"


def _num(v):
    return None if v is None or v == "" else v


_FORMATTERS = {
    "resolution": _resolution,
    "hdr": lambda v: _up(v) if v else None,
    "video_codec": _video_codec,
    "audio_codec": _audio_codec,
    "source": _source,
    "aspect": _aspect,
    "imdb": lambda v: None if _num(v) is None else "IMDb " + str(_round1(v)),
    "rt": lambda v: None if _num(v) is None else "RT " + str(v) + "%",
    "rt_audience": lambda v: None if _num(v) is None else "RT Aud " + str(v) + "%",
    "metacritic": lambda v: None if _num(v) is None else "MC " + str(v),
    "tmdb": lambda v: None if _num(v) is None else "TMDB " + str(_round1(v)),
    "trakt": lambda v: None if _num(v) is None else "Trakt " + str(_round1(v)),
    "tvmaze": lambda v: None if _num(v) is None else "TVmaze " + str(_round1(v)),
    "anilist": lambda v: None if _num(v) is None else "AniList " + str(int(v)),
    "content_rating": lambda v: _up(v) if v else None,
    "status": _status,
    "year": lambda v: None if _num(v) is None else str(v),
    "runtime": _runtime,
    "season_count": lambda v: None if _num(v) is None else str(v) + (" Season" if int(v) == 1 else " Seasons"),
    "episode_count": lambda v: None if _num(v) is None else str(v) + " Episodes",
    # Sub-item (season/episode overlay) fields.
    "season_number": lambda v: None if _num(v) is None else "Season " + str(int(v)),
    "episode_number": lambda v: None if _num(v) is None else "Episode " + str(int(v)),
    "episode_code": lambda v: str(v) if v else None,   # e.g. 'S1E1', built in sample_data
    "subtitles": lambda v: None if _num(v) is None else str(v) + (" Subtitle" if int(v) == 1 else " Subtitles"),
    # Only a badge when there's actually more than one version (a single copy is the norm).
    "versions": lambda v: None if (_num(v) is None or int(v) <= 1) else str(v) + " Versions",
    "mediastinger": lambda v: "After Credits" if v else None,   # presence flag
    "title": lambda v: str(v) if v else None,
    "collection": lambda v: str(v) if v else None,   # franchise (TMDB collection)
    "tagline": lambda v: str(v) if v else None,
    "awards": lambda v: ("Oscar Winner" if str(v).lower() == "oscar" else "Award Winner") if v else None,
    "streaming": lambda v: str(v) if v else None,
    "network": lambda v: str(v) if v else None,
    "studio": lambda v: str(v) if v else None,
    # `genre` carries the full comma-joined genre list (so conditions can match
    # ANY of a title's genres); the badge shows just the primary (first) one.
    "genre": lambda v: (str(v).split(",")[0].strip() or None) if v else None,
}


def format_field(field: str, value) -> str | None:
    """Badge text for a bound field, or None if there's no value (→ don't render)."""
    fn = _FORMATTERS.get(field)
    if not fn:
        return None
    try:
        return fn(value)
    except (TypeError, ValueError):
        return None


# Rating logo badges (#2327): provider scale (10 vs 100) and default display format.
_RATING_SCALE = {
    "imdb": 10.0,
    "tmdb": 10.0,
    "trakt": 10.0,
    "tvmaze": 10.0,
    "rt": 100.0,
    "rt_critic": 100.0,
    "rt_audience": 100.0,
    "metacritic": 100.0,
    "anilist": 100.0,
}

_RATING_DEFAULT_FMT = {
    "imdb": "decimal",
    "tmdb": "decimal",
    "trakt": "decimal",
    "tvmaze": "decimal",
    "rt": "percentage",
    "rt_critic": "percentage",
    "rt_audience": "percentage",
    "metacritic": "numeric",
    "anilist": "percentage",
}


def format_rating_score(provider: str, value, fmt: str = "auto") -> str | None:
    """Score text for a native rating logo badge (#2327).
    Supports 'auto' (provider natural), 'percentage', 'decimal', and 'numeric'.
    Returns None when value is missing or non-numeric so the badge auto-hides."""
    if _num(value) is None:
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if num <= 0:
        return None
    key = str(provider or "imdb").lower()
    scale = _RATING_SCALE.get(key, 10.0)
    mode = str(fmt or "auto").lower()
    if mode == "auto":
        mode = _RATING_DEFAULT_FMT.get(key, "decimal")
    if mode == "percentage":
        pct = num * 10.0 if scale <= 10.0 else num
        return f"{int(round(pct))}%"
    if mode == "decimal":
        dec = num / 10.0 if scale > 10.0 else num
        return f"{round(dec, 1):.1f}"
    # numeric
    if scale > 10.0 or float(num).is_integer():
        return str(int(round(num)))
    return str(_round1(num))
