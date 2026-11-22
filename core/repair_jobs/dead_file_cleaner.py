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


#: the last unreachable report logged, so a library that stays the same
#: says it once at WARNING and then quietly, not as an ERROR every run
_LAST_REPORT: dict = {}


def _unreachable_hint(context, paths: list) -> str:
    """what to check, from where the unreachable paths point.

    - their folders are there but the files are not: deleted or renamed
      outside SoulSync, nothing to map
    - they all sit under one folder that is not a library folder: the music
      moved (an old /app/Transfer) and the records never followed
    - otherwise the mapping / mount advice 3.6.1 gave"""
    from core.library2.paths import resolve_lib2_ancestor
    from core.repair_worker import _path_mapping_hint

    sample = paths[:50]
    if sample and all(resolve_lib2_ancestor(p, context.config_manager) for p in sample):
        return ("Their folders are there but the files are not, so they were most likely "
                "deleted or renamed outside SoulSync. If you deleted them, answer the "
                "finding on the Tools page to clear them.")
    try:
        navidrome = str(context.config_manager.get_active_media_server() or "").lower() \
            == "navidrome"
    except Exception:  # noqa: BLE001 - no server known: not navidrome
        navidrome = False
    prefix = "" if navidrome else _shared_prefix(sample)
    if prefix and not _under_library_folder(prefix, context.config_manager):
        return (f"They all sit under {prefix}, which is not one of your library folders. "
                f"If your music moved, add the new folder in Settings -> Library -> Music "
                f"Paths, then Tools -> Update File Locations points the records at it.")
    return ("That many at once is almost always a path-mapping or mount problem, "
            "not deleted files. " + _path_mapping_hint(context.config_manager))


def _shared_prefix(paths: list) -> str:
    """the folder every path starts with, two levels deep at most
    (/app/Transfer), or '' when they don't share one"""
    split = [[part for part in p.replace("\\", "/").split("/") if part] for p in paths if p]
    if not split:
        return ""
    common = []
    for parts in zip(*[s[:2] for s in split], strict=False):
        if len(set(parts)) != 1:
            break
        common.append(parts[0])
    if not common:
        return ""
    lead = "/" if paths[0].replace("\\", "/").startswith("/") else ""
    return lead + "/".join(common)


def _under_library_folder(prefix: str, config_manager) -> bool:
    try:
        from core.library.path_resolver import library_base_dirs
        bases = [os.path.abspath(b) for b in library_base_dirs(config_manager)]
    except Exception as exc:  # noqa: BLE001 - no roots known: can't say it is outside
        logger.debug("library folders unavailable: %s", exc)
        return True
    if not bases:
        return True
    folder = os.path.abspath(prefix)
    return any(folder == b or folder.startswith(b.rstrip(os.sep) + os.sep)
               or b.startswith(folder.rstrip(os.sep) + os.sep) for b in bases)


def _unreachable_report(context, *, missing: int, checked: int, paths: list) -> str:
    """Say why a run with most of the library out of reach found nothing,
    the way 3.6.1 did: how many, one example path, and what to check.
    Nothing has been written when this runs: the guard looks first."""
    sample = paths[0] if paths else None
    hint = _unreachable_hint(context, paths)
    reason = (f"{missing} of {checked} files point to paths SoulSync can't reach. "
              f"Nothing was marked missing. Example path: {sample or 'unknown'}. {hint}")
    key = (missing, checked, sample)
    level = logger.info if _LAST_REPORT.get("key") == key else logger.warning
    _LAST_REPORT["key"] = key
    level("Dead file scan: %s", reason)
    if context.report_progress:
        context.report_progress(phase="Stopped: too many unreachable paths",
                                log_line=reason, log_type="warning")
    _file_unreachable_finding(context, missing=missing, checked=checked, sample=sample)
    return reason


def _unreachable_paths(context: JobContext, subjects: list):
    """the stored paths of these files that can't be reached, read only (no
    state is written). None when the run was stopped while looking."""
    from core.library2.paths import resolve_lib2_path
    unreachable = []
    for index, subject in enumerate(subjects):
        if index % 500 == 0 and (context.check_stop() or context.wait_if_paused()):
            return None
        path = subject.get("path")
        resolved = resolve_lib2_path(path, config_manager=context.config_manager) if path else None
        if not resolved or not os.path.exists(resolved):
            unreachable.append(path or "")
    return unreachable


@register_job
class DeadFileCleanerJob(RepairJob):
    job_id = "dead_file_cleaner"
    display_name = "Dead File Cleaner"
    description = "Confirms indexed files that are no longer reachable"
    help_text = (
        "Runs the Library's storage-health-aware file scan. The first healthy "
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
            unreachable = _unreachable_paths(context, live)
            if unreachable is None:
                return result
            if len(live) >= minimum and live and len(unreachable) >= len(live) * share:
                result.stopped_early = _unreachable_report(
                    context, missing=len(unreachable), checked=len(live),
                    paths=[p for p in unreachable if p])
                return result
            for start in range(0, total_files, _CHUNK):
                if context.check_stop() or context.wait_if_paused():
                    return result
                # the share test above was over the whole set; the scan's own
                # test would see one chunk, where a run of deleted files next
                # to each other (168 of 3748, all in one chunk) reads as an
                # outage and nothing is ever marked (#2216). each miss still
                # needs its own library folder reachable to count
                scan = rescan_files(context.db, file_ids=file_ids[start:start + _CHUNK],
                                    outage_guard=False)
                result.scanned += int(scan.get("scanned") or 0)
                done = min(start + _CHUNK, total_files)
                if context.update_progress:
                    context.update_progress(done, total_files)
                if context.report_progress:
                    context.report_progress(scanned=done, total=total_files)
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
