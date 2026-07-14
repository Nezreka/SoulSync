"""Automation handler: ``video_refresh_airing_schedules`` action.

Keeps the TV calendar honest. The "Wishlist Episodes Airing Today" automation reads the
LOCAL ``episodes`` table — which is only refreshed from TMDB on initial enrichment, a manual
re-match, or lazily when you open a show's page. So a newly-announced or rescheduled episode
can sit unknown indefinitely, and the airing automation misses it.

This runs daily (a couple hours before the airing run) and re-pulls each still-airing
watchlist show's season/episode schedule from TMDB (air dates, stills, overviews) so the
calendar is current when the airing automation reads it.

Scope: the EFFECTIVE watchlist's continuing LIBRARY shows (follows ∪ airing library shows,
skipping ended/canceled — they'll never air again, and skipping tmdb-only follows, which have
no episodes table to refresh). Like the other video handlers it owns its progress reporting
(``_manages_own_progress``); the show fetch + per-show refresh are injected seams, so the
handler is a pure function tests drive with fakes.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from core.automation.deps import AutomationDeps


def _default_fetch_shows() -> List[Dict[str, Any]]:
    """Production wiring: the still-airing watchlist shows that live in the library."""
    from api.video import get_video_db
    from core.video.sources import resolve_video_server
    return get_video_db().watchlist_continuing_shows(resolve_video_server())


def _default_refresh_show(library_id: Any) -> Dict[str, Any]:
    """Re-pull a library show's TMDB season/episode schedule (the lazy on-view backfill,
    invoked deliberately). Re-matches + cascades episodes, so air dates/stills refresh.
    ``with_ratings=False`` — we only need schedules, and the per-show OMDb ratings call
    would burn the daily quota across a whole watchlist."""
    from core.video.enrichment.engine import get_video_enrichment_engine
    return get_video_enrichment_engine().refresh_show_art(library_id, with_ratings=False) or {}


def auto_video_refresh_airing_schedules(
    config: Dict[str, Any],
    deps: AutomationDeps,
    *,
    fetch_shows: Optional[Callable[[], List[Dict[str, Any]]]] = None,
    refresh_show: Optional[Callable[[Any], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Refresh the TMDB episode schedule for every still-airing watchlist library show, so
    the airing automation's calendar read is current.

    Returns ``{'status': 'completed', 'refreshed': int, 'failed': int, 'shows': int, ...}``."""
    fetch_shows = fetch_shows or _default_fetch_shows
    refresh_show = refresh_show or _default_refresh_show
    automation_id = config.get('_automation_id')
    try:
        deps.update_progress(automation_id, phase='Finding shows to refresh…', progress=8,
                             log_line='Reading your watchlist for still-airing shows', log_type='info')
        shows = fetch_shows() or []
        total = len(shows)
        if not total:
            deps.update_progress(automation_id, status='finished', progress=100, phase='Complete',
                                 log_line='No airing shows on your watchlist to refresh', log_type='success')
            return {'status': 'completed', 'refreshed': 0, 'failed': 0, 'shows': 0,
                    '_manages_own_progress': True}

        refreshed = failed = 0
        for i, s in enumerate(shows):
            title = s.get('title') or ('show %s' % s.get('library_id'))
            deps.update_progress(
                automation_id, phase='Refreshing TV schedules…', progress=10 + int(i / total * 85),
                log_line="Pulling the latest episodes for '%s'  (%d/%d)" % (title, i + 1, total),
                log_type='info')
            try:
                ok = bool((refresh_show(s.get('library_id')) or {}).get('ok'))
            except Exception:   # noqa: BLE001 - one show failing must not stop the rest
                ok = False
            if ok:
                refreshed += 1
            else:
                failed += 1

        done = 'Refreshed %d show schedule(s)' % refreshed + (' · %d failed' % failed if failed else '')
        deps.update_progress(automation_id, status='finished', progress=100, phase='Complete',
                             log_line=done, log_type='success')
        return {'status': 'completed', 'refreshed': refreshed, 'failed': failed, 'shows': total,
                '_manages_own_progress': True}
    except Exception as e:  # noqa: BLE001
        deps.update_progress(automation_id, status='error', phase='Error', log_line=str(e), log_type='error')
        return {'status': 'error', 'error': str(e), '_manages_own_progress': True}
