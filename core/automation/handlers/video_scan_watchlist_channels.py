"""Automation handler: ``video_scan_watchlist_channels`` action.

The "what's new from the channels I follow" scan — the piece that lets SoulSync replace
ytdl-sub-style YouTube auto-downloaders. For every YouTube channel on the video watchlist,
look at its recent uploads and wishlist the new long-form videos the user doesn't already
have, so the (future) fulfillment engine can grab them.

This runs on a SHORT schedule (channels publish at all hours — pair it with a 6-hourly
Schedule trigger; 3h is fine too, the scan is cheap). It's forward-looking and dup-proof:

  * **Baseline = follow time.** What the user had before following isn't our concern — only
    uploads published on/after they followed the channel (the watchlist row's ``date_added``)
    are "new" and get wishlisted, forever forward. When the recent window overflows (a channel
    posts more since you followed than the window holds — a firehose week or a long scan
    outage), the scan pages deeper toward the follow date so nothing since follow is dropped;
    it stops the moment it crosses the baseline or hits videos it already has (break-on-
    existing), bounded by a page cap — so catch-up is complete but steady state stays cheap.
  * **Last-N safety net.** Reaches a little BEFORE the baseline so the user is always kept
    current on the most recent videos even right after following / if a scan was missed. The
    count is the global "videos to grab" setting (Settings → Library, default 5) — the SAME
    knob as the follow backfill, so they can never disagree.
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

import re
import time
from datetime import date

from utils.logging_config import get_logger

logger = get_logger("automation.video_scan_watchlist_channels")
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


def apply_channel_filters(uploads: Iterable, channel_settings: Any) -> List[Dict[str, Any]]:
    """Per-channel content filters (ytdl-sub match_filters parity), applied before
    gap selection. Settings keys (all optional, stored in the channel's cog):

    - ``title_include``: only keep titles matching ANY pattern (comma-separated;
      a ``/…/`` pattern is a regex, anything else a case-insensitive substring)
    - ``title_exclude``: drop titles matching any pattern (same syntax)
    - ``min_minutes``: drop videos with a KNOWN duration under this (unknown
      durations pass — same discipline as the Shorts backstop)

    Pure. A broken regex disables THAT pattern (fail-open, logged) — a typo in
    one filter must not silently blank a channel's whole feed."""
    cs = channel_settings if isinstance(channel_settings, dict) else {}
    include = _patterns(cs.get("title_include"))
    exclude = _patterns(cs.get("title_exclude"))
    try:
        min_secs = max(0.0, float(cs.get("min_minutes") or 0)) * 60
    except (TypeError, ValueError):
        min_secs = 0
    if not include and not exclude and not min_secs:
        return list(uploads or [])
    out = []
    for v in (uploads or []):
        title = str((v or {}).get("title") or "")
        if include and not any(_pat_match(p, title) for p in include):
            continue
        if exclude and any(_pat_match(p, title) for p in exclude):
            continue
        d = (v or {}).get("duration_seconds")
        if min_secs and isinstance(d, (int, float)) and 0 < d < min_secs:
            continue
        out.append(v)
    return out


def _patterns(raw: Any) -> List[str]:
    return [p.strip() for p in str(raw or "").split(",") if p.strip()]


def _pat_match(pattern: str, title: str) -> bool:
    if len(pattern) > 2 and pattern.startswith("/") and pattern.endswith("/"):
        try:
            return re.search(pattern[1:-1], title, re.IGNORECASE) is not None
        except re.error:
            logger.warning("channel filter: bad regex %s — ignoring the pattern", pattern)
            return False
    return pattern.lower() in title.lower()


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


def _default_channel_settings(channel_id: Any) -> Dict[str, Any]:
    from api.video import get_video_db
    return get_video_db().get_channel_settings(channel_id)


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


def _default_backfill_count() -> int:
    """The rolling last-N net = the global 'videos to grab' setting (Settings → Library),
    so the scan net and the follow backfill stay consistent. Defaults to 5."""
    try:
        from api.video import get_video_db
        from core.video import organization as org
        return max(0, min(100, int(org.load(get_video_db()).get("youtube_follow_count", 5))))
    except Exception:   # noqa: BLE001 - missing db/setting → the default
        return 5


# Deep catch-up cap: the most extra InnerTube pages the gap-fill may pull per channel per
# scan. break-on-existing + cross-baseline normally stop far sooner; this is the backstop so
# a channel followed long ago can never page its whole catalog on a scan.
_CATCHUP_MAX_PAGES = 8
_PAGE_DELAY = 0.6                                       # politeness pause between deep pages


def _default_fetch_upload_page(channel_id: Any, continuation: Any) -> Dict[str, Any]:
    """One more InnerTube page of a channel's Videos tab (dated), for the gap-fill pager:
    {"videos": [...newest-first...], "continuation": token|None}."""
    from core.video import youtube as yt
    return yt.innertube_channel_videos_page(channel_id, continuation=continuation)


def _extend_to_baseline(
    channel_id: Any,
    uploads: List[Dict[str, Any]],
    *,
    baseline_date: Optional[str],
    limit: int,
    excluded: Iterable = (),
    page_fn: Callable[[Any, Any], Dict[str, Any]],
    page_sleep: Optional[Callable[[float], None]] = None,
    max_pages: int = _CATCHUP_MAX_PAGES,
) -> List[Dict[str, Any]]:
    """Fill the gap between the recent window and the follow date.

    The recent-window fetch returns only the newest ``limit`` uploads. If a channel posted
    MORE than that since you followed (a firehose week, or a long scan outage), the overflow
    is older than the window yet still newer than your follow date — it'd be silently missed.

    Pages the channel's Videos tab (newest→older) ONLY when that overflow is actually possible
    — the window came back FULL and its oldest dated upload is still on/after the baseline —
    and stops as soon as it crosses the baseline OR reaches a page of already-known videos
    (break-on-existing, so steady-state costs ~1 extra page), capped at ``max_pages``.
    Best-effort: any failure returns the uploads unchanged, so the normal path never breaks.

    Returns the (possibly extended) uploads, newest-first, de-duplicated by id."""
    by_id: Dict[str, Dict[str, Any]] = {}
    dated: List[str] = []
    for v in uploads:
        vid = v.get("youtube_id")
        if vid and vid not in by_id:
            by_id[vid] = v
            d = _day(v.get("published_at"))
            if d:
                dated.append(d)
    # Trigger only when overflow is possible: a full window whose oldest dated upload is still
    # >= the follow date. A short window means we already hold the whole recent history; a
    # window that reaches past the baseline means nothing between it and the follow date was
    # dropped. Either way there is nothing deeper worth paging for.
    if len(uploads) < max(1, int(limit)) or not baseline_date:
        return uploads
    oldest = min(dated) if dated else None
    if not oldest or oldest < baseline_date:
        return uploads

    known = set(excluded or ())
    sleep = page_sleep or time.sleep
    token = None
    pages = 0
    try:
        while pages < max_pages:
            if pages:
                sleep(_PAGE_DELAY)                     # politeness between network pages
            page = page_fn(channel_id, token) or {}
            vids = page.get("videos") or []
            page_ids = [v.get("youtube_id") for v in vids if v.get("youtube_id")]
            for v in vids:
                vid = v.get("youtube_id")
                if vid and vid not in by_id:
                    by_id[vid] = v
            pages += 1
            token = page.get("continuation")
            page_dates = [d for d in (_day(v.get("published_at")) for v in vids) if d]
            if page_dates and min(page_dates) < baseline_date:
                break                                  # paged past the follow date — done
            if not token:
                break                                  # channel exhausted
            if page_ids and all(pid in known for pid in page_ids):
                break                                  # hit already-handled videos — done
        else:
            # Hit the cap before reaching the follow date — log it, never pretend it's complete.
            logger.info("catch-up: %s hit the %d-page cap before the follow date (%s); some "
                        "uploads since follow may wait for the next pass", channel_id, max_pages,
                        baseline_date)
    except Exception:   # noqa: BLE001 - the gap-fill is a safety net; never break the scan
        logger.debug("catch-up paging failed for %s", channel_id, exc_info=True)
    return list(by_id.values())


def auto_video_scan_watchlist_channels(
    config: Dict[str, Any],
    deps: AutomationDeps,
    *,
    fetch_channels: Optional[Callable[[], List[Dict[str, Any]]]] = None,
    fetch_uploads: Optional[Callable[[Any, int], List[Dict[str, Any]]]] = None,
    channel_settings: Optional[Callable[[Any], Dict[str, Any]]] = None,
    wishlisted_ids: Optional[Callable[[Any], Iterable]] = None,
    dismissed_ids: Optional[Callable[[Any], Iterable]] = None,
    downloaded_ids: Optional[Callable[[Any], Iterable]] = None,
    add_videos: Optional[Callable[[Dict[str, Any], List[Dict[str, Any]]], int]] = None,
    today_fn: Optional[Callable[[], str]] = None,
    backfill_fn: Optional[Callable[[], int]] = None,
    fetch_upload_page: Optional[Callable[[Any, Any], Dict[str, Any]]] = None,
    page_sleep: Optional[Callable[[float], None]] = None,
) -> Dict[str, Any]:
    """Scan every followed YouTube channel and wishlist its new long-form uploads.

    Returns ``{'status': 'completed', 'channels': int, 'videos_added': int, ...}``."""
    fetch_channels = fetch_channels or _default_fetch_channels
    fetch_uploads = fetch_uploads or _default_fetch_uploads
    channel_settings = channel_settings or _default_channel_settings
    wishlisted_ids = wishlisted_ids or _default_wishlisted_ids
    dismissed_ids = dismissed_ids or _default_dismissed_ids
    downloaded_ids = downloaded_ids or _default_downloaded_ids
    add_videos = add_videos or _default_add_videos
    today_fn = today_fn or (lambda: date.today().isoformat())
    fetch_upload_page = fetch_upload_page or _default_fetch_upload_page
    automation_id = config.get('_automation_id')
    # The last-N net is the SINGLE global "videos to grab" setting (Settings → Library),
    # shared with the follow backfill. No per-automation override — one knob, no surprises
    # (a stale value baked into an old automation used to silently win over the global).
    backfill = max(0, int((backfill_fn or _default_backfill_count)()))
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
            # Read the dedup sets once — used by both the gap-fill (break-on-existing) and
            # the final selection, so we never call the seams twice per channel.
            wl = list(wishlisted_ids(cid) or [])
            dl = list(downloaded_ids(cid) or [])
            di = list(dismissed_ids(cid) or [])
            # Gap-fill: when the recent window overflowed (a firehose week / a long outage),
            # page deeper toward the follow date so nothing since you followed is dropped.
            # Best-effort + bounded; a no-op when the window already reaches past the baseline.
            uploads = _extend_to_baseline(
                cid, uploads, baseline_date=baseline, limit=limit,
                excluded=set(wl) | set(dl) | set(di),
                page_fn=fetch_upload_page, page_sleep=page_sleep)
            try:
                uploads = apply_channel_filters(uploads, channel_settings(cid))
            except Exception:   # noqa: BLE001 - filters are an assist, never abort a channel
                logger.exception("channel filters failed for %s", cid)
            gaps = select_channel_video_gaps(
                uploads, baseline_date=baseline, backfill_count=backfill,
                wishlisted_ids=wl, dismissed_ids=di,
                downloaded_ids=dl, today=today, min_seconds=min_seconds)
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
