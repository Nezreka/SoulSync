"""Automation handler: ``video_reenrich_stale`` action.

Keeps SoulSync's metadata from going stale. Enrichment normally runs ONCE — a
title is matched, its metadata gap-filled, and it's never looked at again. But
the world moves: ratings drift, overviews get written, a just-released film gets
its cast/backdrop, a reality episode finally gets an air date. Lazy on-view
refresh and the daily airing-schedule pass cover what you're actively looking
at; nothing re-pulls the LIBRARY at large.

This is that missing tier: a rolling re-enrichment. Every run it re-pulls the N
STALEST matched movies/shows (oldest-refreshed first), by their stored TMDB id —
so it re-fetches, never re-searches (zero mis-match risk). ``refresh_movie_art`` /
``refresh_show_art`` gap-fill metadata (never clobber) but OVERWRITE the dynamic
ratings, and refreshing a show cascades its episode list — so movies, shows and
episodes all stay current from the one pass.

Rolling, not big-bang: a staleness floor (``stale_days``, default 14) means a run
skips anything already fresh, and a per-run cap (``batch_size``, default 500)
keeps any single run bounded. Paired with a 6-hourly schedule the whole library
cycles through over time without ever spiking the metadata providers. Like the
other video handlers it owns its progress reporting (``_manages_own_progress``)
and every seam (fetch / refresh / sleep) is injected, so it's a pure function the
tests drive with fakes.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional

from core.automation.deps import AutomationDeps

# Default per-run cap and per-kind staleness floors — overridable from the block's config.
# Both default to ~monthly: metadata drifts slowly, so once a month per item is plenty (and
# at that cadence the steady-state OMDb call volume stays under the free daily quota).
DEFAULT_BATCH = 500
DEFAULT_MOVIE_DAYS = 30
DEFAULT_SHOW_DAYS = 30
# A small courtesy pause between items so a 500-item run doesn't machine-gun the
# metadata providers (refresh_*_art is a direct synchronous call, no worker interval).
_ITEM_PAUSE = 0.25


def _default_fetch_stale(limit: int, movie_days: int, show_days: int) -> List[Dict[str, Any]]:
    """Production wiring: the stalest matched library items (oldest refresh first)."""
    from api.video import get_video_db
    return get_video_db().stale_enriched_items(limit=limit, movie_days=movie_days, show_days=show_days)


def _default_refresh(kind: str, item_id: Any) -> Dict[str, Any]:
    """Re-pull one item's metadata BY STORED ID. Shows pull with ratings and cascade
    their episodes; movies pull cast/genres/backdrop/ratings. Gap-fill for static
    fields, overwrite for the dynamic ratings — so this never destroys owned data."""
    from core.video.enrichment.engine import get_video_enrichment_engine
    eng = get_video_enrichment_engine()
    if kind == "movie":
        return eng.refresh_movie_art(item_id) or {}
    return eng.refresh_show_art(item_id, with_ratings=True) or {}


def auto_video_reenrich_stale(
    config: Dict[str, Any],
    deps: AutomationDeps,
    *,
    fetch_stale: Optional[Callable[[int, int, int], List[Dict[str, Any]]]] = None,
    refresh_item: Optional[Callable[[str, Any], Dict[str, Any]]] = None,
    sleep: Optional[Callable[[float], None]] = None,
) -> Dict[str, Any]:
    """Re-enrich the stalest matched movies/shows so library metadata stays current.

    Config: ``batch_size`` (per-run cap, default 500), ``movie_stale_days`` (skip movies
    refreshed within this many days, default 30), ``show_stale_days`` (same for shows,
    default 30 — both roughly monthly).

    Returns ``{'status': 'completed', 'refreshed': int, 'failed': int, 'items': int, ...}``."""
    fetch_stale = fetch_stale or _default_fetch_stale
    refresh_item = refresh_item or _default_refresh
    sleep = sleep or time.sleep
    automation_id = config.get('_automation_id')

    def _int(key, default, floor):
        try:
            return max(floor, int(config.get(key, default)))
        except (TypeError, ValueError):
            return default

    batch_size = _int('batch_size', DEFAULT_BATCH, 1)
    movie_days = _int('movie_stale_days', DEFAULT_MOVIE_DAYS, 0)
    show_days = _int('show_stale_days', DEFAULT_SHOW_DAYS, 0)

    try:
        deps.update_progress(automation_id, phase='Finding stale metadata…', progress=6,
                             log_line='Looking for the library items overdue for a refresh',
                             log_type='info')
        items = fetch_stale(batch_size, movie_days, show_days) or []
        total = len(items)
        if not total:
            deps.update_progress(automation_id, status='finished', progress=100, phase='Complete',
                                 log_line='Everything is fresh — no movies past %dd or shows past %dd to refresh'
                                          % (movie_days, show_days), log_type='success')
            return {'status': 'completed', 'refreshed': 0, 'failed': 0, 'items': 0,
                    '_manages_own_progress': True}

        refreshed = failed = 0
        for i, it in enumerate(items):
            kind = it.get('kind')
            title = it.get('title') or ('%s %s' % (kind, it.get('id')))
            deps.update_progress(
                automation_id, phase='Refreshing metadata…', progress=8 + int(i / total * 88),
                log_line="Re-pulling '%s'  (%d/%d)" % (title, i + 1, total), log_type='info')
            try:
                ok = bool((refresh_item(kind, it.get('id')) or {}).get('ok'))
            except Exception:   # noqa: BLE001 - one item failing must not stop the rest
                ok = False
            if ok:
                refreshed += 1
            else:
                failed += 1
            if i < total - 1:
                sleep(_ITEM_PAUSE)

        done = 'Refreshed %d item(s)' % refreshed + (' · %d failed' % failed if failed else '')
        deps.update_progress(automation_id, status='finished', progress=100, phase='Complete',
                             log_line=done, log_type='success')
        return {'status': 'completed', 'refreshed': refreshed, 'failed': failed, 'items': total,
                '_manages_own_progress': True}
    except Exception as e:  # noqa: BLE001
        deps.update_progress(automation_id, status='error', phase='Error', log_line=str(e), log_type='error')
        return {'status': 'error', 'error': str(e), '_manages_own_progress': True}
