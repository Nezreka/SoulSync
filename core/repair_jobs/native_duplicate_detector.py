"""Find native duplicate candidates; files change only through approved review."""

from __future__ import annotations

from core.library2.catalogue_duplicates import dismissed_catalogue_pairs, find_catalogue_duplicates, merge_catalogue_duplicate
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
    description = "Reviews duplicate catalogue releases and native files, including fileless wishes"
    help_text = (
        "Finds duplicate candidates independently of confirmed recording links. "
        "Title/artist similarity and download filenames are review evidence only. "
        "Catalogue duplicates are also checked before any files exist. Select a surviving "
        "release group in the Finding; both provider editions and all audio files stay. "
        "Incomplete tracklists, conflicting identities, active downloads and manual "
        "choices require resolution before a merge.\n\n"
        "auto_merge_catalogue (off by default) merges only complete, identical recordings "
        "with at least two agreeing trusted release IDs and no conflicting trusted ID. "
        "Conflicting provider release IDs require manual review.\n\n"
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
    default_settings = {**DEFAULT_SETTINGS, "auto_merge_catalogue": False}
    auto_fix = False
    supports_file_scope = True
    supports_artist_scope = True
    writes_library_files = True

    def estimate_scope(self, context: JobContext) -> int:
        return (len(artist_scoped_subjects(context, active_file_subjects(context.db, context.config_manager)))
                + len(find_catalogue_duplicates(context.db, scope=context.scope, check_stop=context.check_stop)))

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
            # File reviews fingerprint their settings; catalogue options are not theirs.
            file_settings = {k: v for k, v in settings.items() if k != "auto_merge_catalogue"}
            stop = lambda: context.check_stop() or context.wait_if_paused()  # noqa: E731
            candidates = find_duplicate_candidates(
                context.db, context.config_manager, settings=file_settings, scope=scope,
                playlist_membership=membership, server_source=server, check_stop=stop,
            )
            if context.check_stop():
                return result
            release_candidates = find_catalogue_duplicates(context.db, scope=context.scope, check_stop=stop)
            reviewed_catalogue = len(release_candidates)
            merged_catalogue = False
            if settings.get("auto_merge_catalogue") is True:
                def pair(candidate):
                    return frozenset(a["id"] for a in candidate["albums"])
                skip = dismissed_catalogue_pairs(context.db)
                while candidate := next((c for c in release_candidates
                                         if c["auto_merge_eligible"] and pair(c) not in skip), None):
                    if stop():
                        return result
                    applied = merge_catalogue_duplicate(
                        context.db, candidate, candidate["recommended_album_id"], automatic=True)
                    if not applied["success"]:
                        skip.add(pair(candidate))
                        continue
                    result.auto_fixed += 1
                    merged_catalogue = True
                    # A merge changes snapshots and IDs of its own group only:
                    # rebuild that group before applying or publishing again.
                    group = {i for c in release_candidates if pair(c) & pair(candidate) for i in pair(c)}
                    release_candidates = [c for c in release_candidates if not pair(c) & group] + find_catalogue_duplicates(
                        context.db, scope=context.scope, check_stop=stop,
                        album_ids=sorted(group - {applied["merged_album_id"]}))
            for candidate in release_candidates:
                if stop():
                    return result
                keeper = next(a for a in candidate["albums"] if a["id"] == candidate["recommended_album_id"])
                if context.create_finding:
                    created = context.create_finding(
                        job_id=self.job_id, finding_type="native_duplicate_releases", severity="info",
                        entity_type="album", entity_id=f"lib2:{keeper['id']}", file_path=None,
                        title=f"Release duplicate: {keeper['title']} by {keeper['artist_name']}",
                        description="Compare these catalogue entries and select the release group to keep. "
                                    "Provider editions and physical files are preserved.", details=candidate)
                    if created:
                        result.findings_created += 1
                    else:
                        result.findings_skipped_dedup += 1
            result.scanned = total + reviewed_catalogue
            if merged_catalogue:
                candidates = find_duplicate_candidates(
                    context.db, context.config_manager, settings=file_settings, scope=scope,
                    playlist_membership=membership, server_source=server, check_stop=stop)
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
