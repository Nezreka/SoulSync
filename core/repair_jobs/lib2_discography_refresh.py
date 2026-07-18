"""Periodic Library-v2 discography re-expansion (monitor_new_items enforcement).

``monitor_new_items`` promises that newly discovered ('all') or genuinely newer
('new') releases of a monitored artist become wanted automatically — but the enforcement lives in the
discography *re-expansion* path, which used to run only when the user pressed
"Update Discography". This job runs that same re-expansion on a schedule for
every artist whose catalog was already expanded once, so the promise holds
without manual clicks.

Scope rules (deliberately conservative):
- Only artists with ``discography_synced_at`` set — a first expansion stays an
  explicit user action (it writes the artist's whole provider catalog).
- Only monitored artists with ``monitor_new_items`` != 'none'.
- Newly discovered releases are materialized + mirrored via the same shared
  helper the API endpoint uses (``discography.auto_monitor_releases``), so the
  two paths cannot drift.

Scheduled runs have no request context; wishlist mirrors are scoped to the
admin profile (1), matching every other scheduled acquisition path. No-op when
``features.library_v2`` is off. Never touches files.
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
        "artist whose catalog was already fetched once. Releases discovered "
        "since the last expansion are auto-monitored when the artist's "
        "'monitor new items' setting is 'all'; 'new' only accepts a dated "
        "release newer than the previously newest known release. Their tracklist is "
        "materialized and mirrored into the Wishlist, so the normal download "
        "pipeline picks them up. Artists never expanded stay untouched (the "
        "first expansion remains an explicit user action). Does nothing when "
        "the new library feature is off."
    )
    icon = "refresh-cw"
    default_enabled = False
    default_interval_hours = 168  # weekly
    auto_fix = True  # queueing IS the fix; there is nothing to review

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
                  AND discography_synced_at IS NOT NULL
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
        try:
            if context.config_manager.get("features.library_v2", False) is not True:
                logger.debug("Library v2 disabled — discography refresh skipped")
                return result
        except Exception:
            return result

        from core.library2.discography import refresh_artist_discography

        conn = context.db._get_connection()
        try:
            artist_ids = self._artist_ids(conn)
        finally:
            conn.close()

        total = len(artist_ids)
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
                )
                auto_ids = stats.get("auto_monitor_album_ids") or []
                discovered += len(auto_ids)
                mirrored += artist_mirrored
            except Exception as e:  # noqa: BLE001
                logger.debug("discography refresh failed (artist %s): %s", artist_id, e)
                result.errors += 1
            result.scanned += 1
            if context.update_progress:
                context.update_progress(result.scanned, total)

        result.auto_fixed = discovered
        if total:
            logger.info("lib2 discography refresh: %d artists re-expanded, "
                        "%d new releases auto-monitored (%d tracks mirrored)",
                        result.scanned, discovered, mirrored)
        return result
