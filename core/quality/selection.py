"""Quality-aware candidate selection shared by the search engine and the
download orchestrator.

``rank_with_targets`` is the pure core: it ranks candidates against a target
list and reports whether any candidate met a *real* target (strict, fallback
off). The engine uses that ``satisfied`` flag to decide whether the current
source is good enough or it should fall through to the next source in the
hybrid chain.

``rank_for_profile`` is the thin DB-backed wrapper that loads the user's
quality profile (with v2->v3 migration) and delegates.
"""

from __future__ import annotations

from typing import List, Tuple

from core.quality.model import (
    QualityTarget,
    filter_and_rank,
    v2_qualities_to_ranked_targets,
)


def rank_with_targets(
    candidates: list,
    targets: List[QualityTarget],
    *,
    fallback_enabled: bool = True,
) -> Tuple[list, bool]:
    """Rank *candidates* against *targets*.

    Returns ``(ranked, satisfied)`` where ``satisfied`` is True when at least
    one candidate meets a real target. When no targets are configured the
    profile imposes no constraint, so any non-empty result counts as
    satisfied (the first source wins, quality-sorted).
    """
    if not candidates:
        return [], False

    if not targets:
        ranked = filter_and_rank(candidates, targets, fallback_enabled=True)
        return ranked, bool(ranked)

    strict = filter_and_rank(candidates, targets, fallback_enabled=False)
    if strict:
        return strict, True

    if fallback_enabled:
        return filter_and_rank(candidates, targets, fallback_enabled=True), False
    return [], False


def targets_from_profile(profile: dict) -> Tuple[List[QualityTarget], bool]:
    """Convert a quality-profile dict into ``(targets, fallback_enabled)`` with
    v2->v3 migration applied. The single conversion path shared by the import
    guard, the download ranker and the library quality scanner."""
    raw_targets = profile.get('ranked_targets')
    if not raw_targets and 'qualities' in profile:
        raw_targets = v2_qualities_to_ranked_targets(profile['qualities'])

    targets = [QualityTarget.from_dict(t) for t in (raw_targets or [])]
    fallback_enabled = profile.get('fallback_enabled', True)
    return targets, fallback_enabled


def load_profile_targets() -> Tuple[List[QualityTarget], bool]:
    """Load the user's quality profile from the DB and return
    ``(targets, fallback_enabled)`` with v2->v3 migration applied.

    Callers that rank across many sources should load once and reuse via
    :func:`rank_with_targets` rather than calling :func:`rank_for_profile`
    per source.
    """
    from database.music_database import MusicDatabase

    return targets_from_profile(MusicDatabase().get_quality_profile())


def quality_meets_profile(aq, targets: List[QualityTarget]) -> bool:
    """Strict: True iff *aq* satisfies at least one ranked *target*.

    The shared definition of "good enough" for both the import guard and the
    library scanner — bit depth + sample rate are minimums (see
    :meth:`AudioQuality.matches_target`). Fallback is NOT consulted here; it's a
    download-time last-resort concession, not part of what counts as meeting the
    profile. ``targets`` empty → no constraint (True). ``aq`` None (probe
    failed) → True, so an unreadable file is never falsely flagged.
    """
    if not targets:
        return True
    if aq is None:
        return True
    from core.quality.model import rank_candidate

    idx, _ = rank_candidate(aq, targets)
    return idx < len(targets)


_VALID_SEARCH_MODES = ("priority", "best_quality")


def load_search_mode() -> str:
    """Return the download search strategy from the user's quality profile.

    ``'priority'`` (default) keeps today's behaviour — the first source in the
    hybrid chain that meets a quality target wins. ``'best_quality'`` pools
    candidates across all sources and works them best→worst by actual audio
    quality. Any missing/unknown value resolves to ``'priority'`` so existing
    installs are unaffected.
    """
    from database.music_database import MusicDatabase

    try:
        profile = MusicDatabase().get_quality_profile()
        mode = profile.get("search_mode", "priority")
    except Exception:
        return "priority"
    return mode if mode in _VALID_SEARCH_MODES else "priority"


def rank_for_profile(candidates: list) -> Tuple[list, bool]:
    """Load the user's quality profile and rank *candidates* against it.

    Returns ``(ranked, satisfied)`` — see :func:`rank_with_targets`.
    """
    targets, fallback_enabled = load_profile_targets()
    return rank_with_targets(candidates, targets, fallback_enabled=fallback_enabled)
