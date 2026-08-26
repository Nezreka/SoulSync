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


def _season_numbers(detail: Dict[str, Any]) -> List[int]:
    """Every season number on a tmdb_detail payload, specials included."""
    out = set()
    for s in (detail or {}).get("seasons") or []:
        try:
            out.add(int(s.get("season_number")))
        except (TypeError, ValueError):
            continue
    return sorted(out)


def latest_season_numbers(detail: Dict[str, Any], keep: int = 2) -> List[int]:
    """The newest ``keep`` real seasons (>=1). A show that is airing right now is
    airing its newest season, so there is no point pulling the whole history.

    A specials-only show (season 0 and nothing else) falls back to what it has,
    matching the engine's _latest_seasons. Returning [] there would mean shows
    like Critical Role, whose episodes are all season 0, never get an airing
    looked up at all. Pure."""
    nums = _season_numbers(detail)
    regular = [n for n in nums if n >= 1]
    if not regular:
        return nums
    return regular[-max(1, int(keep)):]


def episodes_airing_between(engine: Any, tmdb_id: int, start: str, end: str,
                            seasons_to_check: int = 2) -> Dict[str, Any]:
    """A show's episodes airing in [start, end], straight from TMDB, plus the
    show poster to file them under: ``{"poster_url": str|None, "episodes": []}``.

    This is what the airing feeder uses for a show you follow but do NOT own.
    The calendar is built off the episodes table, which only holds LIBRARY
    shows, so a tmdb-only follow produced nothing at all: you followed a show,
    it aired, and SoulSync sat there. Sonarr monitors a series whether or not
    you have files yet.

    The poster matters: an unowned show has no /api/video/poster/show/<id>
    proxy, and a wishlist row with a null poster_url renders as an initials orb
    that reads as "not matched". TMDB's own poster is what fills that gap.

    Bounded to the newest couple of seasons, so at most one detail call plus
    two season calls per show, all engine-cached. Failures degrade to empty
    because a metadata hiccup must never stop the rest of the run.
    """
    empty: Dict[str, Any] = {"poster_url": None, "episodes": []}
    try:
        detail = engine.tmdb_detail("show", tmdb_id) or {}
    except Exception:   # noqa: BLE001
        logger.warning("airing lookup: show detail failed for %s", tmdb_id)
        return empty
    out: List[Dict[str, Any]] = []
    for sn in latest_season_numbers(detail, seasons_to_check):
        try:
            season = engine.tmdb_season(tmdb_id, sn) or {}
        except Exception:   # noqa: BLE001
            logger.warning("airing lookup: season %s failed for %s", sn, tmdb_id)
            continue
        for ep in season.get("episodes") or []:
            ad = str(ep.get("air_date") or "")[:10]
            if not ad or ad < start or ad > end:
                continue
            if ep.get("episode_number") is None:
                continue
            out.append({"season_number": sn,
                        "episode_number": ep.get("episode_number"),
                        "title": ep.get("title"), "air_date": ad,
                        "overview": ep.get("overview"),
                        "still_url": ep.get("still_url"),
                        "season_poster_url": season.get("poster_url")})
    return {"poster_url": detail.get("poster_url"), "episodes": out}
