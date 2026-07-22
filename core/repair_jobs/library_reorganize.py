"""Library-v2 catalogue scan for file-organization path drift."""

from __future__ import annotations

import os

from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair_jobs.library_reorganize")


@register_job
class LibraryReorganizeJob(RepairJob):
    job_id = "library_reorganize"
    display_name = "Library Reorganize"
    description = "Reviews or queues files whose paths do not match the organization template"
    help_text = (
        "Scans Library-v2 albums that own files and computes their destination "
        "with the same planner and queue as interactive Reorganize. Dry run is "
        "enabled by default and creates review findings; live mode queues albums."
    )
    icon = "repair-icon-reorganize"
    default_enabled = False
    default_interval_hours = 168
    default_settings = {"dry_run": True}
    setting_options = {"dry_run": [True, False]}
    auto_fix = True

    def _dry_run(self, context: JobContext) -> bool:
        if not context.config_manager:
            return True
        nested = context.config_manager.get(
            f"repair.jobs.{self.job_id}.settings", {},
        ) or {}
        if "dry_run" in nested:
            return bool(nested["dry_run"])
        return bool(context.config_manager.get(
            f"repair.jobs.{self.job_id}.settings.dry_run", True,
        ))

    @staticmethod
    def _albums(context: JobContext) -> list[dict]:
        conn = context.db._get_connection()
        try:
            rows = conn.execute(
                """SELECT al.id, al.title, al.legacy_album_id,
                          al.primary_artist_id AS artist_id,
                          COALESCE(ar.name, 'Unknown Artist') AS artist_name
                     FROM lib2_albums al
                     LEFT JOIN lib2_artists ar ON ar.id=al.primary_artist_id
                    WHERE EXISTS (
                        SELECT 1 FROM lib2_tracks t
                        JOIN lib2_track_files f ON f.track_id=t.id
                        WHERE t.album_id=al.id
                    )
                    ORDER BY al.id"""
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    @staticmethod
    def _track_map(context: JobContext, album_id: int) -> dict[str, int]:
        conn = context.db._get_connection()
        try:
            return {
                str(row["legacy_track_id"]): int(row["id"])
                for row in conn.execute(
                    "SELECT id, legacy_track_id FROM lib2_tracks WHERE album_id=?",
                    (int(album_id),),
                ).fetchall()
                if row["legacy_track_id"] is not None
            }
        finally:
            conn.close()

    def scan(self, context: JobContext) -> JobResult:
        from core.library2.reorganize_bridge import (
            ReorganizeBridgeError,
            enqueue_album_reorganize,
            preview_album_reorganize,
        )

        result = JobResult()
        if context.config_manager and not context.config_manager.get(
            "file_organization.enabled", True,
        ):
            return result
        dry_run = self._dry_run(context)
        albums = self._albums(context)
        for index, album in enumerate(albums):
            if context.check_stop():
                break
            album_id = int(album["id"])
            if album.get("legacy_album_id") is None:
                # Provider-only discography albums do not own files under the
                # normal importer. If an external writer attached one, make the
                # unsupported state visible instead of silently dropping it.
                if context.create_finding:
                    inserted = context.create_finding(
                        job_id=self.job_id,
                        finding_type="reorganize_unavailable",
                        severity="warning",
                        entity_type="album",
                        entity_id=str(album_id),
                        file_path=None,
                        title=f'Cannot reorganize: {album["title"]}',
                        description="The file-owning album has no planner back-reference; re-import it to repair identity.",
                        details={"lib2_album_id": album_id},
                    )
                    result.findings_created += int(bool(inserted))
                    result.findings_skipped_dedup += int(not inserted)
                continue
            mode = "api"
            try:
                preview = preview_album_reorganize(
                    context.db, context.config_manager, album_id, mode=mode,
                )
                if preview.get("status") == "no_source_id":
                    tagged = preview_album_reorganize(
                        context.db, context.config_manager, album_id, mode="tags",
                    )
                    if tagged.get("status") == "planned":
                        preview = tagged
                        mode = "tags"
            except ReorganizeBridgeError as exc:
                logger.warning("Reorganize bridge rejected album %s: %s", album_id, exc)
                result.errors += 1
                continue
            except Exception as exc:
                logger.warning("Reorganize preview failed for album %s: %s", album_id, exc)
                result.errors += 1
                continue

            tracks = preview.get("tracks") or []
            result.scanned += len(tracks)
            mismatched = [
                track for track in tracks
                if track.get("matched") and track.get("new_path")
                and not track.get("unchanged") and track.get("file_exists")
            ]
            if dry_run:
                lib2_tracks = self._track_map(context, album_id)
                for track in mismatched:
                    legacy_track_id = track.get("track_id")
                    lib2_track_id = lib2_tracks.get(str(legacy_track_id))
                    if lib2_track_id is None:
                        result.errors += 1
                        continue
                    current_path = track.get("current_path") or ""
                    new_path = track.get("new_path") or ""
                    inserted = context.create_finding and context.create_finding(
                        job_id=self.job_id,
                        finding_type="path_mismatch",
                        severity="info",
                        entity_type="track",
                        entity_id=str(lib2_track_id),
                        file_path=current_path,
                        title=f"Would move: {os.path.basename(current_path) or track.get('title', '')}",
                        description=f"From: {current_path or '?'}\nTo: {new_path or '?'}",
                        details={
                            "from": current_path,
                            "to": new_path,
                            "from_abs": track.get("current_path_abs") or "",
                            "to_abs": track.get("new_path_abs") or "",
                            "album_id": str(album_id),
                            "lib2_album_id": album_id,
                            "lib2_track_id": lib2_track_id,
                            "legacy_track_id": legacy_track_id,
                            "source": preview.get("source"),
                        },
                    )
                    result.findings_created += int(bool(inserted))
                    result.findings_skipped_dedup += int(not inserted)
            elif mismatched:
                try:
                    outcome = enqueue_album_reorganize(
                        context.db, album_id,
                        source=preview.get("source"), mode=mode,
                    )
                    result.auto_fixed += int(bool(outcome.get("queued")))
                    result.skipped += int(not outcome.get("queued"))
                except Exception as exc:
                    logger.warning("Could not queue reorganize album %s: %s", album_id, exc)
                    result.errors += 1
            if context.update_progress:
                context.update_progress(index + 1, len(albums))
        return result

    def estimate_scope(self, context: JobContext) -> int:
        try:
            return len(self._albums(context))
        except Exception:
            return 0
