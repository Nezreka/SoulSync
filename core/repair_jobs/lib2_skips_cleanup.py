"""Housekeeping for Library-v2 manual-skip overrides (``lib2_manual_skips``).

When a user manually downloads while skipping checks (AcoustID / quality),
Library v2 records the override so cleanup/repair jobs respect it instead of
re-flagging the file. Those rows only matter while the file exists and the
override is recent — this job consumes stale ones:

- rows whose ``file_path`` no longer exists on disk (the override's subject is
  gone, nothing left to protect), and
- rows older than the retention window (the user's one-time decision shouldn't
  shield a file forever from ever-improving checks).

Deletes ONLY Library-v2 audit rows — never
files, never findings.
"""

from __future__ import annotations

from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair.skip_audit_cleanup")


@register_job
class Lib2SkipsCleanupJob(RepairJob):
    job_id = "skip_audit_cleanup"
    display_name = "Skip-Audit Cleanup"
    description = "Expire stale manual check-skip overrides (missing files, past retention)."
    help_text = (
        "Manual downloads made with 'skip AcoustID/quality check' are recorded "
        "in the library audit so later repair jobs honor the override. "
        "This job removes entries whose file no longer exists and entries older "
        "than the retention window, so an old one-time decision doesn't shield "
        "files from checks forever. Only audit rows are deleted — never files."
    )
    icon = "broom"
    default_enabled = False
    default_interval_hours = 168  # weekly
    default_settings = {
        "retention_days": 180,
    }
    auto_fix = True

    def _get_settings(self, context: JobContext) -> dict:
        """Job settings from config, merged with defaults (established pattern)."""
        merged = dict(self.default_settings)
        try:
            cfg = context.config_manager.get(f"repair.jobs.{self.job_id}.settings", {})
            if isinstance(cfg, dict):
                merged.update(cfg)
        except Exception as e:  # noqa: BLE001
            logger.debug("settings read failed, using defaults: %s", e)
        return merged

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()
        from core.library2.feature import library_v2_enabled
        library_v2_enabled(context.config_manager)

        settings = self._get_settings(context)
        try:
            retention_days = int(settings.get("retention_days", 180))
        except (TypeError, ValueError):
            retention_days = 180

        conn = context.db._get_connection()
        try:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='lib2_manual_skips'"
            ).fetchone()
            if not exists:
                return result

            rows = conn.execute(
                "SELECT id, file_path, created_at FROM lib2_manual_skips"
            ).fetchall()
            expired = conn.execute(
                "SELECT id FROM lib2_manual_skips "
                "WHERE created_at < datetime('now', ?)",
                (f"-{retention_days} days",),
            ).fetchall()
            expired_ids = {r["id"] for r in expired}

            # Stored paths may be the media server's filesystem view — resolve
            # through the shared lib2 resolver, otherwise a path-mapped setup
            # would look 100% missing and this job would wipe the whole audit.
            from core.library2.paths import resolve_lib2_path

            to_delete = set(expired_ids)
            for row in rows:
                if context.check_stop():
                    break
                result.scanned += 1
                path = row["file_path"]
                if path and resolve_lib2_path(
                        path, config_manager=context.config_manager) is None:
                    to_delete.add(row["id"])

            for skip_id in to_delete:
                conn.execute("DELETE FROM lib2_manual_skips WHERE id=?", (skip_id,))
            conn.commit()
            result.auto_fixed = len(to_delete)
            if to_delete:
                logger.info("lib2 skip-audit cleanup: %d stale overrides removed "
                            "(%d past retention)", len(to_delete), len(expired_ids))
        except Exception as e:  # noqa: BLE001
            logger.error("lib2 skip-audit cleanup failed: %s", e, exc_info=True)
            result.errors += 1
        finally:
            conn.close()
        return result
