"""Optional profile review of native primary files, including unmonitored tracks."""

from core.library2.quality_eval import effective_track_profile, probe_profile_file, quality_issue
from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob, drop_hand_tagged, scoped_file_subjects


@register_job
class QualityProfileAuditJob(RepairJob):
    job_id = "quality_profile_audit"
    display_name = "Quality Profile Audit"
    description = "Review library audio against its current quality profile, including unmonitored tracks"
    help_text = (
        "Measures each library's primary audio file against its inherited quality profile. "
        "The scan only creates review findings; it changes no files, monitoring or downloads. "
        "Unknown quality and formats not targeted by the profile are reported separately.\n\n"
        "Monitor & Upgrade opts that one track into monitoring and uses the existing Wishlist "
        "pipeline after rechecking the current file and profile. Already monitored tracks "
        "keep their normal automatic upgrades; this audit does not pause that automation. "
        "Hand-tagged releases and active manual quality overrides are left alone."
    )
    icon = "repair-icon-quality"
    default_enabled = False
    default_interval_hours = 168
    default_settings = {}
    auto_fix = False
    supports_file_scope = True

    def _subjects(self, context: JobContext) -> list:
        from core.library2.maintenance_subjects import active_file_subjects
        from core.library2.track_files import primary_order

        rows = active_file_subjects(context.db, context.config_manager)
        # Use acquisition's primary order within each owner library. A global
        # primary can belong to another owner, leaving several local copies.
        by_id = {row["file_id"]: row for row in rows}
        primary = {}
        conn = context.db._get_connection()
        try:
            for selected in conn.execute(
                "SELECT id FROM lib2_track_files WHERE COALESCE(file_state,'active')='active' "
                f"ORDER BY {primary_order()}"
            ):
                row = by_id.get(selected["id"])
                if row:
                    primary.setdefault((row["track_id"], row.get("owner_profile_id")), row)
        finally:
            conn.close()
        return drop_hand_tagged(context, scoped_file_subjects(context, list(primary.values())))

    def estimate_scope(self, context: JobContext) -> int:
        return len(self._subjects(context))

    def scan(self, context: JobContext) -> JobResult:
        from core.library2.manual_skips import active_skip_paths

        result = JobResult()
        subjects = self._subjects(context)
        total = len(subjects)
        profiles, skips = {}, {}
        conn = context.db._get_connection()
        try:
            for index, row in enumerate(subjects):
                if context.check_stop() or context.wait_if_paused():
                    result.stopped_early = f"Quality audit interrupted at file {index + 1} of {total}."
                    return result
                result.scanned += 1
                owner = int(row.get("owner_profile_id") or 1)
                if owner not in skips:
                    skips[owner] = active_skip_paths(conn, ("quality", "bit_depth"), profile_id=owner)
                if row["path"] in skips[owner]:
                    result.skipped += 1
                    continue
                tid = int(row["track_id"])
                if tid not in profiles:
                    profiles[tid] = effective_track_profile(conn, tid)
                profile = profiles[tid]
                observed, measured = probe_profile_file(row, context.config_manager)
                issue = quality_issue(observed, profile)
                if issue == "satisfied":
                    result.skipped += 1
                elif context.create_finding:
                    current = measured.label() if measured else "Unknown / unreadable audio"
                    kind = {"below_cutoff": "quality_upgrade_review",
                            "format_not_targeted": "quality_format_not_targeted",
                            "unknown": "quality_unknown"}[issue]
                    descriptions = {
                        "below_cutoff": "Below the current upgrade target. Monitor & Upgrade queues this track using its live profile.",
                        "format_not_targeted": "The profile does not target this format. Review the profile's formats before choosing a replacement.",
                        "unknown": "The file's audio quality could not be measured. Scan or inspect the file before replacing it.",
                    }
                    inserted = context.create_finding(
                        job_id=self.job_id, finding_type=kind,
                        severity="warning" if issue == "unknown" else "info",
                        entity_type="track", entity_id=f"lib2:{tid}", file_path=row["path"],
                        title=f"{self.display_name}: {row['artist_name']} — {row['title']} ({current})",
                        description=descriptions[issue], details={
                            "quality_issue": issue, "lib2_track_id": tid,
                            "lib2_file_id": row["file_id"], "owner_profile_id": row.get("owner_profile_id"),
                            "current_quality": current, "measured_quality": measured.to_dict() if measured else None,
                            "quality_profile_id": profile["id"],
                            "quality_profile_name": profile["name"],
                            "profile_ranked_targets": profile["ranked_targets"],
                            "upgrade_policy": profile["upgrade_policy"],
                            "upgrade_cutoff_index": profile["upgrade_cutoff_index"],
                        },
                    )
                    if inserted:
                        result.findings_created += 1
                    else:
                        result.findings_skipped_dedup += 1
                if context.update_progress:
                    context.update_progress(index + 1, total)
        finally:
            conn.close()
        return result
