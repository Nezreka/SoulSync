"""Dead File Cleaner — native Library-v2 missing-lifecycle review."""

from __future__ import annotations

from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob, scoped_file_subjects
from utils.logging_config import get_logger

logger = get_logger("repair_job.dead_files")

# `_resolve_file_path` used to live here as a "compatibility helper retained for
# non-catalogue file fixers". It was a verbatim pass-through to
# `core.library.path_resolver.resolve_library_file_path` and, after this
# branch's rewrite of this job, had zero callers anywhere -- including the tests
# that name `_resolve_file_path`, which patch `core.repair_worker`'s own copy.


@register_job
class DeadFileCleanerJob(RepairJob):
    job_id = "dead_file_cleaner"
    display_name = "Dead File Cleaner"
    description = "Confirms indexed files that are no longer reachable"
    help_text = (
        "Runs Library v2's storage-health-aware file scan. The first healthy "
        "miss is recorded as 'checking missing'; only a second consecutive "
        "healthy miss creates a review finding. An unavailable storage root "
        "never creates a destructive finding. Approve a finding to remove the "
        "stale file reference or re-queue a monitored track."
    )
    icon = "repair-icon-deadfile"
    default_enabled = True
    default_interval_hours = 24
    default_settings = {}
    auto_fix = False
    supports_file_scope = True

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()
        try:
            from core.library2.maintenance_subjects import (
                active_file_subjects,
                subject_details,
            )
            from core.library2.scan import rescan_files

            before = scoped_file_subjects(context, active_file_subjects(
                context.db, context.config_manager, include_missing=True,
            ))
            if not before:
                return result
            file_ids = [int(subject["file_id"]) for subject in before]
            scan = rescan_files(context.db, file_ids=file_ids)
            result.scanned = int(scan.get("scanned") or 0)
            after = scoped_file_subjects(context, active_file_subjects(
                context.db, context.config_manager, include_missing=True,
            ))
        except Exception as exc:  # noqa: BLE001
            logger.error("Native dead-file scan failed: %s", exc, exc_info=True)
            result.errors += 1
            return result

        confirmed = [
            subject for subject in after
            if subject.get("file_state") == "missing_confirmed"
        ]
        total = len(after)
        if context.update_progress:
            context.update_progress(total, total)
        for subject in confirmed:
            if context.check_stop():
                return result
            details = {
                "track_id": subject["track_id"],
                "artist_id": subject.get("artist_id"),
                "title": subject.get("title"),
                "artist": subject.get("artist_name"),
                "album": subject.get("album_title"),
                "original_path": subject.get("path"),
                "album_thumb_url": subject.get("album_image"),
                "artist_thumb_url": subject.get("artist_image"),
            }
            details.update(subject_details(subject))
            if context.create_finding:
                inserted = context.create_finding(
                    job_id=self.job_id,
                    finding_type="dead_file",
                    severity="warning",
                    entity_type="track",
                    entity_id=f"lib2:{subject['track_id']}",
                    file_path=subject.get("path"),
                    title=f"Missing file: {subject.get('title') or 'Unknown'}",
                    description=(
                        f'"{subject.get("title") or "Unknown"}" by '
                        f'{subject.get("artist_name") or "Unknown"} was missing '
                        "during two healthy storage scans."
                    ),
                    details=details,
                )
                if inserted:
                    result.findings_created += 1
                else:
                    result.findings_skipped_dedup += 1
        return result

    def estimate_scope(self, context: JobContext) -> int:
        try:
            from core.library2.maintenance_subjects import active_file_subjects

            return len(scoped_file_subjects(context, active_file_subjects(
                context.db, context.config_manager, include_missing=True,
            )))
        except Exception:
            return 0
