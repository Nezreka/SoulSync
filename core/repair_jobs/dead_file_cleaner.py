"""Dead File Cleaner — native Library-v2 missing-lifecycle review."""

from __future__ import annotations

from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob, scoped_file_subjects
from utils.logging_config import get_logger

logger = get_logger("repair_job.dead_files")

# files per rescan call: Stop / Pause are honoured between them
_CHUNK = 500
# 3.6.1's guard: from this many files on, half of them unreachable at once is
# a mount or path-mapping problem, not that many deleted files
_GUARD_MIN_FILES = 25
_GUARD_SHARE = 0.5


def _unreachable_report(context, *, missing: int, checked: int, deferred: int,
                        before: list) -> str:
    """Say why a run with most of the library out of reach found nothing,
    the way 3.6.1 did: how many, one example path, and what to check. The
    scan itself already holds off on marking any of them missing."""
    from core.library2.paths import resolve_lib2_path
    from core.repair_worker import _path_mapping_hint

    sample = None
    for subject in before[:200]:
        path = subject.get("path")
        if path and not resolve_lib2_path(path, config_manager=context.config_manager):
            sample = path
            break
    hint = _path_mapping_hint(context.config_manager)
    reason = (f"{missing} of {checked} files point to paths SoulSync can't reach, which is "
              f"almost always a path-mapping or mount problem, not deleted files. Nothing was "
              f"marked missing. Example path: {sample or 'unknown'}. {hint}")
    logger.error("Dead file scan: %s (%d deferred as a storage outage)", reason, deferred)
    if context.report_progress:
        context.report_progress(phase="Stopped: too many unreachable paths",
                                log_line=reason, log_type="error")
    return reason


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
            # in chunks, so Stop and Pause work and the run shows progress, as
            # 3.6.1's did: one rescan_files call over the whole library ran to
            # the end with neither, however long it took
            total_files = len(file_ids)
            if context.report_progress:
                context.report_progress(phase=f'Checking {total_files} files...', total=total_files)
            missing = deferred = 0
            for start in range(0, total_files, _CHUNK):
                if context.check_stop() or context.wait_if_paused():
                    return result
                scan = rescan_files(context.db, file_ids=file_ids[start:start + _CHUNK])
                result.scanned += int(scan.get("scanned") or 0)
                missing += int(scan.get("missing") or 0)
                deferred += int(scan.get("mass_missing_deferred") or 0)
                done = min(start + _CHUNK, total_files)
                if context.update_progress:
                    context.update_progress(done, total_files)
                if context.report_progress:
                    context.report_progress(scanned=done, total=total_files)
            checked = result.scanned + missing
            if deferred or (checked >= _GUARD_MIN_FILES and missing >= checked * _GUARD_SHARE):
                result.stopped_early = _unreachable_report(
                    context, missing=missing, checked=checked, deferred=deferred, before=before)
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
