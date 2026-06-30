"""Per-track quality evaluation against a Library v2 quality profile.

Reuses SoulSync's existing quality model (``core/quality``): a profile is a set of
ranked targets; a file's ``AudioQuality`` either meets them or not, and — depending
on the profile's ``upgrade_policy`` — may still be an *upgrade candidate* even when
it's "acceptable":

- ``acceptable``: good enough once it matches ANY ranked target.
- ``until_top``:  keep proposing upgrades until it matches the TOP (best) target.

This is the read-side that powers the "meets profile / upgrade available" badges and
feeds the upgrade search. Never raises — unknown/unreadable quality is treated as
satisfying the profile so nothing is falsely flagged.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple


def audio_quality_from_file(file_row: Optional[Dict[str, Any]]):
    """Build an ``AudioQuality`` from a ``lib2_track_files`` row, or None."""
    if not file_row or not file_row.get("format"):
        return None
    try:
        from core.quality.model import AudioQuality
        return AudioQuality(
            format=str(file_row.get("format") or "unknown").lower(),
            bitrate=file_row.get("bitrate"),
            sample_rate=file_row.get("sample_rate"),
            bit_depth=file_row.get("bit_depth"),
        )
    except Exception:
        return None


def profile_targets(profile_row: Optional[Dict[str, Any]]) -> Tuple[List[Any], str]:
    """Return ``(targets, upgrade_policy)`` for a ``lib2_quality_profiles`` row."""
    if not profile_row:
        return [], "acceptable"
    try:
        from core.quality.selection import targets_from_profile
        raw = profile_row.get("ranked_targets")
        ranked = json.loads(raw) if isinstance(raw, str) else (raw or [])
        targets, _fallback = targets_from_profile({"ranked_targets": ranked})
        return targets, (profile_row.get("upgrade_policy") or "acceptable")
    except Exception:
        return [], "acceptable"


def evaluate_file(file_row: Optional[Dict[str, Any]], targets: List[Any],
                  upgrade_policy: str) -> Dict[str, Any]:
    """Return ``{meets_profile, upgrade_candidate}`` for one file against targets."""
    if not targets:
        return {"meets_profile": True, "upgrade_candidate": False}
    aq = audio_quality_from_file(file_row)
    if aq is None:
        return {"meets_profile": True, "upgrade_candidate": False}
    try:
        from core.quality.model import rank_candidate
        idx, _score = rank_candidate(aq, targets)
    except Exception:
        return {"meets_profile": True, "upgrade_candidate": False}
    meets = idx < len(targets)
    if upgrade_policy == "until_top":
        # Only the best (index 0) target is "done"; everything else can upgrade.
        upgrade = idx > 0
    else:  # 'acceptable'
        upgrade = not meets
    return {"meets_profile": bool(meets), "upgrade_candidate": bool(upgrade)}


__all__ = ["audio_quality_from_file", "profile_targets", "evaluate_file"]
