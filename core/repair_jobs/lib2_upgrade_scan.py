"""Periodic Library-v2 quality-upgrade scan.

Re-checks every monitored Library-v2 track that HAS a file under an upgrade
policy (``until_top``/``until_cutoff``) and queues genuine upgrade candidates
into the Wishlist — same logic as the manual "Search Upgrades" button
(``core/library2/wishlist_mirror``), just on the repair-worker cadence so
upgrades keep flowing without the user pressing anything.

Scheduled runs have no request context; wishlist mirrors are pinned to the
admin profile explicitly (ADR-01: Library v2 is admin-only, and every other
scheduled acquisition path is scoped the same way) — never to whatever a
default parameter happens to be.

No-op when ``features.library_v2`` is off. Never touches files.
"""

from __future__ import annotations

from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair.lib2_upgrade_scan")


@register_job
class Lib2UpgradeScanJob(RepairJob):
    job_id = "lib2_upgrade_scan"
    display_name = "Library v2 Upgrade Scan"
    description = "Queue monitored Library-v2 tracks below their quality profile's cutoff for upgrade."
    help_text = (
        "Scans the opt-in Library v2 for monitored tracks whose file sits below "
        "the assigned quality profile's upgrade cutoff (profiles with policy "
        "'upgrade until cutoff/top'). Genuine candidates are added to the "
        "Wishlist carrying their quality profile, so the normal download "
        "pipeline searches for and imports the better version. Does nothing "
        "when the Library v2 feature flag is off."
    )
    icon = "arrow-up-circle"
    default_enabled = False
    default_interval_hours = 24
    auto_fix = True  # queueing IS the fix; there is nothing to review

    def estimate_scope(self, context: JobContext) -> int:
        try:
            conn = context.db._get_connection()
            try:
                from core.library2.wishlist_mirror import upgrade_candidate_track_ids
                return len(upgrade_candidate_track_ids(conn))
            finally:
                conn.close()
        except Exception:
            return 0

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()
        try:
            if context.config_manager.get("features.library_v2", False) is not True:
                logger.debug("Library v2 disabled — upgrade scan skipped")
                return result
        except Exception:
            return result

        from core.library2.wishlist_mirror import (
            mirror_tracks_wishlist,
            upgrade_candidate_track_ids,
        )

        conn = context.db._get_connection()
        try:
            track_ids = upgrade_candidate_track_ids(conn)
            total = len(track_ids)
            queued = 0
            # Mirror in small batches so stop/pause requests take effect quickly
            # and progress is visible.
            batch = 25
            for start in range(0, total, batch):
                if context.check_stop() or context.wait_if_paused():
                    break
                chunk = track_ids[start:start + batch]
                from core.library2 import ADMIN_PROFILE_ID
                queued += mirror_tracks_wishlist(context.db, conn, chunk, True,
                                                 profile_id=ADMIN_PROFILE_ID)
                result.scanned += len(chunk)
                if context.update_progress:
                    context.update_progress(result.scanned, total)
            result.auto_fixed = queued
            logger.info("lib2 upgrade scan: %d checked, %d queued", result.scanned, queued)
        except Exception as e:  # noqa: BLE001
            logger.error("lib2 upgrade scan failed: %s", e, exc_info=True)
            result.errors += 1
        finally:
            conn.close()
        return result
