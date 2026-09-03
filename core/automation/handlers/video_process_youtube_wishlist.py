"""Automation handler: ``video_process_youtube_wishlist`` action.

The drain side of the YouTube fulfillment lane. The watchlist-channels scan keeps the
wishlist fed; THIS queues wished videos for download and keeps a few flowing at a time.

There is NO cap on how much of the wishlist gets processed — it queues the WHOLE thing.
The only limit is how many download SIMULTANEOUSLY (``max_concurrent``, default 3): every
wished video becomes a ``queued`` row in the shared ``video_downloads`` table, the handler
starts up to the limit, and each finished download starts the next (one-out-one-in, in the
worker) so the entire queue drains in a controlled stream. This mirrors the music side's
download worker — a concurrency cap plus a small inter-download delay (handled in the
worker) to avoid yt-dlp 429s — but stays on the isolated video side.

The worker (``core.video.youtube_download``) downloads → organises (channel/year/date) →
archives to history → removes the video from the wishlist, so a completed grab leaves the
wishlist and won't be re-queued; videos already queued or downloading are skipped so re-runs
never double-grab.

Shared automation side (may import ``core.video`` / ``api.video``); owns its own progress.
All I/O is injected as seams, so selection + the pump are pure unit-testable functions.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Optional

from core.automation.deps import AutomationDeps
from core.video.wishlist_backoff import retry_delay_hours
from utils.logging_config import get_logger

logger = get_logger("automation.video_process_youtube_wishlist")


YT_MAX_FAIL = 3   # start applying retry backoff after this many failed attempts
_RETRY_POLICIES = {"default", "aggressive", "manual"}


def _retry_policy(settings: Optional[Dict[str, Any]]) -> str:
    p = str((settings or {}).get("retry_policy") or "default").strip().lower()
    return p if p in _RETRY_POLICIES else "default"


def _source_settings_for_video(video: Dict[str, Any], source_settings=None) -> Dict[str, Any]:
    if not source_settings:
        return {}
    if callable(source_settings):
        try:
            return source_settings(video.get("channel_id"), video) or {}
        except TypeError:
            return source_settings(video.get("channel_id")) or {}
    if isinstance(source_settings, dict):
        return source_settings.get(video.get("channel_id")) or source_settings.get(video.get("video_id")) or {}
    return {}


def _retry_verdict(state: Optional[Dict[str, Any]], max_fail: int, policy: str = "default") -> str:
    """``'go' | 'permanent' | 'waiting'`` for one video's failure history.

    Only a GONE failure is permanent - a deleted or members-only video will not
    un-delete, and retrying it hourly learns nothing. Everything else BACKS OFF
    on the same schedule the movie/TV wishlist uses: the wait doubles and caps at
    a week, but the video is never written off. Pure."""
    policy = policy if policy in _RETRY_POLICIES else "default"
    if not isinstance(state, dict):
        return "go"
    if state.get("permanent"):
        return "permanent"
    strikes = int(state.get("strikes", 0) or 0)
    if policy == "aggressive":
        return "go"
    if policy == "manual" and strikes > 0:
        return "waiting"
    if strikes < max_fail:
        return "go"
    wait = retry_delay_hours(strikes)
    since = state.get("hours_since_last")
    if since is None or float(since) >= wait:
        return "go"
    return "waiting"


def videos_to_enqueue(wanted: List[Dict[str, Any]], already_ids: Iterable,
                      retry_state: Optional[Dict[Any, Dict[str, Any]]] = None,
                      max_fail: int = YT_MAX_FAIL, source_settings=None) -> List[Dict[str, Any]]:
    """Wished videos ready to queue: not already queued/downloading, not permanently
    unavailable, and not still inside their retry backoff. No concurrency cap here
    (bounded at start time). Pure."""
    already = {str(x) for x in (already_ids or ()) if x}
    states = retry_state or {}
    out: List[Dict[str, Any]] = []
    for v in wanted or []:
        vid = v.get("video_id")
        if not vid or str(vid) in already:
            continue
        settings = _source_settings_for_video(v, source_settings)
        if _retry_verdict(states.get(vid), max_fail, _retry_policy(settings)) != "go":
            continue
        out.append(v)
    return out


def skip_tally(wanted: List[Dict[str, Any]], already_ids: Iterable,
               retry_state: Optional[Dict[Any, Dict[str, Any]]] = None,
               max_fail: int = YT_MAX_FAIL, source_settings=None) -> Dict[str, int]:
    """How many wished videos each reason accounts for, so the run can SAY what it
    did instead of reporting an empty wishlist. Pure."""
    already = {str(x) for x in (already_ids or ()) if x}
    states = retry_state or {}
    tally = {"ready": 0, "permanent": 0, "waiting": 0, "in_flight": 0, "no_id": 0}
    for v in wanted or []:
        vid = v.get("video_id")
        if not vid:
            tally["no_id"] += 1
        elif str(vid) in already:
            tally["in_flight"] += 1
        else:
            settings = _source_settings_for_video(v, source_settings)
            verdict = _retry_verdict(states.get(vid), max_fail, _retry_policy(settings))
            tally["ready" if verdict == "go" else verdict] += 1
    return tally


def videos_for_manual_run(wanted: List[Dict[str, Any]], already_ids: Iterable,
                         retry_state: Optional[Dict[Any, Dict[str, Any]]] = None,
                         max_fail: int = YT_MAX_FAIL) -> List[Dict[str, Any]]:
    """What a user's "download all waiting" should queue.

    The same list as :func:`videos_to_enqueue` except the retry backoff does not
    apply - the wait paces the hourly tick, and a person clicking out-ranks it,
    exactly as "Search all missing" out-ranks the movie/TV drain's gates.

    Permanently unavailable videos are still skipped. Re-requesting six deleted
    videos on every click is not an override, it is a treadmill; the per-row
    button is there for the one case where the user thinks a video is back.
    Pure."""
    already = {str(x) for x in (already_ids or ()) if x}
    states = retry_state or {}
    out: List[Dict[str, Any]] = []
    for v in wanted or []:
        vid = v.get("video_id")
        if not vid or str(vid) in already:
            continue
        if _retry_verdict(states.get(vid), max_fail) == "permanent":
            continue
        out.append(v)
    return out


def slots_free(running: int, max_concurrent: int) -> int:
    """How many new downloads may start now given how many are already fetching. Pure."""
    return max(0, int(max_concurrent) - max(0, int(running)))


def enqueue_ctx(video: Dict[str, Any], channel_settings: Dict[str, Any]) -> Dict[str, Any]:
    """The download row's ``search_ctx``, applying per-channel overrides: a custom show-name
    (the ``$channel`` folder token) and/or a quality override. Pure."""
    cs = channel_settings if isinstance(channel_settings, dict) else {}
    ctx = {
        "channel": cs.get("custom_name") or video.get("channel_title"),
        "channel_id": video.get("channel_id"),   # so the drawer can open the in-app channel page
        "video_title": video.get("video_title"),
        "published_at": video.get("published_at"),
    }
    if cs.get("quality"):
        ctx["quality"] = cs["quality"]
    return ctx


def _published_year(video: Dict[str, Any]) -> Optional[int]:
    raw = str((video or {}).get("published_at") or "")[:10]
    if len(raw) < 4:
        return None
    try:
        year = int(raw[:4])
    except (TypeError, ValueError):
        return None
    return year if 1900 <= year <= 2200 else None


def youtube_alternate_search_item(video: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """A conservative pseudo-movie identity for alternate transports.

    Torrent/Soulseek/Usenet mirrors of YouTube videos are usually named by channel
    plus upload title, so only search when we have that pair and a real publish year.
    That keeps native YouTube first, and avoids spraying weak title-only searches
    across every configured source. Pure."""
    vid = str((video or {}).get("video_id") or "").strip()
    channel = str((video or {}).get("channel_title") or "").strip()
    title = str((video or {}).get("video_title") or "").strip()
    year = _published_year(video)
    if not (vid and channel and title and year):
        return None
    if len(title) < 4 or title.lower() in {"untitled", "live", "stream"}:
        return None
    return {
        "title": "%s %s" % (channel, title),
        "year": year,
        "poster_url": video.get("thumbnail_url"),
        "_youtube_video": dict(video),
    }


def videos_for_alternate_search(wanted: List[Dict[str, Any]], already_ids: Iterable,
                                retry_state: Optional[Dict[Any, Dict[str, Any]]] = None,
                                max_fail: int = YT_MAX_FAIL, source_settings=None) -> List[Dict[str, Any]]:
    """Videos eligible for alternate transports: already tried natively and now
    waiting on backoff, still wished, and strong enough to search by channel/title/year.
    Pure."""
    already = {str(x) for x in (already_ids or ()) if x}
    states = retry_state or {}
    out: List[Dict[str, Any]] = []
    for v in wanted or []:
        vid = v.get("video_id")
        if not vid or str(vid) in already:
            continue
        settings = _source_settings_for_video(v, source_settings)
        verdict = _retry_verdict(states.get(vid), max_fail, _retry_policy(settings))
        if verdict == "waiting" and youtube_alternate_search_item(v):
            out.append(v)
    return out


# ── production seams ──────────────────────────────────────────────────────────
def _default_youtube_root() -> str:
    from api.video import get_video_db
    return get_video_db().get_setting("youtube_path") or ""


def _default_fetch_wanted() -> List[Dict[str, Any]]:
    from api.video import get_video_db
    return get_video_db().youtube_wishlist_to_download()


def _default_clear_completed_wishlist() -> int:
    from api.video import get_video_db
    try:
        return int(get_video_db().clear_completed_youtube_from_wishlist() or 0)
    except Exception:   # noqa: BLE001 - cleanup assists the drain; it must not block it
        logger.debug("clear_completed_youtube_from_wishlist failed", exc_info=True)
        return 0


def _default_active_ids() -> List[Any]:
    from api.video import get_video_db
    return [d.get("media_id") for d in get_video_db().get_active_video_downloads()
            if d.get("media_id") and (d.get("source") == "youtube" or d.get("kind") == "youtube")]


def _default_retry_state() -> Dict[Any, Dict[str, Any]]:
    from api.video import get_video_db
    return get_video_db().youtube_retry_state(max_fail=YT_MAX_FAIL)


def _default_source_settings(source_id: Any) -> Dict[str, Any]:
    from api.video import get_video_db
    return get_video_db().get_youtube_source_settings(source_id)


def _default_recent_errors(days: int = 3) -> list:
    """Failure texts from the last few days, for the stale-yt-dlp check."""
    from api.video import get_video_db
    try:
        return get_video_db().youtube_recent_failure_errors(days=days)
    except Exception:   # noqa: BLE001 - a diagnostic must never break the drain
        return []


def _warn_if_ytdlp_is_stale(deps, automation_id, recent_errors) -> None:
    """Say it ONCE, plainly, instead of leaving a pile of identical 'Forbidden'
    rows. A cluster of these across different channels is not a coincidence — it is
    YouTube's bot-detection having moved on while yt-dlp stayed still, and the fix
    is a package update the user has to run. Left unsaid, each of those videos
    quietly burns its three attempts and is skipped forever for a fixable reason."""
    try:
        from core.video.youtube_errors import looks_like_stale_ytdlp
        errs = list(recent_errors() or []) if callable(recent_errors) else list(recent_errors or [])
        if not looks_like_stale_ytdlp(errs):
            return
        msg = ("YouTube refused %d recent downloads — this is almost always an "
               "out-of-date yt-dlp. Update it with: pip install -U yt-dlp"
               % sum(1 for e in errs if _is_blocked(e)))
        logger.warning(msg)
        deps.update_progress(automation_id, log_line=msg, log_type='warning')
    except Exception:   # noqa: BLE001 - diagnostics never break the drain
        logger.debug("stale-yt-dlp check failed", exc_info=True)


def _is_blocked(err) -> bool:
    from core.video.youtube_errors import BLOCKED, classify
    return classify(err) == BLOCKED


def _default_running_count() -> int:
    from api.video import get_video_db
    return get_video_db().count_active_youtube_downloads()



def _default_alternate_search(video: Dict[str, Any]):
    from core.automation.handlers.video_process_wishlist import _default_search
    item = youtube_alternate_search_item(video)
    if not item:
        return [], "not enough YouTube metadata for alternate search"
    return _default_search(item, "movie")


def _default_enqueue_alternate(video: Dict[str, Any], best: Dict[str, Any],
                               candidates: List[Dict[str, Any]], root: str) -> Dict[str, Any]:
    """Start an alternate transport for a YouTube video while preserving YouTube
    identity/import placement. Returns ``{ok, error}``."""
    import json
    from api.video import get_video_db
    from core.video import disk_guard, organization
    from core.video.download_monitor import ensure_started
    db = get_video_db()
    room = disk_guard.check_room(root, organization.load(db))
    if not room["ok"]:
        return {"ok": False, "error": disk_guard.shortfall_message(room, root)}
    source = str(best.get("source") or "soulseek").lower()
    transport_source = "torrent" if source == "extto" else source
    if transport_source == "soulseek":
        from core.video.slskd_download import start_download
        started = start_download(best.get("username"), best.get("filename"), best.get("size_bytes") or 0)
        if not started.get("ok"):
            return {"ok": False, "error": started.get("error") or "Soulseek refused the transfer"}
    else:
        from core.video.client_grab import grab
        res = grab(transport_source, best.get("download_url"), fallback_magnet=best.get("magnet_uri"))
        if not res.get("ok"):
            return {"ok": False, "error": res.get("error") or "the download client refused it"}
        best = {**best, "_client_ref": res["ref"]}
    settings = db.get_youtube_source_settings(video.get("channel_id"))
    ctx = enqueue_ctx(video, settings)
    ctx.update({
        "scope": "youtube",
        "title": video.get("video_title"),
        "youtube_id": video.get("video_id"),
        "alternate_transport": transport_source,
        "alternate_source": source,
    })
    rest = [c for c in (candidates or []) if c is not best and c.get("filename") != best.get("filename")]
    db.add_video_download({
        "kind": "youtube", "source": transport_source, "media_source": "youtube",
        "title": video.get("video_title") or video.get("channel_title"),
        "release_title": best.get("title") or best.get("filename"),
        "size_bytes": int(best.get("size_bytes") or 0),
        "quality_label": best.get("quality_label"), "target_dir": root,
        "status": "downloading", "media_id": video.get("video_id"),
        "year": video.get("published_at"), "poster_url": video.get("thumbnail_url"),
        "search_ctx": json.dumps(ctx), "attempts": 0,
        "username": best.get("username"),
        "filename": best.get("filename") or best.get("title"),
        "indexer_id": best.get("indexer_id"),
        "client_ref": best.get("_client_ref"),
        "candidates": json.dumps(rest if transport_source == "soulseek" else []),
        "tried_queries": json.dumps(["%s %s" % (video.get("channel_title") or "", video.get("video_title") or "")]),
        "tried_files": json.dumps([best.get("filename") or best.get("title")]),
    })
    ensure_started(get_video_db)
    return {"ok": True, "error": None}

def _default_enqueue(video: Dict[str, Any], root: str) -> Any:
    """Create a QUEUED download row (no thread spawned here — the pump starts it). Returns
    the row id."""
    import json
    from api.video import get_video_db
    from core.video import disk_guard, organization
    from core.video.sources import resolve_video_server
    db = get_video_db()
    room = disk_guard.check_room(root, organization.load(db))
    if not room["ok"]:
        logger.warning("disk guard: %s — not queuing %s",
                       disk_guard.shortfall_message(room, root), video.get("video_title"))
        return None
    ctx = enqueue_ctx(video, db.get_youtube_source_settings(video.get("channel_id")))
    ctx["server_source"] = resolve_video_server()
    return db.add_video_download({
        "kind": "youtube", "source": "youtube", "media_source": "youtube",
        "title": video.get("video_title") or video.get("channel_title"),
        "media_id": video.get("video_id"), "target_dir": root, "status": "queued",
        "year": video.get("published_at"), "poster_url": video.get("thumbnail_url"),
        "search_ctx": json.dumps(ctx),
    })


def _default_start_next() -> Any:
    """Claim + start the next queued YouTube download (or None). The worker chains the rest."""
    from api.video import get_video_db
    from core.video.youtube_download import start_next_queued
    return start_next_queued(get_video_db)


def _default_reap() -> int:
    """Recover downloads orphaned by a restart (stuck 'downloading', no live worker)."""
    from api.video import get_video_db
    from core.video.youtube_download import requeue_orphaned_youtube
    return requeue_orphaned_youtube(get_video_db)


def auto_video_process_youtube_wishlist(
    config: Dict[str, Any],
    deps: AutomationDeps,
    *,
    youtube_root: Optional[Callable[[], str]] = None,
    fetch_wanted: Optional[Callable[[], List[Dict[str, Any]]]] = None,
    clear_completed_wishlist: Optional[Callable[[], int]] = None,
    active_ids: Optional[Callable[[], Iterable]] = None,
    running_count: Optional[Callable[[], int]] = None,
    enqueue: Optional[Callable[[Dict[str, Any], str], Any]] = None,
    alternate_search: Optional[Callable[[Dict[str, Any]], Any]] = None,
    alternate_enqueue: Optional[Callable[[Dict[str, Any], Dict[str, Any], List[Dict[str, Any]], str], Dict[str, Any]]] = None,
    start_next: Optional[Callable[[], Any]] = None,
    reap: Optional[Callable[[], int]] = None,
    retry_state: Optional[Callable[[], Dict[Any, Dict[str, Any]]]] = None,
    source_settings: Optional[Callable[..., Dict[str, Any]]] = None,
    recent_errors: Optional[Callable[[], List[Any]]] = None,
) -> Dict[str, Any]:
    """Queue the whole YouTube wishlist for download and start up to ``max_concurrent`` now.

    Returns ``{'status': 'completed', 'queued': int, 'started': int, 'running': int, ...}``."""
    youtube_root = youtube_root or _default_youtube_root
    fetch_wanted = fetch_wanted or _default_fetch_wanted
    clear_completed_wishlist = clear_completed_wishlist or _default_clear_completed_wishlist
    active_ids = active_ids or _default_active_ids
    running_count = running_count or _default_running_count
    enqueue = enqueue or _default_enqueue
    alternate_search = alternate_search or _default_alternate_search
    alternate_enqueue = alternate_enqueue or _default_enqueue_alternate
    start_next = start_next or _default_start_next
    reap = reap or _default_reap
    retry_state = retry_state or _default_retry_state
    source_settings = source_settings or _default_source_settings
    recent_errors = recent_errors or _default_recent_errors
    automation_id = config.get('_automation_id')
    max_concurrent = max(1, int(config.get('max_concurrent', 3) or 3))

    try:
        root = youtube_root()
        if not root:
            # Always-on automation: a missing folder isn't a failure, it's "not set up for
            # YouTube" — skip quietly so non-YouTube users don't see a recurring error.
            deps.update_progress(automation_id, status='finished', progress=100, phase='Complete',
                                 log_line='YouTube library folder not set — skipping (Settings → Downloads)',
                                 log_type='info')
            return {'status': 'completed', 'queued': 0, 'started': 0, 'running': 0,
                    'skipped': 'no_youtube_folder', '_manages_own_progress': True}

        # Recover any downloads orphaned by a restart (stuck 'downloading', no worker) so
        # they don't wedge the concurrency count — they go back to 'queued' and re-run.
        recovered = int(reap() or 0)
        if recovered:
            deps.update_progress(automation_id, log_type='info',
                                 log_line='Recovered %d stalled download(s) from a restart' % recovered)

        cleared = int(clear_completed_wishlist() or 0)
        if cleared:
            deps.update_progress(automation_id, log_type='info',
                                 log_line='Cleared %d already-downloaded YouTube wishlist item(s)' % cleared)

        deps.update_progress(automation_id, phase='Checking the YouTube wishlist…', progress=15,
                             log_line='Queueing new videos for download', log_type='info')
        wanted = fetch_wanted() or []
        already = list(active_ids() or [])
        states = retry_state() or {}
        new = videos_to_enqueue(wanted, already, states, source_settings=source_settings)
        tally = skip_tally(wanted, already, states, source_settings=source_settings)
        _warn_if_ytdlp_is_stale(deps, automation_id, recent_errors)

        queued = 0
        alternate_queued = 0
        alternate_searched = 0
        refused = 0
        for v in new:
            try:
                if enqueue(v, root) is not None:
                    queued += 1
                else:
                    # The only None is the disk guard, which logs to app.log
                    # only - the run has to account for it or the user sees
                    # 'nothing to download' beside a full wishlist.
                    refused += 1
            except Exception:   # noqa: BLE001 - one bad enqueue shouldn't stop the rest
                deps.update_progress(automation_id, log_type='warning',
                                     log_line="Couldn't queue '%s'" % (v.get('video_title') or v.get('video_id')))

        # Native YouTube stays first. Alternate transports only step in for rows that
        # already hit native retry backoff and have enough metadata to search safely.
        try:
            from core.automation.handlers.video_process_wishlist import pick_best
            alt_limit = max(0, int(config.get("max_alternate_searches", 3) or 0))
            alt_ready = videos_for_alternate_search(wanted, already, states, source_settings=source_settings)[:alt_limit]
            for v in alt_ready:
                alternate_searched += 1
                cands, err = alternate_search(v)
                if cands is None:
                    deps.update_progress(automation_id, log_type='warning',
                                         log_line="Alternate search skipped for '%s': %s"
                                         % (v.get('video_title') or v.get('video_id'), err or "search didn't run"))
                    continue
                best = pick_best(cands or [])
                if not best:
                    continue
                res = alternate_enqueue(v, best, cands or [], root) or {}
                if res.get("ok"):
                    queued += 1
                    alternate_queued += 1
                else:
                    refused += 1
                    deps.update_progress(automation_id, log_type='warning',
                                         log_line="Alternate transport refused '%s': %s"
                                         % (v.get('video_title') or v.get('video_id'), res.get("error") or "client refused it"))
        except Exception:   # noqa: BLE001 - alternate transports must never stop native queueing
            logger.exception("youtube alternate fallback pass failed")

        # Fill the concurrency slots now; each finished download starts the next, so the
        # whole queue drains on its own from here.
        deps.update_progress(automation_id, phase='Starting downloads…', progress=70,
                             log_line='Queued %d new video(s)' % queued, log_type='info')
        started = 0
        for _ in range(slots_free(running_count() or 0, max_concurrent)):
            if start_next() is None:
                break
            started += 1

        running = (running_count() or 0)
        if queued or started:
            alt = (' including %d alternate transport' % alternate_queued) if alternate_queued else ''
            done = 'Queued %d new%s - %d native downloading now (the rest drain automatically)' % (queued, alt, running)
            log_type = 'success'
        elif running:
            done = '%d already downloading; nothing new to queue' % running
            log_type = 'info'
        elif refused:
            done = ('Nothing queued - %d video(s) held back by the disk-space guard '
                    '(free space is under the floor in Settings > Downloads)' % refused)
            log_type = 'warning'
        elif wanted:
            # THE report that was missing. 'No wished videos' while seventeen
            # sit on the wishlist is why this read as broken rather than as
            # waiting, and it is the whole of the bug report.
            parts = []
            if tally['permanent']:
                parts.append('%d unavailable (deleted, private or members-only)'
                             % tally['permanent'])
            if tally['waiting']:
                parts.append('%d waiting out a retry backoff after repeated failures'
                             % tally['waiting'])
            if tally['in_flight']:
                parts.append('%d already queued' % tally['in_flight'])
            if tally['no_id']:
                parts.append('%d with no video id' % tally['no_id'])
            done = '%d wished video(s), none ready right now - %s' % (
                len(wanted), '; '.join(parts) or 'nothing eligible')
            log_type = 'warning' if (tally['permanent'] or tally['waiting']) else 'info'
        else:
            done = 'No wished YouTube videos to download'
            log_type = 'info'
        deps.update_progress(automation_id, status='finished', progress=100, phase='Complete',
                             log_line=done, log_type=log_type)
        return {'status': 'completed', 'queued': queued, 'started': started, 'running': running,
                'alternate_queued': alternate_queued, 'alternate_searched': alternate_searched,
                'skipped': tally, '_manages_own_progress': True}
    except Exception as e:  # noqa: BLE001
        deps.update_progress(automation_id, status='error', phase='Error', log_line=str(e), log_type='error')
        return {'status': 'error', 'error': str(e), '_manages_own_progress': True}
