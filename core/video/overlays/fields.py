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
    "imdb": lambda v: None if _num(v) is None else "IMDb " + str(_round1(v)),
    "rt": lambda v: None if _num(v) is None else "RT " + str(v) + "%",
    "metacritic": lambda v: None if _num(v) is None else "MC " + str(v),
    "tmdb": lambda v: None if _num(v) is None else "TMDB " + str(_round1(v)),
    "content_rating": lambda v: _up(v) if v else None,
    "status": _status,
    "year": lambda v: None if _num(v) is None else str(v),
    "runtime": _runtime,
    "season_count": lambda v: None if _num(v) is None else str(v) + (" Season" if int(v) == 1 else " Seasons"),
    "episode_count": lambda v: None if _num(v) is None else str(v) + " Episodes",
    "title": lambda v: str(v) if v else None,
    "network": lambda v: str(v) if v else None,
    "studio": lambda v: str(v) if v else None,
    "genre": lambda v: str(v) if v else None,
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
