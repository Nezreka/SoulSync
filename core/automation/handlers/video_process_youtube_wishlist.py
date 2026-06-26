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


def videos_to_enqueue(wanted: List[Dict[str, Any]], already_ids: Iterable) -> List[Dict[str, Any]]:
    """Wished videos not already queued or downloading. NO cap — the whole backlog is
    queued; concurrency is bounded at start time, not here. Pure."""
    already = {str(x) for x in (already_ids or ()) if x}
    out: List[Dict[str, Any]] = []
    for v in wanted or []:
        vid = v.get("video_id")
        if vid and str(vid) not in already:
            out.append(v)
    return out


def slots_free(running: int, max_concurrent: int) -> int:
    """How many new downloads may start now given how many are already fetching. Pure."""
    return max(0, int(max_concurrent) - max(0, int(running)))


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


def _default_running_count() -> int:
    from api.video import get_video_db
    return get_video_db().count_active_youtube_downloads()


def _default_enqueue(video: Dict[str, Any], root: str) -> Any:
    """Create a QUEUED download row (no thread spawned here — the pump starts it). Returns
    the row id."""
    import json
    from api.video import get_video_db
    from core.video.sources import resolve_video_server
    return get_video_db().add_video_download({
        "kind": "youtube", "source": "youtube", "media_source": "youtube",
        "title": video.get("video_title") or video.get("channel_title"),
        "media_id": video.get("video_id"), "target_dir": root, "status": "queued",
        "year": video.get("published_at"), "poster_url": video.get("thumbnail_url"),
        "search_ctx": json.dumps({"channel": video.get("channel_title"),
                                  "video_title": video.get("video_title"),
                                  "published_at": video.get("published_at"),
                                  "server_source": resolve_video_server()}),
    })


def _default_start_next() -> Any:
    """Claim + start the next queued YouTube download (or None). The worker chains the rest."""
    from api.video import get_video_db
    from core.video.youtube_download import start_next_queued
    return start_next_queued(get_video_db)


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
) -> Dict[str, Any]:
    """Queue the whole YouTube wishlist for download and start up to ``max_concurrent`` now.

    Returns ``{'status': 'completed', 'queued': int, 'started': int, 'running': int, ...}``."""
    youtube_root = youtube_root or _default_youtube_root
    fetch_wanted = fetch_wanted or _default_fetch_wanted
    active_ids = active_ids or _default_active_ids
    running_count = running_count or _default_running_count
    enqueue = enqueue or _default_enqueue
    start_next = start_next or _default_start_next
    automation_id = config.get('_automation_id')
    max_concurrent = max(1, int(config.get('max_concurrent', 3) or 3))

    try:
        root = youtube_root()
        if not root:
            msg = 'Set the YouTube library folder on Settings → Downloads first'
            deps.update_progress(automation_id, status='error', phase='Error',
                                 log_line=msg, log_type='error')
            return {'status': 'error', 'error': msg, '_manages_own_progress': True}

        deps.update_progress(automation_id, phase='Checking the YouTube wishlist…', progress=15,
                             log_line='Queueing new videos for download', log_type='info')
        wanted = fetch_wanted() or []
        already = list(active_ids() or [])
        new = videos_to_enqueue(wanted, already)

        queued = 0
        for v in new:
            try:
                if enqueue(v, root) is not None:
                    queued += 1
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
        else:
            done = 'No wished YouTube videos to download'
            log_type = 'info'
        deps.update_progress(automation_id, status='finished', progress=100, phase='Complete',
                             log_line=done, log_type=log_type)
        return {'status': 'completed', 'queued': queued, 'started': started, 'running': running,
                '_manages_own_progress': True}
    except Exception as e:  # noqa: BLE001
        deps.update_progress(automation_id, status='error', phase='Error', log_line=str(e), log_type='error')
        return {'status': 'error', 'error': str(e), '_manages_own_progress': True}
