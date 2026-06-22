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


# ── watchlist hygiene: drop shows that have ended/been canceled ───────────────
_TERMINAL = ('ended', 'canceled', 'cancelled', 'completed')


def _is_terminal_status(status) -> bool:
    """A show that won't air again — pointless to keep on the (watch-for-new) list."""
    return str(status or '').strip().lower() in _TERMINAL


def _default_fetch_follows() -> List[Dict[str, Any]]:
    from api.video import get_video_db
    return get_video_db().followed_shows()


def _default_show_status(tmdb_id: Any) -> Optional[str]:
    """TMDB status for a follow with no local status (a tmdb-only follow). Cached by
    the engine; returns None on any hiccup so we never prune on uncertainty."""
    from core.video.enrichment.engine import get_video_enrichment_engine
    d = get_video_enrichment_engine().tmdb_full_detail('show', tmdb_id) or {}
    return d.get('status')


def _default_remove_show(tmdb_id: Any) -> None:
    from api.video import get_video_db
    get_video_db().remove_from_watchlist('show', tmdb_id)


def prune_ended_show_follows(deps, automation_id=None, *, fetch_follows=None,
                             show_status=None, remove_show=None) -> int:
    """Remove explicitly-followed shows that are no longer airing.

    Auto-airing LIBRARY shows are already excluded by status, so this targets explicit
    eye-button follows (which persist regardless). For follows with no local status (a
    tmdb-only follow), look the status up on TMDB. Only prunes on a DEFINITIVE terminal
    status — unknown status is left alone. Pure: all I/O injected. Returns count removed."""
    fetch_follows = fetch_follows or _default_fetch_follows
    show_status = show_status or _default_show_status
    remove_show = remove_show or _default_remove_show
    removed = 0
    for f in (fetch_follows() or []):
        tid = f.get('tmdb_id')
        if not tid:
            continue
        status = f.get('status')
        if not status:                       # tmdb-only follow — ask TMDB
            try:
                status = show_status(tid)
            except Exception:   # noqa: BLE001 - never prune on a lookup failure
                status = None
        if status and _is_terminal_status(status):
            try:
                remove_show(tid)
                removed += 1
                deps.update_progress(
                    automation_id, log_line="Removed ended show '%s' from the watchlist"
                    % (f.get('title') or tid), log_type='info')
            except Exception:   # noqa: BLE001, S110 - a progress-log failure must not abort pruning
                pass
    return removed


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
    prune_follows: Optional[Callable[[], List[Dict[str, Any]]]] = None,
    show_status: Optional[Callable[[Any], Any]] = None,
    remove_show: Optional[Callable[[Any], None]] = None,
) -> Dict[str, Any]:
    """Add today's airing (unowned, followed-show) episodes to the video wishlist, and
    first tidy the watchlist by dropping shows that have ended / been canceled.

    Returns ``{'status': 'completed', 'episodes_added': int, 'shows': int,
    'shows_pruned': int, ...}``."""
    fetch_airing = fetch_airing or _default_fetch_airing
    add_episodes = add_episodes or _default_add_episodes
    season_meta = season_meta or _default_season_meta
    today_fn = today_fn or (lambda: date.today().isoformat())
    automation_id = config.get('_automation_id')
    prune_ended = config.get('prune_ended', True)
    try:
        today = today_fn()
        # Watchlist hygiene first: a followed show that has since ended/been canceled
        # won't air again, so drop it (ended LIBRARY shows are already auto-excluded;
        # this catches explicit eye-button follows).
        pruned = 0
        if prune_ended:
            deps.update_progress(automation_id, phase='Tidying the watchlist…', progress=10,
                                 log_line='Removing shows that have ended or been canceled', log_type='info')
            pruned = prune_ended_show_follows(deps, automation_id, fetch_follows=prune_follows,
                                              show_status=show_status, remove_show=remove_show)
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

        done = ('Added %d airing episode(s) across %d show(s) to the wishlist'
                % (added, shows)) if added else 'No new airing episodes to wishlist today'
        if pruned:
            done += ' · pruned %d ended show(s)' % pruned
        deps.update_progress(
            automation_id, status='finished', progress=100, phase='Complete',
            log_line=done, log_type='success')
        return {'status': 'completed', 'episodes_added': added, 'shows': shows,
                'shows_pruned': pruned, '_manages_own_progress': True}
    except Exception as e:  # noqa: BLE001
        deps.update_progress(automation_id, status='error', phase='Error', log_line=str(e), log_type='error')
        return {'status': 'error', 'error': str(e), '_manages_own_progress': True}
