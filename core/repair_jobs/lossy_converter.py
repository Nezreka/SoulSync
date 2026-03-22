"""Lossy Converter Job — finds FLAC files that don't have a lossy copy.

Scans the library for FLAC files without a corresponding lossy copy alongside
them, and creates a finding for each. The fix action converts the file using
ffmpeg with the user's configured codec/bitrate settings.
"""

import os

from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair_job.lossy_converter")

CODEC_MAP = {
    'mp3':  '.mp3',
    'opus': '.opus',
    'aac':  '.m4a',
}


def _resolve_file_path(file_path, transfer_folder, download_folder=None):
    """Resolve a stored DB path to an actual file on disk."""
    if not file_path:
        return None
    if os.path.exists(file_path):
        return file_path
    path_parts = file_path.replace('\\', '/').split('/')
    for base_dir in [transfer_folder, download_folder]:
        if not base_dir or not os.path.isdir(base_dir):
            continue
        for i in range(1, len(path_parts)):
            candidate = os.path.join(base_dir, *path_parts[i:])
            if os.path.exists(candidate):
                return candidate
    return None


@register_job
class LossyConverterJob(RepairJob):
    job_id = 'lossy_converter'
    display_name = 'Lossy Converter'
    description = 'Finds FLAC files without a lossy copy'
    help_text = (
        'Scans your library for FLAC files that don\'t already have a lossy copy '
        '(MP3, Opus, or AAC) alongside them.\n\n'
        'Uses the codec setting from your Lossy Copy configuration on the Settings '
        'page. Enable Lossy Copy in Settings first, then run this job to find FLAC '
        'files missing a lossy copy.\n\n'
        'Each finding can be fixed individually or in bulk — the fix action converts '
        'the FLAC file using ffmpeg at your configured bitrate.\n\n'
        'Requires ffmpeg to be installed.'
    )
    icon = 'repair-icon-lossy'
    default_enabled = False
    default_interval_hours = 0  # Manual only
    default_settings = {
        'delete_original': False,  # Blasphemy Mode — delete FLAC after conversion
    }
    auto_fix = False

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()

        if not context.config_manager:
            logger.warning("Config manager not available")
            return result

        if not context.config_manager.get('lossy_copy.enabled', False):
            if context.report_progress:
                context.report_progress(
                    phase='Skipped — Lossy Copy not enabled in Settings',
                    log_line='Enable Lossy Copy in Settings before running this job',
                    log_type='warning'
                )
            return result

        codec = context.config_manager.get('lossy_copy.codec', 'mp3').lower()
        bitrate = context.config_manager.get('lossy_copy.bitrate', '320')
        out_ext = CODEC_MAP.get(codec, '.mp3')
        quality_label = f'{codec.upper()}-{bitrate}'

        # Get all FLAC tracks from DB
        tracks = []
        conn = None
        try:
            conn = context.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT t.id, t.title, ar.name, al.title, t.file_path,
                       al.thumb_url, ar.thumb_url
                FROM tracks t
                LEFT JOIN artists ar ON ar.id = t.artist_id
                LEFT JOIN albums al ON al.id = t.album_id
                WHERE t.file_path IS NOT NULL AND t.file_path != ''
                  AND LOWER(t.file_path) LIKE '%.flac'
            """)
            tracks = cursor.fetchall()
        except Exception as e:
            logger.error("Error fetching tracks: %s", e)
            result.errors += 1
            return result
        finally:
            if conn:
                conn.close()

        total = len(tracks)
        if context.update_progress:
            context.update_progress(0, total)
        if context.report_progress:
            context.report_progress(
                phase=f'Scanning {total} FLAC files for missing {quality_label} copies...',
                total=total
            )

        download_folder = None
        if context.config_manager:
            download_folder = context.config_manager.get('soulseek.download_path', '')

        for i, row in enumerate(tracks):
            if context.check_stop():
                return result
            if i % 200 == 0 and context.wait_if_paused():
                return result

            track_id, title, artist_name, album_title, file_path, album_thumb, artist_thumb = row
            result.scanned += 1

            if context.report_progress and i % 50 == 0:
                context.report_progress(
                    scanned=i + 1, total=total,
                    phase=f'Scanning {i + 1} / {total}',
                    log_line=f'Checking: {title or "Unknown"} — {artist_name or "Unknown"}',
                    log_type='info'
                )

            # Resolve path
            resolved = _resolve_file_path(file_path, context.transfer_folder, download_folder)
            if not resolved or not os.path.exists(resolved):
                continue

            # Check if lossy copy already exists
            out_path = os.path.splitext(resolved)[0] + out_ext
            if os.path.exists(out_path):
                continue

            # Create finding
            if context.report_progress:
                context.report_progress(
                    log_line=f'Missing {quality_label}: {title or "Unknown"} — {artist_name or "Unknown"}',
                    log_type='skip'
                )

            if context.create_finding:
                try:
                    file_size = os.path.getsize(resolved)
                    context.create_finding(
                        job_id=self.job_id,
                        finding_type='missing_lossy_copy',
                        severity='info',
                        entity_type='track',
                        entity_id=str(track_id),
                        file_path=file_path,
                        title=f'No {quality_label} copy: {title or "Unknown"}',
                        description=(
                            f'FLAC file "{title}" by {artist_name or "Unknown"} does not have '
                            f'a {quality_label} copy alongside it'
                        ),
                        details={
                            'track_id': track_id,
                            'title': title,
                            'artist': artist_name,
                            'album': album_title,
                            'file_path': file_path,
                            'resolved_path': resolved,
                            'codec': codec,
                            'bitrate': bitrate,
                            'quality_label': quality_label,
                            'file_size': file_size,
                            'album_thumb_url': album_thumb or None,
                            'artist_thumb_url': artist_thumb or None,
                        }
                    )
                    result.findings_created += 1
                except Exception as e:
                    logger.debug("Error creating finding for track %s: %s", track_id, e)
                    result.errors += 1

            if context.update_progress and (i + 1) % 100 == 0:
                context.update_progress(i + 1, total)

        if context.update_progress:
            context.update_progress(total, total)

        if context.report_progress:
            context.report_progress(
                scanned=total, total=total,
                phase='Complete',
                log_line=f'Found {result.findings_created} FLAC files without {quality_label} copies',
                log_type='success' if result.findings_created == 0 else 'info'
            )

        logger.info("Lossy converter scan: %d scanned, %d missing lossy copies",
                     result.scanned, result.findings_created)
        return result

    def estimate_scope(self, context: JobContext) -> int:
        conn = None
        try:
            conn = context.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COUNT(*) FROM tracks
                WHERE file_path IS NOT NULL AND file_path != ''
                  AND LOWER(file_path) LIKE '%.flac'
            """)
            row = cursor.fetchone()
            return row[0] if row else 0
        except Exception:
            return 0
        finally:
            if conn:
                conn.close()
