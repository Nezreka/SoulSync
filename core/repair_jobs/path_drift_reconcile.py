"""Stale index path reconcile — the review half of pathdrift25-01.

``lib2_track_files.path`` can point at a filename that no longer exists while
the file itself sits in that very directory under a slightly different name
(a template/disc-prefix change, a rename that only reached the legacy index).
The shared resolver cannot repair that, so the track's metadata scan stays
``pending`` forever and the missing lifecycle starts walking a present file
towards ``missing_confirmed``.

The forward fix (H-13: reorganize writes both indices atomically) does nothing
for data that drifted *before* it landed. This job is the promised backfill,
and it is deliberately review-only: it reads directories, proposes at most one
unambiguous replacement per row, and leaves ambiguous cases to a human. It
never renames, moves, creates or deletes a file — approving a finding only
repoints the catalogue at a file that is already there.
"""

from __future__ import annotations

from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair.path_drift_reconcile")


@register_job
class PathDriftReconcileJob(RepairJob):
    job_id = "path_drift_reconcile"
    display_name = "Stale Index Paths"
    description = "Find catalogue rows whose file was renamed underneath them"
    help_text = (
        "Some library rows point at a filename that no longer exists even "
        "though the song is still in that folder under a slightly different "
        "name — usually after a naming-template change. Those tracks show a "
        "metadata scan that never finishes and can end up flagged as missing. "
        "This job only reads: it proposes the one file in the folder that "
        "clearly belongs to the row and reports anything ambiguous for you to "
        "decide. Approving a finding updates the stored path — no file is "
        "moved, renamed or deleted."
    )
    icon = "repair-icon-path"
    default_enabled = False
    default_interval_hours = 168  # weekly
    default_settings = {"max_rows": 500}
    auto_fix = False

    def _limit(self, context: JobContext) -> int:
        merged = dict(self.default_settings)
        try:
            cfg = context.config_manager.get(f"repair.jobs.{self.job_id}.settings", {})
            if isinstance(cfg, dict):
                merged.update(cfg)
        except Exception as exc:  # noqa: BLE001
            logger.debug("settings read failed, using defaults: %s", exc)
        try:
            return max(1, int(merged.get("max_rows", 500)))
        except (TypeError, ValueError):
            return 500

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()

        from core.library2.path_drift import scan_path_drift

        try:
            report = scan_path_drift(
                context.db,
                limit=self._limit(context),
                config_manager=context.config_manager,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Path drift scan failed: %s", exc, exc_info=True)
            result.errors += 1
            return result

        result.scanned = int(report.get("checked") or 0)
        entries = list(report.get("proposals") or [])
        # Ambiguity is a finding too: the operator is the only one allowed to
        # break the tie, and staying silent would leave the row stuck.
        entries += [
            entry for entry in report.get("unresolved") or []
            if entry.get("status") in ("ambiguous", "claimed")
        ]
        total = len(entries)
        if context.update_progress:
            context.update_progress(total, total)

        for entry in entries:
            if context.check_stop():
                return result
            proposed = entry.get("proposed_stored_path")
            candidate = entry.get("candidate_path")
            actionable = entry.get("status") == "proposed" and bool(candidate)
            details = {
                "track_id": entry.get("track_id"),
                "stored_path": entry.get("stored_path"),
                "directory": entry.get("directory"),
                "proposed_path": candidate if actionable else None,
                "proposed_stored_path": proposed if actionable else None,
                "alternatives": entry.get("alternatives") or [],
                "match_reason": entry.get("reason"),
                "drift_status": entry.get("status"),
                "_fix_action": "repoint" if actionable else "review",
            }
            title = (
                "Indexed file was renamed"
                if actionable else "Indexed file is missing under an unclear name"
            )
            description = (
                f'The catalogue points at "{entry.get("stored_path")}", which no '
                "longer exists. "
                + (
                    f'"{candidate}" in the same folder matches this row '
                    f'({entry.get("reason")}). Approving updates the stored path; '
                    "nothing on disk changes."
                    if actionable else
                    f'{entry.get("reason") or "No single file could be matched"}.'
                )
            )
            if context.create_finding:
                inserted = context.create_finding(
                    job_id=self.job_id,
                    finding_type="stale_index_path",
                    severity="warning" if actionable else "info",
                    entity_type="file",
                    entity_id=f"lib2:{entry['file_id']}",
                    file_path=entry.get("stored_path"),
                    title=title,
                    description=description,
                    details=details,
                )
                if inserted:
                    result.findings_created += 1
                else:
                    result.findings_skipped_dedup += 1
        return result

    def estimate_scope(self, context: JobContext) -> int:
        conn = None
        try:
            conn = context.db._get_connection()
            row = conn.execute(
                """SELECT COUNT(*) FROM lib2_track_files
                    WHERE path IS NOT NULL AND path <> ''
                      AND COALESCE(file_state,'active')<>'deleted'"""
            ).fetchone()
            return int(row[0] or 0) if row else 0
        except Exception:  # noqa: BLE001
            return 0
        finally:
            if conn is not None:
                conn.close()
