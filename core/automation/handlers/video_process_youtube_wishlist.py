"""Automation handler: ``video_process_youtube_wishlist`` action.

The drain side of the YouTube fulfillment lane. The watchlist-channels scan keeps the
wishlist fed with new uploads; THIS takes wished YouTube videos and pushes them into the
shared ``video_downloads`` queue, spawning the yt-dlp worker per video. The worker
(``core.video.youtube_download``) downloads → organises (channel/year/date) → archives to
history → removes the video from the wishlist, so a completed grab leaves the wishlist and
won't be re-queued.

Polite by default: only a small BATCH is enqueued per run (a big first-time backlog drains
over several scheduled runs rather than spawning hundreds of yt-dlp processes at once), and
videos already in flight (an active ``source='youtube'`` download) are skipped so re-runs
never double-grab.

Shared automation side (may import ``core.video`` / ``api.video``); owns its own progress.
All I/O is injected as seams, so the selection + batching is a pure unit-testable function;
production lazily binds the real DB + the worker spawn.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Optional

from core.automation.deps import AutomationDeps


def select_to_enqueue(wanted: List[Dict[str, Any]], active_ids: Iterable, batch_size: int) -> List[Dict[str, Any]]:
    """Which wished videos to enqueue now: skip ones already in flight, cap at the batch
    size (0 = no cap). Pure."""
    active = {str(x) for x in (active_ids or ()) if x}
    out: List[Dict[str, Any]] = []
    for v in wanted or []:
        vid = v.get("video_id")
        if not vid or str(vid) in active:
            continue
        out.append(v)
        if batch_size and len(out) >= batch_size:
            break
    return out


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


def _default_enqueue(video: Dict[str, Any], root: str) -> Any:
    """Create the download row + spawn the yt-dlp worker thread. Returns the row id."""
    import json
    import threading
    from api.video import get_video_db
    from core.video.sources import resolve_video_server
    from core.video.youtube_download import run_youtube_download
    db = get_video_db()
    dl_id = db.add_video_download({
        "kind": "youtube", "source": "youtube", "media_source": "youtube",
        "title": video.get("video_title") or video.get("channel_title"),
        "media_id": video.get("video_id"), "target_dir": root, "status": "downloading",
        "year": video.get("published_at"), "poster_url": video.get("thumbnail_url"),
        "search_ctx": json.dumps({"channel": video.get("channel_title"),
                                  "video_title": video.get("video_title"),
                                  "published_at": video.get("published_at"),
                                  "server_source": resolve_video_server()}),
    })
    threading.Thread(target=run_youtube_download, args=(dl_id, get_video_db),
                     daemon=True, name="yt-dl-%s" % dl_id).start()
    return dl_id


def auto_video_process_youtube_wishlist(
    config: Dict[str, Any],
    deps: AutomationDeps,
    *,
    youtube_root: Optional[Callable[[], str]] = None,
    fetch_wanted: Optional[Callable[[], List[Dict[str, Any]]]] = None,
    active_ids: Optional[Callable[[], Iterable]] = None,
    enqueue: Optional[Callable[[Dict[str, Any], str], Any]] = None,
) -> Dict[str, Any]:
    """Enqueue a batch of wished YouTube videos for download.

    Returns ``{'status': 'completed', 'queued': int, 'remaining': int, ...}``."""
    youtube_root = youtube_root or _default_youtube_root
    fetch_wanted = fetch_wanted or _default_fetch_wanted
    active_ids = active_ids or _default_active_ids
    enqueue = enqueue or _default_enqueue
    automation_id = config.get('_automation_id')
    batch_size = max(0, int(config.get('batch_size', 3) or 0))

    try:
        root = youtube_root()
        if not root:
            msg = 'Set the YouTube library folder on Settings → Downloads first'
            deps.update_progress(automation_id, status='error', phase='Error',
                                 log_line=msg, log_type='error')
            return {'status': 'error', 'error': msg, '_manages_own_progress': True}

        deps.update_progress(automation_id, phase='Checking the YouTube wishlist…', progress=10,
                             log_line='Looking for new videos to download', log_type='info')
        wanted = fetch_wanted() or []
        active = list(active_ids() or [])
        picks = select_to_enqueue(wanted, active, batch_size)
        if not picks:
            note = ('All %d wished video(s) are already downloading' % len(active)) if active \
                else 'No wished YouTube videos to download'
            deps.update_progress(automation_id, status='finished', progress=100, phase='Complete',
                                 log_line=note, log_type='info')
            return {'status': 'completed', 'queued': 0, 'remaining': max(0, len(wanted) - len(active)),
                    '_manages_own_progress': True}

        queued = 0
        for i, v in enumerate(picks):
            deps.update_progress(automation_id, phase='Queueing downloads…',
                                 progress=20 + int(70 * i / max(len(picks), 1)),
                                 log_line="Queued '%s'" % (v.get('video_title') or v.get('video_id')),
                                 log_type='info')
            try:
                if enqueue(v, root) is not None:
                    queued += 1
            except Exception:   # noqa: BLE001 - one bad enqueue shouldn't stop the batch
                deps.update_progress(automation_id, log_type='warning',
                                     log_line="Couldn't queue '%s'" % (v.get('video_title') or v.get('video_id')))

        remaining = max(0, len(wanted) - len(active) - queued)
        done = 'Queued %d YouTube download(s)' % queued
        if remaining:
            done += ' · %d more waiting (drains over the next runs)' % remaining
        deps.update_progress(automation_id, status='finished', progress=100, phase='Complete',
                             log_line=done, log_type='success')
        return {'status': 'completed', 'queued': queued, 'remaining': remaining,
                '_manages_own_progress': True}
    except Exception as e:  # noqa: BLE001
        deps.update_progress(automation_id, status='error', phase='Error', log_line=str(e), log_type='error')
        return {'status': 'error', 'error': str(e), '_manages_own_progress': True}
