"""Orphan File Detector Job — finds files in the transfer folder not tracked in the DB."""

import os
import time

from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair_job.orphan_files")

AUDIO_EXTENSIONS = {'.mp3', '.flac', '.ogg', '.opus', '.m4a', '.aac', '.wav', '.wma', '.aiff', '.aif'}


@register_job
class OrphanFileDetectorJob(RepairJob):
    job_id = 'orphan_file_detector'
    display_name = 'Orphan File Detector'
    description = 'Finds audio files not tracked in the database'
    help_text = (
        'Walks your transfer folder looking for audio files (FLAC, MP3, M4A, OGG, WAV, etc.) '
        'that exist on disk but have no matching entry in the SoulSync database.\n\n'
        'Orphan files can appear after manual folder edits, interrupted downloads, or database '
        'issues. Each orphan is reported as a finding so you can decide whether to import it '
        'into your library or remove it.\n\n'
        'This job only scans and reports — it never moves or deletes files on its own.'
    )
    icon = 'repair-icon-orphan'
    default_enabled = True
    default_interval_hours = 24
    default_settings = {}
    auto_fix = False

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()

        transfer = context.transfer_folder
        if not os.path.isdir(transfer):
            logger.warning("Transfer folder does not exist: %s", transfer)
            return result

        # Build set of all known file paths from DB
        known_paths = set()
        conn = None
        try:
            conn = context.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT file_path FROM tracks WHERE file_path IS NOT NULL AND file_path != ''")
            for row in cursor.fetchall():
                # Normalize path for comparison
                known_paths.add(os.path.normpath(row[0]))
        except Exception as e:
            logger.error("Error reading known file paths from DB: %s", e, exc_info=True)
            result.errors += 1
            return result
        finally:
            if conn:
                conn.close()

        # Walk transfer folder and find orphans
        audio_files = []
        for root, _dirs, files in os.walk(transfer):
            if context.check_stop():
                return result
            for fname in files:
                ext = os.path.splitext(fname)[1].lower()
                if ext in AUDIO_EXTENSIONS:
                    audio_files.append(os.path.join(root, fname))

        total = len(audio_files)
        if context.update_progress:
            context.update_progress(0, total)

        for i, fpath in enumerate(audio_files):
            if context.check_stop():
                return result
            if i % 100 == 0 and context.wait_if_paused():
                return result

            result.scanned += 1
            norm_path = os.path.normpath(fpath)

            if norm_path not in known_paths:
                # This file is an orphan — create finding
                try:
                    stat = os.stat(fpath)
                    ext = os.path.splitext(fpath)[1].lower().lstrip('.')
                    if context.create_finding:
                        context.create_finding(
                            job_id=self.job_id,
                            finding_type='orphan_file',
                            severity='info',
                            entity_type='file',
                            entity_id=None,
                            file_path=fpath,
                            title=f'Orphan file: {os.path.basename(fpath)}',
                            description=f'Audio file in transfer folder is not tracked in the database',
                            details={
                                'file_size': stat.st_size,
                                'format': ext,
                                'modified': time.strftime('%Y-%m-%d %H:%M:%S',
                                                          time.localtime(stat.st_mtime)),
                                'folder': os.path.dirname(fpath),
                            }
                        )
                        result.findings_created += 1
                except Exception as e:
                    logger.debug("Error creating orphan finding for %s: %s", fpath, e)
                    result.errors += 1

            if context.update_progress and (i + 1) % 50 == 0:
                context.update_progress(i + 1, total)

        if context.update_progress:
            context.update_progress(total, total)

        logger.info("Orphan file scan: %d files scanned, %d orphans found",
                     result.scanned, result.findings_created)
        return result

    def estimate_scope(self, context: JobContext) -> int:
        transfer = context.transfer_folder
        if not os.path.isdir(transfer):
            return 0
        count = 0
        for _root, _dirs, files in os.walk(transfer):
            for fname in files:
                if os.path.splitext(fname)[1].lower() in AUDIO_EXTENSIONS:
                    count += 1
        return count
