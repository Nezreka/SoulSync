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

_automation_thread: Optional[threading.Thread] = None
_automation_running = False
_automation_lock = threading.Lock()
_stop_event = threading.Event()

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


def get_scan_status() -> Dict[str, Any]:
    """Return the status and stats of the podcast automation service."""
    with _automation_lock:
        return dict(_last_scan_status)


def scan_and_auto_download_podcasts(profile_id: int = 1) -> Dict[str, Any]:
    """Scan all watchlisted podcasts, auto-download new episodes, and prune expired ones.

    Can be triggered periodically by the background worker or manually via API.
    """
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

                    latest_ep = sorted_episodes[0]
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
                            "duration_seconds": latest_ep.duration_seconds,
                            "enclosure_type": latest_ep.enclosure_type or "audio/mpeg",
                            "enclosure_length": latest_ep.enclosure_length,
                            "feed_url": feed_url,
                            "season": latest_ep.season,
                            "episode_number": latest_ep.episode_number,
                            "episode_type": latest_ep.episode_type,
                        }
                        res = queue_podcast_download(dl_payload)
                        if res.get("success"):
                            episodes_queued += 1
                            logger.info(
                                "Auto-download queued for latest episode of '%s': '%s'",
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


def _automation_loop():
    """Background worker loop that triggers scans at the configured interval."""
    logger.info("Podcast automation loop started")
    # Small initial delay to let server boot completely
    if _stop_event.wait(15):
        return

    while not _stop_event.is_set():
        try:
            scan_and_auto_download_podcasts()
        except Exception as e:
            logger.error("Unexpected error in podcast automation loop: %s", e)

        # Determine interval (default 30 minutes = 1800 seconds)
        interval_seconds = 1800
        try:
            from core.settings import config_manager
            if config_manager:
                mins = config_manager.get("podcasts.auto_download_interval_minutes", 30)
                interval_seconds = max(60, int(mins) * 60)
        except Exception as cfg_err:
            logger.debug("Could not read podcast interval config, using default: %s", cfg_err)

        if _stop_event.wait(interval_seconds):
            break

    logger.info("Podcast automation loop stopped")


def start_podcast_automation() -> None:
    """Start the podcast automation background thread if not already running."""
    global _automation_thread, _automation_running
    with _automation_lock:
        if _automation_running:
            return
        _stop_event.clear()
        _automation_thread = threading.Thread(
            target=_automation_loop,
            daemon=True,
            name="podcast-automation-worker",
        )
        _automation_running = True
        _automation_thread.start()
        logger.info("Podcast automation background service started")


def stop_podcast_automation() -> None:
    """Signal the podcast automation worker to stop."""
    global _automation_running
    with _automation_lock:
        _stop_event.set()
        _automation_running = False
