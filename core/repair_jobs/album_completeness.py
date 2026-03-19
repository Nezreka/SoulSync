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
    help_text = (
        'Compares the number of tracks you have for each album against the expected total '
        'from the Spotify tracklist. Albums where tracks are missing get flagged as findings '
        'with details about which tracks are absent.\n\n'
        'Useful for catching partial downloads or albums where some tracks failed to download. '
        'You can use the Download Missing feature from the album page to fill gaps.\n\n'
        'Settings:\n'
        '- Min Tracks For Check: Only check albums with at least this many expected tracks '
        '(skips singles and EPs)'
    )
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

        # Fetch all albums with a spotify_album_id — filter by expected track count in the loop
        albums = []
        conn = None
        try:
            conn = context.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT al.id, al.title, ar.name, al.spotify_album_id, al.track_count,
                       COUNT(t.id) as actual_count, al.thumb_url, ar.thumb_url
                FROM albums al
                LEFT JOIN artists ar ON ar.id = al.artist_id
                LEFT JOIN tracks t ON t.album_id = al.id
                WHERE al.spotify_album_id IS NOT NULL AND al.spotify_album_id != ''
                GROUP BY al.id
            """)
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

        if context.report_progress:
            context.report_progress(phase=f'Checking {total} albums...', total=total)

        for i, row in enumerate(albums):
            if context.check_stop():
                return result
            if i % 10 == 0 and context.wait_if_paused():
                return result

            album_id, title, artist_name, spotify_album_id, db_track_count, actual_count, album_thumb, artist_thumb = row
            result.scanned += 1

            if context.report_progress:
                context.report_progress(
                    scanned=i + 1, total=total,
                    phase=f'Checking {i + 1} / {total}',
                    log_line=f'Album: {title or "Unknown"} — {artist_name or "Unknown"}',
                    log_type='info'
                )

            # If we don't know the expected track count, try to get it from API
            expected_total = db_track_count

            if not expected_total and context.spotify_client and not context.is_spotify_rate_limited():
                try:
                    album_data = context.spotify_client.get_album(spotify_album_id)
                    if album_data:
                        expected_total = album_data.get('total_tracks', 0)
                except Exception:
                    pass

            # Skip singles/EPs based on expected track count (not local count)
            if expected_total and expected_total < min_tracks:
                result.skipped += 1
                if context.update_progress and (i + 1) % 5 == 0:
                    context.update_progress(i + 1, total)
                continue

            if not expected_total or actual_count >= expected_total:
                result.skipped += 1
                if context.update_progress and (i + 1) % 5 == 0:
                    context.update_progress(i + 1, total)
                continue

            # Album is incomplete — try to find which tracks are missing
            missing_tracks = []
            if context.spotify_client and not context.is_spotify_rate_limited():
                try:
                    api_tracks = context.spotify_client.get_album_tracks(spotify_album_id)
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
                                # Extract artist names from Spotify track data
                                track_artists = []
                                for a in item.get('artists', []):
                                    if isinstance(a, dict):
                                        track_artists.append(a.get('name', ''))
                                    elif isinstance(a, str):
                                        track_artists.append(a)
                                missing_tracks.append({
                                    'track_number': tn,
                                    'name': item.get('name', ''),
                                    'disc_number': item.get('disc_number', 1),
                                    'spotify_track_id': item.get('id', ''),
                                    'duration_ms': item.get('duration_ms', 0),
                                    'artists': track_artists,
                                })
                except Exception as e:
                    logger.debug("Error getting album tracks for %s: %s", spotify_album_id, e)

            if context.report_progress:
                context.report_progress(
                    log_line=f'Incomplete: {title or "Unknown"} ({actual_count}/{expected_total})',
                    log_type='skip'
                )
            if context.create_finding:
                try:
                    context.create_finding(
                        job_id=self.job_id,
                        finding_type='incomplete_album',
                        severity='info',
                        entity_type='album',
                        entity_id=str(album_id),
                        file_path=None,
                        title=f'Incomplete: {title or "Unknown"} ({actual_count}/{expected_total})',
                        description=(
                            f'Album "{title}" by {artist_name or "Unknown"} has {actual_count} of '
                            f'{expected_total} tracks'
                        ),
                        details={
                            'album_id': album_id,
                            'album_title': title,
                            'artist': artist_name,
                            'spotify_album_id': spotify_album_id,
                            'expected_tracks': expected_total,
                            'actual_tracks': actual_count,
                            'missing_tracks': missing_tracks,
                            'album_thumb_url': album_thumb or None,
                            'artist_thumb_url': artist_thumb or None,
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
                WHERE spotify_album_id IS NOT NULL AND spotify_album_id != ''
            """)
            row = cursor.fetchone()
            return row[0] if row else 0
        except Exception:
            return 0
        finally:
            if conn:
                conn.close()
