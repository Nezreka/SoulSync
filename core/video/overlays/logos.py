"""Logo-badge resolver: field value -> a logo file reference in a drop-in pack.

The overlay editor's logo badge binds a field (audio_codec, resolution, network,
...); at render time we map the title's VALUE for that field to a canonical file
name inside a pack folder. Packs live beside the video DB, one folder per field:

    <data>/video_poster_assets/logos/<pack>/<name>.png   (also .webp/.jpg)

We ship NO brand art — dropping in a pack (Kometa's public image set, or your
own) lights the badges up; until then the badge falls back to a text label. This
module is PURE (value -> (pack, name)); the AssetStore turns that into bytes.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple


def _slug(v) -> str:
    """Lowercase, collapse any run of non-alphanumerics to a single '_'."""
    return re.sub(r"[^a-z0-9]+", "_", str(v).lower()).strip("_")


def _resolution(v) -> Optional[str]:
    s = str(v).lower()
    if "2160" in s or s == "4k":
        return "4k"
    if "1080" in s:
        return "1080p"
    if "720" in s:
        return "720p"
    if "480" in s or "576" in s:
        return "480p"
    return _slug(v) or None


def _hdr(v) -> Optional[str]:
    s = str(v).lower()
    if "dolby" in s or s == "dv":
        return "dolby_vision"
    if "10+" in s or "10 plus" in s or "hdr10plus" in s:
        return "hdr10plus"
    return "hdr" if s else None


def _video_codec(v) -> Optional[str]:
    s = str(v).lower()
    if "hevc" in s or "265" in s:
        return "hevc"
    if "264" in s or s == "avc":
        return "h264"
    if "av1" in s:
        return "av1"
    if "vp9" in s:
        return "vp9"
    return _slug(v) or None


def _audio_codec(v) -> Optional[str]:
    s = str(v).lower()
    if "atmos" in s:
        return "atmos"
    if "truehd" in s:
        return "truehd"
    if "dts" in s and ("hd" in s or "ma" in s or "x" in s):
        return "dts_hd"
    if "dts" in s:
        return "dts"
    if "eac3" in s or "e-ac3" in s or "ddp" in s or "digital plus" in s:
        return "eac3"
    if "ac3" in s or "dolby digital" in s:
        return "ac3"
    if "aac" in s:
        return "aac"
    if "flac" in s:
        return "flac"
    return _slug(v) or None


def _source(v) -> Optional[str]:
    s = str(v).lower()
    if "remux" in s:
        return "remux"
    if "bluray" in s or "blu-ray" in s or "bdrip" in s:
        return "bluray"
    if "web" in s:
        return "web"
    if "hdtv" in s:
        return "hdtv"
    if "dvd" in s:
        return "dvd"
    return _slug(v) or None


# field -> value normalizer. The pack folder name matches the field name.
_NORMALIZERS = {
    "resolution": _resolution,
    "hdr": _hdr,
    "video_codec": _video_codec,
    "audio_codec": _audio_codec,
    "source": _source,
    # generic slug is right for these — the file name is just the sanitized value
    "content_rating": _slug,
    "status": _slug,
    "network": _slug,
    "studio": _slug,
    "streaming": _slug,
    "edition": _slug,
    # presence flag → a single icon (any truthy value maps to the same file)
    "mediastinger": lambda v: "stinger" if v else None,
}

# Which fields can be shown as a logo badge (i.e. have a pack).
LOGO_FIELDS = tuple(_NORMALIZERS.keys())


def logo_ref(field: str, value) -> Optional[Tuple[str, str]]:
    """(pack, name) for a field's value, or None when it can't map to a logo
    (unknown field, empty value). Pure — no disk access."""
    if value is None or value == "":
        return None
    norm = _NORMALIZERS.get(field)
    if norm is None:
        return None
    name = norm(value)
    return (field, name) if name else None
