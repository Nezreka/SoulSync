"""Automation handler: ``video_add_airing_episodes`` action.

Sonarr-style "monitor airings": add every episode airing TODAY — for the TV shows you
follow on the video watchlist — to the video WISHLIST, skipping ones you already own.
Runs on a daily schedule so the day's airings queue up to be grabbed automatically.

Like the other video handlers it lives on the SHARED automation side (so it may import
``core.video`` / ``api.video`` — the isolation contract only forbids the reverse) and
owns its own progress reporting (``_manages_own_progress``). The calendar read + wishlist
write are injected seams, so the handler is a pure function: tests pass fakes and never
touch a DB or a media server; production lazily binds the real calls.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Callable, Dict, List, Optional

from core.automation.deps import AutomationDeps


def _default_fetch_airing(today: str) -> List[Dict[str, Any]]:
    """Production wiring: the calendar's episodes airing on ``today`` for followed shows."""
    from api.video import get_video_db
    from core.video.sources import resolve_video_server
    return get_video_db().calendar_upcoming(
        today, today, server_source=resolve_video_server(), watchlist_only=True)


def _default_add_episodes(show_tmdb_id: Any, show_title: Any, episodes: List[Dict[str, Any]],
                          library_id: Any = None, poster_url: Any = None) -> int:
    from api.video import get_video_db
    from core.video.sources import resolve_video_server
    return get_video_db().add_episodes_to_wishlist(
        show_tmdb_id, show_title, episodes, poster_url=poster_url, library_id=library_id,
        server_source=resolve_video_server())


def _show_poster_url(library_id: Any) -> Optional[str]:
    """The SAME poster a manual add stores for a library show — the show poster proxy
    path the wishlist orb renders directly. Mirrors the get-modal's
    pUrl = '/api/video/poster/show/<library_id>'. Without it the orb falls back to the
    show's initials, reading as 'not matched'."""
    return ('/api/video/poster/show/%s' % library_id) if library_id is not None else None


def _default_season_meta(tmdb_id: Any, season_number: Any):
    """The SAME TMDB season fetch the show modal uses for a manual add — so auto-added
    episodes carry identical stills / overviews / season posters, not the patchy values
    the local DB happens to hold."""
    from core.video.enrichment.engine import get_video_enrichment_engine
    return get_video_enrichment_engine().tmdb_season(tmdb_id, season_number)


def _season_lookup(season_meta, tmdb_id, season_number, cache):
    """(season_poster_url, {episode_number: tmdb_episode}) for a show+season, fetched
    once and cached. A TMDB hiccup degrades to empty (DB values fill in)."""
    key = (tmdb_id, season_number)
    if key not in cache:
        try:
            sm = season_meta(tmdb_id, season_number) or {}
        except Exception:   # noqa: BLE001 - never let a metadata fetch break the run
            sm = {}
        emap = {e.get('episode_number'): e for e in (sm.get('episodes') or []) if isinstance(e, dict)}
        cache[key] = (sm.get('poster_url'), emap)
    return cache[key]


def auto_video_add_airing_episodes(
    config: Dict[str, Any],
    deps: AutomationDeps,
    *,
    fetch_airing: Optional[Callable[[str], List[Dict[str, Any]]]] = None,
    add_episodes: Optional[Callable[[Any, Any, List[Dict[str, Any]]], int]] = None,
    today_fn: Optional[Callable[[], str]] = None,
    season_meta: Optional[Callable[[Any, Any], Any]] = None,
) -> Dict[str, Any]:
    """Add today's airing (unowned, followed-show) episodes to the video wishlist.

    Returns ``{'status': 'completed', 'episodes_added': int, 'shows': int, ...}``."""
    fetch_airing = fetch_airing or _default_fetch_airing
    add_episodes = add_episodes or _default_add_episodes
    season_meta = season_meta or _default_season_meta
    today_fn = today_fn or (lambda: date.today().isoformat())
    automation_id = config.get('_automation_id')
    try:
        today = today_fn()
        deps.update_progress(automation_id, phase="Checking today's airings…", progress=25,
                             log_line='Reading the calendar for episodes airing today', log_type='info')
        rows = fetch_airing(today) or []

        # Group what to wish for by show: airing today, NOT already owned, with a
        # real season/episode. add_episodes_to_wishlist is idempotent, so re-runs
        # never duplicate.
        by_show: Dict[tuple, Dict[str, Any]] = {}
        season_cache: Dict[tuple, tuple] = {}
        for r in rows:
            if r.get('has_file'):
                continue
            tid = r.get('show_tmdb_id')
            sn, en = r.get('season_number'), r.get('episode_number')
            if not tid or sn is None or en is None:
                continue
            # Pull the SAME TMDB metadata a manual add gets (still + overview + season
            # poster); fall back to the calendar/DB values if TMDB is unavailable.
            poster, emap = _season_lookup(season_meta, tid, sn, season_cache)
            tm = emap.get(en) or {}
            # library_id (the show's library row id, given as show_id) is REQUIRED — the
            # wishlist resolves a show's synopsis + cast from /detail/show/<library_id>;
            # without it the show shows as un-matched with no synopsis/actors.
            grp = by_show.setdefault((tid, r.get('show_title')),
                                     {'library_id': r.get('show_id'), 'eps': []})
            grp['eps'].append({
                'season_number': sn,
                'episode_number': en,
                'title': r.get('title') or tm.get('title'),
                'air_date': r.get('air_date') or tm.get('air_date'),
                'overview': tm.get('overview') or r.get('overview'),
                'still_url': tm.get('still_url') or r.get('still_url'),
                'season_poster_url': poster,
            })

        added = 0
        for (tid, title), grp in by_show.items():
            added += int(add_episodes(tid, title, grp['eps'], grp['library_id'],
                                      _show_poster_url(grp['library_id'])) or 0)
        shows = len(by_show)

        deps.update_progress(
            automation_id, status='finished', progress=100, phase='Complete',
            log_line=('Added %d airing episode(s) across %d show(s) to the wishlist'
                      % (added, shows)) if added else 'No new airing episodes to wishlist today',
            log_type='success')
        return {'status': 'completed', 'episodes_added': added, 'shows': shows,
                '_manages_own_progress': True}
    except Exception as e:  # noqa: BLE001
        deps.update_progress(automation_id, status='error', phase='Error', log_line=str(e), log_type='error')
        return {'status': 'error', 'error': str(e), '_manages_own_progress': True}
