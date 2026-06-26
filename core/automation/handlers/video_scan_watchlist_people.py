"""Automation handler: ``video_scan_watchlist_people`` action.

The video "watchlist" follows ongoing *things* — shows, channels, and people. For shows
the ``video_add_airing_episodes`` automation already keeps the wishlist fed. This handler
does the equivalent for the PEOPLE you follow:

For every person on the watchlist, look up their filmography and wishlist every MOVIE the
user doesn't already own — the whole back catalog they acted in or directed, plus anything
upcoming. The first run is the meaty one (it backlogs everything); later runs are fast
because they skip movies already on the wishlist and only promote ones that have since been
released.

Design decisions (Boulder):
  * MOVIES ONLY — no TV episodes for a person (shows are handled by their own automation).
  * Both ACTOR (Acting credits, minus "playing themselves") and DIRECTOR credits count.
  * UNRELEASED movies are added as ``status='monitored'`` so the wishlist/download engine
    leaves them alone until they're out; a later scan PROMOTES them to ``'wanted'``.
  * Best-in-class UX: grab as much data as possible at add time (backdrop, overview, genres,
    runtime, rating, top cast, director, release date, + provenance "because you follow X")
    and stash it as a rich ``detail_json`` blob on the wishlist row, so the UI renders a full
    card later without re-fetching.

Like the other video handlers this lives on the SHARED automation side (so it may import
``core.video`` / ``api.video`` — the isolation contract only forbids the reverse) and owns
its own progress (``_manages_own_progress``). All I/O is injected as seams, so the logic is
a pure function in tests (no DB, no TMDB); production lazily binds the real calls.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Callable, Dict, Iterable, List, Optional

from core.automation.deps import AutomationDeps
from core.video.discovery_gaps import filmography_gaps


# ── pure credit classification ────────────────────────────────────────────────
# An actor "playing themselves" in a documentary / talk show / award broadcast isn't a
# dramatic role — drop those so the wishlist stays films they actually acted in.
_SELF_MARKERS = ('self', 'himself', 'herself', 'themselves', 'archive footage',
                 'archival footage')


def is_self_credit(role) -> bool:
    """True for a 'plays themselves' / archive-footage credit (role text only)."""
    r = str(role or '').strip().lower()
    return bool(r) and any(m in r for m in _SELF_MARKERS)


def is_actor_movie_credit(c: Dict[str, Any]) -> bool:
    return (c.get('kind') == 'movie'
            and str(c.get('department') or '') == 'Acting'
            and not is_self_credit(c.get('role')))


def is_director_movie_credit(c: Dict[str, Any]) -> bool:
    return c.get('kind') == 'movie' and str(c.get('role') or '').strip().lower() == 'director'


def is_relevant_movie_credit(c: Any) -> bool:
    """A movie this person ACTED in (not as themselves) or DIRECTED."""
    return isinstance(c, dict) and (is_actor_movie_credit(c) or is_director_movie_credit(c))


def is_released(date_str: Any, today: str) -> bool:
    """True if the release date is on/before ``today``. No date → NOT yet released, so we
    monitor it (a later scan promotes once a real, past date arrives)."""
    d = str(date_str or '').strip()
    return bool(d) and d[:10] <= today


def _int_set(values: Iterable) -> set:
    out = set()
    for x in values or []:
        try:
            out.add(int(x))
        except (TypeError, ValueError):
            continue
    return out


def select_person_movie_gaps(credits: List[Dict[str, Any]], owned_ids: Iterable,
                             ignored_ids: Iterable, *, today: str) -> List[Dict[str, Any]]:
    """The pure core: a followed person's un-owned actor/director MOVIE credits.

    Keeps only relevant movie credits, drops owned + ignored + duplicates, ranks by
    popularity (via ``filmography_gaps``), and tags each with the wishlist ``_status`` it
    should get — ``'wanted'`` if released, else ``'monitored'``. No I/O."""
    relevant = [c for c in (credits or []) if is_relevant_movie_credit(c)]
    ignored = _int_set(ignored_ids)
    out: List[Dict[str, Any]] = []
    for g in filmography_gaps(owned_ids, relevant, kinds=("movie",)):
        if int(g['tmdb_id']) in ignored:
            continue
        g = dict(g)
        g['_status'] = 'wanted' if is_released(g.get('date'), today) else 'monitored'
        out.append(g)
    return out


def build_detail_blob(detail: Optional[Dict[str, Any]], credit: Dict[str, Any],
                      person: Dict[str, Any]) -> Dict[str, Any]:
    """Trim TMDB full-detail down to a rich-but-lean card blob + provenance.

    Drops the heavy ``_extras`` (similar/recommendations/keywords/reviews/providers) and
    keeps what a wishlist card / detail view wants. Degrades to the credit's own fields when
    ``detail`` is missing or a library redirect."""
    via = {
        'person_tmdb_id': person.get('tmdb_id'),
        'person_name': person.get('title') or person.get('name'),
        'role': credit.get('role'),
        'as': 'director' if is_director_movie_credit(credit) else 'actor',
    }
    if not detail or detail.get('redirect'):
        return {
            'title': credit.get('title'), 'year': credit.get('year'),
            'release_date': credit.get('date'), 'poster_url': credit.get('poster'),
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
        'poster_url': detail.get('poster_url') or credit.get('poster'),
        'backdrop_url': detail.get('backdrop_url'),
        'logo': detail.get('logo'),
        'genres': detail.get('genres') or [],
        'runtime_minutes': detail.get('runtime_minutes'),
        'studio': detail.get('studio'),
        'year': detail.get('year') or credit.get('year'),
        'release_date': detail.get('release_date') or credit.get('date'),
        'cast': (detail.get('cast') or [])[:15],
        'director': director,
        'added_via': via,
    }


# ── production seams ──────────────────────────────────────────────────────────
def _default_fetch_people() -> List[Dict[str, Any]]:
    from api.video import get_video_db
    return get_video_db().list_watchlist('person')


def _default_fetch_credits(tmdb_id: Any) -> List[Dict[str, Any]]:
    from core.video.enrichment.engine import get_video_enrichment_engine
    d = get_video_enrichment_engine().person_detail(tmdb_id) or {}
    return d.get('credits') or []


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


def auto_video_scan_watchlist_people(
    config: Dict[str, Any],
    deps: AutomationDeps,
    *,
    fetch_people: Optional[Callable[[], List[Dict[str, Any]]]] = None,
    fetch_credits: Optional[Callable[[Any], List[Dict[str, Any]]]] = None,
    fetch_detail: Optional[Callable[[Any], Optional[Dict[str, Any]]]] = None,
    owned_ids: Optional[Callable[[], Iterable]] = None,
    ignored_ids: Optional[Callable[[], List[Any]]] = None,
    wishlisted_status: Optional[Callable[[], Dict[int, str]]] = None,
    add_movie: Optional[Callable[..., bool]] = None,
    today_fn: Optional[Callable[[], str]] = None,
) -> Dict[str, Any]:
    """Scan every followed person's filmography and wishlist the movies the user is missing.

    Returns ``{'status': 'completed', 'people': int, 'movies_added': int, 'upcoming': int,
    'promoted': int, ...}`` — ``movies_added`` = released wishlisted, ``upcoming`` = monitored
    (unreleased) wishlisted, ``promoted`` = monitored rows flipped to wanted now they're out."""
    fetch_people = fetch_people or _default_fetch_people
    fetch_credits = fetch_credits or _default_fetch_credits
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
                             log_line='Loading the people you follow', log_type='info')
        people = fetch_people() or []
        if not people:
            deps.update_progress(automation_id, status='finished', progress=100, phase='Complete',
                                 log_line='No people on the watchlist to scan', log_type='info')
            return {'status': 'completed', 'people': 0, 'movies_added': 0, 'upcoming': 0,
                    'promoted': 0, '_manages_own_progress': True}

        owned = owned_ids() or set()
        ignored = ignored_ids() or []
        # Snapshot of what's already wishlisted {tmdb_id: status}; updated as we add so a
        # movie credited to two followed people is handled once.
        wished: Dict[int, str] = dict(wishlisted_status() or {})

        added = upcoming = promoted = 0
        total = len(people)
        for i, person in enumerate(people):
            pid = person.get('tmdb_id')
            pname = person.get('title') or person.get('name') or pid
            deps.update_progress(automation_id, phase='Scanning filmographies…',
                                 progress=10 + int(80 * i / max(total, 1)),
                                 log_line="Looking up %s's movies" % pname, log_type='info')
            if not pid:
                continue
            try:
                credits = fetch_credits(pid) or []
            except Exception:   # noqa: BLE001 - one bad lookup shouldn't abort the whole scan
                deps.update_progress(automation_id, log_line="Couldn't fetch %s — skipping" % pname,
                                     log_type='warning')
                continue

            for g in select_person_movie_gaps(credits, owned, ignored, today=today):
                tid = int(g['tmdb_id'])
                want = g['_status']                  # 'wanted' | 'monitored'
                existing = wished.get(tid)
                if existing is not None:
                    # Already wishlisted — the only action left is promoting a monitored
                    # row that has since been released (don't re-fetch detail for it).
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

                # New gap — grab the rich detail (cached), build the blob, add it.
                try:
                    detail = fetch_detail(tid)
                except Exception:   # noqa: BLE001 - degrade to the cheap credit fields
                    detail = None
                blob = build_detail_blob(detail, g, person)
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
        done = ('Wishlisted ' + ', '.join(parts) + ' across %d followed person(s)' % total) \
            if parts else ('Watchlist is up to date — nothing new across %d person(s)' % total)
        deps.update_progress(automation_id, status='finished', progress=100, phase='Complete',
                             log_line=done, log_type='success')
        return {'status': 'completed', 'people': total, 'movies_added': added,
                'upcoming': upcoming, 'promoted': promoted, '_manages_own_progress': True}
    except Exception as e:  # noqa: BLE001
        deps.update_progress(automation_id, status='error', phase='Error', log_line=str(e), log_type='error')
        return {'status': 'error', 'error': str(e), '_manages_own_progress': True}
