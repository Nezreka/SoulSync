"""Periodically reconcile pending Library-v2 mirror-outbox operations.

Request handlers opportunistically drain their own outbox rows, but a process
can stop after the atomic Lib2 commit and before that drain. This maintenance
job closes that crash window using the existing scheduler. It deliberately
does not reset terminal ``failed`` rows; those remain visible for an operator
to inspect and retry explicitly.
"""

from __future__ import annotations

from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair.lib2_mirror_reconcile")


@register_job
class Lib2MirrorReconcileJob(RepairJob):
    job_id = "lib2_mirror_reconcile"
    display_name = "Library v2 Mirror Reconcile"
    description = "Retry pending Library-v2 Wishlist/Watchlist mirror operations."
    help_text = (
        "Replays pending transactional outbox rows that survived a restart or "
        "temporary database failure. Completed history is bounded; terminal "
        "failed rows remain visible and require an explicit retry. No library "
        "files are touched."
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
                f"repair.jobs.{self.job_id}.settings", {})
            if isinstance(configured, dict):
                settings.update(configured)
        except Exception:
            pass
        try:
            batch_size = int(settings["batch_size"])
        except (TypeError, ValueError):
            batch_size = self.default_settings["batch_size"]
        try:
            keep_done = int(settings["keep_done"])
        except (TypeError, ValueError):
            keep_done = self.default_settings["keep_done"]
        settings["batch_size"] = max(1, min(batch_size, 5000))
        settings["keep_done"] = max(0, min(keep_done, 10000))
        return settings

    @staticmethod
    def _pending_count(context: JobContext) -> int:
        conn = context.db._get_connection()
        try:
            return int(conn.execute(
                "SELECT COUNT(*) FROM lib2_mirror_outbox WHERE status='pending'"
            ).fetchone()[0])
        finally:
            conn.close()

    def estimate_scope(self, context: JobContext) -> int:
        try:
            return self._pending_count(context)
        except Exception:
            return 0

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()
        try:
            if context.config_manager.get("features.library_v2", False) is not True:
                return result
        except Exception:
            return result
        if context.check_stop() or context.wait_if_paused():
            return result

        from core.library2.mirror_outbox import drain, prune_done

        settings = self._settings(context)
        outcome = drain(context.db, limit=settings["batch_size"])
        result.auto_fixed = int(outcome["done"])
        result.errors = int(outcome["failed"])
        result.scanned = result.auto_fixed + result.errors

        conn = context.db._get_connection()
        try:
            pruned = prune_done(conn, keep=settings["keep_done"])
            conn.commit()
        finally:
            conn.close()
        if result.scanned or pruned:
            logger.info(
                "lib2 mirror reconcile: %d done, %d failed, %d old rows pruned",
                result.auto_fixed, result.errors, pruned,
            )
        return result
