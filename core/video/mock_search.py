"""Mock indexer — a stand-in for a real slskd/Prowlarr search while the download
engine is being built.

Given a scope (movie / episode / season / series), a title, and a SOURCE, it returns
plausible raw 'indexer hits' ({title, size_bytes, seeders}) — and each source returns
a DIFFERENT set (different release groups, seeder counts, and which qualities show up),
the way real indexers do. The real parse → evaluate → rank pipeline (release_parse +
quality_eval) runs on these exactly as it will on real hits. Deterministic (no RNG) so
results are stable for tests and reloads. THIS is the single swap-point: replace
``mock_search`` with a real indexer client and nothing downstream changes.

Pure (no DB, no network). Isolated — imports only typing; the music side never imports it.
"""

from __future__ import annotations

from typing import Any

_GB = 1024 ** 3

# Quality strings (WITHOUT a release group — the group is appended per source below)
# and an approximate size in GB. Ordered best→worst; the evaluator re-ranks anyway.
_MOVIE = [
    ("2160p.UHD.BluRay.REMUX.HDR.DV.TrueHD.Atmos", 58),
    ("2160p.WEB-DL.DDP5.1.HDR.HEVC", 19),
    ("1080p.BluRay.x265.10bit.DTS-HD.MA.5.1", 11),
    ("1080p.WEB-DL.DDP5.1.H264", 7),
    ("1080p.WEBRip.x264.AAC", 4),
    ("720p.HDTV.x264", 2),
    ("HDCAM.x264", 2),
]
_EPISODE = [
    ("2160p.WEB-DL.DDP5.1.HDR.HEVC", 5),
    ("1080p.BluRay.x265", 3),
    ("1080p.WEB-DL.H264", 2),
    ("720p.HDTV.x264", 1),
]
_SEASON = [
    ("2160p.WEB-DL.DDP5.1.HDR.HEVC", 48),
    ("1080p.BluRay.x265.10bit", 26),
    ("1080p.WEB-DL.H264", 18),
    ("720p.HDTV.x264", 9),
]
_SERIES = [
    ("COMPLETE.1080p.BluRay.x265.10bit", 120),
    ("COMPLETE.1080p.WEB-DL.H264", 88),
    ("COMPLETE.720p.WEB-DL.x264", 40),
]

# Per-source "flavour": which slice of the quality list shows up, a seeder multiplier,
# and a release-group pool. Makes Soulseek vs Torrent vs Usenet return distinct hits.
_SRC_FLAVOR = {
    "soulseek": {"slice": (1, None), "seed": 0.5, "groups": ["YIFY", "GalaxyRG", "RARBG"]},
    "torrent": {"slice": (0, None), "seed": 1.0, "groups": ["FraMeSToR", "FLUX", "NTb", "RARBG"]},
    "usenet": {"slice": (0, -1), "seed": 1.5, "groups": ["NTb", "FLUX", "TEPES", "playWEB"]},
    "youtube": {"slice": (2, None), "seed": 1.0, "groups": ["YT"]},
}
_DEFAULT_FLAVOR = _SRC_FLAVOR["torrent"]


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
                episode: Any = None, season_end: Any = None, source: Any = "torrent") -> list:
    """Return plausible raw hits for a scope + source. Replace with a real indexer later."""
    slug = _slug(title)
    scope = (scope or "movie").lower()
    if scope == "movie":
        prefix, base = slug + ("." + str(year) if year else ""), _MOVIE
    elif scope == "episode":
        prefix = slug + "." + _ss(season) + ("E%02d" % int(episode) if episode is not None else "E01")
        base = _EPISODE
    elif scope == "season":
        prefix, base = slug + "." + _ss(season), _SEASON
    elif scope == "series":
        prefix, base = slug + ".S01-" + _ss(season_end or 5), _SERIES
    else:
        return []

    fl = _SRC_FLAVOR.get(str(source or "").lower(), _DEFAULT_FLAVOR)
    lo, hi = fl["slice"]
    chosen = list(enumerate(base))[lo:hi]
    hits = []
    for pos, (i, (suffix, gb)) in enumerate(chosen):
        group = fl["groups"][pos % len(fl["groups"])]
        hits.append({
            "title": prefix + "." + suffix + "-" + group,
            "size_bytes": int(gb * _GB),
            "seeders": max(1, int(_seeders(slug, i) * fl["seed"])),
        })
    return hits


__all__ = ["mock_search"]
