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


YT_MAX_FAIL = 3   # give up re-grabbing a video after this many failed attempts


def _retry_verdict(state: Optional[Dict[str, Any]], max_fail: int) -> str:
    """``'go' | 'permanent' | 'waiting'`` for one video's failure history.

    Only a GONE failure is permanent - a deleted or members-only video will not
    un-delete, and retrying it hourly learns nothing. Everything else BACKS OFF
    on the same schedule the movie/TV wishlist uses: the wait doubles and caps at
    a week, but the video is never written off. Pure."""
    if not isinstance(state, dict):
        return "go"
    if state.get("permanent"):
        return "permanent"
    strikes = int(state.get("strikes", 0) or 0)
    if strikes < max_fail:
        return "go"
    wait = retry_delay_hours(strikes)
    since = state.get("hours_since_last")
    if since is None or float(since) >= wait:
        return "go"
    return "waiting"


def videos_to_enqueue(wanted: List[Dict[str, Any]], already_ids: Iterable,
                      retry_state: Optional[Dict[Any, Dict[str, Any]]] = None,
                      max_fail: int = YT_MAX_FAIL) -> List[Dict[str, Any]]:
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
        if _retry_verdict(states.get(vid), max_fail) != "go":
            continue
        out.append(v)
    return out


def skip_tally(wanted: List[Dict[str, Any]], already_ids: Iterable,
               retry_state: Optional[Dict[Any, Dict[str, Any]]] = None,
               max_fail: int = YT_MAX_FAIL) -> Dict[str, int]:
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
            verdict = _retry_verdict(states.get(vid), max_fail)
            tally["ready" if verdict == "go" else verdict] += 1
    return tally


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


# ── production seams ──────────────────────────────────────────────────────────
def _default_youtube_root() -> str:
    from api.video import get_video_db
    return get_video_db().get_setting("youtube_path") or ""


def _default_fetch_wanted() -> List[Dict[str, Any]]:
    from api.video import get_video_db
    return get_video_db().youtube_wishlist_to_download()


def _default_active_ids() -> List[Any]:
    from api.video import get_video_db
    return [d.get("media_id") for d in get_video_db().get_active_video_downloads()
            if d.get("source") == "youtube" and d.get("media_id")]


def _default_retry_state() -> Dict[Any, Dict[str, Any]]:
    from api.video import get_video_db
    return get_video_db().youtube_retry_state(max_fail=YT_MAX_FAIL)


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


def _default_enqueue(video: Dict[str, Any], root: str) -> Any:
    """Create a QUEUED download row (no thread spawned here — the pump starts it). Returns
    the row id."""
    import json
    from api.video import get_video_db
    from core.video import disk_guard, organization
    from core.video.sources import resolve_video_server
    db = get_video_db()
    ok_room, free = disk_guard.has_room(root, organization.load(db))
    if not ok_room:
        logger.warning("disk guard: %.1f GB free on %s — not queuing %s",
                       free or 0, root, video.get("video_title"))
        return None
    ctx = enqueue_ctx(video, db.get_channel_settings(video.get("channel_id")))
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
    active_ids: Optional[Callable[[], Iterable]] = None,
    running_count: Optional[Callable[[], int]] = None,
    enqueue: Optional[Callable[[Dict[str, Any], str], Any]] = None,
    start_next: Optional[Callable[[], Any]] = None,
    reap: Optional[Callable[[], int]] = None,
    retry_state: Optional[Callable[[], Dict[Any, Dict[str, Any]]]] = None,
    recent_errors: Optional[Callable[[], List[Any]]] = None,
) -> Dict[str, Any]:
    """Queue the whole YouTube wishlist for download and start up to ``max_concurrent`` now.

    Returns ``{'status': 'completed', 'queued': int, 'started': int, 'running': int, ...}``."""
    youtube_root = youtube_root or _default_youtube_root
    fetch_wanted = fetch_wanted or _default_fetch_wanted
    active_ids = active_ids or _default_active_ids
    running_count = running_count or _default_running_count
    enqueue = enqueue or _default_enqueue
    start_next = start_next or _default_start_next
    reap = reap or _default_reap
    retry_state = retry_state or _default_retry_state
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

        deps.update_progress(automation_id, phase='Checking the YouTube wishlist…', progress=15,
                             log_line='Queueing new videos for download', log_type='info')
        wanted = fetch_wanted() or []
        already = list(active_ids() or [])
        states = retry_state() or {}
        new = videos_to_enqueue(wanted, already, states)
        tally = skip_tally(wanted, already, states)
        _warn_if_ytdlp_is_stale(deps, automation_id, recent_errors)

        queued = 0
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
            done = 'Queued %d new · %d downloading now (the rest drain automatically)' % (queued, running)
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
                'skipped': tally, '_manages_own_progress': True}
    except Exception as e:  # noqa: BLE001
        deps.update_progress(automation_id, status='error', phase='Error', log_line=str(e), log_type='error')
        return {'status': 'error', 'error': str(e), '_manages_own_progress': True}
