"""Follow-time monitor policies for shows (arr-parity P2).

Sonarr asks "what should I monitor?" when a series is added. SoulSync's follow
has always meant "future episodes" (the daily airing feeder wishes new ones as
they air); back catalog was a manual detail-page action. A policy expands the
back-catalog part AT FOLLOW TIME:

    future         — the classic follow; nothing wished now (default)
    all            — every already-AIRED episode of every real season
    first_season   — season 1's aired episodes
    latest_season  — the newest season's aired episodes
    pilot          — just S01E01 (taste-test a show)

Unaired episodes are never wished here — the airing feeder owns the future, so
the two paths can't double-add (add_episodes_to_wishlist is idempotent anyway).
Season 0 (specials) is excluded from every policy. Pure logic + an injected
engine; the API route owns the wiring.
"""

from __future__ import annotations

from typing import Any, Dict, List

from utils.logging_config import get_logger

logger = get_logger("video.monitor_policy")

POLICIES = ("future", "all", "first_season", "latest_season", "pilot")


def season_numbers_for_policy(detail: Dict[str, Any], policy: str) -> List[int]:
    """Which real seasons (>=1) a policy covers, from a tmdb_detail payload. Pure."""
    nums = sorted({int(s.get("season_number") or 0)
                   for s in (detail or {}).get("seasons") or []
                   if int(s.get("season_number") or 0) >= 1})
    if not nums:
        return []
    if policy == "all":
        return nums
    if policy in ("first_season", "pilot"):
        return [nums[0]]
    if policy == "latest_season":
        return [nums[-1]]
    return []


def episodes_for_policy(engine: Any, tmdb_id: int, policy: str, today: str) -> List[Dict[str, Any]]:
    """The already-aired episodes a follow policy should wish right now —
    [{season_number, episode_number, title, air_date}, ...]. 'future'/unknown
    → []. Engine/TMDB failures degrade to [] (the follow itself must never
    fail because the back-catalog lookup hiccupped)."""
    policy = str(policy or "future").lower()
    if policy not in POLICIES or policy == "future":
        return []
    try:
        detail = engine.tmdb_detail("show", tmdb_id) or {}
    except Exception:   # noqa: BLE001
        logger.warning("monitor policy %r: show detail lookup failed for %s", policy, tmdb_id)
        return []
    out: List[Dict[str, Any]] = []
    for sn in season_numbers_for_policy(detail, policy):
        try:
            season = engine.tmdb_season(tmdb_id, sn) or {}
        except Exception:   # noqa: BLE001
            logger.warning("monitor policy %r: season %s lookup failed for %s", policy, sn, tmdb_id)
            continue
        for ep in season.get("episodes") or []:
            ad = str(ep.get("air_date") or "")[:10]
            if not ad or ad > today:
                continue                      # unaired — the airing feeder owns the future
            out.append({"season_number": sn,
                        "episode_number": ep.get("episode_number"),
                        "title": ep.get("title"), "air_date": ad})
            if policy == "pilot":
                return out[:1]
    return out
