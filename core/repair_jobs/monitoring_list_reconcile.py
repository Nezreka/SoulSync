"""Repair Library-v2 monitoring against the Watchlist/Wishlist mirrors."""

from __future__ import annotations

from contextlib import closing

from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair.monitoring_list_reconcile")


@register_job
class MonitoringListReconcileJob(RepairJob):
    """Reassert definitive monitoring and drain crash-surviving mirror ops."""

    job_id = "monitoring_list_reconcile"
    display_name = "Watchlist & Wishlist Reconcile"
    description = "Repair Artist↔Watchlist and wanted Track↔Wishlist drift."
    help_text = (
        "Retries pending Watchlist/Wishlist mirror operations, reconciles "
        "artist monitoring with the Watchlist, and reasserts monitored missing "
        "tracks into the Wishlist. Existing external Wishlist "
        "entries first restore their concrete track monitoring. Explicit Library "
        "monitoring wins; imported/default artist flags follow the Watchlist. "
        "No files are changed.\n\n"
        "This is the single writer that puts wanted tracks with no file back "
        "into the Wishlist. Refresh & Scan already reports what it changed "
        "straight to it, so on a healthy install it should mostly find "
        "nothing to do there.\n\n"
        "It also asks for better copies of tracks you have below their quality "
        "profile, a few each run (Upgrades per run, 0 turns it off). A track "
        "that found nothing better waits before it is asked for again, and the "
        "wait doubles each time, starting at Upgrade retry days. A search that "
        "found nothing in 12 hours leaves the Wishlist until its next turn. "
        "Enhance Quality asks for chosen tracks right away."
    )
    icon = "refresh-cw"
    default_enabled = True
    default_interval_hours = 1
    # upgrades_per_run: owned tracks below their quality profile asked for a
    # better copy each run (0 turns automatic upgrades off);
    # upgrade_retry_days: the first wait before a track is asked for again
    default_settings = {"batch_size": 500, "keep_done": 500,
                        "upgrades_per_run": 5, "upgrade_retry_days": 7}
    auto_fix = True

    def _settings(self, context: JobContext) -> dict:
        settings = dict(self.default_settings)
        try:
            configured = context.config_manager.get(
                f"repair.jobs.{self.job_id}.settings", {},
            )
            if isinstance(configured, dict):
                settings.update(configured)
        except Exception as exc:  # noqa: BLE001
            logger.debug("monitoring-list settings lookup failed: %s", exc)
        for key, minimum, maximum in (("batch_size", 1, 5000), ("keep_done", 0, 10000),
                                      ("upgrades_per_run", 0, 500),
                                      ("upgrade_retry_days", 1, 90)):
            try:
                settings[key] = max(minimum, min(int(settings[key]), maximum))
            except (TypeError, ValueError):
                settings[key] = self.default_settings[key]
        return settings

    def estimate_scope(self, context: JobContext) -> int:
        try:
            with closing(context.db._get_connection()) as conn:
                from core.library2 import ADMIN_PROFILE_ID
                pending = int(conn.execute(
                    "SELECT COUNT(*) FROM lib2_mirror_outbox WHERE status='pending'"
                ).fetchone()[0])
                artists = int(conn.execute(
                    "SELECT COUNT(*) FROM lib2_artists"
                ).fetchone()[0])
                wanted = int(conn.execute(
                    "SELECT COUNT(*) FROM lib2_wanted_tracks "
                    "WHERE profile_id=? AND wanted=1",
                    (ADMIN_PROFILE_ID,),
                ).fetchone()[0])
                return pending + artists + wanted
        except Exception:  # noqa: BLE001
            return 0

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()
        if context.check_stop() or context.wait_if_paused():
            return result

        from core.library2 import ADMIN_PROFILE_ID
        from core.library2.mirror_outbox import drain, prune_done
        from core.library2.monitor_sync import (
            reconcile_artist_watchlist,
            reconcile_track_wishlist,
        )

        settings = self._settings(context)
        try:
            pending = drain(context.db, limit=settings["batch_size"])
            result.auto_fixed += int(pending["done"])
            result.errors += int(pending["failed"])
            result.scanned += int(pending["done"]) + int(pending["failed"])

            # every library's lists (E-14): the shared one's, and each own
            # library's under its profile
            from core.library_scope import own_library_ids
            for profile_id in (ADMIN_PROFILE_ID, *sorted(own_library_ids())):
                if context.check_stop() or context.wait_if_paused():
                    return result
                artist_stats = reconcile_artist_watchlist(
                    context.db, profile_id=profile_id,
                )
                result.scanned += int(artist_stats["scanned"])
                result.auto_fixed += (
                    int(artist_stats["monitor_flags_changed"])
                    + int(artist_stats["mirrored"])
                )

                if context.check_stop() or context.wait_if_paused():
                    return result
                wishlist_stats = reconcile_track_wishlist(
                    context.db,
                    profile_id=profile_id,
                    batch=settings["batch_size"],
                    # never wait_if_paused here: the reconcile holds a write
                    # transaction between chunks
                    should_stop=context.stop_or_paused,
                    progress=context.update_progress,
                    upgrades_per_run=settings["upgrades_per_run"],
                    upgrade_retry_days=settings["upgrade_retry_days"],
                )
                result.scanned += int(wishlist_stats["scanned"]) + int(wishlist_stats.get('intent_repaired', 0))
                result.auto_fixed += int(wishlist_stats["mirrored"]) + int(wishlist_stats.get('intent_repaired', 0))

            with closing(context.db._get_connection()) as conn:
                prune_done(conn, keep=settings["keep_done"])
                conn.commit()
        except Exception as exc:  # noqa: BLE001
            logger.error("monitoring-list reconcile failed: %s", exc, exc_info=True)
            result.errors += 1
        return result


__all__ = ["MonitoringListReconcileJob"]
