"""Automation handler: ``video_scan_watchlist_studios`` action.

The video "watchlist" follows ongoing *things* — shows, channels, people, and now STUDIOS
(production companies: Pixar, A24, Disney…). This handler is the studio equivalent of the
people scan: for every studio you follow, wishlist every MOVIE it produced that you don't
already own — forward from when you followed it (plus anything upcoming), widened by a
per-studio lookback window.

Design decisions (Boulder):
  * MOVIES ONLY — a studio's films (TV is out of scope for a studio follow).
  * FORWARD-ONLY default + per-studio lookback — same spine as the people scan
    (``person_cutoff``): only films released since you followed, unless you widen it.
  * A SETTLED-FILMS VOTE FLOOR keeps a big studio (Disney) from dumping every obscure short
    onto the wishlist — but it only gates films old enough for votes to have accrued, so a
    brand-new or upcoming release is NEVER blocked. (Phase 3 turns the floor into a per-studio
    slider; for now it's a sensible constant.)
  * UNRELEASED films are added ``status='monitored'`` (the downloader leaves them alone) and a
    later scan PROMOTES them to ``'wanted'`` once they're out — identical to the people scan.

Like the other video handlers this lives on the SHARED automation side (so it may import
``core.video`` / ``api.video``) and owns its own progress. All I/O is injected as seams, so
the selection logic is a pure function in tests (no DB, no TMDB).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Callable, Dict, Iterable, List, Optional

from core.automation.deps import AutomationDeps
from core.video.discovery_gaps import filmography_gaps
# Reuse the people scan's generic date helpers (forward-only cutoff + release check) so the
# two scans share one definition of "the back-catalog window".
from core.automation.handlers.video_scan_watchlist_people import person_cutoff, is_released


# A big studio's back catalog is full of obscure shorts/specials with a handful of votes;
# gate those out. But votes accrue over time, so ONLY apply the floor to films old enough to
# have settled (released before ``today - SETTLE_DAYS``) — a new/upcoming release always
# passes regardless of its (still-zero) vote count.
DEFAULT_VOTE_FLOOR = 40
SETTLE_DAYS = 45


def _int_set(values: Iterable) -> set:
    out = set()
    for x in values or []:
        try:
            out.add(int(x))
        except (TypeError, ValueError):
            continue
    return out


def _settle_cutoff(today: str, settle_days: int = SETTLE_DAYS) -> Optional[str]:
    try:
        return (date.fromisoformat(str(today)[:10]) - timedelta(days=settle_days)).isoformat()
    except (ValueError, TypeError):
        return None


def select_studio_movie_gaps(films: List[Dict[str, Any]], owned_ids: Iterable,
                             ignored_ids: Iterable, *, today: str,
                             since: Optional[str] = None,
                             vote_floor: int = DEFAULT_VOTE_FLOOR,
                             settle_days: int = SETTLE_DAYS) -> List[Dict[str, Any]]:
    """The pure core: a followed studio's un-owned MOVIE catalog, filtered + tagged.

    Drops owned + ignored + duplicates (ranked by popularity via ``filmography_gaps``), applies
    the back-catalog ``since`` cutoff (forward-only default / lookback window — a film RELEASED
    before it is skipped, undated + upcoming always kept), and a SETTLED-films vote floor
    (only gates films released before ``today - settle_days`` — never a new/upcoming one).
    Tags each with the wishlist ``_status`` (``'wanted'`` if released, else ``'monitored'``).
    No I/O."""
    ignored = _int_set(ignored_ids)
    settle = _settle_cutoff(today, settle_days) if vote_floor else None
    out: List[Dict[str, Any]] = []
    for g in filmography_gaps(owned_ids, films or [], kinds=("movie",)):
        tid = int(g['tmdb_id'])
        if tid in ignored:
            continue
        gd = str(g.get('date') or '')[:10]
        if since and len(gd) == 10 and gd < since:
            continue                 # released before the cutoff → old back-catalog, skip
        if (vote_floor and settle and len(gd) == 10 and gd < settle
                and (g.get('vote_count') or 0) < vote_floor):
            continue                 # settled but obscure → skip (new/upcoming exempt)
        g = dict(g)
        g['_status'] = 'wanted' if is_released(g.get('date'), today) else 'monitored'
        out.append(g)
    return out


def build_detail_blob(detail: Optional[Dict[str, Any]], film: Dict[str, Any],
                      studio: Dict[str, Any]) -> Dict[str, Any]:
    """Trim TMDB full-detail down to a rich-but-lean wishlist-card blob + studio provenance.

    Degrades to the film's own catalog fields when ``detail`` is missing or a library
    redirect. Mirrors the people scan's blob so the wishlist card renders identically."""
    via = {
        'studio_tmdb_id': studio.get('tmdb_id'),
        'studio_name': studio.get('title') or studio.get('name'),
        'as': 'studio',
    }
    if not detail or detail.get('redirect'):
        return {
            'title': film.get('title'), 'year': film.get('year'),
            'release_date': film.get('date'), 'poster_url': film.get('poster'),
            'added_via': via,
        }
    director = next((p.get('name') for p in (detail.get('crew') or [])
                     if str(p.get('job')) == 'Director'), None)
    return {
        'title': detail.get('title'),
        'overview': detail.get('overview'),
        'tagline': detail.get('tagline'),
        'status': detail.get('status'),
        'rating': detail.get('rating'),
        'imdb_id': detail.get('imdb_id'),
        'poster_url': detail.get('poster_url') or film.get('poster'),
        'backdrop_url': detail.get('backdrop_url'),
        'logo': detail.get('logo'),
        'genres': detail.get('genres') or [],
        'runtime_minutes': detail.get('runtime_minutes'),
        'studio': detail.get('studio'),
        'year': detail.get('year') or film.get('year'),
        'release_date': detail.get('release_date') or film.get('date'),
        'cast': (detail.get('cast') or [])[:15],
        'director': director,
        'added_via': via,
    }


# ── production seams ──────────────────────────────────────────────────────────
def _default_fetch_studios() -> List[Dict[str, Any]]:
    from api.video import get_video_db
    return get_video_db().list_watchlist('studio')


def _default_fetch_films(company_id: Any) -> List[Dict[str, Any]]:
    from core.video.enrichment.engine import get_video_enrichment_engine
    return get_video_enrichment_engine().company_films(company_id) or []


def _default_fetch_detail(tmdb_id: Any) -> Optional[Dict[str, Any]]:
    from core.video.enrichment.engine import get_video_enrichment_engine
    return get_video_enrichment_engine().tmdb_detail('movie', tmdb_id)


def _default_owned_ids() -> Iterable:
    from api.video import get_video_db
    from core.video.sources import resolve_video_server
    return get_video_db().owned_movie_tmdb_ids(resolve_video_server())


def _default_ignored_ids() -> List[Any]:
    from api.video import get_video_db
    return [r.get('tmdb_id') for r in (get_video_db().list_ignored() or [])
            if r.get('kind') == 'movie']


def _default_wishlisted_status() -> Dict[int, str]:
    from api.video import get_video_db
    return get_video_db().wishlisted_movie_status()


def _default_add_movie(tmdb_id, title, *, year, poster_url, status, detail_json) -> bool:
    from api.video import get_video_db
    from core.video.sources import resolve_video_server
    return get_video_db().add_movie_to_wishlist(
        tmdb_id, title, year=year, poster_url=poster_url, status=status,
        detail_json=detail_json, server_source=resolve_video_server())


def auto_video_scan_watchlist_studios(
    config: Dict[str, Any],
    deps: AutomationDeps,
    *,
    fetch_studios: Optional[Callable[[], List[Dict[str, Any]]]] = None,
    fetch_films: Optional[Callable[[Any], List[Dict[str, Any]]]] = None,
    fetch_detail: Optional[Callable[[Any], Optional[Dict[str, Any]]]] = None,
    owned_ids: Optional[Callable[[], Iterable]] = None,
    ignored_ids: Optional[Callable[[], List[Any]]] = None,
    wishlisted_status: Optional[Callable[[], Dict[int, str]]] = None,
    add_movie: Optional[Callable[..., bool]] = None,
    today_fn: Optional[Callable[[], str]] = None,
) -> Dict[str, Any]:
    """Scan every followed studio's catalog and wishlist the movies the user is missing.

    Returns ``{'status': 'completed', 'studios': int, 'movies_added': int, 'upcoming': int,
    'promoted': int, ...}`` — ``movies_added`` = released wishlisted, ``upcoming`` = monitored
    (unreleased) wishlisted, ``promoted`` = monitored rows flipped to wanted now they're out."""
    fetch_studios = fetch_studios or _default_fetch_studios
    fetch_films = fetch_films or _default_fetch_films
    fetch_detail = fetch_detail or _default_fetch_detail
    owned_ids = owned_ids or _default_owned_ids
    ignored_ids = ignored_ids or _default_ignored_ids
    wishlisted_status = wishlisted_status or _default_wishlisted_status
    add_movie = add_movie or _default_add_movie
    today_fn = today_fn or (lambda: date.today().isoformat())
    automation_id = config.get('_automation_id')

    try:
        today = today_fn()
        deps.update_progress(automation_id, phase='Reading your watchlist…', progress=5,
                             log_line='Loading the studios you follow', log_type='info')
        studios = fetch_studios() or []
        if not studios:
            deps.update_progress(automation_id, status='finished', progress=100, phase='Complete',
                                 log_line='No studios on the watchlist to scan', log_type='info')
            return {'status': 'completed', 'studios': 0, 'movies_added': 0, 'upcoming': 0,
                    'promoted': 0, '_manages_own_progress': True}

        owned = owned_ids() or set()
        ignored = ignored_ids() or []
        wished: Dict[int, str] = dict(wishlisted_status() or {})

        added = upcoming = promoted = 0
        total = len(studios)
        for i, studio in enumerate(studios):
            sid = studio.get('tmdb_id')
            sname = studio.get('title') or studio.get('name') or sid
            deps.update_progress(automation_id, phase='Scanning studio catalogs…',
                                 progress=10 + int(80 * i / max(total, 1)),
                                 log_line="Looking up %s's films" % sname, log_type='info')
            if not sid:
                continue
            try:
                films = fetch_films(sid) or []
            except Exception:   # noqa: BLE001 - one bad lookup shouldn't abort the whole scan
                deps.update_progress(automation_id, log_line="Couldn't fetch %s — skipping" % sname,
                                     log_type='warning')
                continue

            # Forward-only by default: only films released since the studio was followed
            # (date_added), widened by its own lookback window if set.
            since = person_cutoff(studio.get('date_added'), studio.get('lookback_years'))
            for g in select_studio_movie_gaps(films, owned, ignored, today=today, since=since):
                tid = int(g['tmdb_id'])
                want = g['_status']                  # 'wanted' | 'monitored'
                existing = wished.get(tid)
                if existing is not None:
                    # Already wishlisted (maybe via another studio/person) — only action left
                    # is promoting a monitored row that's since been released.
                    if existing == 'monitored' and want == 'wanted':
                        if add_movie(tid, g.get('title'), year=g.get('year'),
                                     poster_url=g.get('poster'), status='wanted', detail_json=None):
                            wished[tid] = 'wanted'
                            promoted += 1
                            deps.update_progress(
                                automation_id, log_type='success',
                                log_line="'%s' is out now — promoted to wanted"
                                % (g.get('title') or tid))
                    continue

                try:
                    detail = fetch_detail(tid)
                except Exception:   # noqa: BLE001 - degrade to the cheap catalog fields
                    detail = None
                blob = build_detail_blob(detail, g, studio)
                if add_movie(tid, g.get('title') or blob.get('title'), year=g.get('year'),
                             poster_url=blob.get('poster_url') or g.get('poster'),
                             status=want, detail_json=blob):
                    wished[tid] = want
                    if want == 'wanted':
                        added += 1
                    else:
                        upcoming += 1

        parts = []
        if added:
            parts.append('%d new movie(s)' % added)
        if upcoming:
            parts.append('%d upcoming' % upcoming)
        if promoted:
            parts.append('%d promoted' % promoted)
        done = ('Wishlisted ' + ', '.join(parts) + ' across %d followed studio(s)' % total) \
            if parts else ('Watchlist is up to date — nothing new across %d studio(s)' % total)
        deps.update_progress(automation_id, status='finished', progress=100, phase='Complete',
                             log_line=done, log_type='success')
        return {'status': 'completed', 'studios': total, 'movies_added': added,
                'upcoming': upcoming, 'promoted': promoted, '_manages_own_progress': True}
    except Exception as e:  # noqa: BLE001
        deps.update_progress(automation_id, status='error', phase='Error', log_line=str(e), log_type='error')
        return {'status': 'error', 'error': str(e), '_manages_own_progress': True}
