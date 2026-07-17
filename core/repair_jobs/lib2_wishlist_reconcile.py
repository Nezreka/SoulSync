"""Periodically re-assert the Library-v2 wanted projection into the Wishlist.

The lib2 → Wishlist mirror is edge-triggered: it fires only when the user
toggles monitoring. But the Wishlist is a volatile queue — entries leave when
downloaded, cleared, or aged out — so a track that is authoritatively ``wanted``
and still missing never re-enters the Wishlist once its entry is gone (§69.1
"Ein monitored missing Track muss in der Wishlist landen"). This job closes that
gap on the repair-worker cadence: it recomputes the projection and mirrors it,
re-adding wanted+missing tracks and pruning stale entries. Respects the
ignore-list (deliberate user cancels stay cancelled). No-op when the feature
flag is off. Never touches files.
"""

from __future__ import annotations

from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair.lib2_wishlist_reconcile")


@register_job
class Lib2WishlistReconcileJob(RepairJob):
    job_id = "lib2_wishlist_reconcile"
    display_name = "Library v2 Wishlist Reconcile"
    description = "Re-add monitored+missing Library-v2 tracks that dropped out of the Wishlist."
    help_text = (
        "Re-derives the authoritative Library v2 wanted projection and mirrors it "
        "into the Wishlist: monitored tracks that are still missing but whose "
        "Wishlist entry was downloaded, cleared, or aged away get re-added, and "
        "entries whose track is no longer wanted get pruned. Deliberate user "
        "cancels (ignore-list) are respected. Does nothing when the Library v2 "
        "feature flag is off."
    )
    icon = "list-checks"
    default_enabled = True
    default_interval_hours = 6
    auto_fix = True  # queueing IS the fix; there is nothing to review

    def estimate_scope(self, context: JobContext) -> int:
        try:
            conn = context.db._get_connection()
            try:
                from core.library2 import ADMIN_PROFILE_ID
                from core.library2.wanted import wanted_track_ids
                return len(wanted_track_ids(conn, profile_id=ADMIN_PROFILE_ID))
            finally:
                conn.close()
        except Exception:
            return 0

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()
        try:
            if context.config_manager.get("features.library_v2", False) is not True:
                logger.debug("Library v2 disabled — wishlist reconcile skipped")
                return result
        except Exception:
            return result
        if context.check_stop() or context.wait_if_paused():
            return result

        from core.library2 import ADMIN_PROFILE_ID
        from core.library2.monitor_sync import reconcile_track_wishlist

        try:
            stats = reconcile_track_wishlist(
                context.db,
                profile_id=ADMIN_PROFILE_ID,
                should_stop=lambda: context.check_stop() or context.wait_if_paused(),
                progress=context.update_progress,
            )
            result.scanned = int(stats.get("scanned", 0))
            result.auto_fixed = int(stats.get("mirrored", 0))
        except Exception as e:  # noqa: BLE001
            logger.error("lib2 wishlist reconcile failed: %s", e, exc_info=True)
            result.errors += 1
        return result
