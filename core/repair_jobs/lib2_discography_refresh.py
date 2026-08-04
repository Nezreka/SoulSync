"""Periodic Library-v2 discography re-expansion (monitor_new_items enforcement).

``monitor_new_items`` promises that newly discovered ('all') or genuinely newer
('new') releases of a monitored artist become wanted automatically — but the enforcement lives in the
discography *re-expansion* path, which used to run only when the user pressed
"Update Discography". This job runs that same re-expansion on a schedule for
every monitored artist, so the promise holds without manual clicks — including
an artist that was only ever imported/watchlisted and never had its provider
catalog fetched (review A5): a monitored artist whose first fetch happens to
land on this sweep instead of a manual click is not a special case downstream.
``_expand_artist_discography``'s own ``eligible_reexpansion`` gate (it requires
``had_discography`` — i.e. NOT this artist's first-ever fetch) already keeps a
first expansion from auto-monitoring the whole back catalog, first-fetched-by-
sweep or first-fetched-by-click alike, so this job doesn't need its own
first-expansion guard on top of that.

Scope rules:
- Only monitored artists with ``monitor_new_items`` != 'none'.
- Newly discovered releases are materialized + mirrored via the same shared
  helper the API endpoint uses (``discography.auto_monitor_releases``), so the
  two paths cannot drift.

Scheduled runs have no request context; wishlist mirrors are scoped to the
admin profile (1), matching every other scheduled acquisition path. No-op when
the native Library-v2 catalogue. Never touches files.
"""

from __future__ import annotations

from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair.monitored_discography_refresh")


@register_job
class Lib2DiscographyRefreshJob(RepairJob):
    job_id = "monitored_discography_refresh"
    display_name = "Monitored Discography Refresh"
    description = "Re-fetch monitored artists' provider catalogs so new releases become wanted."
    help_text = (
        "Re-expands the provider discography of every monitored library "
        "artist, including one never manually expanded before — its first "
        "fetch happening here instead of an 'Update Discography' click "
        "changes nothing (an artist's very first fetch never auto-monitors "
        "its back catalog either way). Releases discovered since the last "
        "expansion are auto-monitored when the artist's 'monitor new items' "
        "setting is 'all'; 'new' only accepts a dated release newer than the "
        "previously newest known release. Their tracklist is materialized "
        "and mirrored into the Wishlist, so the normal download pipeline "
        "picks them up. Does nothing when the new library feature is off."
    )
    icon = "refresh-cw"
    default_enabled = False
    default_interval_hours = 168  # weekly
    default_settings = {"mode": "automatic"}
    setting_options = {"mode": ["automatic", "review"]}
    auto_fix = True

    def _mode(self, context: JobContext) -> str:
        run_mode = str((context.scope or {}).get("mode", "")).strip().lower()
        if run_mode in {"automatic", "review"}:
            return run_mode
        try:
            settings = context.config_manager.get(
                f"repair.jobs.{self.job_id}.settings", {}) or {}
            mode = str(settings.get("mode", "automatic")).strip().lower()
        except Exception:  # noqa: BLE001
            mode = "automatic"
        return mode if mode in {"automatic", "review"} else "automatic"

    def _create_review_findings(self, context: JobContext, album_ids: list[int],
                                result: JobResult) -> None:
        if not context.create_finding or not album_ids:
            return
        conn = context.db._get_connection()
        try:
            for album_id in album_ids:
                row = conn.execute(
                    """SELECT al.id, al.title, ar.name AS artist_name,
                              al.release_date, al.year
                         FROM lib2_albums al
                    LEFT JOIN lib2_artists ar ON ar.id=al.primary_artist_id
                        WHERE al.id=?""",
                    (int(album_id),),
                ).fetchone()
                if not row:
                    continue
                inserted = context.create_finding(
                    job_id=self.job_id,
                    finding_type="missing_discography_release",
                    severity="info",
                    entity_type="album",
                    entity_id=f"lib2:{row['id']}",
                    file_path=None,
                    title=f"New release: {row['title']} — {row['artist_name'] or 'Unknown'}",
                    description=(
                        "This release was discovered by monitor-new-items. "
                        "Approve it to materialize its tracklist and add wanted tracks."
                    ),
                    details={
                        "lib2_album_id": int(row["id"]),
                        "artist_name": row["artist_name"],
                        "release_date": row["release_date"],
                        "year": row["year"],
                    },
                )
                if inserted:
                    result.findings_created += 1
        finally:
            conn.close()

    def _artist_ids(self, conn) -> list:
        # §40: alias-member rows (canonical_artist_id set) are skipped as
        # sweep roots — refresh_artist_discography fans out across the whole
        # alias group when it processes the CANONICAL row, so an alias row
        # would otherwise get its group refreshed again on its own turn too
        # (an N-member group would cost N^2 fetches per sweep instead of N).
        return [r[0] for r in conn.execute(
            """SELECT id FROM lib2_artists
                WHERE monitored = 1
                  AND COALESCE(monitor_new_items, 'all') <> 'none'
                  AND canonical_artist_id IS NULL
                ORDER BY id"""
        )]

    def estimate_scope(self, context: JobContext) -> int:
        try:
            conn = context.db._get_connection()
            try:
                return len(self._artist_ids(conn))
            finally:
                conn.close()
        except Exception:
            return 0

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()
        from core.library2.feature import library_v2_enabled
        library_v2_enabled(context.config_manager)

        from core.library2.discography import refresh_artist_discography

        conn = context.db._get_connection()
        try:
            artist_ids = self._artist_ids(conn)
        finally:
            conn.close()

        total = len(artist_ids)
        mode = self._mode(context)
        discovered = 0
        mirrored = 0
        for _i, artist_id in enumerate(artist_ids):
            if context.check_stop() or context.wait_if_paused():
                break
            try:
                stats, artist_mirrored = refresh_artist_discography(
                    context.db,
                    artist_id,
                    context.config_manager,
                    wishlist_profile_id=1,
                    auto_monitor=(mode == "automatic"),
                )
                auto_ids = stats.get("auto_monitor_album_ids") or []
                discovered += len(auto_ids)
                mirrored += artist_mirrored
                if mode == "review":
                    self._create_review_findings(context, auto_ids, result)
            except Exception as e:  # noqa: BLE001
                logger.debug("discography refresh failed (artist %s): %s", artist_id, e)
                result.errors += 1
            result.scanned += 1
            if context.update_progress:
                context.update_progress(result.scanned, total)

        result.auto_fixed = discovered if mode == "automatic" else 0
        if total:
            logger.info("lib2 discography refresh (%s): %d artists re-expanded, "
                        "%d new releases discovered (%d tracks mirrored, %d findings)",
                        mode, result.scanned, discovered, mirrored,
                        result.findings_created)
        return result
