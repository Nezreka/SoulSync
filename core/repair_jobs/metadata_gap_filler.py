"""Metadata Gap Filler Job — finds tracks missing key metadata and locates it from APIs."""

import time

from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair_job.metadata_gap")


@register_job
class MetadataGapFillerJob(RepairJob):
    job_id = 'metadata_gap_filler'
    display_name = 'Metadata Gap Filler'
    description = 'Finds tracks missing ISRC or MusicBrainz IDs and locates them'
    help_text = (
        'Searches for tracks in your library that are missing important metadata identifiers: '
        'ISRC codes and MusicBrainz recording IDs. These identifiers are used for accurate '
        'matching, scrobbling, and enrichment.\n\n'
        'For each track with gaps, the job queries MusicBrainz by title and artist to find '
        'the correct IDs. Results are reported as findings for your review.\n\n'
        'Settings:\n'
        '- Fill ISRC: Look up missing ISRC codes\n'
        '- Fill MusicBrainz ID: Look up missing MusicBrainz recording IDs'
    )
    icon = 'repair-icon-metadata'
    default_enabled = False
    default_interval_hours = 72
    default_settings = {
        'fill_isrc': True,
        'fill_musicbrainz_id': True,
    }
    auto_fix = False

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()

        settings = self._get_settings(context)
        fill_isrc = settings.get('fill_isrc', True)
        fill_mb_id = settings.get('fill_musicbrainz_id', True)

        # Build WHERE clauses for missing fields (only columns that exist on tracks)
        conditions = []
        if fill_isrc:
            conditions.append("(t.isrc IS NULL OR t.isrc = '')")
        if fill_mb_id:
            conditions.append("(t.musicbrainz_recording_id IS NULL OR t.musicbrainz_recording_id = '')")

        if not conditions:
            return result

        where = " OR ".join(conditions)

        # Fetch tracks with gaps, prioritizing those with spotify_track_id
        tracks = []
        conn = None
        try:
            conn = context.db._get_connection()
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT t.id, t.title, ar.name, al.title, t.spotify_track_id,
                       t.isrc, t.musicbrainz_recording_id,
                       al.thumb_url, ar.thumb_url
                FROM tracks t
                LEFT JOIN artists ar ON ar.id = t.artist_id
                LEFT JOIN albums al ON al.id = t.album_id
                WHERE t.title IS NOT NULL AND t.title != ''
                  AND ({where})
                ORDER BY
                    CASE WHEN t.spotify_track_id IS NOT NULL AND t.spotify_track_id != '' THEN 0 ELSE 1 END,
                    t.id
                LIMIT 500
            """)
            tracks = cursor.fetchall()
        except Exception as e:
            logger.error("Error fetching tracks with metadata gaps: %s", e, exc_info=True)
            result.errors += 1
            return result
        finally:
            if conn:
                conn.close()

        total = len(tracks)
        if context.update_progress:
            context.update_progress(0, total)

        logger.info("Found %d tracks with metadata gaps", total)

        if context.report_progress:
            context.report_progress(phase=f'Enriching {total} tracks...', total=total)

        for i, row in enumerate(tracks):
            if context.check_stop():
                return result
            if i % 20 == 0 and context.wait_if_paused():
                return result

            track_id, title, artist_name, album_title, spotify_track_id, isrc, mb_id, album_thumb, artist_thumb = row
            result.scanned += 1

            if context.report_progress:
                context.report_progress(
                    scanned=i + 1, total=total,
                    phase=f'Enriching {i + 1} / {total}',
                    log_line=f'Looking up: {title or "Unknown"} — {artist_name or "Unknown"}',
                    log_type='info'
                )
            found_fields = {}

            # Try Spotify enrichment first (most reliable for ISRC)
            if spotify_track_id and context.spotify_client and not context.is_spotify_rate_limited():
                try:
                    track_data = context.spotify_client.get_track_details(spotify_track_id)
                    if track_data:
                        if fill_isrc and not isrc:
                            ext_ids = track_data.get('external_ids', {})
                            if ext_ids.get('isrc'):
                                found_fields['isrc'] = ext_ids['isrc']
                except Exception as e:
                    logger.debug("Spotify enrichment failed for track %s: %s", track_id, e)

            # Try MusicBrainz for MB recording ID
            if fill_mb_id and not mb_id and context.mb_client:
                try:
                    recordings = context.mb_client.search_recording(
                        title, artist_name=artist_name, limit=1
                    )
                    if recordings:
                        found_fields['musicbrainz_recording_id'] = recordings[0].get('id', '')
                except Exception as e:
                    logger.debug("MusicBrainz lookup failed for track %s: %s", track_id, e)

            # Create finding for user to review instead of auto-writing
            if found_fields:
                if context.report_progress:
                    context.report_progress(
                        log_line=f'Found: {", ".join(found_fields.keys())} for {title or "Unknown"}',
                        log_type='success'
                    )
                if context.create_finding:
                    try:
                        field_names = ', '.join(found_fields.keys())
                        context.create_finding(
                            job_id=self.job_id,
                            finding_type='metadata_gap',
                            severity='info',
                            entity_type='track',
                            entity_id=str(track_id),
                            file_path=None,
                            title=f'Missing metadata: {title or "Unknown"}',
                            description=(
                                f'Track "{title}" by {artist_name or "Unknown"} is missing: {field_names}. '
                                f'Found values from API lookup.'
                            ),
                            details={
                                'track_id': track_id,
                                'title': title,
                                'artist': artist_name,
                                'album': album_title,
                                'spotify_track_id': spotify_track_id,
                                'found_fields': found_fields,
                                'album_thumb_url': album_thumb or None,
                                'artist_thumb_url': artist_thumb or None,
                            }
                        )
                        result.findings_created += 1
                    except Exception as e:
                        logger.debug("Error creating metadata gap finding for track %s: %s", track_id, e)
                        result.errors += 1
            else:
                result.skipped += 1

            # Rate limit API calls
            if spotify_track_id:
                if context.sleep_or_stop(0.5):
                    return result

            if context.update_progress and (i + 1) % 10 == 0:
                context.update_progress(i + 1, total)

        if context.update_progress:
            context.update_progress(total, total)

        logger.info("Metadata gap scan: %d tracks checked, %d gaps found, %d skipped",
                     result.scanned, result.findings_created, result.skipped)
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
                SELECT COUNT(*) FROM tracks
                WHERE title IS NOT NULL AND title != ''
                  AND ((isrc IS NULL OR isrc = '')
                    OR (musicbrainz_recording_id IS NULL OR musicbrainz_recording_id = ''))
            """)
            row = cursor.fetchone()
            return min(row[0], 500) if row else 0
        except Exception:
            return 0
        finally:
            if conn:
                conn.close()
