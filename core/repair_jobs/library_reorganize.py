"""Library-v2 catalogue scan for file-organization path drift."""

from __future__ import annotations

import os

from core.repair_jobs import register_job
from core.repair_jobs.base import (
    JobContext, JobResult, RepairJob, file_path_in_scope, get_scope_file_paths,
)
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
    supports_file_scope = True
    # Moves/rewrites real library files, so a LIVE run is refused when the
    # preflight says this process cannot see the library (upstream 36f13cdd8).
    writes_library_files = True

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
        """Albums that own files, narrowed to the run's file scope.

        The scope is derived from the allowlist rather than from an artist
        name: an unfiltered album query was how "run this for one artist" set
        the whole library moving. `allowed is None` is library-wide; an empty
        allowlist scans nothing (base.get_scope_file_paths' fail-closed rule).
        """
        allowed = get_scope_file_paths(context)
        conn = context.db._get_connection()
        try:
            rows = conn.execute(
                """SELECT al.id, al.title,
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
            albums = [dict(row) for row in rows]
            if allowed is None:
                return albums
            if not allowed:
                return []
            in_scope = {
                int(row[0])
                for row in conn.execute(
                    """SELECT DISTINCT t.album_id, f.path
                         FROM lib2_tracks t
                         JOIN lib2_track_files f ON f.track_id=t.id
                        WHERE f.path IS NOT NULL AND f.path<>''"""
                ).fetchall()
                if file_path_in_scope(row[1], allowed)
            }
            return [album for album in albums if int(album["id"]) in in_scope]
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
            # There is deliberately no `legacy_album_id IS NOT NULL` gate here.
            # That back-reference is written ONLY by the one-shot upgrade
            # importer; the post-download catalogue writer (autolink) never
            # sets it. So every album acquired after the migration -- and every
            # album on a fresh install -- is lib2-native with a NULL back-ref,
            # and it does own files (the EXISTS filter in `_albums` proves it).
            # Gating on it emitted one permanently unfixable `reorganize_
            # unavailable` warning per album (no fix handler, no UI treatment)
            # and reorganized nothing, advising a re-import that cannot help.
            # The bridge validates the album id itself; nothing downstream
            # consults the legacy back-reference.
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
                for track in mismatched:
                    # `track_id` is ALREADY the lib2 id: the preview is built
                    # from `SELECT t.* FROM lib2_tracks t` (core/library_
                    # reorganize.load_album_and_tracks) and there is no legacy
                    # hop anywhere in the bridge. It used to be looked up in a
                    # map keyed by `legacy_track_id`, which missed every time
                    # -- zero findings, one error per mis-pathed track -- and,
                    # where a lib2 id happened to equal some other track's
                    # legacy id, silently created the finding against the WRONG
                    # track and baked that id into its details permanently.
                    try:
                        lib2_track_id = int(track.get("track_id"))
                    except (TypeError, ValueError):
                        result.errors += 1
                        continue
                    if lib2_track_id <= 0:
                        result.errors += 1
                        continue
                    current_path = track.get("current_path") or ""
                    new_path = track.get("new_path") or ""
                    inserted = context.create_finding and context.create_finding(
                        job_id=self.job_id,
                        finding_type="path_mismatch",
                        severity="info",
                        entity_type="track",
                        entity_id=f"lib2:{lib2_track_id}",  # T-12
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
                            "source": preview.get("source"),
                            "library_v2_native": True,
                            "library_v2": {
                                "album_ids": [album_id],
                                "track_ids": [lib2_track_id],
                            },
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
