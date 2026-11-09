"""One ranking for every video release pick (#2033).

The interactive search on a title, the wishlist 'Search now', the hourly
drain, RSS sync, upgrades and the download monitor's retry all judge a hit
with ``quality_eval.evaluate_release`` and rank it here, so they can't drift.

The order is Radarr/Sonarr's: among releases that meet the profile,
  1. quality: the ladder tier (in the profile's own order) plus the
     profile's preferences (codec, HDR, audio, repack, audio language)
  2. the custom format score
  3. small tie-breaks: lossless audio, then closeness to the preferred size
  4. availability (seeders / free slots), then the bigger file
Each level only matters when everything above it ties. It used to be one
flat sum, so a +100 MeGusta format lost to a release that only spelled out
ENGLISH or carried Atmos.

Every ranked release carries ``breakdown`` (each factor and its points) and
``rank`` (the compared values, best first) so the UI can say why.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

GROUPS = ("quality", "formats", "tiebreak")


def apply_formats(verdict: Dict[str, Any], name: Any, formats: Optional[List[Dict[str, Any]]],
                  profile: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Add the custom formats a release name matches to an evaluate_release
    verdict: their points, one breakdown line each, and the profile's
    minimum format score as a hard reject. Returns a new verdict."""
    from core.video.custom_formats import format_matches
    hits = format_matches(name, formats or [], profile) if formats else []
    fscore = sum(sc for _, sc in hits)
    parts = dict(verdict.get("parts") or {})
    parts["formats"] = fscore
    breakdown = list(verdict.get("breakdown") or [])
    breakdown += [{"group": "formats", "label": "Custom format: %s" % n, "points": sc}
                  for n, sc in hits]
    out = {**verdict, "parts": parts, "breakdown": breakdown,
           "score": int(verdict.get("score") or 0) + fscore,
           "format_score": fscore, "formats": [n for n, _ in hits]}
    floor = (profile or {}).get("min_format_score") or 0
    if formats and floor and out.get("accepted") and fscore < floor:
        out["accepted"] = False
        out["rejected"] = "Format score %d is below your minimum %d" % (fscore, floor)
    return out


def rank_values(verdict: Dict[str, Any], avail: Any = 0, size_bytes: Any = 0) -> List[Any]:
    """The values a release is compared on, most important first."""
    parts = verdict.get("parts") or {}

    def _n(v):
        try:
            return int(v or 0)
        except (TypeError, ValueError):
            return 0
    return [1 if verdict.get("accepted") else 0, _n(parts.get("quality")),
            _n(parts.get("formats")), _n(parts.get("tiebreak")), _n(parts.get("size_fit")),
            _n(avail), _n(size_bytes)]


def rank_key(row: Dict[str, Any]) -> tuple:
    """Sort key for a ranked release (higher is better). A row from before
    the ranking carried ``rank`` falls back to the old flat order."""
    r = row.get("rank") if isinstance(row, dict) else None
    if isinstance(r, (list, tuple)) and r:
        return tuple(r)
    row = row if isinstance(row, dict) else {}
    return (1 if row.get("accepted") else 0, int(row.get("score") or 0), 0, 0, 0,
            int(row.get("_avail") or 0), int(row.get("size_bytes") or 0))


def sort_ranked(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Best first. Stable, so equal releases keep their source order."""
    return sorted(rows or [], key=rank_key, reverse=True)


def judge(parsed: Dict[str, Any], name: Any, profile: Dict[str, Any],
          formats: Optional[List[Dict[str, Any]]], *, avail: Any = 0, size_bytes: Any = 0,
          **evaluate_kwargs: Any) -> Dict[str, Any]:
    """evaluate_release + custom formats + rank, the whole judgement of one hit."""
    from core.video.quality_eval import evaluate_release
    verdict = evaluate_release(parsed, profile, **evaluate_kwargs)
    verdict = apply_formats(verdict, name, formats, profile)
    verdict["rank"] = rank_values(verdict, avail, size_bytes)
    return verdict


__all__ = ["GROUPS", "apply_formats", "rank_values", "rank_key", "sort_ranked", "judge"]
