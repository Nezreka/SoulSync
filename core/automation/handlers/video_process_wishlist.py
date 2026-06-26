"""Automation handlers: ``video_process_movie_wishlist`` / ``video_process_episode_wishlist``.

The Soulseek counterpart of the YouTube wishlist drain — the piece that finally makes the
people/airing scans pay off. For wished, RELEASED movies (and aired episodes) it searches
Soulseek, picks the best release per the quality profile, and enqueues the download; the
existing ``download_monitor`` finishes + organises + archives it, exactly like a manual grab.

Shape mirrors the YouTube drain (Boulder: "same standard"): it processes the WHOLE eligible
wishlist (no total cap), but the slow part — each item needs a ~20s blocking Soulseek search
— runs only a FEW at a time (``max_concurrent``). A ``guard`` keeps the next hourly tick from
overlapping a run that's still working, so it can't pile up.

Movies are gated on ``status='wanted'`` (released; skips 'monitored'/unreleased). Episodes
are all-wished (the airing scan only adds aired ones). Items already downloading are skipped
so re-runs never double-grab. The pick is the top ACCEPTED release — the ranker already
encodes the quality profile's accept/reject/score, so no extra rules here.

Shared automation side (may import ``core.video`` / ``api.video``); owns its own progress.
The search + enqueue are injected seams, so selection/pick/record are pure + unit-tested.
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, Iterable, List, Optional

from core.automation.deps import AutomationDeps


# ── pure helpers ──────────────────────────────────────────────────────────────
def pick_best(candidates: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The best ACCEPTED release from a ranked candidate list. ``_evaluate_hits`` already
    sorts best-first (accepted, score, availability), so the first accepted is the pick."""
    for c in candidates or []:
        if c.get("accepted"):
            return c
    return None


def item_key(item: Dict[str, Any], media_type: str) -> tuple:
    """Stable identity for de-duping a wished item against active downloads."""
    if media_type == "movie":
        return ("movie", str(item.get("tmdb_id")))
    return ("episode", str(item.get("show_tmdb_id")),
            int(item.get("season_number") or 0), int(item.get("episode_number") or 0))


def active_download_keys(active: Iterable[Dict[str, Any]]) -> set:
    """Identity keys for the movie/episode downloads already in flight, so we don't
    re-grab them. Episodes read season/episode out of the row's ``search_ctx``."""
    keys = set()
    for d in active or []:
        kind = str(d.get("kind") or "").lower()
        if kind == "movie":
            keys.add(("movie", str(d.get("media_id"))))
        elif kind == "episode":
            ctx = d.get("search_ctx")
            if isinstance(ctx, str):
                try:
                    ctx = json.loads(ctx)
                except (ValueError, TypeError):
                    ctx = {}
            ctx = ctx if isinstance(ctx, dict) else {}
            keys.add(("episode", str(d.get("media_id")),
                      int(ctx.get("season") or 0), int(ctx.get("episode") or 0)))
    return keys


def search_context(item: Dict[str, Any], media_type: str) -> Dict[str, Any]:
    """The ``search_ctx`` the download row carries (drives the monitor's requery)."""
    if media_type == "movie":
        return {"scope": "movie", "title": item.get("title"), "year": item.get("year")}
    return {"scope": "episode", "title": item.get("show_title"),
            "season": item.get("season_number"), "episode": item.get("episode_number"),
            "year": (str(item.get("air_date") or "")[:4] or None)}


def build_download_record(item: Dict[str, Any], best: Dict[str, Any], candidates: List[Dict[str, Any]],
                          *, media_type: str, target_dir: str, query: Any) -> Dict[str, Any]:
    """The ``add_video_download`` row for a chosen release — identical shape to a manual
    grab, so the monitor finishes it the same way (other accepted hits become the retry
    pool)."""
    ctx = search_context(item, media_type)
    rest = [c for c in (candidates or []) if c.get("filename") != best.get("filename")]
    media_id = str(item.get("tmdb_id") if media_type == "movie" else item.get("show_tmdb_id"))
    return {
        "kind": media_type, "title": ctx["title"],
        "release_title": best.get("title") or best.get("filename"),
        "source": "soulseek", "username": best.get("username"), "filename": best.get("filename"),
        "size_bytes": int(best.get("size_bytes") or 0), "quality_label": best.get("quality_label"),
        "target_dir": target_dir, "status": "downloading",
        "media_id": media_id, "media_source": "tmdb", "year": ctx.get("year"),
        "poster_url": item.get("poster_url"),
        "candidates": json.dumps(rest), "search_ctx": json.dumps(ctx),
        "tried_queries": json.dumps([query] if query else []),
        "tried_files": json.dumps([best.get("filename")]), "attempts": 0,
    }


# ── production seams ──────────────────────────────────────────────────────────
def _default_fetch_items(media_type: str) -> List[Dict[str, Any]]:
    from api.video import get_video_db
    db = get_video_db()
    return db.movie_wishlist_to_download() if media_type == "movie" else db.episode_wishlist_to_download()


def _default_active_keys(media_type: str) -> set:
    from api.video import get_video_db
    return active_download_keys(get_video_db().get_active_video_downloads())


def _default_target_dir(media_type: str) -> str:
    from api.video import get_video_db
    db = get_video_db()
    if media_type == "movie":
        return db.get_setting("movies_path") or db.get_setting("transfer_path") or ""
    return db.get_setting("tv_path") or ""


def _default_search(item: Dict[str, Any], media_type: str):
    """A bounded blocking Soulseek search → ranked candidates (same path the retry worker +
    manual search use). Returns [] for a real empty result, or **None** if the search never
    ran (slskd not configured / errored / rate-limited) so the caller can say so."""
    from api.video.downloads import _evaluate_hits
    from core.video.download_monitor import _search_for_retry
    from core.video.quality_profile import load as load_profile
    from core.video.slskd_search import build_query
    from api.video import get_video_db
    ctx = search_context(item, media_type)
    query = build_query(ctx["scope"], ctx["title"], year=ctx.get("year"),
                        season=ctx.get("season"), episode=ctx.get("episode"))
    res = _search_for_retry(query) or {}
    if res.get("started") is False:
        return None                         # slskd didn't accept the search
    profile = load_profile(get_video_db())
    return _evaluate_hits(res.get("hits") or [], profile, ctx["scope"],
                          ctx.get("season"), ctx.get("episode"))


def _default_enqueue(item: Dict[str, Any], best: Dict[str, Any], candidates: List[Dict[str, Any]],
                     media_type: str, target_dir: str) -> bool:
    """Start the slskd transfer + write the download row (exactly like the manual flow),
    then ensure the monitor is running. Returns True if slskd accepted it."""
    from api.video import get_video_db
    from core.video.download_monitor import ensure_started
    from core.video.slskd_download import start_download
    from core.video.slskd_search import build_query
    started = start_download(best.get("username"), best.get("filename"), best.get("size_bytes") or 0)
    if not started.get("ok"):
        return False
    ctx = search_context(item, media_type)
    query = build_query(ctx["scope"], ctx["title"], year=ctx.get("year"),
                        season=ctx.get("season"), episode=ctx.get("episode"))
    get_video_db().add_video_download(
        build_download_record(item, best, candidates, media_type=media_type,
                              target_dir=target_dir, query=query))
    ensure_started(get_video_db)
    return True


# ── guard: keep an in-progress drain from overlapping the next tick ───────────
_running: Dict[str, bool] = {"movie": False, "episode": False}


def is_running(media_type: str) -> bool:
    return bool(_running.get(media_type))


def auto_video_process_wishlist(
    config: Dict[str, Any],
    deps: AutomationDeps,
    *,
    media_type: str = "movie",
    fetch_items: Optional[Callable[[str], List[Dict[str, Any]]]] = None,
    active_keys: Optional[Callable[[str], Iterable]] = None,
    target_dir: Optional[Callable[[str], str]] = None,
    search: Optional[Callable[[Dict[str, Any], str], List[Dict[str, Any]]]] = None,
    enqueue: Optional[Callable[..., bool]] = None,
) -> Dict[str, Any]:
    """Auto-grab the wished movies (or episodes): search Soulseek, pick the best release,
    enqueue. Processes the whole eligible wishlist, a few searches at a time.

    Returns ``{'status': 'completed', 'searched': int, 'grabbed': int, ...}``."""
    fetch_items = fetch_items or _default_fetch_items
    active_keys = active_keys or _default_active_keys
    target_dir = target_dir or _default_target_dir
    search = search or _default_search
    enqueue = enqueue or _default_enqueue
    automation_id = config.get('_automation_id')
    concurrency = max(1, int(config.get('max_concurrent', 3) or 3))
    label = 'movie' if media_type == 'movie' else 'episode'

    _running[media_type] = True
    try:
        root = target_dir(media_type)
        if not root:
            where = 'Movie' if media_type == 'movie' else 'TV'
            deps.update_progress(automation_id, status='finished', progress=100, phase='Complete',
                                 log_line='%s library folder not set — skipping (Settings → Downloads)' % where,
                                 log_type='info')
            return {'status': 'completed', 'searched': 0, 'grabbed': 0,
                    'skipped': 'no_folder', '_manages_own_progress': True}

        deps.update_progress(automation_id, phase='Checking the wishlist…', progress=5,
                             log_line='Looking for wished %ss to grab' % label, log_type='info')
        items = fetch_items(media_type) or []
        active = set(active_keys(media_type) or set())
        todo = [it for it in items if item_key(it, media_type) not in active]
        if not todo:
            deps.update_progress(automation_id, status='finished', progress=100, phase='Complete',
                                 log_line='Nothing new to grab (%d already in flight)' % len(active),
                                 log_type='info')
            return {'status': 'completed', 'searched': 0, 'grabbed': 0, '_manages_own_progress': True}

        grabbed = [0]
        searched = [0]
        noresults = [0]    # search came back empty (the source had nothing)
        rejected = [0]     # source had hits, but none passed the quality profile
        notrun = [0]       # the search never ran (slskd didn't accept it)
        total = len(todo)
        lock = threading.Lock()

        def _one(it):
            cands = search(it, media_type)
            didnt_run = cands is None       # slskd not configured / errored / rate-limited
            cands = cands or []
            best = pick_best(cands)
            ok = bool(best) and bool(enqueue(it, best, cands, media_type, root))
            name = it.get('title') or it.get('show_title') or '?'
            if media_type == 'episode':
                name = "%s S%02dE%02d" % (name, int(it.get('season_number') or 0),
                                          int(it.get('episode_number') or 0))
            # tell apart: grabbed / search-didn't-run / source-empty / hits-but-all-rejected.
            if ok:
                msg, lt = "Grabbed '%s'" % name, 'success'
            elif didnt_run:
                msg, lt = "Search didn't run for '%s' — slskd not responding?" % name, 'warning'
            elif not cands:
                msg, lt = "No search results for '%s'" % name, 'info'
            else:
                why = (cands[0].get('rejected') or 'none met your quality profile')
                msg, lt = "%d result(s) for '%s', none accepted — %s" % (len(cands), name, why), 'info'
            with lock:
                searched[0] += 1
                if ok:
                    grabbed[0] += 1
                elif didnt_run:
                    notrun[0] += 1
                elif not cands:
                    noresults[0] += 1
                else:
                    rejected[0] += 1
                deps.update_progress(
                    automation_id, phase='Searching + grabbing…',
                    progress=10 + int(85 * searched[0] / max(total, 1)),
                    log_line=msg, log_type=lt)

        with ThreadPoolExecutor(max_workers=concurrency) as ex:
            list(ex.map(_one, todo))

        # Headline with the WHY breakdown: it's the difference between "the source has
        # nothing" (noresults) and "it has stuff but your quality profile rejects it" (rejected).
        tail = []
        if notrun[0]:
            tail.append("%d search(es) didn't run (slskd?)" % notrun[0])
        if noresults[0]:
            tail.append('%d had no results' % noresults[0])
        if rejected[0]:
            tail.append('%d rejected on quality' % rejected[0])
        breakdown = (' · ' + ', '.join(tail)) if tail else ''
        done = ('Grabbed %d %s(s) of %d searched%s' % (grabbed[0], label, searched[0], breakdown)) if grabbed[0] \
            else ('Searched %d %s(s), grabbed 0%s' % (searched[0], label, breakdown))
        deps.update_progress(automation_id, status='finished', progress=100, phase='Complete',
                             log_line=done, log_type='success' if grabbed[0] else 'info')
        return {'status': 'completed', 'searched': searched[0], 'grabbed': grabbed[0],
                'noresults': noresults[0], 'rejected': rejected[0], 'notrun': notrun[0],
                '_manages_own_progress': True}
    except Exception as e:  # noqa: BLE001
        deps.update_progress(automation_id, status='error', phase='Error', log_line=str(e), log_type='error')
        return {'status': 'error', 'error': str(e), '_manages_own_progress': True}
    finally:
        _running[media_type] = False
