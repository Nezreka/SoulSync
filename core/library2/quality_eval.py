"""Per-track quality evaluation against an app-wide quality profile.

Reuses SoulSync's existing quality model (``core/quality``): a profile is a set of
ranked targets; a file's ``AudioQuality`` either meets them or not, and — depending
on the profile's ``upgrade_policy`` — may still be an *upgrade candidate* even when
it's "acceptable":

- ``acceptable``:   good enough once it matches ANY ranked target.
- ``until_cutoff``: keep proposing upgrades until the target at
  ``upgrade_cutoff_index`` (or better) is reached — Lidarr's quality cutoff.
- ``until_top``:    legacy alias for ``until_cutoff`` with cutoff 0.

Profiles are rows of the app-wide ``quality_profiles`` table (the same rows the
wishlist/download pipeline resolves via ``core/quality/selection``), so the
badges here and the pipeline's accept/upgrade decisions can't drift apart.

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


def is_upgrade_policy(policy: Optional[str]) -> bool:
    """Whether a profile keeps searching after an acceptable file exists.

    Both persisted upgrade modes are valid: ``until_cutoff`` uses the
    configured ``upgrade_cutoff_index``; legacy ``until_top`` means the same
    thing with an implicit cutoff index of 0. ``acceptable`` never upgrades a
    file that already matches any ranked target.
    """
    return (policy or "") in ("until_top", "until_cutoff")


def profile_targets(profile_row: Optional[Dict[str, Any]]) -> Tuple[List[Any], str, int]:
    """Return ``(targets, upgrade_policy, cutoff_index)`` for a profile row.

    ``upgrade_policy`` is ``acceptable``, ``until_cutoff`` or the persisted
    compatibility alias ``until_top``. Consumers must preserve the alias;
    :func:`evaluate_file` gives it the explicit top-target cutoff of 0.
    """
    if not profile_row:
        return [], "acceptable", 0
    try:
        from core.quality.selection import targets_from_profile
        raw = profile_row.get("ranked_targets")
        ranked = json.loads(raw) if isinstance(raw, str) else (raw or [])
        targets, _fallback = targets_from_profile({"ranked_targets": ranked})
        policy = profile_row.get("upgrade_policy") or "acceptable"
        try:
            cutoff = int(profile_row.get("upgrade_cutoff_index") or 0)
        except (TypeError, ValueError):
            cutoff = 0
        return targets, policy, cutoff
    except Exception:
        return [], "acceptable", 0


def evaluate_file(file_row: Optional[Dict[str, Any]], targets: List[Any],
                  upgrade_policy: str, cutoff_index: int = 0) -> Dict[str, Any]:
    """Return ``{meets_profile, upgrade_candidate}`` for one file.

    Policy contract: ``acceptable`` stops at any matching target,
    ``until_cutoff`` stops at ``cutoff_index`` or better, and legacy
    ``until_top`` stops only at target 0.
    """
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
    if is_upgrade_policy(upgrade_policy):
        # Done once the cutoff target (or better) is reached; 'until_top' is
        # the legacy alias for cutoff 0.
        cutoff = cutoff_index if upgrade_policy == "until_cutoff" else 0
        cutoff = max(0, min(int(cutoff or 0), len(targets) - 1))
        upgrade = idx > cutoff
    else:  # 'acceptable'
        upgrade = not meets
    return {"meets_profile": bool(meets), "upgrade_candidate": bool(upgrade)}


__all__ = ["audio_quality_from_file", "profile_targets", "evaluate_file", "is_upgrade_policy"]
