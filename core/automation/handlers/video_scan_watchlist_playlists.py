"""Automation handler: ``video_scan_watchlist_playlists`` action.

Sibling of the watchlist-CHANNELS scan, and now the SAME 'cap + new' rule (Boulder, 2026-07:
following a big playlist used to wishlist the WHOLE thing at once — a flood). Instead of a
date baseline (playlist videos are often undated / curator-ordered), a playlist uses a
**membership baseline**: the first scan wishlists only the newest N (the global "videos to
grab" setting, shared with channels) and records every current member as "seen"; later scans
wishlist only members NOT yet seen — genuine additions the curator made, regardless of upload
date. An already-mirror-flooded playlist self-migrates cleanly (everything is already owned →
first pass adds nothing, just baselines the list → only true additions from then on).

Organisation: the playlist becomes its own "show" (playlist-as-show) — its videos are
wishlisted under the playlist's title, so the download worker files them as
``Playlist Name / Season YEAR / Playlist Name - DATE - Title`` (matches the ytdl-sub
``tv_show_name``-on-a-playlist convention). All other plumbing is shared with channels: the
same wishlist rows, the same "Process YouTube Wishlist" drain, quality + org template.

Edge: a video in both a followed channel AND a followed playlist is wishlisted once (dedup
by video id); whichever scan touched it last sets its show-name. Accepted.

Pure selection with all I/O injected; shared automation side; owns its own progress.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Callable, Dict, Iterable, List, Optional

from core.automation.deps import AutomationDeps
from core.automation.handlers.video_scan_watchlist_channels import (
    _day,
    _default_backfill_count,
    long_form_uploads,
)
from utils.logging_config import get_logger

logger = get_logger("automation.video_scan_watchlist_playlists")

# how many playlist entries to read per scan (yt-dlp flat). Big enough for almost any real
# playlist; genuinely huge ones truncate (logged) rather than hammering YouTube each scan.
_PLAYLIST_FETCH_LIMIT = 1000


def select_playlist_additions(
    videos: List[Dict[str, Any]],
    *,
    seen_ids: Iterable = (),
    backfill_count: int,
    wishlisted_ids: Iterable = (),
    downloaded_ids: Iterable = (),
    dismissed_ids: Iterable = (),
    today: str,
    min_seconds: int = 60,
) -> tuple:
    """Which of a playlist's videos to wishlist now — the same 'cap + new' rule as channels.

    ``seen_ids`` is the membership baseline captured on the first scan. When it's EMPTY (a
    fresh follow, or an existing playlist not yet baselined) only the newest ``backfill_count``
    long-form members you don't already own are wishlisted, and the WHOLE current membership is
    returned to baseline — so a mirror-flooded playlist stops re-adding and a fresh follow is
    capped. Afterwards only members NOT in the baseline (genuine additions the curator added)
    are wishlisted, regardless of upload date. Shorts + unaired premieres skipped. Pure, no I/O.

    Returns ``(to_wishlist, to_baseline)`` — the videos to add now, and the ids to remember."""
    longs = [v for v in long_form_uploads(videos, min_seconds)
             if not (_day(v.get("published_at")) and today and _day(v["published_at"]) > today)]
    excluded = set(wishlisted_ids or ()) | set(downloaded_ids or ()) | set(dismissed_ids or ())
    seen = set(seen_ids or ())
    if not seen:
        # First pass: cap to the newest N you don't already own, and baseline EVERYTHING now
        # present so a flooded/curated playlist never re-adds and additions are tracked forward.
        n = max(0, int(backfill_count or 0))
        picks = [v for v in longs if v["youtube_id"] not in excluded][:n]
        return picks, [v["youtube_id"] for v in longs]
    # Steady state: only members not yet baselined (new additions) and not already handled.
    picks = [v for v in longs if v["youtube_id"] not in seen and v["youtube_id"] not in excluded]
    return picks, [v["youtube_id"] for v in picks]


def select_playlist_video_gaps(
    videos: List[Dict[str, Any]],
    *,
    wishlisted_ids: Iterable = (),
    downloaded_ids: Iterable = (),
    dismissed_ids: Iterable = (),
    today: str,
    min_seconds: int = 60,
) -> List[Dict[str, Any]]:
    """DEPRECATED (kept for callers/tests): every long-form video not already wishlisted /
    downloaded / dismissed — mirror the whole list. The scan now uses
    ``select_playlist_additions`` (cap + new) instead. No I/O."""
    excluded = set(wishlisted_ids or ()) | set(downloaded_ids or ()) | set(dismissed_ids or ())
    out: List[Dict[str, Any]] = []
    for v in long_form_uploads(videos, min_seconds):
        vid = v["youtube_id"]
        if vid in excluded:
            continue
        d = _day(v.get("published_at"))
        if d and today and d > today:
            continue                                  # unaired premiere — not out yet
        out.append(v)
    return out


# ── production seams ──────────────────────────────────────────────────────────
def _default_fetch_playlists() -> List[Dict[str, Any]]:
    from api.video import get_video_db
    return get_video_db().list_watchlist_playlists()


def _default_fetch_videos(playlist_id: Any) -> List[Dict[str, Any]]:
    """A playlist's videos (flat, with durations) with upload dates merged from the cache.
    Playlists have no per-channel RSS, so dates come from whatever's cached (filled over
    time by the channel/InnerTube enrichers); undated entries organise cleanly."""
    from api.video import get_video_db
    from core.video import youtube as yt
    db = get_video_db()
    vids = yt.playlist_videos(str(playlist_id), limit=_PLAYLIST_FETCH_LIMIT) or []
    ids = [v.get("youtube_id") for v in vids if v.get("youtube_id")]
    dates = db.get_video_dates(ids) or {}
    for v in vids:
        if not v.get("published_at") and dates.get(v.get("youtube_id")):
            v["published_at"] = dates[v["youtube_id"]]
    return vids


def _default_wishlisted_ids(playlist_id: Any) -> List[Any]:
    # parent_source_id holds the playlist id for playlist-sourced wishlist videos.
    from api.video import get_video_db
    return get_video_db().wishlisted_video_ids_for_channel(playlist_id)


def _default_downloaded_ids(playlist_id: Any) -> List[Any]:
    # Already-downloaded YouTube videos (global). A completed download leaves the wishlist,
    # so without this the scan would re-add + re-download it.
    from api.video import get_video_db
    return get_video_db().downloaded_youtube_video_ids()


def _default_dismissed_ids(playlist_id: Any) -> List[Any]:
    return []   # no youtube "dismissed" store yet (see channels-scan note)


def _default_seen_ids(playlist_id: Any) -> List[Any]:
    from api.video import get_video_db
    return get_video_db().get_playlist_seen(playlist_id)


def _default_mark_seen(playlist_id: Any, video_ids: Iterable) -> None:
    from api.video import get_video_db
    get_video_db().add_playlist_seen(playlist_id, list(video_ids or []))


def _default_add_videos(playlist: Dict[str, Any], videos: List[Dict[str, Any]]) -> int:
    """Wishlist under the PLAYLIST as the show (title = playlist name → playlist-as-show)."""
    from api.video import get_video_db
    from core.video.sources import resolve_video_server
    return get_video_db().add_videos_to_wishlist(playlist, videos, server_source=resolve_video_server())


def auto_video_scan_watchlist_playlists(
    config: Dict[str, Any],
    deps: AutomationDeps,
    *,
    fetch_playlists: Optional[Callable[[], List[Dict[str, Any]]]] = None,
    fetch_videos: Optional[Callable[[Any], List[Dict[str, Any]]]] = None,
    wishlisted_ids: Optional[Callable[[Any], Iterable]] = None,
    downloaded_ids: Optional[Callable[[Any], Iterable]] = None,
    dismissed_ids: Optional[Callable[[Any], Iterable]] = None,
    add_videos: Optional[Callable[[Dict[str, Any], List[Dict[str, Any]]], int]] = None,
    today_fn: Optional[Callable[[], str]] = None,
    seen_ids: Optional[Callable[[Any], Iterable]] = None,
    mark_seen: Optional[Callable[[Any, Iterable], None]] = None,
    backfill_fn: Optional[Callable[[], int]] = None,
) -> Dict[str, Any]:
    """Scan every followed YouTube playlist — the SAME 'cap + new' rule as channels.

    Returns ``{'status': 'completed', 'playlists': int, 'videos_added': int, ...}``."""
    fetch_playlists = fetch_playlists or _default_fetch_playlists
    fetch_videos = fetch_videos or _default_fetch_videos
    wishlisted_ids = wishlisted_ids or _default_wishlisted_ids
    downloaded_ids = downloaded_ids or _default_downloaded_ids
    dismissed_ids = dismissed_ids or _default_dismissed_ids
    add_videos = add_videos or _default_add_videos
    today_fn = today_fn or (lambda: date.today().isoformat())
    seen_ids = seen_ids or _default_seen_ids
    mark_seen = mark_seen or _default_mark_seen
    automation_id = config.get('_automation_id')
    min_seconds = max(0, int(config.get('min_seconds', 60) or 0))
    backfill = max(0, int((backfill_fn or _default_backfill_count)()))

    try:
        today = today_fn()
        deps.update_progress(automation_id, phase='Reading your watchlist…', progress=5,
                             log_line='Loading the playlists you follow', log_type='info')
        playlists = fetch_playlists() or []
        if not playlists:
            deps.update_progress(automation_id, status='finished', progress=100, phase='Complete',
                                 log_line='No playlists on the watchlist to scan', log_type='info')
            return {'status': 'completed', 'playlists': 0, 'videos_added': 0,
                    '_manages_own_progress': True}

        added = 0
        total = len(playlists)
        for i, pl in enumerate(playlists):
            pid = pl.get('playlist_id')
            ptitle = pl.get('title') or pid
            deps.update_progress(automation_id, phase='Scanning playlists…',
                                 progress=10 + int(80 * i / max(total, 1)),
                                 log_line="Checking '%s' for new videos" % ptitle, log_type='info')
            if not pid:
                continue
            try:
                videos = fetch_videos(pid) or []
            except Exception:   # noqa: BLE001 - one flaky playlist shouldn't abort the scan
                deps.update_progress(automation_id, log_line="Couldn't reach '%s' — skipping" % ptitle,
                                     log_type='warning')
                continue

            picks, baseline = select_playlist_additions(
                videos, seen_ids=seen_ids(pid), backfill_count=backfill,
                wishlisted_ids=wishlisted_ids(pid), downloaded_ids=downloaded_ids(pid),
                dismissed_ids=dismissed_ids(pid), today=today, min_seconds=min_seconds)
            if picks:
                n = int(add_videos({'youtube_id': pid, 'title': ptitle,
                                    'avatar_url': pl.get('poster_url')}, picks) or 0)
                added += n
                if n:
                    deps.update_progress(
                        automation_id, log_type='success',
                        log_line="Wishlisted %d new video(s) from '%s'" % (n, ptitle))
            if baseline:
                try:
                    mark_seen(pid, baseline)          # remember membership so later scans see only additions
                except Exception:   # noqa: BLE001 - baseline is best-effort; a miss just re-checks next scan
                    logger.debug("playlist seen-baseline write failed for %s", pid, exc_info=True)

        done = ('Wishlisted %d new video(s) across %d playlist(s)' % (added, total)) if added \
            else ('Playlists are up to date — nothing new across %d playlist(s)' % total)
        deps.update_progress(automation_id, status='finished', progress=100, phase='Complete',
                             log_line=done, log_type='success')
        return {'status': 'completed', 'playlists': total, 'videos_added': added,
                '_manages_own_progress': True}
    except Exception as e:  # noqa: BLE001
        deps.update_progress(automation_id, status='error', phase='Error', log_line=str(e), log_type='error')
        return {'status': 'error', 'error': str(e), '_manages_own_progress': True}
