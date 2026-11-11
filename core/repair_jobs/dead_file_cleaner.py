"""Dead File Cleaner — native Library-v2 missing-lifecycle review."""

from __future__ import annotations

import os

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


UNREACHABLE_TYPE = "library_unreachable"


def _file_unreachable_finding(context, *, missing: int, checked: int, sample) -> None:
    """one finding that asks the question the guard can't answer: a dropped
    mount, or files deleted on purpose? (#2005: someone who emptied their
    library had no way to say so, and it never cleared.)"""
    if not context.create_finding:
        return
    context.create_finding(
        job_id="dead_file_cleaner",
        finding_type=UNREACHABLE_TYPE,
        severity="warning",
        entity_type="library",
        entity_id="unreachable",
        file_path=None,
        title=f"{missing} of {checked} files can't be reached",
        description=(
            "This many files missing at once is usually a disconnected drive or "
            "a path mapping, so nothing was marked missing. If you deleted them, "
            "say so here and they are cleared."
        ),
        # counts move from run to run; they are not what the finding is about
        details={"outage": {"unreachable": missing, "checked": checked,
                            "example_path": sample}},
    )


def file_dead_findings(create_finding, subjects, check_stop=None):
    """A Dead Files finding for each confirmed-missing file. Returns
    ``(created, already_listed)``."""
    from core.library2.maintenance_subjects import subject_details

    created = skipped = 0
    for subject in subjects:
        if check_stop and check_stop():
            break
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
        inserted = create_finding(
            job_id="dead_file_cleaner",
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
            created += 1
        else:
            skipped += 1
    return created, skipped


def confirm_unreachable_gone(db, config_manager, create_finding) -> dict:
    """The person's answer to an unreachable-library finding: the files are
    gone. Every file that still can't be reached is confirmed missing and
    listed under Dead Files. A file that is back is left alone."""
    from core.library2.maintenance_subjects import active_file_subjects
    from core.library2.paths import resolve_lib2_path
    from core.library2.scan import rescan_files

    def _gone(subject) -> bool:
        path = subject.get("path")
        resolved = resolve_lib2_path(path, config_manager=config_manager) if path else None
        return not resolved or not os.path.exists(resolved)

    waiting = [s for s in active_file_subjects(db, config_manager, include_missing=True)
               if (s.get("file_state") or "active") in ("active", "missing_suspected")
               and _gone(s)]
    if not waiting:
        return {"success": False,
                "error": "Every file can be reached now, so there is nothing to clear. "
                         "Dismiss this or run Dead File Cleaner again."}
    file_ids = [int(s["file_id"]) for s in waiting]
    confirmed = released = 0
    for start in range(0, len(file_ids), _CHUNK):
        stats = rescan_files(db, file_ids=file_ids[start:start + _CHUNK],
                             confirmed_gone=True, source="dead_file_cleaner")
        confirmed += int(stats.get("missing_confirmed") or 0)
        released += int(stats.get("released") or 0)
    wanted = set(file_ids)
    dead = [s for s in active_file_subjects(db, config_manager, include_missing=True)
            if s.get("file_state") == "missing_confirmed" and int(s["file_id"]) in wanted]
    file_dead_findings(create_finding, dead)
    noun = "file" if confirmed == 1 else "files"
    message = (f"{confirmed} {noun} marked missing. They are under Dead Files, "
               f"to download again or remove.")
    if released:
        message += f" {released} track{'s are' if released != 1 else ' is'} no longer wanted."
    logger.info("Dead file cleaner: %d files confirmed gone by hand (%d of %d asked)",
                confirmed, len(dead), len(file_ids))
    return {"success": True, "action": "confirmed_missing", "message": message,
            "confirmed": confirmed}


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
    _file_unreachable_finding(context, missing=missing, checked=checked, sample=sample)
    return reason


def _count_unreachable(context: JobContext, subjects: list):
    """how many of these files can't be reached, read only (no state is
    written). None when the run was stopped while looking."""
    from core.library2.paths import resolve_lib2_path
    unreachable = 0
    for index, subject in enumerate(subjects):
        if index % 500 == 0 and (context.check_stop() or context.wait_if_paused()):
            return None
        path = subject.get("path")
        resolved = resolve_lib2_path(path, config_manager=context.config_manager) if path else None
        if not resolved or not os.path.exists(resolved):
            unreachable += 1
    return unreachable


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
    # 3.6.1's guard settings: from this many files on, this share unreachable
    # at once is a mount or mapping problem
    default_settings = {'max_unresolved_fraction': _GUARD_SHARE,
                        'min_tracks_for_guard': _GUARD_MIN_FILES}
    auto_fix = False
    supports_file_scope = True

    def _guard_settings(self, context: JobContext):
        """The saved guard values, the 3.6.1 defaults when absent or garbage."""
        saved = {}
        try:
            raw = context.config_manager.get(self.get_config_key('settings'), {}) \
                if context.config_manager else {}
            saved = raw if isinstance(raw, dict) else {}
        except Exception as e:  # noqa: BLE001
            logger.debug("dead file cleaner settings not read: %s", e)
        try:
            share = float(saved.get('max_unresolved_fraction', _GUARD_SHARE))
            share = share if 0 < share <= 1 else _GUARD_SHARE
        except (TypeError, ValueError):
            share = _GUARD_SHARE
        try:
            minimum = int(saved.get('min_tracks_for_guard', _GUARD_MIN_FILES))
            minimum = minimum if minimum >= 0 else _GUARD_MIN_FILES
        except (TypeError, ValueError):
            minimum = _GUARD_MIN_FILES
        return share, minimum

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()
        try:
            from core.library2.maintenance_subjects import active_file_subjects
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
            # 3.6.1's guard, before anything is written: look first (read
            # only), and stop when too much of the set is out of reach. the
            # rescan below marks files missing as it goes, so a guard after
            # it only described what had already happened
            # (only files live now: one already marked missing is a known
            # miss waiting for review, not a sign of an outage)
            share, minimum = self._guard_settings(context)
            live = [s for s in before
                    if (s.get("file_state") or "active") == "active"]
            unreachable = _count_unreachable(context, live)
            if unreachable is None:
                return result
            if len(live) >= minimum and live and unreachable >= len(live) * share:
                result.stopped_early = _unreachable_report(
                    context, missing=unreachable, checked=len(live), deferred=0, before=live)
                return result
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
            # the scan's own storage-outage deferral (its writes held back)
            if deferred:
                result.stopped_early = _unreachable_report(
                    context, missing=missing, checked=checked, deferred=deferred, before=before)
                # 3.6.1: a run that tripped the guard files nothing. files a
                # healthy run confirmed missing earlier wait for the next one,
                # so a review never opens while the library is out of reach
                return result
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
        if context.create_finding:
            created, skipped = file_dead_findings(
                context.create_finding, confirmed, check_stop=context.check_stop)
            result.findings_created += created
            result.findings_skipped_dedup += skipped
        return result

    def estimate_scope(self, context: JobContext) -> int:
        try:
            from core.library2.maintenance_subjects import active_file_subjects

            return len(scoped_file_subjects(context, active_file_subjects(
                context.db, context.config_manager, include_missing=True,
            )))
        except Exception:
            return 0
