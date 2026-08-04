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
feeds the upgrade search. Never raises — unknown/unreadable quality is represented
as a third state (``None``), never as a false successful evaluation.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class UpgradeDecision:
    applicable: bool
    allowed: bool
    reason: str
    track_id: int
    profile_id: Optional[int] = None
    existing_path: Optional[str] = None
    existing_resolved_path: Optional[str] = None
    existing_quality: Any = None
    incoming_quality: Any = None


def effective_track_profile(conn, track_id: int) -> Dict[str, Any]:
    """Load one track's live Track→Album→Artist→Global profile row."""
    from core.library2.profile_lookup import effective_quality_profile

    resolved = effective_quality_profile(conn, "tracks", int(track_id))
    row = conn.execute(
        "SELECT * FROM quality_profiles WHERE id=?", (resolved["id"],)
    ).fetchone()
    if row is None:
        raise LookupError("Effective quality profile not found")
    profile = dict(row)
    profile.update({
        "source": resolved["source"],
        "source_id": resolved["source_id"],
        "explicit": resolved["explicit"],
    })
    return profile


def decide_track_upgrade(conn, track_id: int, incoming_path: str) -> UpgradeDecision:
    """Compare real old/new audio under the track's live profile and cutoff."""
    from config.settings import config_manager
    from core.imports.file_ops import probe_audio_quality
    from core.library2.paths import resolve_lib2_path
    from core.library2.track_files import primary_file_row
    from core.quality.model import rank_candidate

    track_id = int(track_id)
    profile = effective_track_profile(conn, track_id)
    existing = primary_file_row(conn, track_id)
    if not existing:
        return UpgradeDecision(
            False, True, "The previous file is no longer present", track_id,
            profile_id=profile["id"],
        )

    existing_path = str(existing.get("path") or "")
    resolved_existing = existing_path if os.path.isfile(existing_path) else resolve_lib2_path(
        existing_path, config_manager=config_manager,
    )
    old_quality = probe_audio_quality(resolved_existing) if resolved_existing else None
    new_quality = probe_audio_quality(incoming_path)
    base = {
        "track_id": track_id,
        "profile_id": profile["id"],
        "existing_path": existing_path,
        "existing_resolved_path": resolved_existing,
        "existing_quality": old_quality,
        "incoming_quality": new_quality,
    }
    if old_quality is None:
        return UpgradeDecision(True, False, "Existing file quality could not be verified", **base)
    if new_quality is None:
        return UpgradeDecision(True, False, "Downloaded file quality could not be verified", **base)

    targets, fallback_enabled, policy, cutoff = profile_upgrade_settings(profile)
    if not targets or not is_upgrade_policy(policy):
        return UpgradeDecision(True, False, "The effective profile no longer requests upgrades", **base)

    old_rank, old_score = rank_candidate(old_quality, targets)
    new_rank, new_score = rank_candidate(new_quality, targets)
    effective_cutoff = cutoff if policy == "until_cutoff" else 0
    effective_cutoff = max(0, min(int(effective_cutoff or 0), len(targets) - 1))
    if old_rank <= effective_cutoff:
        return UpgradeDecision(True, False, "The existing file already meets the upgrade cutoff", **base)

    if new_rank == len(targets) and not fallback_enabled:
        return UpgradeDecision(
            True, False, "Downloaded quality is outside the profile and fallback is disabled", **base,
        )

    strictly_better = (
        new_rank < old_rank
        or (
            new_rank == old_rank
            and new_score > old_score + 0.001
            and (
                new_rank == len(targets)
                or str(new_quality.format).lower() == str(old_quality.format).lower()
            )
        )
    )
    if not strictly_better:
        return UpgradeDecision(
            True,
            False,
            f"Downloaded quality {new_quality.label()} is not better than "
            f"{old_quality.label()}",
            **base,
        )
    return UpgradeDecision(
        True,
        True,
        f"Upgrade {old_quality.label()} → {new_quality.label()}",
        **base,
    )


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


def profile_upgrade_settings(
    profile_row: Optional[Dict[str, Any]],
) -> Tuple[List[Any], bool, str, int]:
    """Return targets, fallback, policy and cutoff from one profile contract."""
    if not profile_row:
        return [], True, "acceptable", 0
    try:
        from core.library2.feature import coerce_bool
        from core.quality.selection import targets_from_profile
        raw = profile_row.get("ranked_targets")
        ranked = json.loads(raw) if isinstance(raw, str) else (raw or [])
        targets, fallback = targets_from_profile({**profile_row, "ranked_targets": ranked})
        policy = profile_row.get("upgrade_policy") or "acceptable"
        try:
            cutoff = int(profile_row.get("upgrade_cutoff_index") or 0)
        except (TypeError, ValueError):
            cutoff = 0
        return targets, coerce_bool(fallback, True), policy, cutoff
    except Exception:
        return [], True, "acceptable", 0


def profile_targets(profile_row: Optional[Dict[str, Any]]) -> Tuple[List[Any], str, int]:
    """Return ``(targets, upgrade_policy, cutoff_index)`` for compatibility.

    ``upgrade_policy`` is ``acceptable``, ``until_cutoff`` or the persisted
    compatibility alias ``until_top``. Consumers must preserve the alias;
    :func:`evaluate_file` gives it the explicit top-target cutoff of 0.
    """
    targets, _fallback, policy, cutoff = profile_upgrade_settings(profile_row)
    return targets, policy, cutoff


def evaluate_file(file_row: Optional[Dict[str, Any]], targets: List[Any],
                  upgrade_policy: str, cutoff_index: int = 0) -> Dict[str, Any]:
    """Return tri-state ``{meets_profile, upgrade_candidate}`` for one file.

    Policy contract: ``acceptable`` stops at any matching target,
    ``until_cutoff`` stops at ``cutoff_index`` or better, and legacy
    ``until_top`` stops only at target 0.
    """
    if not targets:
        return {"meets_profile": True, "upgrade_candidate": False}
    aq = audio_quality_from_file(file_row)
    if aq is None or str(aq.format or "").strip().lower() == "unknown":
        return {"meets_profile": None, "upgrade_candidate": None}
    try:
        from core.quality.model import rank_candidate
        idx, _score = rank_candidate(aq, targets)
    except Exception:
        return {"meets_profile": None, "upgrade_candidate": None}
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


__all__ = [
    "audio_quality_from_file",
    "decide_track_upgrade",
    "effective_track_profile",
    "evaluate_file",
    "is_upgrade_policy",
    "profile_upgrade_settings",
    "profile_targets",
    "UpgradeDecision",
]
