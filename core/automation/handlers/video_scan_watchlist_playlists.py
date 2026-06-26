"""Automation handler: ``video_scan_watchlist_playlists`` action.

Sibling of the watchlist-CHANNELS scan, with a deliberately different rule. A channel is a
creator posting new stuff over time, so that scan is forward-looking (new uploads + a
last-N net). A **playlist** is a curated, finite set someone assembled — the reason you
follow it is "give me this whole list and keep it complete." So this scan MIRRORS the
playlist: it wishlists every long-form video in it you don't already have, plus anything
later added. No follow-date baseline, no last-N net.

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
from core.automation.handlers.video_scan_watchlist_channels import _day, long_form_uploads

# how many playlist entries to read per scan (yt-dlp flat). Big enough for almost any real
# playlist; genuinely huge ones truncate (logged) rather than hammering YouTube each scan.
_PLAYLIST_FETCH_LIMIT = 1000


def select_playlist_video_gaps(
    videos: List[Dict[str, Any]],
    *,
    wishlisted_ids: Iterable = (),
    downloaded_ids: Iterable = (),
    dismissed_ids: Iterable = (),
    today: str,
    min_seconds: int = 60,
) -> List[Dict[str, Any]]:
    """The pure core: every long-form video in a playlist not already wishlisted /
    downloaded / dismissed (mirror the whole list). Future-dated (unaired) entries are
    skipped. No baseline, no last-N — a curated playlist is wanted in full. No I/O."""
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
    return []   # populated once the download-history ledger carries youtube grabs


def _default_dismissed_ids(playlist_id: Any) -> List[Any]:
    return []   # no youtube "dismissed" store yet (see channels-scan note)


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
) -> Dict[str, Any]:
    """Mirror every followed YouTube playlist into the wishlist (whole list + new additions).

    Returns ``{'status': 'completed', 'playlists': int, 'videos_added': int, ...}``."""
    fetch_playlists = fetch_playlists or _default_fetch_playlists
    fetch_videos = fetch_videos or _default_fetch_videos
    wishlisted_ids = wishlisted_ids or _default_wishlisted_ids
    downloaded_ids = downloaded_ids or _default_downloaded_ids
    dismissed_ids = dismissed_ids or _default_dismissed_ids
    add_videos = add_videos or _default_add_videos
    today_fn = today_fn or (lambda: date.today().isoformat())
    automation_id = config.get('_automation_id')
    min_seconds = max(0, int(config.get('min_seconds', 60) or 0))

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
                                 log_line="Mirroring '%s'" % ptitle, log_type='info')
            if not pid:
                continue
            try:
                videos = fetch_videos(pid) or []
            except Exception:   # noqa: BLE001 - one flaky playlist shouldn't abort the scan
                deps.update_progress(automation_id, log_line="Couldn't reach '%s' — skipping" % ptitle,
                                     log_type='warning')
                continue

            gaps = select_playlist_video_gaps(
                videos, wishlisted_ids=wishlisted_ids(pid), downloaded_ids=downloaded_ids(pid),
                dismissed_ids=dismissed_ids(pid), today=today, min_seconds=min_seconds)
            if gaps:
                n = int(add_videos({'youtube_id': pid, 'title': ptitle,
                                    'avatar_url': pl.get('poster_url')}, gaps) or 0)
                added += n
                if n:
                    deps.update_progress(
                        automation_id, log_type='success',
                        log_line="Wishlisted %d new video(s) from '%s'" % (n, ptitle))

        done = ('Wishlisted %d new video(s) across %d playlist(s)' % (added, total)) if added \
            else ('Playlists are up to date — nothing new across %d playlist(s)' % total)
        deps.update_progress(automation_id, status='finished', progress=100, phase='Complete',
                             log_line=done, log_type='success')
        return {'status': 'completed', 'playlists': total, 'videos_added': added,
                '_manages_own_progress': True}
    except Exception as e:  # noqa: BLE001
        deps.update_progress(automation_id, status='error', phase='Error', log_line=str(e), log_type='error')
        return {'status': 'error', 'error': str(e), '_manages_own_progress': True}
