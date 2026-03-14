"""Album Completeness Checker Job — finds albums missing tracks."""

from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair_job.album_complete")


@register_job
class AlbumCompletenessJob(RepairJob):
    job_id = 'album_completeness'
    display_name = 'Album Completeness'
    description = 'Checks if all tracks from albums are present'
    icon = 'repair-icon-completeness'
    default_enabled = False
    default_interval_hours = 168
    default_settings = {
        'min_tracks_for_check': 3,
    }
    auto_fix = False

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()

        settings = self._get_settings(context)
        min_tracks = settings.get('min_tracks_for_check', 3)

        # Fetch albums with spotify_id that have enough tracks to check
        albums = []
        conn = None
        try:
            conn = context.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT a.id, a.title, a.artist, a.spotify_id, a.total_tracks,
                       COUNT(t.id) as track_count
                FROM albums a
                LEFT JOIN tracks t ON t.album_id = a.id
                WHERE a.spotify_id IS NOT NULL AND a.spotify_id != ''
                GROUP BY a.id
                HAVING track_count >= ?
            """, (min_tracks,))
            albums = cursor.fetchall()
        except Exception as e:
            logger.error("Error fetching albums: %s", e, exc_info=True)
            result.errors += 1
            return result
        finally:
            if conn:
                conn.close()

        total = len(albums)
        if context.update_progress:
            context.update_progress(0, total)

        logger.info("Checking completeness of %d albums", total)

        for i, row in enumerate(albums):
            if context.check_stop():
                return result
            if i % 10 == 0 and context.wait_if_paused():
                return result

            album_id, title, artist, spotify_id, total_tracks, track_count = row
            result.scanned += 1

            # If we don't know total_tracks, try to get it from API
            expected_total = total_tracks
            missing_tracks = []

            if not expected_total and context.spotify_client:
                try:
                    album_data = context.spotify_client.get_album(spotify_id)
                    if album_data:
                        expected_total = album_data.get('total_tracks', 0)
                except Exception:
                    pass

            if not expected_total or track_count >= expected_total:
                result.skipped += 1
                if context.update_progress and (i + 1) % 5 == 0:
                    context.update_progress(i + 1, total)
                continue

            # Album is incomplete — try to find which tracks are missing
            if context.spotify_client:
                try:
                    api_tracks = context.spotify_client.get_album_tracks(spotify_id)
                    if api_tracks and 'items' in api_tracks:
                        # Get track numbers we already have
                        owned_numbers = set()
                        conn2 = context.db._get_connection()
                        cursor2 = conn2.cursor()
                        cursor2.execute(
                            "SELECT track_number FROM tracks WHERE album_id = ? AND track_number IS NOT NULL",
                            (album_id,)
                        )
                        for tr in cursor2.fetchall():
                            owned_numbers.add(tr[0])
                        conn2.close()

                        for item in api_tracks['items']:
                            tn = item.get('track_number')
                            if tn and tn not in owned_numbers:
                                missing_tracks.append({
                                    'track_number': tn,
                                    'name': item.get('name', ''),
                                    'disc_number': item.get('disc_number', 1),
                                })
                except Exception as e:
                    logger.debug("Error getting album tracks for %s: %s", spotify_id, e)

            if context.create_finding:
                try:
                    context.create_finding(
                        job_id=self.job_id,
                        finding_type='incomplete_album',
                        severity='info',
                        entity_type='album',
                        entity_id=str(album_id),
                        file_path=None,
                        title=f'Incomplete: {title or "Unknown"} ({track_count}/{expected_total})',
                        description=(
                            f'Album "{title}" by {artist or "Unknown"} has {track_count} of '
                            f'{expected_total} tracks'
                        ),
                        details={
                            'album_id': album_id,
                            'album_title': title,
                            'artist': artist,
                            'spotify_id': spotify_id,
                            'expected_tracks': expected_total,
                            'actual_tracks': track_count,
                            'missing_tracks': missing_tracks,
                        }
                    )
                    result.findings_created += 1
                except Exception as e:
                    logger.debug("Error creating completeness finding for album %s: %s", album_id, e)
                    result.errors += 1

            if context.update_progress and (i + 1) % 5 == 0:
                context.update_progress(i + 1, total)

        if context.update_progress:
            context.update_progress(total, total)

        logger.info("Completeness check: %d albums checked, %d incomplete found",
                     result.scanned, result.findings_created)
        return result

    def _get_settings(self, context: JobContext) -> dict:
        if not context.config_manager:
            return self.default_settings.copy()
        cfg = context.config_manager.get(f'repair.jobs.{self.job_id}.settings', {})
        merged = self.default_settings.copy()
        merged.update(cfg)
        return merged

    def estimate_scope(self, context: JobContext) -> int:
        conn = None
        try:
            conn = context.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COUNT(*) FROM albums
                WHERE spotify_id IS NOT NULL AND spotify_id != ''
            """)
            row = cursor.fetchone()
            return row[0] if row else 0
        except Exception:
            return 0
        finally:
            if conn:
                conn.close()
