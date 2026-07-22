"""Periodic Library-v2 quality-upgrade scan.

Re-checks every monitored Library-v2 track that HAS a file under an upgrade
policy (``until_top``/``until_cutoff``) and queues genuine upgrade candidates
into the Wishlist — same logic as the manual "Search Upgrades" button
(``core/library2/wishlist_mirror``), just on the repair-worker cadence so
upgrades keep flowing without the user pressing anything.

Scheduled runs have no request context; wishlist mirrors are pinned to the
admin profile explicitly (ADR-01: Library v2 is admin-only, and every other
scheduled acquisition path is scoped the same way) — never to whatever a
default parameter happens to be.

Runs against the native Library-v2 catalogue. Never touches files.
"""

from __future__ import annotations

from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair.quality_upgrade_scan")


@register_job
class Lib2UpgradeScanJob(RepairJob):
    job_id = "quality_upgrade_scan"
    display_name = "Quality Upgrade Scan (monitored)"
    description = "Find monitored tracks below their quality profile's cutoff — queue automatically or review first."
    help_text = (
        "Scans the active library for monitored tracks whose file sits below "
        "the assigned quality profile's upgrade cutoff (profiles with policy "
        "'upgrade until cutoff/top').\n\n"
        "Mode 'automatic' adds genuine candidates straight to the Wishlist "
        "carrying their quality profile, so the normal download pipeline "
        "searches for and imports the better version.\n\n"
        "Mode 'review' creates one finding per candidate instead: you see the "
        "current file quality against the profile and decide per track whether "
        "to queue the upgrade (or dismiss it). Does nothing when the new "
        "library feature is off."
    )
    icon = "arrow-up-circle"
    default_enabled = False
    default_interval_hours = 24
    default_settings = {"mode": "automatic"}
    setting_options = {"mode": ["automatic", "review"]}
    auto_fix = True  # in automatic mode queueing IS the fix

    def _mode(self, context: JobContext) -> str:
        run_mode = str((context.scope or {}).get('mode', '')).strip().lower()
        if run_mode in ('automatic', 'review'):
            return run_mode
        try:
            settings = context.config_manager.get(
                f"repair.jobs.{self.job_id}.settings", {}) or {}
            mode = str(settings.get("mode", "automatic")).strip().lower()
        except Exception:  # noqa: BLE001
            mode = "automatic"
        return mode if mode in ("automatic", "review") else "automatic"

    def estimate_scope(self, context: JobContext) -> int:
        try:
            conn = context.db._get_connection()
            try:
                from core.library2.wishlist_mirror import upgrade_candidate_track_ids
                return len(upgrade_candidate_track_ids(conn))
            finally:
                conn.close()
        except Exception:
            return 0

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()
        from core.library2.feature import library_v2_enabled
        library_v2_enabled(context.config_manager)

        from core.library2.wishlist_mirror import (
            mirror_projected_tracks_wishlist,
            upgrade_candidate_track_ids,
        )

        mode = self._mode(context)
        conn = context.db._get_connection()
        try:
            track_ids = upgrade_candidate_track_ids(conn)
            total = len(track_ids)
            queued = 0
            if mode == "review":
                for track_id in track_ids:
                    if context.check_stop() or context.wait_if_paused():
                        break
                    result.scanned += 1
                    self._create_review_finding(context, conn, track_id, result)
                    if context.update_progress:
                        context.update_progress(result.scanned, total)
            else:
                # Mirror in small batches so stop/pause requests take effect
                # quickly and progress is visible.
                batch = 25
                for start in range(0, total, batch):
                    if context.check_stop() or context.wait_if_paused():
                        break
                    chunk = track_ids[start:start + batch]
                    from core.library2 import ADMIN_PROFILE_ID
                    queued += mirror_projected_tracks_wishlist(
                        context.db,
                        conn,
                        chunk,
                        profile_id=ADMIN_PROFILE_ID,
                    )
                    result.scanned += len(chunk)
                    if context.update_progress:
                        context.update_progress(result.scanned, total)
            result.auto_fixed = queued
            logger.info("lib2 upgrade scan (%s): %d checked, %d queued, %d findings",
                        mode, result.scanned, queued, result.findings_created)
        except Exception as e:  # noqa: BLE001
            logger.error("lib2 upgrade scan failed: %s", e, exc_info=True)
            result.errors += 1
        finally:
            conn.close()
        return result

    def _create_review_finding(self, context: JobContext, conn, track_id: int,
                               result: JobResult) -> None:
        """Review mode: surface one finding per genuine upgrade candidate."""
        if not context.create_finding:
            return
        try:
            from core.library2.quality_eval import evaluate_file, profile_targets
            from core.library2.track_files import primary_order

            row = conn.execute(
                f"""SELECT t.id, t.title, al.id AS album_id, al.title AS album_title,
                           al.primary_artist_id AS artist_id,
                           ar.name AS artist_name, qp.name AS profile_name,
                           qp.ranked_targets, qp.upgrade_policy, qp.upgrade_cutoff_index,
                           (SELECT f.id FROM lib2_track_files f
                             WHERE f.track_id=t.id AND f.path IS NOT NULL AND f.path<>''
                               AND COALESCE(f.file_state,'active')
                                   NOT IN ('missing_confirmed','deleted')
                             ORDER BY {primary_order('f')} LIMIT 1) AS file_id
                      FROM lib2_tracks t
                      JOIN lib2_albums al ON al.id=t.album_id
                 LEFT JOIN lib2_artists ar ON ar.id=al.primary_artist_id
                 LEFT JOIN quality_profiles qp ON qp.id=t.quality_profile_id
                     WHERE t.id=?""",
                (int(track_id),),
            ).fetchone()
            if row is None or row["file_id"] is None:
                result.skipped += 1
                return
            file_row = dict(conn.execute(
                "SELECT * FROM lib2_track_files WHERE id=?", (row["file_id"],)
            ).fetchone())
            targets, policy, cutoff_index = profile_targets(dict(row))
            verdict = evaluate_file(file_row, targets, policy, cutoff_index)
            if verdict.get("upgrade_candidate") is not True:
                result.skipped += 1
                return
            current = " / ".join(str(part) for part in (
                file_row.get("format"), file_row.get("bitrate") and f"{file_row['bitrate']} kbps",
                file_row.get("bit_depth") and f"{file_row['bit_depth']}-bit",
            ) if part)
            inserted = context.create_finding(
                job_id=self.job_id,
                finding_type="quality_below_cutoff",
                severity="info",
                entity_type="track",
                entity_id=f"lib2:{row['id']}",
                file_path=file_row.get("path"),
                title=f"Below cutoff: {row['title']} — {row['artist_name'] or 'Unknown'}",
                description=(
                    f'"{row["title"]}" ({current or "unknown quality"}) sits below the '
                    f'upgrade cutoff of profile "{row["profile_name"] or "?"}". '
                    "Approve to queue the upgrade search."
                ),
                details={
                    "title": row["title"],
                    "artist": row["artist_name"],
                    "album": row["album_title"],
                    "profile_name": row["profile_name"],
                    "current_quality": current or None,
                    "meets_profile": verdict.get("meets_profile"),
                    "upgrade_candidate": True,
                    "library_v2_native": True,
                    "library_v2": {
                        "artist_id": row["artist_id"],
                        "album_id": row["album_id"],
                        "track_id": row["id"],
                        "file_id": file_row.get("id"),
                        "artist_ids": [row["artist_id"]] if row["artist_id"] else [],
                        "album_ids": [row["album_id"]],
                        "track_ids": [row["id"]],
                        "file_ids": [file_row["id"]] if file_row.get("id") else [],
                    },
                },
            )
            if inserted:
                result.findings_created += 1
            else:
                result.findings_skipped_dedup += 1
        except Exception as e:  # noqa: BLE001
            logger.debug("review finding failed for track %s: %s", track_id, e)
            result.errors += 1
