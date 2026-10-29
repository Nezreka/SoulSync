"""Automation handler: ``video_add_airing_episodes`` action.

Sonarr-style "monitor airings": add every episode that aired in the last week — for the
TV shows you follow on the video watchlist — to the video WISHLIST, once each, skipping
ones you already own. Runs every few hours, so a run the server missed costs hours, not
a day, and an episode TMDB learns about late still gets offered.

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


# Every run looks back a week, and a per-episode receipt says what was already
# offered. The old shape covered (bookmark + 1 .. today) once a day at 01:00, and
# that lost episodes two ways: a daily slot the server was restarting through is
# discarded (it missed Oct 21, 24 and 27 on Boulder's box, each healed a day
# late), and an episode that reached TMDB on or after its own air date (a
# same-day streaming drop, a date moved earlier) sat behind a bookmark that had
# already passed it, so it was never offered at all. The receipt is also what
# keeps an episode the user deleted from the wishlist from coming back.
LOOKBACK_DAYS = 7
_BOOKMARK_KEY = 'airing_last_covered'      # the old coverage line, read once to seed
_SEEDED_KEY = 'airing_offers_seeded'


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


def _default_get_offered(keys) -> set:
    from api.video import get_video_db
    return get_video_db().airing_offered_keys(_profile(), keys)


def _default_mark_offered(keys) -> None:
    from api.video import get_video_db
    get_video_db().mark_airing_offered(_profile(), keys)


def _default_is_seeded() -> bool:
    from api.video import get_video_db
    return (get_video_db().get_setting('%s:%d' % (_SEEDED_KEY, _profile())) or '') == '1'


def _default_set_seeded() -> None:
    from api.video import get_video_db
    get_video_db().set_setting('%s:%d' % (_SEEDED_KEY, _profile()), '1')


def lookback_start(today: str, days: int = LOOKBACK_DAYS) -> str:
    """The window start: ``days`` days ending today. Pure."""
    return (date.fromisoformat(str(today)) - timedelta(days=max(1, int(days)) - 1)).isoformat()


def seed_boundary(today: str, bookmark: Optional[str]) -> str:
    """The last day the old bookmark rules already covered, so the first
    receipt-based run doesn't re-offer what they offered (and the user may have
    since deleted). No usable bookmark means a fresh install, which never
    backfilled history either: everything before today counts as covered. Pure."""
    yesterday = (date.fromisoformat(str(today)) - timedelta(days=1)).isoformat()
    try:
        bm = date.fromisoformat(str(bookmark)).isoformat() if bookmark else None
    except ValueError:
        bm = None
    if not bm or bm > yesterday:
        return yesterday
    return bm


def _key(tid, sn, en) -> tuple:
    return (int(tid), int(sn), int(en))


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

    receipts are per profile (the background profile is set for each pass),
    so the passes share nothing and one failing never costs another its days.
    """
    from core.automation.handlers.video_profile_fanout import run_per_profile

    fetch_airing = kwargs.pop('fetch_airing', None) or _default_fetch_airing
    fetch_follows = kwargs.pop('fetch_follows', None) or _default_followed_shows
    follower_profiles = kwargs.pop('follower_profiles', None)

    def run_pass(pass_deps, pid, is_owner):
        extra = dict(kwargs)
        if not is_owner:
            follows = {str(f.get('tmdb_id')) for f in (fetch_follows(pid) or [])}
            extra['fetch_airing'] = lambda start, end: [
                r for r in (fetch_airing(start, end) or [])
                if str(r.get('show_tmdb_id')) in follows]
        else:
            extra['fetch_airing'] = fetch_airing
        return auto_video_add_airing_episodes(config, pass_deps, **extra)

    return run_per_profile(run_pass, deps, ['show'], follower_profiles=follower_profiles)


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
    get_offered: Optional[Callable[[Any], set]] = None,
    mark_offered: Optional[Callable[[Any], None]] = None,
    is_seeded: Optional[Callable[[], bool]] = None,
    set_seeded: Optional[Callable[[], None]] = None,
    unowned_follows: Optional[Callable[[], List[Dict[str, Any]]]] = None,
    tmdb_airings: Optional[Callable[[Any, str, str], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Add the last week's aired (unowned, monitored, followed-show) episodes to the
    video wishlist, each one once, and first tidy the watchlist by dropping shows
    that have ended / been canceled.

    Returns ``{'status': 'completed', 'episodes_added': int, 'shows': int,
    'shows_pruned': int, ...}``."""
    with _RUN_GUARD:
        fetch_airing = fetch_airing or _default_fetch_airing
        add_episodes = add_episodes or _default_add_episodes
        season_meta = season_meta or _default_season_meta
        today_fn = today_fn or (lambda: _today_in_tz(config.get('_trigger_tz')))
        get_bookmark = get_bookmark or _default_get_bookmark
        get_offered = get_offered or _default_get_offered
        mark_offered = mark_offered or _default_mark_offered
        is_seeded = is_seeded or _default_is_seeded
        set_seeded = set_seeded or _default_set_seeded
        unowned_follows = unowned_follows or _default_unowned_follows
        tmdb_airings = tmdb_airings or _default_tmdb_airings
        automation_id = config.get('_automation_id')
        prune_ended = config.get('prune_ended', True)
        try:
            today = today_fn()
            window_start = lookback_start(today)
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
                automation_id, phase='Checking recent airings…', progress=25,
                log_line='Reading the calendar for %s → %s' % (window_start, today),
                log_type='info')
            rows = fetch_airing(window_start, today) or []

            # What could be wished for: aired in the window, NOT owned, NOT
            # deliberately unmonitored, with a real season/episode.
            cand = []
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
                cand.append(r)

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
            covered = {str(r.get('show_tmdb_id')) for r in cand}
            tmdb_groups = []
            for show in unowned:
                tid = show.get('tmdb_id')
                title = show.get('title') or tid
                if str(tid) in covered:
                    continue                      # already covered by the calendar pass
                try:
                    res = tmdb_airings(tid, window_start, today) or {}
                except Exception:   # noqa: BLE001 - one bad lookup can't sink the run
                    deps.update_progress(automation_id, log_type='warning',
                                         log_line="Couldn't check %s on TMDB" % title)
                    continue
                eps = [e for e in (res.get('episodes') or [])
                       if e.get('season_number') is not None and e.get('episode_number') is not None]
                if eps:
                    covered.add(str(tid))
                    tmdb_groups.append((tid, title, res.get('poster_url'), eps))

            # Receipts. The first run on a profile marks what the old bookmark
            # already covered as offered, without adding it.
            keys = [_key(r['show_tmdb_id'], r['season_number'], r['episode_number']) for r in cand]
            for tid, _t, _p, eps in tmdb_groups:
                keys += [_key(tid, e['season_number'], e['episode_number']) for e in eps]
            if not is_seeded():
                try:
                    bookmark = get_bookmark()
                except Exception:   # noqa: BLE001 - unreadable reads like a fresh install
                    bookmark = None
                line = seed_boundary(today, bookmark)
                old = [_key(r['show_tmdb_id'], r['season_number'], r['episode_number'])
                       for r in cand if str(r.get('air_date') or '')[:10] <= line]
                for tid, _t, _p, eps in tmdb_groups:
                    old += [_key(tid, e['season_number'], e['episode_number'])
                            for e in eps if str(e.get('air_date') or '')[:10] <= line]
                mark_offered(old)
                set_seeded()
            offered = get_offered(keys) if keys else set()

            # Group what's left by show, carrying the SAME TMDB metadata a manual
            # add gets (still + overview + season poster); the calendar/DB values
            # fill in if TMDB is unavailable.
            by_show: Dict[tuple, Dict[str, Any]] = {}
            season_cache: Dict[tuple, tuple] = {}
            for r in cand:
                tid, sn, en = r['show_tmdb_id'], r['season_number'], r['episode_number']
                if _key(tid, sn, en) in offered:
                    continue
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
            for tid, title, poster_url, eps in tmdb_groups:
                fresh = [e for e in eps
                         if _key(tid, e['season_number'], e['episode_number']) not in offered]
                if fresh:
                    # No library_id (it isn't owned), so no /api/video/poster/show/<id>
                    # proxy either. Carry TMDB's own poster instead — a wishlist row
                    # with a null poster_url renders as an initials orb that reads as
                    # "not matched", which is the exact symptom 94e06f2d fixed.
                    by_show[(tid, title)] = {'library_id': None, 'eps': fresh,
                                             'poster_url': poster_url}

            # Each show's receipt lands right after its write, so a failure
            # part-way leaves the rest to be offered by the next run.
            added = 0
            for (tid, title), grp in by_show.items():
                poster = _show_poster_url(grp['library_id']) or grp.get('poster_url')
                added += int(add_episodes(tid, title, grp['eps'], grp['library_id'], poster) or 0)
                mark_offered([_key(tid, e['season_number'], e['episode_number']) for e in grp['eps']])
            shows = len(by_show)

            done = ('Added %d aired episode(s) across %d show(s) to the wishlist'
                    % (added, shows)) if added else 'No new aired episodes to wishlist'
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
