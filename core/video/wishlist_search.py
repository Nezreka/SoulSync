"""User-initiated wishlist searches — the manual 'Search now' the drain never had.

Two entry points, both non-blocking (a Soulseek search is ~20s; the endpoint
returns immediately and the download monitor + badge polling surface progress):

  · manual_search(scope, tmdb_id, ...) — ONE wished movie / episode / season /
    show, straight through the drain's own search → pick → enqueue seams, but
    WITHOUT the release-window gate (the click is the override, like Sonarr's
    manual search). A user-triggered row carries an import policy: equal-or-better
    valid files replace the current copy; lower-quality files are allowed to land
    beside it as an intentional recovery/alternate copy.

  · search_all() — the whole eligible wishlist NOW (movies + episodes), gates
    intact: this is "don't wait for the hourly tick", not "hunt unreleased
    films". Takes the drain's own overlap guard so a manual run and the hourly
    tick can never double-search.

Everything acquisition-shaped is reused from the drain handler — this module
adds only dispatch, de-dupe and the in-flight bookkeeping.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List

from core.automation.handlers import video_process_wishlist as vpw
from utils.logging_config import get_logger

logger = get_logger("video.wishlist_search")

_lock = threading.Lock()
_inflight: set = set()          # item keys a manual search is currently working


def _cutoff_rank() -> int:
    try:
        return vpw._default_cutoff_rank()
    except Exception:   # noqa: BLE001 - no profile → no cutoff
        return 0


def _prepare(items: List[Dict[str, Any]], media_type: str, *, user_initiated: bool = False) -> List[Dict[str, Any]]:
    """The drain's pre-flight over raw wishlist rows: upgrade-until-cutoff
    judging for owned rows, then de-dupe against active downloads AND other
    in-flight manual searches. Marks survivors in-flight (caller must _finish).

    A direct user click means "go get this", so it bypasses the owned/cutoff skip
    that protects the background drain from pointless equal-quality churn."""
    if any(it.get("owned") for it in items) and not user_initiated:
        per_item = vpw._cutoff_rank_for_item if any(
            it.get("quality_profile_id") for it in items) else None
        items = vpw.annotate_upgrades(items, _cutoff_rank(), cutoff_for=per_item)
    active = set(vpw._default_active_keys(media_type) or set())
    todo = []
    with _lock:
        for it in items:
            k = vpw.item_key(it, media_type)
            if k in active or k in _inflight:
                continue
            _inflight.add(k)
            todo.append(it)
    return todo


def _finish(items: List[Dict[str, Any]], media_type: str) -> None:
    with _lock:
        for it in items:
            _inflight.discard(vpw.item_key(it, media_type))


def _all_source_search(item: Dict[str, Any], media_type: str):
    """Search every video source for a user retry, not just the configured first winner."""
    chain = ["torrent", "usenet", "soulseek"]
    snapshot: Dict[str, Any] = {"chain": chain, "sources": {}}
    item["_source_snapshot"] = snapshot
    out: List[Dict[str, Any]] = []
    skips: List[str] = []
    for src in chain:
        cands, err = vpw._search_one_source(src, item, media_type)
        snapshot["sources"][src] = vpw.source_outcome(cands, err)
        if cands is None:
            skips.append("%s skipped — %s" % (src, err or "search didn't run"))
            continue
        out.extend(cands or [])
    out.sort(key=lambda c: (1 if c.get("accepted") else 0, int(c.get("score") or 0), c.get("_avail") or 0),
             reverse=True)
    if out:
        return out, "; ".join(skips) or None
    if len(skips) == len(chain):
        return None, "; ".join(skips) or None
    return [], "; ".join(skips) or None


def _one(item: Dict[str, Any], media_type: str, target: str, *, all_sources: bool = False) -> bool:
    try:
        item = {**item, "_user_initiated": True}
        found = _all_source_search(item, media_type) if all_sources else vpw._default_search(item, media_type)
        cands = found[0] if isinstance(found, tuple) else found
        best = vpw.pick_best(cands or [], int(item.get("_min_rank") or 0))
        if not best:
            logger.info("manual search: no acceptable release for %s",
                        item.get("title") or item.get("show_title"))
            return False
        return bool(vpw._default_enqueue(item, best, cands or [], media_type, target))
    except Exception:   # noqa: BLE001 - one item failing must not kill the batch
        logger.exception("manual wishlist search failed for %r",
                         item.get("title") or item.get("show_title"))
        return False


def _run_batch(todo: List[Dict[str, Any]], media_type: str, *, all_sources: bool = False) -> None:
    try:
        target = vpw._default_target_dir(media_type)
        if not target:
            logger.warning("manual search: no %s library folder set — dropping %d item(s)",
                           media_type, len(todo))
            return
        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="wl-manual") as ex:
            grabbed = sum(1 for ok in ex.map(lambda it: _one(it, media_type, target, all_sources=all_sources), todo) if ok)
        logger.info("manual wishlist search: %d/%d %s item(s) grabbed", grabbed, len(todo), media_type)
    finally:
        _finish(todo, media_type)


def manual_search(scope: str, tmdb_id, season_number=None, episode_number=None,
                  *, all_sources: bool = False) -> Dict[str, Any]:
    """Kick a background search for one wished item (or a season/show of
    episodes). Returns immediately: {queued, skipped, total}. 'skipped' =
    already downloading / already being searched."""
    from api.video import get_video_db
    media_type = "movie" if scope == "movie" else "episode"
    items = get_video_db().wishlist_manual_search_items(
        scope, tmdb_id, season_number=season_number, episode_number=episode_number)
    if not items:
        return {"queued": 0, "skipped": 0, "total": 0}
    todo = _prepare(items, media_type, user_initiated=True)
    if todo and not vpw._default_target_dir(media_type):
        _finish(todo, media_type)
        return {"queued": 0, "skipped": len(items) - len(todo), "total": len(items),
                "missing_target": media_type}
    if todo:
        threading.Thread(target=_run_batch, args=(todo, media_type), kwargs={"all_sources": all_sources},
                         daemon=True, name="wishlist-manual-search").start()
    return {"queued": len(todo), "skipped": len(items) - len(todo), "total": len(items)}


def search_all() -> Dict[str, str]:
    """Run the full (gated) wishlist drain NOW for both kinds, in the background.
    Per kind: 'started', 'busy' (a drain tick is already running), or 'empty'."""
    from api.video import get_video_db
    db = get_video_db()
    out: Dict[str, str] = {}
    # due_only=False: the user asked for everything NOW. Backoff paces the
    # machine's own hourly tick, it does not overrule a person clicking search.
    for media_type, fetch in (("movie", lambda: db.movie_wishlist_to_download(due_only=False)),
                              ("episode", lambda: db.episode_wishlist_to_download(due_only=False))):
        if vpw.is_running(media_type):
            out[media_type] = "busy"
            continue
        if not vpw._default_target_dir(media_type):
            out[media_type] = "unconfigured"
            continue
        if media_type == "movie":
            # same pre-flight as the drain: resolve availability dates so the
            # release-window gate can skip cinema-only films
            vpw._backfill_movie_available_dates()
        items = fetch() or []
        todo = _prepare(items, media_type, user_initiated=True)
        if not todo:
            out[media_type] = "empty"
            continue

        def _guarded(todo=todo, media_type=media_type):
            # take the drain's own guard so the hourly tick skips while we work
            if vpw._running.get(media_type):
                _finish(todo, media_type)
                return
            vpw._running[media_type] = True
            try:
                _run_batch(todo, media_type)
            finally:
                vpw._running[media_type] = False

        threading.Thread(target=_guarded, daemon=True, name="wishlist-search-all").start()
        out[media_type] = "started"
    return out
