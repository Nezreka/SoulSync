"""Acquisition quality versus intentional retained-output transformations.

A download can satisfy a quality profile and then deliberately be transformed:
hi-res FLAC may be downsampled to 16/44.1, or a lossless source may be replaced
by a lossy library copy.  Upgrade decisions must remember the quality SoulSync
actually acquired or they will propose the same download forever.

The persisted provenance is deliberately small and source agnostic.  When it is
missing or malformed, callers fall back to the measured file quality so older
libraries never receive an unearned quality claim.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Optional

from core.quality.model import AudioQuality, QualityTarget, rank_candidate
from core.quality.selection import quality_meets_profile


ACQUIRED_QUALITY_CONTEXT_KEY = "_acquired_audio_quality"
RETENTION_CONTEXT_KEY = "_retention_transforms"


def quality_json(quality: Optional[AudioQuality]) -> Optional[str]:
    """Serialize an acquired quality, returning ``None`` when unavailable."""
    if quality is None:
        return None
    return json.dumps(quality.to_dict(), sort_keys=True, separators=(",", ":"))


def transforms_json(transforms: Any) -> Optional[str]:
    """Serialize applied transform records; empty/non-list values stay NULL."""
    if not isinstance(transforms, list) or not transforms:
        return None
    return json.dumps(transforms, sort_keys=True, separators=(",", ":"))


def acquired_quality_from_json(value: Any) -> Optional[AudioQuality]:
    """Parse trusted acquisition provenance, failing closed on old/bad data."""
    if not value:
        return None
    try:
        data = json.loads(value) if isinstance(value, str) else value
        return AudioQuality.from_dict(data)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def has_destructive_retention(value: Any) -> bool:
    """Whether provenance says the acquired representation was replaced."""
    if not value:
        return False
    try:
        steps = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    return isinstance(steps, list) and any(
        isinstance(step, dict) and bool(step.get("source_replaced"))
        for step in steps
    )


def evaluation_qualities(
    measured: Optional[AudioQuality],
    acquired_quality_json: Any = None,
    retention_json: Any = None,
) -> list[AudioQuality]:
    """Qualities that can honestly satisfy an upgrade policy.

    Measured retained quality always participates.  Acquired quality only joins
    it when explicit provenance proves a destructive, intentional transform.
    This prevents a stale/random JSON value from suppressing real upgrades.
    """
    values = [measured] if measured is not None else []
    if has_destructive_retention(retention_json):
        acquired = acquired_quality_from_json(acquired_quality_json)
        if acquired is not None and acquired.to_dict() not in [v.to_dict() for v in values]:
            values.append(acquired)
    return values


def best_quality_for_targets(
    measured: Optional[AudioQuality],
    targets: Iterable[QualityTarget],
    *,
    acquired_quality_json: Any = None,
    retention_json: Any = None,
) -> Optional[AudioQuality]:
    """Best honest quality representation for a ranked profile."""
    target_list = list(targets)
    values = evaluation_qualities(measured, acquired_quality_json, retention_json)
    if not values:
        return None
    return min(values, key=lambda quality: rank_candidate(quality, target_list))


def retention_meets_profile(
    measured: Optional[AudioQuality],
    targets: Iterable[QualityTarget],
    *,
    cutoff_index: Optional[int] = None,
    acquired_quality_json: Any = None,
    retention_json: Any = None,
) -> bool:
    """Apply an upgrade cutoff to measured + intentionally acquired quality."""
    target_list = list(targets)
    values = evaluation_qualities(measured, acquired_quality_json, retention_json)
    if not values:
        return False
    if cutoff_index is not None:
        return any(rank_candidate(value, target_list)[0] <= cutoff_index for value in values)
    return any(quality_meets_profile(value, target_list) for value in values)
