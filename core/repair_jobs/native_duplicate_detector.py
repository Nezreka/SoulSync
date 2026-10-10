"""Find native duplicate candidates; files change only through approved review."""

from __future__ import annotations

from core.library2.duplicate_review import DEFAULT_SETTINGS, find_duplicate_candidates
from core.library2.maintenance_subjects import active_file_subjects
from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob, artist_scoped_subjects
from utils.logging_config import get_logger

logger = get_logger("repair_jobs.native_duplicate_detector")


@register_job
class NativeDuplicateDetectorJob(RepairJob):
    job_id = "native_duplicate_detector"
    display_name = "Duplicate Detector"
    description = "Reviews native duplicate files, including unlinked and cross-format copies"
    help_text = (
        "Finds duplicate candidates independently of confirmed recording links. "
        "Title/artist similarity and download filenames are review evidence only. "
        "The scan writes Findings; it never links tracks or removes files.\n\n"
        "After approval, Keep Best recommends a playlist copy first, then a manual "
        "file pick, then the native quality ranking. You may pick an exact file. "
        "Weak matches require explicit confirmation of the same recording.\n\n"
        "Redundant files move through the delete journal into recoverable quarantine. "
        "Intentional companion formats, shared files, playlists, manual metadata, "
        "hand tags, pins and profile ownership are protected.\n\n"
        "Settings: title_similarity and artist_similarity (0–1); ignore_cross_album "
        "limits candidates to the same native album."
    )
    icon = "repair-icon-duplicate"
    data_basis = "lib2"
    library_v2_effects = frozenset({"observe", "metadata", "delete", "wanted"})
    default_enabled = False
    default_interval_hours = 168
    default_settings = DEFAULT_SETTINGS.copy()
    auto_fix = False
    supports_file_scope = True
    supports_artist_scope = True
    writes_library_files = True

    def estimate_scope(self, context: JobContext) -> int:
        return len(artist_scoped_subjects(context, active_file_subjects(context.db, context.config_manager)))

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()
        if context.check_stop() or context.wait_if_paused():
            return result
        settings = {**self.default_settings, **(context.config_manager.get(
            f"repair.jobs.{self.job_id}.settings", {}) or {})} if context.config_manager else self.default_settings.copy()
        scope = context.scope
        try:
            subjects = artist_scoped_subjects(context, active_file_subjects(context.db, context.config_manager))
            if context.scope_artist_name():
                scope = {**(scope or {}), "file_paths": [s["path"] for s in subjects]}
            total = len(subjects)
            if context.report_progress:
                context.report_progress(phase=f"Reviewing {total} native files for duplicates...", total=total)
            if context.update_progress:
                context.update_progress(0, total)
            membership = context.playlist_membership() if context.playlist_membership else None
            # A context map is server-ID keyed. The active server identifies
            # its namespace; native track/file IDs are never lookup keys.
            from core.library.playlist_membership import _active_server_and_client
            server, _ = _active_server_and_client()
            candidates = find_duplicate_candidates(
                context.db, context.config_manager, settings=settings, scope=scope,
                playlist_membership=membership, server_source=server,
                check_stop=lambda: context.check_stop() or context.wait_if_paused(),
            )
            if context.check_stop():
                return result
            result.scanned = total
            for candidate in candidates:
                if context.check_stop() or context.wait_if_paused():
                    return result
                keeper = next(f for f in candidate["tracks"] if f["file_id"] == candidate["recommended_file_id"])
                if not context.create_finding:
                    continue
                created = context.create_finding(
                    job_id=self.job_id, finding_type="native_duplicate_tracks", severity="info",
                    entity_type="track", entity_id=f"lib2:{keeper['track_id']}", file_path=keeper["path"],
                    title=f"Duplicate: {keeper['title']} by {keeper['artist_name']}",
                    description=(f"{candidate['count']} copies to review; redundant files are recoverable. "
                                 + ("Confirm the same recording before Keep Best." if candidate["requires_recording_confirmation"]
                                    else "Keep Best requires approval.")),
                    details=candidate,
                )
                if created:
                    result.findings_created += 1
                else:
                    result.findings_skipped_dedup += 1
            if context.update_progress:
                context.update_progress(total, total)
        except Exception as exc:
            logger.warning("Native duplicate review failed: %s", exc)
            result.errors += 1
        return result
