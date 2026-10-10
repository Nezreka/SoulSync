"""Scheduled catalogue completion, using the same loader as the album page."""

from contextlib import closing

from core.repair_jobs import register_job
from core.repair_jobs.base import (
    JobContext, JobResult, RepairJob, hand_tagged_path_keys, is_hand_tagged_path,
)
from utils.logging_config import get_logger

logger = get_logger('repair.album_catalogue_backfill')


@register_job
class AlbumCatalogueBackfillJob(RepairJob):
    job_id = 'album_catalogue_backfill'
    display_name = 'Album Catalogue Backfill'
    description = 'Completes album, EP and single tracklists without opening their pages.'
    help_text = (
        'Loads the full provider tracklist for incomplete or unverified releases '
        'that have at least one owned or monitored track, across all libraries. '
        'A one-track single is checked too when its full tracklist is still unknown. '
        'Other releases in an artist\'s discography are left for browsing.\n\n'
        'Uses the same album loader, edition references and cached snapshots as '
        'the album page. Existing monitoring, quality profiles, manual values and '
        'edition pins remain authoritative; hand-tagged releases are skipped. '
        'This job loads metadata, not audio files, and does not opt missing '
        'siblings into monitoring. Existing monitored tracks continue through '
        'the normal Wanted/Wishlist pipeline.\n\n'
        'Runs one album at a time in bounded batches. The saved position advances '
        'even when a provider cannot resolve one release, so later releases still '
        'get processed. Subsequent runs wrap around and retry remaining gaps.'
    )
    icon = 'refresh-cw'
    default_enabled = True
    default_interval_hours = 1
    default_settings = {'batch_size': 100}
    auto_fix = True

    def _number(self, context: JobContext, key: str, default: int) -> int:
        if not context.config_manager:
            return default
        try:
            raw = context.config_manager.get(key, default)
            if isinstance(raw, bool):
                return default
            return max(0, int(raw))
        except (TypeError, ValueError):
            return default

    def _pending(self, context: JobContext) -> list[int]:
        from core.library2.completeness import pending_album_catalogues

        keys = hand_tagged_path_keys(context.db)
        with closing(context.db._get_connection()) as conn:
            ids = pending_album_catalogues(conn)
            if not keys:
                return ids
            protected = {
                int(row[0]) for row in conn.execute(
                    'SELECT t.album_id,f.path FROM lib2_tracks t '
                    'JOIN lib2_track_files f ON f.track_id=t.id '
                    "WHERE COALESCE(f.file_state,'active')='active'"
                ) if is_hand_tagged_path(row[1], keys)
            }
        return [album_id for album_id in ids if album_id not in protected]

    def estimate_scope(self, context: JobContext) -> int:
        try:
            return len(self._pending(context))
        except Exception:
            return 0

    def scan(self, context: JobContext) -> JobResult:
        from core.library2.completeness import load_album_catalogue

        result = JobResult()
        if context.check_stop() or context.wait_if_paused():
            return result
        try:
            candidates = self._pending(context)
        except Exception as exc:
            logger.warning('Catalogue backfill enumeration failed: %s', exc)
            result.errors += 1
            return result

        checkpoint_key = f'repair.jobs.{self.job_id}.checkpoint_id'
        checkpoint = self._number(context, checkpoint_key, 0)
        pending = [album_id for album_id in candidates if album_id > checkpoint] or candidates
        settings = context.config_manager.get(
            f'repair.jobs.{self.job_id}.settings', {}) if context.config_manager else {}
        try:
            value = settings.get('batch_size', 100)
            limit = 100 if isinstance(value, bool) else max(1, min(500, int(value)))
        except (AttributeError, TypeError, ValueError):
            limit = 100
        pending = pending[:limit]
        if context.report_progress:
            context.report_progress(
                phase=f'Loading {len(pending)} of {len(candidates)} incomplete album catalogues...',
                total=len(pending))
        if context.update_progress:
            context.update_progress(0, len(pending))

        last_attempted = None
        for album_id in pending:
            if context.check_stop() or context.wait_if_paused():
                break
            try:
                count = 'SELECT COUNT(*) FROM lib2_tracks WHERE album_id=?'
                with closing(context.db._get_connection()) as conn:
                    before = conn.execute(count, (album_id,)).fetchone()[0]
                    load_album_catalogue(
                        context.db, context.config_manager, conn, album_id,
                        inherit_monitoring=False,
                    )
                    added = conn.execute(count, (album_id,)).fetchone()[0] - before
                # A release that stays partial is retried hourly; only one
                # that actually gained tracks is a change worth recording.
                if added > 0:
                    result.auto_fixed += 1
                    if context.report_change:
                        context.report_change(
                            entity_type='album', entity_id=f'lib2:{album_id}',
                            action='catalogue_completed',
                            details={'lib2_album_id': album_id, 'tracks_added': added},
                            result={'success': True},
                        )
                else:
                    result.skipped += 1
            except Exception as exc:
                logger.warning('Catalogue backfill failed for album %s: %s', album_id, exc)
                result.errors += 1
            result.scanned += 1
            last_attempted = album_id
            if context.update_progress:
                context.update_progress(result.scanned, len(pending))

        if last_attempted is not None and context.config_manager:
            try:
                context.config_manager.set(checkpoint_key, last_attempted)
            except Exception as exc:
                # Catalogue commits already succeeded. A failed progress save
                # must not discard their result; replaying the batch is safe.
                logger.warning('Could not save catalogue backfill position: %s', exc)
                result.errors += 1
        return result
