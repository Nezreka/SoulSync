"""Dead File Cleaner Job — finds DB track entries where the file no longer exists."""

import os

from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair_job.dead_files")


@register_job
class DeadFileCleanerJob(RepairJob):
    job_id = 'dead_file_cleaner'
    display_name = 'Dead File Cleaner'
    description = 'Finds database entries pointing to missing files'
    help_text = (
        'Checks every track in your database to verify the actual audio file still exists '
        'on disk. If a file has been moved, renamed, or deleted outside of SoulSync, the '
        'database entry becomes a "dead" reference.\n\n'
        'Each dead reference is reported as a finding. You can then resolve it by re-downloading '
        'the track or dismiss it to clean up the database entry.\n\n'
        'This job only scans and reports — it never deletes database entries automatically.'
    )
    icon = 'repair-icon-deadfile'
    default_enabled = True
    default_interval_hours = 24
    default_settings = {}
    auto_fix = False

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()

        # Fetch all tracks with file paths, joining to get artist/album names
        tracks = []
        conn = None
        try:
            conn = context.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT t.id, t.title, ar.name, al.title, t.file_path
                FROM tracks t
                LEFT JOIN artists ar ON ar.id = t.artist_id
                LEFT JOIN albums al ON al.id = t.album_id
                WHERE t.file_path IS NOT NULL AND t.file_path != ''
            """)
            tracks = cursor.fetchall()
        except Exception as e:
            logger.error("Error fetching tracks from DB: %s", e, exc_info=True)
            result.errors += 1
            return result
        finally:
            if conn:
                conn.close()

        total = len(tracks)
        if context.update_progress:
            context.update_progress(0, total)

        for i, row in enumerate(tracks):
            if context.check_stop():
                return result
            if i % 200 == 0 and context.wait_if_paused():
                return result

            track_id, title, artist_name, album_title, file_path = row
            result.scanned += 1

            if not os.path.exists(file_path):
                # File is missing — create finding
                if context.create_finding:
                    try:
                        context.create_finding(
                            job_id=self.job_id,
                            finding_type='dead_file',
                            severity='warning',
                            entity_type='track',
                            entity_id=str(track_id),
                            file_path=file_path,
                            title=f'Missing file: {title or "Unknown"}',
                            description=f'Track "{title}" by {artist_name or "Unknown"} points to a file that no longer exists',
                            details={
                                'track_id': track_id,
                                'title': title,
                                'artist': artist_name,
                                'album': album_title,
                                'original_path': file_path,
                            }
                        )
                        result.findings_created += 1
                    except Exception as e:
                        logger.debug("Error creating dead file finding for track %s: %s", track_id, e)
                        result.errors += 1

            if context.update_progress and (i + 1) % 100 == 0:
                context.update_progress(i + 1, total)

        if context.update_progress:
            context.update_progress(total, total)

        logger.info("Dead file scan: %d tracks checked, %d missing files found",
                     result.scanned, result.findings_created)
        return result

    def estimate_scope(self, context: JobContext) -> int:
        conn = None
        try:
            conn = context.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM tracks WHERE file_path IS NOT NULL AND file_path != ''")
            row = cursor.fetchone()
            return row[0] if row else 0
        except Exception:
            return 0
        finally:
            if conn:
                conn.close()
