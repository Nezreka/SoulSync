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

from datetime import date, timedelta
from typing import Any, Callable, Dict, List, Optional

from core.automation.deps import AutomationDeps

from core.automation.handlers.video_run_guard import VideoRunGuard

# Overlap guard: the engine skips a run while this is True (busy).
_RUN_GUARD = VideoRunGuard(timeout_seconds=3600)


def is_airing_already_running() -> bool:
    """Guard for the engine: True if a auto_video_add_airing_episodes run is in progress."""
    return _RUN_GUARD()


# Self-healing catch-up (Boulder's Sunday gap): a daily_time trigger that fires
# while the box is asleep is discarded, not made up — so with a today-only window
# every slept-through day was permanently skipped. Each run now covers
# (last-covered-bookmark + 1 .. today), capped at this many days including today,
# so any gap ≤ a week heals on the next nightly run. Delivery is at-least-once:
# the bookmark normally advances once per successful run, but if the bookmark
# write itself fails the next run re-covers the window (the wishlist upsert is
# idempotent, so re-offers never duplicate — though a deliberately-deleted episode
# can boomerang back in that rare case).
CATCHUP_MAX_DAYS = 7
_BOOKMARK_KEY = 'airing_last_covered'


def _profile() -> int:
    """The automation owner's profile: the watchlist/wishlist are per-profile,
    so the airing automation reads and writes its owner's lists (the engine
    sets the background profile to the owner before the run)."""
    try:
        from core.profile_context import get_current_profile_id
        return int(get_current_profile_id() or 1)
    except Exception:
        return 1


def _default_fetch_airing(start: str, end: str) -> List[Dict[str, Any]]:
    """Production wiring: the calendar's episodes airing in [start, end] for followed shows."""
    from api.video import get_video_db
    from core.video.sources import resolve_video_server
    return get_video_db().calendar_upcoming(
        start, end, server_source=resolve_video_server(), watchlist_only=True,
        profile_id=_profile())


def _default_get_bookmark() -> Optional[str]:
    from api.video import get_video_db
    return get_video_db().get_setting(_BOOKMARK_KEY)


def _default_set_bookmark(day: str) -> None:
    from api.video import get_video_db
    get_video_db().set_setting(_BOOKMARK_KEY, day)


def compute_catchup_window(today: str, bookmark: Optional[str],
                           max_days: int = CATCHUP_MAX_DAYS) -> str:
    """The window START for this run: the day after the bookmark, clamped to
    [today - (max_days-1), today]. No/invalid bookmark → today (the pre-catch-up
    behavior — a fresh install never backfills history). Pure."""
    if not bookmark:
        return today
    try:
        bm_d = date.fromisoformat(str(bookmark))
        t_d = date.fromisoformat(str(today))
    except ValueError:
        return today
    start_d = bm_d + timedelta(days=1)
    floor_d = t_d - timedelta(days=max(1, int(max_days)) - 1)
    if start_d < floor_d:
        start_d = floor_d
    if start_d > t_d:
        start_d = t_d
    return start_d.isoformat()


def _default_add_episodes(show_tmdb_id: Any, show_title: Any, episodes: List[Dict[str, Any]],
                          library_id: Any = None, poster_url: Any = None) -> int:
    from api.video import get_video_db
    from core.video.sources import resolve_video_server
    return get_video_db().add_episodes_to_wishlist(
        show_tmdb_id, show_title, episodes, poster_url=poster_url, library_id=library_id,
        server_source=resolve_video_server(), profile_id=_profile())


def _show_poster_url(library_id: Any) -> Optional[str]:
    """The SAME poster a manual add stores for a library show — the show poster proxy
    path the wishlist orb renders directly. Mirrors the get-modal's
    pUrl = '/api/video/poster/show/<library_id>'. Without it the orb falls back to the
    show's initials, reading as 'not matched'."""
    return ('/api/video/poster/show/%s' % library_id) if library_id is not None else None


def _default_unowned_follows() -> List[Dict[str, Any]]:
    """Followed shows with no library row ON THE ACTIVE SERVER. The library_id
    resolution is scoped to the same server as the calendar pass — otherwise a
    show owned only on the non-default server resolves a library_id here, fails
    the None test, and is skipped by BOTH passes (never wishlisted, silently)."""
    from api.video import get_video_db
    from core.video.sources import resolve_video_server
    return [r for r in (get_video_db().followed_shows(
        profile_id=_profile(), server_source=resolve_video_server()) or [])
        if r.get('library_id') is None and r.get('tmdb_id')]


def _default_tmdb_airings(tmdb_id: Any, start: str, end: str) -> Dict[str, Any]:
    """{"poster_url", "episodes"} for an unowned followed show."""
    from core.video.enrichment.engine import get_video_enrichment_engine
    from core.video.monitor_policy import episodes_airing_between
    return episodes_airing_between(get_video_enrichment_engine(), tmdb_id, start, end)


def _default_season_meta(tmdb_id: Any, season_number: Any):
    """The SAME TMDB season fetch the show modal uses for a manual add — so auto-added
    episodes carry identical stills / overviews / season posters, not the patchy values
    the local DB happens to hold."""
    from core.video.enrichment.engine import get_video_enrichment_engine
    return get_video_enrichment_engine().tmdb_season(tmdb_id, season_number)


# ── watchlist hygiene: drop shows that have ended/been canceled ───────────────
_TERMINAL = ('ended', 'canceled', 'cancelled')
# NOTE: 'completed' was removed — nothing ever writes it into shows.status.
# TMDB's raw statuses are Returning Series/Planned/In Production/Ended/Canceled/Pilot.


def _today_in_tz(tz_name: str | None) -> str:
    """Today's date in the trigger's timezone.

    Timed triggers fire in the engine's _default_tz (which honors the
    automation.default_timezone override), but date.today() is server-local.
    When they differ, the airing window shifts by a day. Deriving "today" in
    the trigger's tz keeps them aligned.
    """
    from datetime import datetime, timezone
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(tz_name) if tz_name else timezone.utc
    except Exception:   # noqa: BLE001 - bad tz name falls back to server-local
        return date.today().isoformat()
    return datetime.now(tz).date().isoformat()


def _is_terminal_status(status) -> bool:
    """A show that won't air again — pointless to keep on the (watch-for-new) list."""
    return str(status or '').strip().lower() in _TERMINAL


def _default_fetch_follows() -> List[Dict[str, Any]]:
    from api.video import get_video_db
    return get_video_db().followed_shows(profile_id=_profile())


def _default_show_status(tmdb_id: Any) -> Optional[str]:
    """TMDB status for a follow with no local status (a tmdb-only follow). Cached by
    the engine; returns None on any hiccup so we never prune on uncertainty."""
    from core.video.enrichment.engine import get_video_enrichment_engine
    d = get_video_enrichment_engine().tmdb_full_detail('show', tmdb_id) or {}
    return d.get('status')


def _default_remove_show(tmdb_id: Any) -> None:
    from api.video import get_video_db
    get_video_db().remove_from_watchlist('show', tmdb_id, profile_id=_profile())


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


def auto_video_add_airing_episodes_all_profiles(config: Dict[str, Any], deps: AutomationDeps,
                                                **kwargs) -> Dict[str, Any]:
    """the airing run for the owner, then for every other profile that follows a show.

    the watchlist is per-profile and the system automation is admin's, so
    without this a non-admin's followed shows never got their new episodes.
    an extra pass only wishes episodes of shows that profile explicitly
    follows: the calendar also counts every airing library show as watched by
    default, and copying those into every profile's wishlist is noise.

    the catch-up bookmark is shared, so it is read once up front and written
    once after every pass worked. otherwise the first pass would advance it
    and the next profile would lose the days it was meant to catch up.
    """
    from core.automation.handlers.video_profile_fanout import run_per_profile

    real_get = kwargs.pop('get_bookmark', None) or _default_get_bookmark
    real_set = kwargs.pop('set_bookmark', None) or _default_set_bookmark
    fetch_airing = kwargs.pop('fetch_airing', None) or _default_fetch_airing
    fetch_follows = kwargs.pop('fetch_follows', None) or _default_followed_shows
    follower_profiles = kwargs.pop('follower_profiles', None)
    try:
        bookmark, bookmark_err = real_get(), None
    except Exception as e:   # noqa: BLE001 - each pass degrades to today-only, no write
        bookmark, bookmark_err = None, e

    def get_bookmark():
        if bookmark_err is not None:
            raise bookmark_err
        return bookmark

    wrote: List[str] = []

    def run_pass(pass_deps, pid, is_owner):
        extra = dict(kwargs)
        if not is_owner:
            follows = {str(f.get('tmdb_id')) for f in (fetch_follows(pid) or [])}
            extra['fetch_airing'] = lambda start, end: [
                r for r in (fetch_airing(start, end) or [])
                if str(r.get('show_tmdb_id')) in follows]
        else:
            extra['fetch_airing'] = fetch_airing
        return auto_video_add_airing_episodes(
            config, pass_deps, get_bookmark=get_bookmark, set_bookmark=wrote.append, **extra)

    result = run_per_profile(run_pass, deps, ['show'], follower_profiles=follower_profiles)
    if wrote and result.get('status') != 'error':
        try:
            real_set(wrote[-1])
        except Exception as e:   # noqa: BLE001 - same rule as the single run: re-cover next time
            result = dict(result, status='error',
                          error='Could not persist the catch-up bookmark: %s' % e)
    return result


def _default_followed_shows(profile_id: int) -> List[Dict[str, Any]]:
    from api.video import get_video_db
    return get_video_db().followed_shows(profile_id=profile_id)


def auto_video_add_airing_episodes(
    config: Dict[str, Any],
    deps: AutomationDeps,
    *,
    fetch_airing: Optional[Callable[[str, str], List[Dict[str, Any]]]] = None,
    add_episodes: Optional[Callable[[Any, Any, List[Dict[str, Any]]], int]] = None,
    today_fn: Optional[Callable[[], str]] = None,
    season_meta: Optional[Callable[[Any, Any], Any]] = None,
    prune_follows: Optional[Callable[[], List[Dict[str, Any]]]] = None,
    show_status: Optional[Callable[[Any], Any]] = None,
    remove_show: Optional[Callable[[Any], None]] = None,
    get_bookmark: Optional[Callable[[], Optional[str]]] = None,
    set_bookmark: Optional[Callable[[str], None]] = None,
    unowned_follows: Optional[Callable[[], List[Dict[str, Any]]]] = None,
    tmdb_airings: Optional[Callable[[Any, str, str], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Add today's airing (unowned, followed-show) episodes to the video wishlist, and
    first tidy the watchlist by dropping shows that have ended / been canceled.

    Returns ``{'status': 'completed', 'episodes_added': int, 'shows': int,
    'shows_pruned': int, ...}``."""
    with _RUN_GUARD:
        fetch_airing = fetch_airing or _default_fetch_airing
        add_episodes = add_episodes or _default_add_episodes
        season_meta = season_meta or _default_season_meta
        today_fn = today_fn or (lambda: _today_in_tz(config.get('_trigger_tz')))
        get_bookmark = get_bookmark or _default_get_bookmark
        set_bookmark = set_bookmark or _default_set_bookmark
        unowned_follows = unowned_follows or _default_unowned_follows
        tmdb_airings = tmdb_airings or _default_tmdb_airings
        automation_id = config.get('_automation_id')
        prune_ended = config.get('prune_ended', True)
        try:
            today = today_fn()
            # Catch-up window: heal any days a slept-through 01:00 run skipped.
            # A bookmark READ failure degrades to today-only — but the bookmark must
            # NOT advance on such a run, or the skipped catch-up days would be
            # stamped as covered and lost forever. The next healthy run re-covers
            # them instead.
            bookmark_degraded = False
            try:
                bookmark = get_bookmark()
            except Exception:   # noqa: BLE001 - a bookmark read failure degrades to today-only
                bookmark = None
                bookmark_degraded = True
            window_start = compute_catchup_window(today, bookmark)
            caught_up_days = (date.fromisoformat(today) - date.fromisoformat(window_start)).days
            # Watchlist hygiene first: a followed show that has since ended/been canceled
            # won't air again, so drop it (ended LIBRARY shows are already auto-excluded;
            # this catches explicit eye-button follows).
            pruned = 0
            if prune_ended:
                deps.update_progress(automation_id, phase='Tidying the watchlist…', progress=10,
                                     log_line='Removing shows that have ended or been canceled', log_type='info')
                pruned = prune_ended_show_follows(deps, automation_id, fetch_follows=prune_follows,
                                                  show_status=show_status, remove_show=remove_show)
            deps.update_progress(
                automation_id, phase="Checking today's airings…", progress=25,
                log_line=('Reading the calendar for episodes airing today' if not caught_up_days
                          else 'Reading the calendar for %s → %s (catching up %d missed day(s))'
                          % (window_start, today, caught_up_days)),
                log_type='info')
            rows = fetch_airing(window_start, today) or []

            # Group what to wish for by show: airing today, NOT already owned, NOT
            # deliberately unmonitored, with a real season/episode.
            # add_episodes_to_wishlist is idempotent, so re-runs never duplicate.
            by_show: Dict[tuple, Dict[str, Any]] = {}
            season_cache: Dict[tuple, tuple] = {}
            for r in rows:
                if r.get('has_file'):
                    continue
                if not r.get('monitored'):
                    # unmonitored is a deliberate "stop hunting this" — the calendar
                    # treats it as a decision, not a gap; wishlisting it anyway would
                    # re-create exactly the work the user dismissed (Sonarr-parity).
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

            # Second pass: shows you follow but do NOT own. The calendar only knows
            # library shows, so without this a tmdb-only follow never acquired
            # anything, ever. Ask TMDB directly for what aired in the window.
            try:
                unowned = unowned_follows() or []
            except Exception:   # noqa: BLE001 - the calendar pass must still land
                unowned = []
            if unowned:
                deps.update_progress(
                    automation_id, phase='Checking followed shows you don\'t own yet…', progress=55,
                    log_line='Looking up %d followed show(s) that are not in your library' % len(unowned),
                    log_type='info')
            # dedup on the tmdb id ALONE. the calendar keys off shows.title and this
            # pass off video_watchlist.title, and those drift the moment someone
            # renames a show in the Manage panel — a title-keyed check would then
            # look the show up twice and add it twice.
            covered = {tid for (tid, _t) in by_show}
            for show in unowned:
                tid = show.get('tmdb_id')
                title = show.get('title') or tid
                if tid in covered:
                    continue                      # already covered by the calendar pass
                try:
                    res = tmdb_airings(tid, window_start, today) or {}
                except Exception:   # noqa: BLE001 - one bad lookup can't sink the run
                    deps.update_progress(automation_id, log_type='warning',
                                         log_line="Couldn't check %s on TMDB" % title)
                    continue
                eps = res.get('episodes') or []
                if eps:
                    # No library_id (it isn't owned), so no /api/video/poster/show/<id>
                    # proxy either. Carry TMDB's own poster instead — a wishlist row
                    # with a null poster_url renders as an initials orb that reads as
                    # "not matched", which is the exact symptom 94e06f2d fixed.
                    covered.add(tid)
                    by_show[(tid, title)] = {'library_id': None, 'eps': eps,
                                             'poster_url': res.get('poster_url')}

            added = 0
            for (tid, title), grp in by_show.items():
                poster = _show_poster_url(grp['library_id']) or grp.get('poster_url')
                added += int(add_episodes(tid, title, grp['eps'], grp['library_id'], poster) or 0)
            shows = len(by_show)

            # Advance the bookmark ONLY on success — a failed run must re-cover its
            # window next time. A degraded run (bookmark unreadable, covered today
            # only) must not advance it either: stamping today would mark the
            # un-covered catch-up days as done and lose them permanently.
            # A bookmark WRITE failure fails the run outright — the wishlist writes
            # already landed, but coverage was not committed, so the next run must
            # re-cover the window (at-least-once; the upsert is idempotent).
            if not bookmark_degraded:
                try:
                    set_bookmark(today)
                except Exception as e:   # noqa: BLE001
                    err = 'Could not persist the catch-up bookmark: %s' % e
                    deps.update_progress(automation_id, status='error', phase='Error',
                                         log_line=err, log_type='error')
                    return {'status': 'error', 'error': err, 'episodes_added': added,
                            'shows': shows, 'shows_pruned': pruned,
                            '_manages_own_progress': True}
            else:
                deps.update_progress(automation_id, log_line='Bookmark unreadable — covered today only, '
                                     'catch-up days will be re-covered next run',
                                     log_type='warning')

            done = ('Added %d airing episode(s) across %d show(s) to the wishlist'
                    % (added, shows)) if added else 'No new airing episodes to wishlist today'
            if caught_up_days:
                done += ' · caught up %d missed day(s)' % caught_up_days
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
