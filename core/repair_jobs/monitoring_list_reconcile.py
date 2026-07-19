"""Repair Library-v2 monitoring against the Watchlist/Wishlist mirrors."""

from __future__ import annotations

from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair.monitoring_list_reconcile")


@register_job
class MonitoringListReconcileJob(RepairJob):
    """Reassert definitive monitoring and drain crash-surviving mirror ops."""

    job_id = "monitoring_list_reconcile"
    display_name = "Monitoring List Reconcile"
    description = "Repair Artist↔Watchlist and wanted Track↔Wishlist drift."
    help_text = (
        "Retries pending Watchlist/Wishlist mirror operations, reconciles "
        "artist monitoring with the Watchlist, and reasserts monitored missing "
        "or upgrade-eligible tracks into the Wishlist. Explicit Library-v2 "
        "monitoring wins; imported/default artist flags follow the Watchlist. "
        "No files are changed."
    )
    icon = "refresh-cw"
    default_enabled = True
    default_interval_hours = 1
    default_settings = {"batch_size": 500, "keep_done": 500}
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
        for key, maximum in (("batch_size", 5000), ("keep_done", 10000)):
            try:
                settings[key] = max(0 if key == "keep_done" else 1,
                                    min(int(settings[key]), maximum))
            except (TypeError, ValueError):
                settings[key] = self.default_settings[key]
        return settings

    def estimate_scope(self, context: JobContext) -> int:
        try:
            conn = context.db._get_connection()
            try:
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
            finally:
                conn.close()
        except Exception:  # noqa: BLE001
            return 0

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()
        try:
            if context.config_manager.get("features.library_v2", False) is not True:
                return result
        except Exception:  # noqa: BLE001
            return result
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

            if context.check_stop() or context.wait_if_paused():
                return result
            artist_stats = reconcile_artist_watchlist(
                context.db, profile_id=ADMIN_PROFILE_ID,
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
                profile_id=ADMIN_PROFILE_ID,
                batch=settings["batch_size"],
                should_stop=lambda: context.check_stop() or context.wait_if_paused(),
                progress=context.update_progress,
            )
            result.scanned += int(wishlist_stats["scanned"])
            result.auto_fixed += int(wishlist_stats["mirrored"])

            conn = context.db._get_connection()
            try:
                prune_done(conn, keep=settings["keep_done"])
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001
            logger.error("monitoring-list reconcile failed: %s", exc, exc_info=True)
            result.errors += 1
        return result


__all__ = ["MonitoringListReconcileJob"]
