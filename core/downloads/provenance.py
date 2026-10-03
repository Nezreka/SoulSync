"""Provenance for a candidate search run: which search, when, under what policy.

One provenance per automatic worker run and one per interactive inspection.
``search_mode`` is ``'automatic'`` (the download worker) or ``'interactive'``
(a person opening Interactive Search). ``searched_at`` is UTC, ``policy_run_id``
is 12 characters. The policy facet describes the quality ladder in effect —
target index/label/count, the tier score reached, whether fallback was allowed —
built only from the existing ladder, never invented scoring.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any, Dict, Optional

SEARCH_MODES = ('automatic', 'interactive')


def new_policy_run_id() -> str:
    """A 12-character run id, one per search run."""
    return secrets.token_hex(6)


def utc_now_iso() -> str:
    """UTC timestamp for ``searched_at``: ``YYYY-MM-DD HH:MM:SS``."""
    return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')


def new_provenance(search_mode: str) -> Dict[str, str]:
    """Fresh provenance for one search run."""
    if search_mode not in SEARCH_MODES:
        raise ValueError(f"unknown search mode {search_mode!r}")
    return {
        'search_mode': search_mode,
        'searched_at': utc_now_iso(),
        'policy_run_id': new_policy_run_id(),
    }


def build_policy_facet(profile: Optional[Dict[str, Any]],
                       candidate: Any = None) -> Dict[str, Any]:
    """The ladder facet of a search run, from the existing quality ladder only.

    ``candidate`` is the chosen file (anything with an ``audio_quality``);
    ``rank_candidate`` places it on the ladder, so ``target_index``/
    ``target_label`` name the rung it reached, ``target_count`` is the ladder
    length, ``tier_score`` its AudioQuality tier score, and
    ``fallback_enabled`` the profile's fallback switch. No candidate (or no
    audio_quality on it) means it reached no rung: index == count. Nothing
    here invents scoring — it's the ladder's own ``(target_index,
    tier_score)`` pair.
    """
    from core.quality.model import QualityTarget, rank_candidate

    profile = profile or {}
    raw_targets = profile.get('ranked_targets') or []
    targets = [QualityTarget.from_dict(t) for t in raw_targets if isinstance(t, dict)]
    audio_quality = getattr(candidate, 'audio_quality', None)
    target_index: int = len(targets)
    tier_score: Optional[float] = None
    if audio_quality is not None:
        try:
            target_index, tier_score = rank_candidate(audio_quality, targets)
            tier_score = float(tier_score)
        except Exception:  # noqa: BLE001 - a facet never breaks a record
            target_index, tier_score = len(targets), None
    if 0 <= target_index < len(targets):
        target_label = str(targets[target_index].label or '')
    else:
        target_label = ''
    return {
        'target_index': target_index,
        'target_label': target_label,
        'target_count': len(targets),
        'tier_score': tier_score,
        'fallback_enabled': bool(profile.get('fallback_enabled', True)),
    }
