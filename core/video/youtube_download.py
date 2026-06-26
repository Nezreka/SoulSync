"""YouTube download worker — the fulfillment lane for wished YouTube videos.

Model B (Boulder): YouTube grabs flow through the SAME ``video_downloads`` queue +
history as movies/TV, so the Downloads page, live progress, and History modal all work
for YouTube for free. But the mechanism is different — there's no slskd transfer to poll;
yt-dlp fetches the stream directly. So this worker owns a YouTube download end to end:

    pick stream (quality profile → yt-dlp format) → download into the library, organised
    as a Plex "TV by date" show (channel/Season YEAR/channel - DATE - title) → mark the
    row completed + archive it to history → remove the video from the wishlist.

The slskd ``download_monitor`` simply SKIPS ``source='youtube'`` rows (they have no
transfer to match), so this lane never disturbs the movie/TV pipeline.

The orchestration (``process_youtube_download``) is PURE — the actual yt-dlp run and all
DB writes are injected seams — so the lifecycle (dest planning, completion → archive +
unwish, failure → archive, no unwish) is unit-tested without a network or a DB. Production
(``run_youtube_download``) lazily binds the real calls and runs it on a worker thread.

Isolated: imports only sibling ``core.video`` modules; nothing from the music side.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Callable, Dict, Optional

from core.video import organization, youtube_quality
from core.video.youtube_quality import format_selection

logger = logging.getLogger(__name__)

try:
    import yt_dlp
except Exception:   # noqa: BLE001 - optional at import; absence handled at call time
    yt_dlp = None


def youtube_fields_from_download(dl: Dict[str, Any]) -> Dict[str, Any]:
    """Organising fields for a YouTube download row. The channel/video-title/date that
    ``render_path('youtube', …)`` needs ride in ``search_ctx`` (a generic JSON column);
    fall back to the row's own columns when absent."""
    ctx = dl.get("search_ctx")
    if isinstance(ctx, str):
        try:
            ctx = json.loads(ctx)
        except (ValueError, TypeError):
            ctx = {}
    if not isinstance(ctx, dict):
        ctx = {}
    return {
        "channel": ctx.get("channel") or dl.get("title"),
        "title": ctx.get("video_title") or dl.get("title"),
        "published_at": ctx.get("published_at") or dl.get("year"),
        "youtube_id": dl.get("media_id"),
    }


def plan_destination(dl: Dict[str, Any], settings: Dict[str, Any], container: str) -> Dict[str, str]:
    """Where this video lands in the library: ``{dir, filename, path}`` under the youtube
    root (``target_dir``), organised by the youtube template. Pure."""
    ext = "." + str(container or "mp4").lstrip(".")
    return organization.render_path("youtube", dl.get("target_dir"),
                                    youtube_fields_from_download(dl), settings, ext)


def ydl_download_opts(profile: Any, dest_dir: str, dest_stem: str,
                      *, progress_hook: Optional[Callable] = None, cookie_opts: Optional[dict] = None) -> dict:
    """The yt-dlp options dict for one download: format selection from the quality profile,
    a fixed output path (dir + stem + yt-dlp's own ext), polite defaults. Pure."""
    sel = format_selection(profile)
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "retries": 3,
        "format": sel["format"],
        "format_sort": sel["format_sort"],
        "merge_output_format": sel["merge_output_format"],
        "paths": {"home": str(dest_dir or "")},
        "outtmpl": dest_stem + ".%(ext)s",
    }
    if cookie_opts:
        opts.update(cookie_opts)
    if progress_hook:
        opts["progress_hooks"] = [progress_hook]
    return opts


def _stem_and_container(dest: Dict[str, str], container: str) -> tuple:
    """(filename stem, final ext) — strip the ext render_path put on the filename so
    yt-dlp can own the extension during the merge."""
    fn = dest.get("filename") or "download"
    cont = str(container or "mp4").lstrip(".")
    stem = fn[:-(len(cont) + 1)] if fn.lower().endswith("." + cont.lower()) else os.path.splitext(fn)[0]
    return stem or "download", cont


def download_one(video_id: Any, dest_dir: str, dest_stem: str, profile: Any, container: str,
                 *, ydl_factory=None, progress_hook=None, cookie_opts=None) -> Dict[str, Any]:
    """Run yt-dlp for ONE video into ``dest_dir/dest_stem.ext``. Returns
    ``{ok, dest_path|None, error|None}``. The yt-dlp class is injectable for tests."""
    vid = str(video_id or "").strip()
    if not vid:
        return {"ok": False, "dest_path": None, "error": "No video id"}
    factory = ydl_factory or (yt_dlp.YoutubeDL if yt_dlp else None)
    if factory is None:
        return {"ok": False, "dest_path": None, "error": "yt-dlp unavailable"}
    opts = ydl_download_opts(profile, dest_dir, dest_stem,
                             progress_hook=progress_hook, cookie_opts=cookie_opts)
    url = vid if vid.startswith("http") else "https://www.youtube.com/watch?v=" + vid
    try:
        with factory(opts) as ydl:
            ydl.download([url])
    except Exception as e:   # noqa: BLE001 - any yt-dlp failure → a failed download, not a crash
        logger.info("youtube download failed for %s: %s", vid, e)
        return {"ok": False, "dest_path": None, "error": str(e)}
    dest_path = os.path.join(str(dest_dir or ""), dest_stem + "." + str(container or "mp4").lstrip("."))
    return {"ok": True, "dest_path": dest_path, "error": None}


def process_youtube_download(
    dl: Dict[str, Any],
    *,
    profile: Any,
    settings: Dict[str, Any],
    download: Callable = download_one,
    update_row: Callable[..., Any],
    archive: Callable[[Dict[str, Any], Dict[str, Any]], Any],
    clear_wishlist: Callable[[Any], Any],
    progress_hook: Optional[Callable] = None,
    cookie_opts: Optional[dict] = None,
    now: Optional[Callable[[], str]] = None,
) -> Dict[str, Any]:
    """Fulfil one queued YouTube download. PURE — all I/O injected.

    On success: row → completed (with dest_path), snapshot to history, and remove the video
    from the wishlist (it's in the library now; history is the permanent record). On
    failure: row → failed + a history snapshot; the wishlist row is LEFT so a later scan/run
    can retry. Returns a small result dict."""
    now = now or (lambda: "")
    settings = settings if isinstance(settings, dict) else {}
    container = format_selection(profile)["merge_output_format"]
    dest = plan_destination(dl, settings, container)
    stem, cont = _stem_and_container(dest, container)

    # Record where it's going up front (Downloads page shows the organised name).
    update_row(dl.get("id"), status="downloading", progress=0,
               target_dir=dest.get("dir"), filename=dest.get("filename"))

    res = download(dl.get("media_id"), dest.get("dir"), stem, profile, cont,
                   progress_hook=progress_hook, cookie_opts=cookie_opts)

    if res.get("ok"):
        dest_path = res.get("dest_path") or dest.get("path")
        completed = now()
        update_row(dl.get("id"), status="completed", progress=100,
                   dest_path=dest_path, completed_at=completed)
        archive(dl, {"status": "completed", "dest_path": dest_path, "completed_at": completed})
        try:
            clear_wishlist(dl.get("media_id"))
        except Exception:   # noqa: BLE001 - unwish is best-effort; the file is already in place
            logger.exception("youtube download %s: unwish failed", dl.get("id"))
        return {"status": "completed", "dest_path": dest_path}

    err = res.get("error") or "Download failed"
    completed = now()
    update_row(dl.get("id"), status="failed", error=err, completed_at=completed)
    archive(dl, {"status": "failed", "error": err, "completed_at": completed})
    return {"status": "failed", "error": err}


# ── concurrency + pacing (the music side's lesson: cap concurrency AND space starts) ──
# yt-dlp 429s if hammered, so fetch STARTS are spaced ≥ this far apart across all workers.
_DELAY_SECONDS = 3.0
_pace_lock = threading.Lock()
_last_start = [0.0]


def _pace(delay: float) -> None:
    """Block until at least ``delay`` seconds after the previous fetch start, then reserve
    this start slot. Reserving under the lock (sleeping outside it) staggers concurrent
    workers without serialising them past the delay."""
    if delay <= 0:
        return
    with _pace_lock:
        now = time.monotonic()
        start_at = max(now, _last_start[0] + delay) if _last_start[0] else now
        _last_start[0] = start_at
    wait = start_at - time.monotonic()
    if wait > 0:
        time.sleep(wait)


def _spawn_worker(dl_id: Any, db_provider: Callable) -> None:
    threading.Thread(target=run_youtube_download, args=(dl_id, db_provider),
                     daemon=True, name="yt-dl-%s" % dl_id).start()


def start_next_queued(db_provider: Callable) -> Any:
    """Claim the next queued YouTube download and start it. Returns its id, or None if the
    queue is empty. The wishlist pump uses this to fill slots; each finished worker calls it
    once (one-out-one-in) so the queue drains continuously at the established concurrency."""
    row = db_provider().claim_next_youtube_queued()
    if not row:
        return None
    _spawn_worker(row["id"], db_provider)
    return row["id"]


# ── production wiring ─────────────────────────────────────────────────────────
def run_youtube_download(dl_id: Any, db_provider: Callable) -> None:
    """Production entry: fetch the row, bind real seams, fulfil it. Called on a worker
    thread by the pump; on finish it starts the next queued download (one-out-one-in)."""
    db = db_provider()
    dl = db.get_video_download(dl_id)
    if not dl:
        start_next_queued(db_provider)        # keep the queue moving even on a stale id
        return
    profile = youtube_quality.load(db)
    settings = organization.load(db)
    from datetime import datetime, timezone

    def _now():
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def _progress(d):
        # yt-dlp progress hook → row progress %. Best-effort; never raises into yt-dlp.
        try:
            if d.get("status") == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                got = d.get("downloaded_bytes") or 0
                if total:
                    db.update_video_download(dl_id, progress=int(got * 100 / total))
        except Exception:   # noqa: BLE001, S110 - a progress glitch must not abort the download
            pass

    def _archive(row, upd):
        try:
            db.record_download_history({**row, **upd})
        except Exception:
            logger.exception("youtube download %s: history snapshot failed", dl_id)

    cookie_opts = None
    try:
        from core.video.youtube import _cookie_opts
        cookie_opts = _cookie_opts()
    except Exception:   # noqa: BLE001 - cookies are optional
        cookie_opts = None

    try:
        _pace(_DELAY_SECONDS)                  # space fetch starts to avoid yt-dlp 429s
        process_youtube_download(
            dl, profile=profile, settings=settings,
            update_row=db.update_video_download, archive=_archive,
            clear_wishlist=lambda vid: db.remove_youtube_from_wishlist("video", vid),
            progress_hook=_progress, cookie_opts=cookie_opts, now=_now)
    finally:
        start_next_queued(db_provider)         # one out, one in — drain the queue


__all__ = [
    "youtube_fields_from_download", "plan_destination", "ydl_download_opts",
    "download_one", "process_youtube_download", "run_youtube_download",
    "start_next_queued",
]
