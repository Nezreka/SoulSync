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


def _default_add_episodes(show_tmdb_id: Any, show_title: Any, episodes: List[Dict[str, Any]]) -> int:
    from api.video import get_video_db
    from core.video.sources import resolve_video_server
    return get_video_db().add_episodes_to_wishlist(
        show_tmdb_id, show_title, episodes, server_source=resolve_video_server())


def auto_video_add_airing_episodes(
    config: Dict[str, Any],
    deps: AutomationDeps,
    *,
    fetch_airing: Optional[Callable[[str], List[Dict[str, Any]]]] = None,
    add_episodes: Optional[Callable[[Any, Any, List[Dict[str, Any]]], int]] = None,
    today_fn: Optional[Callable[[], str]] = None,
) -> Dict[str, Any]:
    """Add today's airing (unowned, followed-show) episodes to the video wishlist.

    Returns ``{'status': 'completed', 'episodes_added': int, 'shows': int, ...}``."""
    fetch_airing = fetch_airing or _default_fetch_airing
    add_episodes = add_episodes or _default_add_episodes
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
        by_show: Dict[tuple, List[Dict[str, Any]]] = {}
        for r in rows:
            if r.get('has_file'):
                continue
            tid = r.get('show_tmdb_id')
            if not tid or r.get('season_number') is None or r.get('episode_number') is None:
                continue
            by_show.setdefault((tid, r.get('show_title')), []).append({
                'season_number': r.get('season_number'),
                'episode_number': r.get('episode_number'),
                'title': r.get('title'),
                'air_date': r.get('air_date'),
                # carry the rich metadata so auto-added episodes look like manual ones
                # (synopsis + still thumbnail), not blank rows
                'overview': r.get('overview'),
                'still_url': r.get('still_url'),
            })

        added = 0
        for (tid, title), eps in by_show.items():
            added += int(add_episodes(tid, title, eps) or 0)
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
