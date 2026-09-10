"""Podcast Automation Service — background monitoring, auto-download, and retention management.

Features:
- Periodic or on-demand scanning of watchlisted podcasts.
- Ingests RSS feeds via PodcastClient and updates episode counts and scan timestamps.
- Automatically queues downloads for newly discovered episodes if auto_download is enabled.
- Avoids re-downloading episodes already downloaded or tracked in history/download tables.
- Applies retention policies: deletes downloaded audio files on disk and marks records
  as pruned when they exceed the configured retention_days.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from core.podcast_client import get_podcast_client
from utils.logging_config import get_logger

logger = get_logger("podcasts.automation")

_automation_lock = threading.Lock()

# Last scan summary state for observability
_last_scan_status: Dict[str, Any] = {
    "last_scan_time": None,
    "podcasts_checked": 0,
    "episodes_queued": 0,
    "episodes_pruned": 0,
    "errors": [],
    "in_progress": False,
}


def _get_db():
    try:
        from database.music_database import get_database
        return get_database()
    except Exception as e:
        logger.error("Failed to acquire music database: %s", e)
        return None


def _as_utc(value: Any) -> Optional[datetime]:
    """Best effort parse of a timestamp into an aware UTC datetime, else None.

    date_added comes back from sqlite as a naive string in UTC, while an
    episode's pub_date is already aware. Comparing those two directly raises,
    so both ends go through here first.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def get_scan_status() -> Dict[str, Any]:
    """Return the status and stats of the podcast automation service."""
    with _automation_lock:
        return dict(_last_scan_status)


# How many missing episodes one show may claim in a single pass.
#
# The scan used to take the newest episode and nothing else, which loses the
# backlog silently: a show that posts three between two passes had two of them
# skipped forever, because the next pass looks at the newest again, finds it
# already downloaded, and never looks further back. Nobody sees an error - the
# episodes simply never arrive.
#
# It is a cap rather than "everything missing" because the first scan after
# somebody follows a show with 800 episodes should not queue 800 downloads.
# Each pass takes a bite and the backlog drains over a few of them.
MAX_BACKLOG_PER_SHOW_PER_PASS = 5


def scan_and_auto_download_podcasts(profile_id: Optional[int] = None) -> Dict[str, Any]:
    """Scan watchlisted podcasts, auto-download new episodes, and prune expired ones.

    profile_id None means every profile that follows anything, which is what a
    timed pass needs: a system automation runs as profile 1, so scoping to the
    caller meant a second person's shows were never scanned at all. Pass an
    explicit profile to scan just that one, which is what a person clicking
    Scan Now wants.
    """
    if profile_id is None:
        db = _get_db()
        if not db:
            return {"success": False, "error": "Database unavailable"}
        try:
            profiles = [int(p) for p in (db.profiles_with_podcast_rows() or [])]
        except Exception as exc:                             # noqa: BLE001
            # a database that hands back something unusable must not stop the
            # scan, it just means we fall back to the one profile that has
            # always been scanned
            logger.debug("Could not list podcast profiles: %s", exc)
            profiles = [1]
        if not profiles:
            profiles = [1]
        totals = {"success": True, "podcasts_checked": 0, "episodes_queued": 0,
                  "episodes_pruned": 0, "errors": []}
        for pid in profiles:
            one = scan_and_auto_download_podcasts(profile_id=pid)
            totals["podcasts_checked"] += one.get("podcasts_checked", 0)
            totals["episodes_queued"] += one.get("episodes_queued", 0)
            totals["episodes_pruned"] += one.get("episodes_pruned", 0)
            totals["errors"].extend(one.get("errors", []))
        totals["success"] = not totals["errors"]
        totals["profiles_scanned"] = len(profiles)
        return totals
    global _last_scan_status
    with _automation_lock:
        if _last_scan_status["in_progress"]:
            logger.info("Podcast automation scan already in progress; skipping.")
            return {"success": False, "error": "Scan already in progress", "in_progress": True}
        _last_scan_status["in_progress"] = True

    db = _get_db()
    if not db:
        with _automation_lock:
            _last_scan_status["in_progress"] = False
        return {"success": False, "error": "Database unavailable"}

    podcasts_checked = 0
    episodes_queued = 0
    episodes_pruned = 0
    scan_errors = []

    try:
        podcasts = db.get_watchlist_podcasts(profile_id=profile_id)
        client = get_podcast_client()

        for pod in podcasts:
            feed_url = pod.get("feed_url")
            if not feed_url:
                continue

            podcasts_checked += 1
            show_title = pod.get("title") or "Podcast"
            author = pod.get("author") or ""
            auto_download = bool(pod.get("auto_download", False))
            retention_days = pod.get("retention_days")

            try:
                # 1. Fetch live RSS feed
                show = client.fetch_feed(feed_url)
                if not show:
                    logger.warning("Podcast feed fetch returned None for: %s", feed_url)
                    continue

                # 2. Update scan timestamp and total episode count
                ep_count = len(show.episodes) if show.episodes else 0
                db.mark_watchlist_podcast_scanned(feed_url, episode_count=ep_count)

                # 3. If auto_download is enabled, find the single most recent episode
                if auto_download and show.episodes:
                    from api.podcasts import queue_podcast_download

                    def _ep_sort_key(e):
                        p = getattr(e, "pub_date", None)
                        if p and hasattr(p, "timestamp"):
                            return p.timestamp()
                        return 0.0

                    # If pub_dates are available, sort newest first;
                    # otherwise keep RSS feed's natural top-to-bottom order (standard newest-first).
                    if any(getattr(e, "pub_date", None) for e in show.episodes):
                        sorted_episodes = sorted(show.episodes, key=_ep_sort_key, reverse=True)
                    else:
                        sorted_episodes = list(show.episodes)

                    # What counts as new is "published since you followed the
                    # show", not "the newest one". This used to take
                    # sorted_episodes[0] and stop, which does protect against a
                    # first sync grabbing an 800 episode back catalogue - but it
                    # threw away the ordinary case with it: three episodes
                    # posted between two passes meant two of them silently never
                    # arrived, because the next pass looks at the newest again,
                    # finds it downloaded, and never looks further back.
                    #
                    # A follow date settles both. Nothing older than the day you
                    # followed is ever claimed, and everything since is, capped
                    # per pass so even a long gap drains over a few runs rather
                    # than in one flood.
                    #
                    # No usable follow date means an older row, so it keeps
                    # exactly the behaviour it has always had: newest only.
                    followed_at = _as_utc(pod.get("date_added"))
                    if followed_at is None:
                        sorted_episodes = sorted_episodes[:1]

                    claimed = 0
                    for latest_ep in sorted_episodes:
                        if claimed >= MAX_BACKLOG_PER_SHOW_PER_PASS:
                            break
                        if followed_at is not None:
                            published = _as_utc(getattr(latest_ep, "pub_date", None))
                            # an episode with no date at all is treated as new:
                            # a feed that omits pub_date would otherwise never
                            # auto-download anything
                            if published is not None and published < followed_at:
                                break
                        enclosure_url = (latest_ep.enclosure_url or "").strip()
                        guid = (latest_ep.guid or enclosure_url).strip()
                        title = (latest_ep.title or "Episode").strip()

                        if enclosure_url and not db.is_podcast_episode_downloaded(
                            feed_url=feed_url,
                            enclosure_url=enclosure_url,
                            guid=guid,
                            title=title,
                        ):
                            dl_payload = {
                                "enclosure_url": enclosure_url,
                                "title": title,
                                "guid": guid,
                                "show_title": show.title or show_title,
                                "author": show.author or author,
                                "pub_date": latest_ep.pub_date.isoformat() if latest_ep.pub_date else None,
                                "artwork_url": latest_ep.artwork_url or show.artwork_url or pod.get("artwork_url") or "",
                                "show_artwork_url": show.artwork_url or pod.get("artwork_url") or "",
                                "show_description": show.description or pod.get("description") or "",
                                "description": latest_ep.description or "",
                                "show_notes": latest_ep.show_notes or latest_ep.description or "",
                                "duration_seconds": latest_ep.duration_seconds,
                                "enclosure_type": latest_ep.enclosure_type or "audio/mpeg",
                                "enclosure_length": latest_ep.enclosure_length,
                                "feed_url": feed_url,
                                "season": latest_ep.season,
                                "episode_number": latest_ep.episode_number,
                                "episode_type": latest_ep.episode_type,
                                "itunes_id": show.itunes_id or pod.get("itunes_id"),
                                "website": show.website or pod.get("website") or "",
                                "categories": show.categories or [],
                            }
                            res = queue_podcast_download(dl_payload)
                            if res.get("success"):
                                episodes_queued += 1
                                claimed += 1
                                logger.info(
                                    "Auto-download queued for '%s': '%s'",
                                    show_title,
                                    title,
                                )

                # 4. Prune expired episodes if retention_days is configured (> 0)
                if retention_days and int(retention_days) > 0:
                    prune_cutoff_seconds = int(retention_days) * 86400
                    now_ts = time.time()

                    downloaded_records = db.get_downloaded_podcast_episodes(
                        feed_url=feed_url,
                        unpruned_only=True,
                    )

                    for rec in downloaded_records:
                        dl_time_str = rec.get("downloaded_at")
                        rec_ts = None
                        if dl_time_str:
                            try:
                                dt = datetime.fromisoformat(str(dl_time_str).replace("Z", "+00:00"))
                                rec_ts = dt.timestamp()
                            except Exception as parse_err:
                                logger.debug("Failed to parse podcast downloaded_at %r: %s", dl_time_str, parse_err)

                        # If record age exceeds retention_days, prune file
                        if rec_ts and (now_ts - rec_ts) >= prune_cutoff_seconds:
                            file_path = rec.get("file_path")
                            if file_path and os.path.exists(file_path):
                                try:
                                    os.remove(file_path)
                                    logger.info(
                                        "Pruned expired podcast file (%d days retention): %s",
                                        retention_days,
                                        file_path,
                                    )
                                    # Clean up empty parent directory if empty
                                    parent = os.path.dirname(file_path)
                                    if parent and os.path.isdir(parent) and not os.listdir(parent):
                                        try:
                                            os.rmdir(parent)
                                        except OSError:
                                            pass
                                except Exception as del_err:
                                    logger.warning("Failed to remove file %s during prune: %s", file_path, del_err)

                            # Mark pruned in DB
                            db.mark_podcast_episode_pruned(rec["id"])
                            episodes_pruned += 1

            except Exception as pod_err:
                logger.error("Error processing podcast %s: %s", feed_url, pod_err)
                scan_errors.append(f"{feed_url}: {str(pod_err)}")

    finally:
        with _automation_lock:
            _last_scan_status = {
                "last_scan_time": datetime.now(timezone.utc).isoformat(),
                "podcasts_checked": podcasts_checked,
                "episodes_queued": episodes_queued,
                "episodes_pruned": episodes_pruned,
                "errors": scan_errors,
                "in_progress": False,
            }

    return {
        "success": len(scan_errors) == 0,
        "podcasts_checked": podcasts_checked,
        "episodes_queued": episodes_queued,
        "episodes_pruned": episodes_pruned,
        "errors": scan_errors,
    }


# The scan is scheduled by the shared automation engine as the
# ``scan_watchlist_podcasts`` system automation, the same way audiobooks drain
# through ``audiobook_process_wishlist`` and artists through ``scan_watchlist``.
#
# There used to be a private daemon thread and a timer here as well. It was
# never started outside a test, so it scheduled nothing, but it is worth saying
# why it is gone rather than leaving it for somebody to wire up: an automation
# shows on the Automations page, can be paused, rescheduled or run by hand, and
# obeys the master switch. A private thread is none of those, and a user who
# wanted it to stop would have had nowhere to say so.
