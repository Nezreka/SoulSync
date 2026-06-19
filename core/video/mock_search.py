"""Mock indexer — a stand-in for a real slskd/Prowlarr search while the download
engine is being built.

Given a scope (movie / episode / season / series) and a title, it returns a list of
plausible raw 'indexer hits' ({title, size_bytes, seeders}). The real parse → evaluate
→ rank pipeline (release_parse + quality_eval) runs on these exactly as it will on real
hits, so the search UI is fully exercised. Deterministic (no RNG) so results are stable
for tests and reloads. THIS is the single swap-point: replace ``mock_search`` with a
real indexer client and nothing downstream changes.

Pure (no DB, no network). Isolated — imports only typing; the music side never imports it.
"""

from __future__ import annotations

from typing import Any

_GB = 1024 ** 3

# (quality suffix, size in GB). Ordered best→worst-ish; the evaluator re-ranks anyway.
_MOVIE = [
    ("2160p.UHD.BluRay.REMUX.HDR.DV.TrueHD.Atmos-FraMeSToR", 58),
    ("2160p.WEB-DL.DDP5.1.HDR.HEVC-FLUX", 19),
    ("1080p.BluRay.x265.10bit.DTS-HD.MA.5.1-GROUP", 11),
    ("1080p.WEB-DL.DDP5.1.H264-NTb", 7),
    ("1080p.WEBRip.x264.AAC-RARBG", 4),
    ("720p.HDTV.x264-GROUP", 2),
    ("HDCAM.x264-CRUDE", 2),
]
_EPISODE = [
    ("2160p.WEB-DL.DDP5.1.HDR.HEVC-FLUX", 5),
    ("1080p.BluRay.x265-GROUP", 3),
    ("1080p.WEB-DL.H264-NTb", 2),
    ("720p.HDTV.x264-GROUP", 1),
]
_SEASON = [
    ("2160p.WEB-DL.DDP5.1.HDR.HEVC-NTb", 48),
    ("1080p.BluRay.x265.10bit-GROUP", 26),
    ("1080p.WEB-DL.H264-FLUX", 18),
    ("720p.HDTV.x264-GROUP", 9),
]
_SERIES = [
    ("COMPLETE.1080p.BluRay.x265.10bit-GROUP", 120),
    ("COMPLETE.1080p.WEB-DL.H264-NTb", 88),
    ("COMPLETE.720p.WEB-DL.x264-GROUP", 40),
]


def _slug(title: Any) -> str:
    s = "".join(c if (c.isalnum() or c == " ") else " " for c in str(title or "").strip())
    return ".".join(p for p in s.split() if p) or "Unknown"


def _seeders(slug: str, i: int) -> int:
    # Deterministic spread (no RNG): a stable hash of the title + index.
    h = 0
    for ch in slug:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return ((h >> (i % 7)) % 240) + 3


def _ss(n) -> str:
    try:
        return "S%02d" % int(n)
    except (TypeError, ValueError):
        return "S01"


def mock_search(scope: str, title: Any, *, year: Any = None, season: Any = None,
                episode: Any = None, season_end: Any = None) -> list:
    """Return plausible raw hits for a scope. Replace with a real indexer client later."""
    slug = _slug(title)
    scope = (scope or "movie").lower()
    if scope == "movie":
        prefix = slug + ("." + str(year) if year else "")
        rows = _MOVIE
    elif scope == "episode":
        prefix = slug + "." + _ss(season) + ("E%02d" % int(episode) if episode is not None else "E01")
        rows = _EPISODE
    elif scope == "season":
        prefix = slug + "." + _ss(season)
        rows = _SEASON
    elif scope == "series":
        end = season_end or 5
        prefix = slug + ".S01-" + _ss(end)
        rows = _SERIES
    else:
        return []

    hits = []
    for i, (suffix, gb) in enumerate(rows):
        hits.append({
            "title": prefix + "." + suffix,
            "size_bytes": int(gb * _GB),
            "seeders": _seeders(slug, i),
        })
    return hits


__all__ = ["mock_search"]
