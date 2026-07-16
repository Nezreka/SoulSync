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
from utils.logging_config import get_logger

logger = get_logger("automation.video_process_wishlist")


# ── pure helpers ──────────────────────────────────────────────────────────────
def pick_best(candidates: List[Dict[str, Any]], min_rank: int = 0) -> Optional[Dict[str, Any]]:
    """The best ACCEPTED release from a ranked candidate list. ``_evaluate_hits`` already
    sorts best-first (accepted, score, availability), so the first accepted is the pick.

    ``min_rank`` > 0 = an UPGRADE pick for an owned item: only releases with a
    resolution STRICTLY better than the current copy qualify (a same-quality
    re-grab would just import_fail as 'not an upgrade' — the old re-download
    loop). Unknown-resolution releases can't prove they're better, so they
    don't qualify either."""
    from core.video.quality_eval import resolution_rank
    for c in candidates or []:
        if not c.get("accepted"):
            continue
        if min_rank and resolution_rank(c.get("resolution")) <= min_rank:
            continue
        return c
    return None


def annotate_upgrades(items: List[Dict[str, Any]], cutoff_rank: int,
                      cutoff_for: Optional[Callable[[Dict[str, Any]], int]] = None) -> List[Dict[str, Any]]:
    """Upgrade-until-cutoff eligibility over the wishlist rows (pure).

    Unowned items pass through untouched. Owned items (the queries annotate
    ``owned`` + ``owned_resolutions``) are judged against the cutoff:
      · already meet it       → skipped (their row should be gone; the Wishlist
                                Audit job sweeps stragglers)
      · below it              → kept, carrying ``_min_rank`` = the current
                                copy's rank so only strictly-better wins
      · resolution unreadable → skipped (can't prove an upgrade; the audit job
                                surfaces these)
    An empty cutoff ('always chase the best') means owned items are never
    'done' — they stay upgrade-eligible forever.

    ``cutoff_for`` (P2, per-title profiles): when given, each owned item is
    judged against ITS OWN profile's cutoff instead of the global
    ``cutoff_rank``. Still pure — the callable is injected."""
    from core.video.quality_eval import resolution_rank
    out = []
    for it in items or []:
        if not it.get("owned"):
            out.append(it)
            continue
        rks = [resolution_rank(r) for r in str(it.get("owned_resolutions") or "").split(",")
               if r.strip()]
        cur = max(rks, default=0)
        if cur == 0:
            continue
        eff_cutoff = cutoff_for(it) if cutoff_for is not None else cutoff_rank
        if eff_cutoff and cur >= eff_cutoff:
            continue
        it = dict(it)
        it["_min_rank"] = cur
        out.append(it)
    return out


def _cutoff_rank_for_item(item: Dict[str, Any]) -> int:
    """The cutoff rank under the item's OWN profile (per-title, P2)."""
    from api.video import get_video_db
    from core.video.quality_eval import resolution_rank
    from core.video.quality_profile import load_for_item
    return resolution_rank((load_for_item(get_video_db(), item) or {}).get("cutoff_resolution"))


def _default_cutoff_rank() -> int:
    """The profile cutoff as a resolution rank (0 = no cutoff set)."""
    from api.video import get_video_db
    from core.video.quality_eval import resolution_rank
    from core.video.quality_profile import load as load_profile
    return resolution_rank((load_profile(get_video_db()) or {}).get("cutoff_resolution"))


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


def _acceptable_titles(primary: Any, kind: str, tmdb_id: Any) -> List[str]:
    """[primary title, *TMDB alternative titles] — deduped, primary first. The alias
    set the release-title gate matches against (so a release named by a known aka still
    matches). Best-effort: just the primary when TMDB is unavailable."""
    aliases: List[str] = []
    if tmdb_id:
        try:
            from core.video.enrichment.engine import get_video_enrichment_engine
            aliases = get_video_enrichment_engine().alt_titles_for(kind, tmdb_id) or []
        except Exception:   # noqa: BLE001 - a matching assist must never break a grab
            aliases = []
    out, seen = [], set()
    for t in [primary, *aliases]:
        t = str(t or "").strip()
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out


def search_context(item: Dict[str, Any], media_type: str) -> Dict[str, Any]:
    """The ``search_ctx`` the download row carries (drives the monitor's requery).
    Carries ``titles`` — the primary title plus TMDB aliases — so both the initial pick
    and every retry gate releases against the full alias set."""
    if media_type == "movie":
        ctx = {"scope": "movie", "title": item.get("title"), "year": item.get("year")}
        tmdb_id, kind = item.get("tmdb_id"), "movie"
    else:
        ctx = {"scope": "episode", "title": item.get("show_title"),
               "season": item.get("season_number"), "episode": item.get("episode_number"),
               "year": (str(item.get("air_date") or "")[:4] or None),
               # full air date — daily series (Daily Show / Kimmel / soaps) release by
               # DATE, not SxxExx; the ranker + retry queries key off this.
               "air_date": (str(item.get("air_date") or "")[:10] or None)}
        # Series type (P8): daily/anime shows QUERY differently. Anime also carries
        # the wanted ABSOLUTE episode number (scene anime is numbered 'Show - 1071',
        # no season) — derived from the library's episode list, best-effort.
        stype = str(item.get("series_type") or "").strip().lower()
        if stype in ("daily", "anime"):
            ctx["series_type"] = stype
        if stype == "anime":
            try:
                from api.video import get_video_db
                ctx["absolute"] = get_video_db().episode_absolute_number(
                    item.get("show_tmdb_id"), item.get("season_number"),
                    item.get("episode_number"))
            except Exception:   # noqa: BLE001 - a numbering assist must never break a grab
                ctx["absolute"] = None
        tmdb_id, kind = item.get("show_tmdb_id"), "show"
    titles = _acceptable_titles(ctx["title"], kind, tmdb_id)
    if len(titles) > 1:
        ctx["titles"] = titles
    return ctx


def build_download_record(item: Dict[str, Any], best: Dict[str, Any], candidates: List[Dict[str, Any]],
                          *, media_type: str, target_dir: str, query: Any) -> Dict[str, Any]:
    """The ``add_video_download`` row for a chosen release — identical shape to a manual
    grab, so the monitor finishes it the same way (other accepted hits become the retry
    pool)."""
    ctx = search_context(item, media_type)
    # stash the chosen source's peer stats so the drawer can show its availability snapshot
    # (free slot / queue depth / speed at grab time). Retry ignores the extra key.
    peer = {k: best.get(k) for k in ("slots", "queue", "speed", "availability") if best.get(k) is not None}
    if peer:
        ctx = {**ctx, "peer": peer}
    media_id = str(item.get("tmdb_id") if media_type == "movie" else item.get("show_tmdb_id"))
    source = str(best.get("source") or "soulseek").lower()
    common = {
        "kind": media_type, "title": ctx["title"],
        "release_title": best.get("title") or best.get("filename"),
        "size_bytes": int(best.get("size_bytes") or 0), "quality_label": best.get("quality_label"),
        "target_dir": target_dir, "status": "downloading",
        "media_id": media_id, "media_source": "tmdb", "year": ctx.get("year"),
        "poster_url": item.get("poster_url"), "search_ctx": json.dumps(ctx), "attempts": 0,
        # the profile this grab was judged under — the monitor's cutoff/requery
        # decisions stay consistent even if the title is reassigned mid-flight
        "quality_profile_id": item.get("quality_profile_id"),
    }
    if source == "soulseek":
        rest = [c for c in (candidates or []) if c.get("filename") != best.get("filename")]
        return {**common, "source": "soulseek", "username": best.get("username"),
                "filename": best.get("filename"), "candidates": json.dumps(rest),
                "tried_queries": json.dumps([query] if query else []),
                "tried_files": json.dumps([best.get("filename")])}
    # torrent / usenet — tracked by the client ref the grab returned; no Soulseek requery pool.
    return {**common, "source": source, "username": best.get("username"),   # indexer (display)
            "filename": best.get("title") or best.get("filename"), "client_ref": best.get("_client_ref"),
            "candidates": json.dumps([]), "tried_queries": json.dumps([]), "tried_files": json.dumps([])}


# ── production seams ──────────────────────────────────────────────────────────
def _default_fetch_items(media_type: str) -> List[Dict[str, Any]]:
    from api.video import get_video_db
    db = get_video_db()
    return db.movie_wishlist_to_download() if media_type == "movie" else db.episode_wishlist_to_download()


def _backfill_movie_available_dates(limit: int = 25) -> None:
    """Resolve the DOWNLOADABLE (home/digital) date for wished movies that don't have one yet —
    TMDB digital/physical, or theatrical + a home-release window (Radarr's 'minimum availability
    = released'). This is what lets the drain SKIP a film that's still only in cinemas instead of
    grabbing a wrong/fake copy. Bounded per run + engine-cached; best-effort. A past-date sentinel
    is stored when TMDB knows nothing, so it isn't re-queried forever (the year check still guards)."""
    try:
        from api.video import get_video_db
        from core.video.enrichment.engine import get_video_enrichment_engine
        db = get_video_db()
        # One-time reset: an earlier version anchored the estimate on TMDB PREMIERE dates
        # (festival screenings months before release), so previously-derived dates are wrong.
        # Wipe them once and re-derive with the wide-theatrical logic.
        if db.get_setting("avail_dates_logic") != "v2":
            db.clear_wishlist_movie_release_dates()
            db.set_setting("avail_dates_logic", "v2")
        need = db.wishlist_movies_missing_release_date(limit)
        if not need:
            return
        eng = get_video_enrichment_engine()
        for tmdb_id in need:
            try:
                db.set_wishlist_release_date(tmdb_id, eng.movie_available_date(tmdb_id) or "1970-01-01")
            except Exception:   # noqa: BLE001 - one lookup failing shouldn't stall the rest
                logger.debug("available-date backfill failed for %s", tmdb_id, exc_info=True)
    except Exception:   # noqa: BLE001 - the backfill is an assist; never block the drain
        logger.debug("movie available-date backfill failed", exc_info=True)


def _default_active_keys(media_type: str) -> set:
    from api.video import get_video_db
    return active_download_keys(get_video_db().get_active_video_downloads())


def _default_target_dir(media_type: str) -> str:
    from api.video import get_video_db
    db = get_video_db()
    if media_type == "movie":
        return db.get_setting("movies_path") or db.get_setting("transfer_path") or ""
    return db.get_setting("tv_path") or ""


def _search_one_source(source: str, item: Dict[str, Any], media_type: str):
    """Search ONE source → (ranked candidates tagged with source, error). soulseek via slskd,
    torrent/usenet via Prowlarr. Returns (None, error) when the search couldn't run."""
    from api.video import get_video_db
    from api.video.downloads import _evaluate_hits
    from core.video.quality_profile import load_for_item
    ctx = search_context(item, media_type)
    profile = load_for_item(get_video_db(), item)   # per-title profile (P2)
    if source == "soulseek":
        from core.video.download_monitor import _search_for_retry
        from core.video.slskd_search import build_query
        query = build_query(ctx["scope"], ctx["title"], year=ctx.get("year"),
                            season=ctx.get("season"), episode=ctx.get("episode"),
                            air_date=ctx.get("air_date"), absolute=ctx.get("absolute"),
                            series_type=ctx.get("series_type"))
        res = _search_for_retry(query) or {}
        if res.get("started") is False:
            return None, res.get("error")
        hits = res.get("hits") or []
    elif source in ("torrent", "usenet"):
        from core.video.prowlarr_search import prowlarr_search
        pres = prowlarr_search(ctx["scope"], ctx["title"], year=ctx.get("year"),
                               season=ctx.get("season"), episode=ctx.get("episode"), source=source,
                               air_date=ctx.get("air_date"), absolute=ctx.get("absolute"),
                               series_type=ctx.get("series_type"))
        if not pres.get("configured"):
            return None, "Prowlarr not configured"
        if pres.get("error"):
            return None, pres["error"]
        hits = pres["hits"]
    else:
        return None, "unsupported source %r" % source
    cands = _evaluate_hits(hits, profile, ctx["scope"], ctx.get("season"), ctx.get("episode"),
                           want_year=ctx.get("year"),
                           want_title=ctx.get("titles") or ctx.get("title"),
                           want_date=ctx.get("air_date"), want_absolute=ctx.get("absolute"))
    for c in cands:
        c["source"] = source
    return cands, None


def _default_search(item: Dict[str, Any], media_type: str):
    """Ranked candidates for a wished item, honoring the download mode/order. In hybrid mode the
    sources are tried IN ORDER — the first that yields an ACCEPTED release wins (mirrors the
    music per-item quality-fallback). Returns [] for a real empty result across all sources, or
    **None** (with the error) if no source's search could even run.

    When SOME source in the chain couldn't run (e.g. torrent is first but Prowlarr
    isn't configured), that skip rides back in the error slot alongside the surviving
    results — silent degradation to a weaker source misled a whole run once ('why
    can't it find what's plainly on TPB?'), so the run log must say it every time."""
    from core.video import download_config
    from api.video import get_video_db
    cfg = download_config.load(get_video_db())
    mode = str(cfg.get("download_mode") or "soulseek")
    chain = (cfg.get("hybrid_order") or ["soulseek"]) if mode == "hybrid" else [mode]
    skips: List[str] = []
    fallback = None      # hits that didn't pass the profile — kept so the caller can say 'rejected'
    for src in chain:
        cands, err = _search_one_source(src, item, media_type)
        if cands is None:
            skips.append("%s skipped — %s" % (src, err or "search didn't run"))
            continue
        if any(c.get("accepted") for c in cands):
            return cands, None                       # first source with a usable release wins
        if cands:
            fallback = cands
    note = "; ".join(skips) or None
    if fallback is not None:
        return fallback, note                        # → 'rejected' (hits, none accepted)
    if len(skips) == len(chain):
        return None, note                            # → 'search didn't run' (nothing ran at all)
    return [], note                                  # → 'source empty' (+ any skip note)


def _default_enqueue(item: Dict[str, Any], best: Dict[str, Any], candidates: List[Dict[str, Any]],
                     media_type: str, target_dir: str) -> bool:
    """Start the slskd transfer + write the download row (exactly like the manual flow),
    then ensure the monitor is running. Returns True if slskd accepted it."""
    from api.video import get_video_db
    from core.video import disk_guard, organization
    from core.video.download_monitor import ensure_started
    from core.video.slskd_search import build_query
    ok_room, free = disk_guard.has_room(target_dir, organization.load(get_video_db()))
    if not ok_room:
        logger.warning("disk guard: %.1f GB free on %s — skipping grab of %s",
                       free or 0, target_dir, item.get("title"))
        return False
    source = str(best.get("source") or "soulseek").lower()
    if source == "soulseek":
        from core.video.slskd_download import start_download
        started = start_download(best.get("username"), best.get("filename"), best.get("size_bytes") or 0)
        if not started.get("ok"):
            return False
    else:
        # torrent / usenet — hand off to the shared client; carry the returned ref into the row.
        from core.video.client_grab import grab
        res = grab(source, best.get("download_url"))
        if not res.get("ok"):
            logger.warning("video hybrid: %s grab refused for %s: %s", source, item.get("title"), res.get("error"))
            return False
        best = {**best, "_client_ref": res["ref"]}
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
        if media_type == 'movie':
            _backfill_movie_available_dates()   # resolve downloadable dates so the gate can skip cinema-only films
        items = fetch_items(media_type) or []
        # Upgrade-until-cutoff: owned rows are judged against the profile cutoff
        # (skip when met; strictly-better-only when below). Only loaded when an
        # owned row is actually present — the common all-new case stays DB-free.
        if any(it.get("owned") for it in items):
            try:
                cutoff_rank = _default_cutoff_rank()
            except Exception:   # noqa: BLE001 - no profile → treat as no cutoff
                cutoff_rank = 0
            # per-title profiles: judge each owned item against ITS profile's
            # cutoff when any assignment exists (the common no-assignment case
            # stays on the single global read)
            per_item = _cutoff_rank_for_item if any(
                it.get("quality_profile_id") for it in items) else None
            items = annotate_upgrades(items, cutoff_rank, cutoff_for=per_item)
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
            found = search(it, media_type)
            # the seam returns (candidates, error); tolerate a bare list/None too (test fakes)
            cands, err = found if isinstance(found, tuple) else (found, None)
            didnt_run = cands is None       # slskd not configured / errored / rate-limited
            cands = cands or []
            best = pick_best(cands, it.get("_min_rank") or 0)
            ok = bool(best) and bool(enqueue(it, best, cands, media_type, root))
            name = it.get('title') or it.get('show_title') or '?'
            if media_type == 'episode':
                name = "%s S%02dE%02d" % (name, int(it.get('season_number') or 0),
                                          int(it.get('episode_number') or 0))
            # tell apart: grabbed / search-didn't-run / source-empty / hits-but-all-rejected.
            # `err` alongside RESULTS is a non-fatal note (a chain source was skipped,
            # e.g. 'torrent skipped — Prowlarr not configured') — always show it, or a
            # mis-configured first source silently degrades every search.
            if ok:
                msg, lt = "Grabbed '%s'" % name, 'success'
            elif didnt_run:
                msg = ("Search didn't run for '%s' — %s" % (name, err)) if err \
                    else ("Search didn't run for '%s' — slskd not responding?" % name)
                lt = 'warning'
            elif not cands:
                msg, lt = "No search results for '%s'" % name, 'info'
                if err:
                    msg, lt = msg + " · " + str(err), 'warning'
            elif it.get('_min_rank'):
                msg = ("%d result(s) for '%s', none better than your current copy — "
                       "still watching for an upgrade" % (len(cands), name))
                lt = 'info'
            else:
                why = (cands[0].get('rejected') or 'none met your quality profile')
                msg, lt = "%d result(s) for '%s', none accepted — %s" % (len(cands), name, why), 'info'
                if err:
                    msg, lt = msg + " · " + str(err), 'warning'
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
