"""Automation handler: ``video_scan_watchlist_channels`` action.

The "what's new from the channels I follow" scan — the piece that lets SoulSync replace
ytdl-sub-style YouTube auto-downloaders. For every YouTube channel on the video watchlist,
look at its recent uploads and wishlist the new long-form videos the user doesn't already
have, so the (future) fulfillment engine can grab them.

This runs on a SHORT schedule (channels publish at all hours — pair it with a 6-hourly
Schedule trigger; 3h is fine too, the scan is cheap). It's forward-looking and dup-proof:

  * **Baseline = follow time.** What the user had before following isn't our concern — only
    uploads published on/after they followed the channel (the watchlist row's ``date_added``)
    are "new" and get wishlisted, forever forward.
  * **Last-N safety net.** A per-channel default (10) reaches a little BEFORE the baseline so
    the user is always kept current on the most recent videos even right after following / if
    a scan was missed. Global setting now; per-channel override (the hover settings modal,
    like watchlist-artist settings) comes later.
  * **Long-form only.** Shorts are excluded (the channel's Videos tab + a duration floor);
    livestreams/premieres that haven't aired are skipped (future-dated).
  * **Never duplicates.** Each candidate is diffed against what's already wishlisted, what's
    already been downloaded (the permanent download-history ledger — empty for YouTube until
    the fulfillment engine lands, wired here so it's correct the day it does), and what the
    user has dismissed.

Like the other video handlers it lives on the SHARED automation side (may import
``core.video`` / ``api.video`` — the isolation contract only forbids the reverse) and owns
its own progress (``_manages_own_progress``). All I/O is injected as seams, so the selection
logic is a pure, unit-testable function; production lazily binds the real calls.

NOTE (follow-up): "remove from the YouTube wishlist" currently just deletes the row, so the
last-N net could re-add a video the user deliberately removed. The scan already respects a
``dismissed_ids`` seam — wiring the wishlist-remove to record a dismissal (once the wishlist
is a real download queue) closes that loop. Tracked, out of scope for this scan-only phase.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Callable, Dict, Iterable, List, Optional

from core.automation.deps import AutomationDeps


# ── pure selection ────────────────────────────────────────────────────────────
def is_short(video: Dict[str, Any], min_seconds: int) -> bool:
    """A YouTube Short — a known duration under the floor. Unknown duration is NOT
    assumed short (the Videos-tab listing already excludes Shorts; this is a backstop)."""
    d = video.get("duration_seconds")
    return isinstance(d, (int, float)) and 0 < d < min_seconds


def long_form_uploads(uploads: Iterable, min_seconds: int) -> List[Dict[str, Any]]:
    """The channel's real videos, newest-first order preserved: drop Shorts + entries
    with no id."""
    return [v for v in (uploads or [])
            if isinstance(v, dict) and v.get("youtube_id") and not is_short(v, min_seconds)]


def _day(value: Any) -> Optional[str]:
    s = str(value or "").strip()
    return s[:10] or None


def select_channel_video_gaps(
    uploads: List[Dict[str, Any]],
    *,
    baseline_date: Optional[str],
    backfill_count: int,
    wishlisted_ids: Iterable = (),
    dismissed_ids: Iterable = (),
    downloaded_ids: Iterable = (),
    today: str,
    min_seconds: int = 60,
) -> List[Dict[str, Any]]:
    """The pure core: which of a channel's recent uploads to wishlist now.

    ``uploads`` is the channel's recent videos newest-first (rich shape: youtube_id, title,
    published_at, duration_seconds, thumbnail_url, …). Keeps a long-form upload if it's not
    already wishlisted / downloaded / dismissed AND either it's within the newest
    ``backfill_count`` (the safety net) or it was published on/after ``baseline_date`` (the
    forward-looking part). Future-dated (unaired premiere/stream) is skipped. No I/O."""
    longs = long_form_uploads(uploads, min_seconds)
    n = max(0, int(backfill_count or 0))
    net_ids = {v["youtube_id"] for v in longs[:n]}
    excluded = set(wishlisted_ids or ()) | set(dismissed_ids or ()) | set(downloaded_ids or ())

    out: List[Dict[str, Any]] = []
    for v in longs:
        vid = v["youtube_id"]
        if vid in excluded:
            continue
        d = _day(v.get("published_at"))
        if d and today and d > today:
            continue                                  # scheduled / unaired — not out yet
        if vid in net_ids or (d and baseline_date and d >= baseline_date):
            out.append(v)
    return out


# ── production seams ──────────────────────────────────────────────────────────
def _default_fetch_channels() -> List[Dict[str, Any]]:
    from api.video import get_video_db
    return get_video_db().list_watchlist_channels()


def _default_fetch_uploads(channel_id: Any, limit: int) -> List[Dict[str, Any]]:
    """A channel's recent uploads (Videos tab → long-form, with durations) with upload
    dates merged on from the cache + a cheap RSS pull — the same composition the channel
    detail endpoint uses. Caches any dates it learns so later scans get them free."""
    from api.video import get_video_db
    from core.video import youtube as yt
    db = get_video_db()
    cid = str(channel_id)
    channel = yt.resolve_channel("https://www.youtube.com/channel/" + cid,
                                 limit=max(1, min(90, int(limit)))) or {}
    vids = channel.get("videos") or []
    ids = [v.get("youtube_id") for v in vids if v.get("youtube_id")]
    dates = db.get_video_dates(ids) or {}
    try:
        dates.update(yt.channel_recent_dates(cid) or {})
    except Exception:   # noqa: BLE001, S110 - RSS is best-effort; cached dates still apply
        pass
    for v in vids:
        if not v.get("published_at") and dates.get(v.get("youtube_id")):
            v["published_at"] = dates[v["youtube_id"]]
    try:
        db.cache_video_dates([{"youtube_id": v["youtube_id"], "published_at": v.get("published_at")}
                              for v in vids if v.get("published_at")])
    except Exception:   # noqa: BLE001, S110 - caching is opportunistic
        pass
    return vids


def _default_wishlisted_ids(channel_id: Any) -> List[Any]:
    from api.video import get_video_db
    return get_video_db().wishlisted_video_ids_for_channel(channel_id)


def _default_dismissed_ids(channel_id: Any) -> List[Any]:
    # No YouTube "dismissed" store yet (video_ignored is movie/show-level). Returns empty;
    # wiring wishlist-remove → dismiss is the tracked follow-up (see module docstring).
    return []


def _default_downloaded_ids(channel_id: Any) -> List[Any]:
    # Already-downloaded YouTube videos (global; a video id is unique). A completed download
    # leaves the wishlist, so without this the scan would re-add + re-download it.
    from api.video import get_video_db
    return get_video_db().downloaded_youtube_video_ids()


def _default_add_videos(channel: Dict[str, Any], videos: List[Dict[str, Any]]) -> int:
    from api.video import get_video_db
    from core.video.sources import resolve_video_server
    return get_video_db().add_videos_to_wishlist(channel, videos, server_source=resolve_video_server())


def auto_video_scan_watchlist_channels(
    config: Dict[str, Any],
    deps: AutomationDeps,
    *,
    fetch_channels: Optional[Callable[[], List[Dict[str, Any]]]] = None,
    fetch_uploads: Optional[Callable[[Any, int], List[Dict[str, Any]]]] = None,
    wishlisted_ids: Optional[Callable[[Any], Iterable]] = None,
    dismissed_ids: Optional[Callable[[Any], Iterable]] = None,
    downloaded_ids: Optional[Callable[[Any], Iterable]] = None,
    add_videos: Optional[Callable[[Dict[str, Any], List[Dict[str, Any]]], int]] = None,
    today_fn: Optional[Callable[[], str]] = None,
) -> Dict[str, Any]:
    """Scan every followed YouTube channel and wishlist its new long-form uploads.

    Returns ``{'status': 'completed', 'channels': int, 'videos_added': int, ...}``."""
    fetch_channels = fetch_channels or _default_fetch_channels
    fetch_uploads = fetch_uploads or _default_fetch_uploads
    wishlisted_ids = wishlisted_ids or _default_wishlisted_ids
    dismissed_ids = dismissed_ids or _default_dismissed_ids
    downloaded_ids = downloaded_ids or _default_downloaded_ids
    add_videos = add_videos or _default_add_videos
    today_fn = today_fn or (lambda: date.today().isoformat())
    automation_id = config.get('_automation_id')
    backfill = max(0, int(config.get('backfill_count', 10) or 0))
    min_seconds = max(0, int(config.get('min_seconds', 60) or 0))
    limit = max(backfill, 30)

    try:
        today = today_fn()
        deps.update_progress(automation_id, phase='Reading your watchlist…', progress=5,
                             log_line='Loading the channels you follow', log_type='info')
        channels = fetch_channels() or []
        if not channels:
            deps.update_progress(automation_id, status='finished', progress=100, phase='Complete',
                                 log_line='No channels on the watchlist to scan', log_type='info')
            return {'status': 'completed', 'channels': 0, 'videos_added': 0,
                    '_manages_own_progress': True}

        added = 0
        total = len(channels)
        for i, ch in enumerate(channels):
            cid = ch.get('youtube_id')
            ctitle = ch.get('title') or cid
            deps.update_progress(automation_id, phase='Scanning channels…',
                                 progress=10 + int(80 * i / max(total, 1)),
                                 log_line="Checking %s for new videos" % ctitle, log_type='info')
            if not cid:
                continue
            try:
                uploads = fetch_uploads(cid, limit) or []
            except Exception:   # noqa: BLE001 - one flaky channel shouldn't abort the scan
                deps.update_progress(automation_id, log_line="Couldn't reach %s — skipping" % ctitle,
                                     log_type='warning')
                continue

            baseline = _day(ch.get('date_added')) or today
            gaps = select_channel_video_gaps(
                uploads, baseline_date=baseline, backfill_count=backfill,
                wishlisted_ids=wishlisted_ids(cid), dismissed_ids=dismissed_ids(cid),
                downloaded_ids=downloaded_ids(cid), today=today, min_seconds=min_seconds)
            if gaps:
                n = int(add_videos({'youtube_id': cid, 'title': ctitle,
                                    'avatar_url': ch.get('poster_url')}, gaps) or 0)
                added += n
                if n:
                    deps.update_progress(
                        automation_id, log_type='success',
                        log_line="Wishlisted %d new video(s) from %s" % (n, ctitle))

        done = ('Wishlisted %d new video(s) across %d channel(s)' % (added, total)) if added \
            else ('Channels are up to date — nothing new across %d channel(s)' % total)
        deps.update_progress(automation_id, status='finished', progress=100, phase='Complete',
                             log_line=done, log_type='success')
        return {'status': 'completed', 'channels': total, 'videos_added': added,
                '_manages_own_progress': True}
    except Exception as e:  # noqa: BLE001
        deps.update_progress(automation_id, status='error', phase='Error', log_line=str(e), log_type='error')
        return {'status': 'error', 'error': str(e), '_manages_own_progress': True}
