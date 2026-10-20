"""Untagged Downloads job: downloads that landed with no title/artist/album.

a crash inside metadata enrichment can leave a file with its tags cleared
but still filed into the right folder (oct 2026: an int album id, 25
Discover Weekly tracks showed up as Unknown Album). the download history
still knows what each file is, so this checks every downloaded file's tags
and offers to write the missing ones back from that record.

scan only reads tags. download history is pruned, so this runs daily to
catch a file while its record is still there.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

from core.library.path_resolver import resolve_library_file_path
from core.metadata.core_tag_guard import CORE_FIELDS
from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair_jobs.untagged_downloads")

_TRACK_PREFIX = re.compile(r'^\s*(\d{1,3})\s*[-.\s]')


def track_number_from_filename(file_path: str) -> Optional[int]:
    """"01 - Title.flac" -> 1. None when the name has no number prefix."""
    # history paths are the media server's view, often windows-style on a linux box
    match = _TRACK_PREFIX.match(re.split(r'[\\/]', file_path or '')[-1])
    if not match:
        return None
    num = int(match.group(1))
    return num or None


def missing_core_fields(tags: Dict[str, Any]) -> List[str]:
    """pure: which of title/artist/album the read tags have no value for."""
    if not tags or tags.get('error'):
        return []
    return [f for f in CORE_FIELDS if not str(tags.get(f) or '').strip()]


def _resolve(file_path: str, context: JobContext) -> Optional[str]:
    if not file_path:
        return None
    resolved = resolve_library_file_path(
        file_path,
        transfer_folder=context.transfer_folder,
        config_manager=context.config_manager,
    )
    if not resolved and os.path.isfile(file_path):
        resolved = file_path
    return resolved


@register_job
class UntaggedDownloadsJob(RepairJob):
    job_id = 'untagged_downloads'
    display_name = 'Untagged Downloads'
    description = 'Finds downloads that landed with no title, artist or album tag'
    help_text = (
        'Checks every file in your download history for missing title, artist '
        'or album tags. A file like that is usually in the right folder but '
        'shows up as Unknown Album on your media server, because tagging failed '
        'partway through.\n\n'
        'Applying a finding writes the missing tags back from what SoulSync '
        'recorded when it downloaded the file. Tags the file already has are '
        'left alone, and nothing is moved or renamed. Rescan your media server '
        'afterwards to pick up the change.\n\n'
        'Download history is only kept for a while, so this runs daily.'
    )
    icon = 'repair-icon-metadata'
    default_enabled = True
    default_interval_hours = 24
    default_settings = {}
    auto_fix = False

    def _rows(self, context: JobContext):
        conn = None
        try:
            conn = context.db._get_connection()
            cursor = conn.cursor()
            # newest record per file wins: a re-download replaces the old one
            cursor.execute("""
                SELECT h.id, h.title, h.artist_name, h.album_name, h.file_path
                FROM library_history h
                WHERE h.event_type = 'download'
                  AND h.file_path IS NOT NULL AND h.file_path != ''
                  AND h.id = (SELECT MAX(h2.id) FROM library_history h2
                              WHERE h2.file_path = h.file_path
                                AND h2.event_type = 'download')
                ORDER BY h.id DESC
            """)
            return cursor.fetchall()
        finally:
            if conn:
                conn.close()

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()
        try:
            from core.tag_writer import read_file_tags
            rows = self._rows(context)
        except Exception as e:
            logger.error("[Untagged Downloads] could not read download history: %s", e, exc_info=True)
            result.errors += 1
            return result

        total = len(rows)
        if context.update_progress:
            context.update_progress(0, total)
        if context.report_progress:
            context.report_progress(phase=f'Checking tags on {total} downloads...', total=total)

        for i, row in enumerate(rows):
            if context.check_stop():
                return result
            if i % 10 == 0 and context.wait_if_paused():
                return result

            history_id, title, artist_name, album_name, file_path = row[:5]
            result.scanned += 1

            resolved = _resolve(file_path, context)
            if not resolved:
                result.skipped += 1
                continue

            missing = missing_core_fields(read_file_tags(resolved))
            if not missing:
                result.skipped += 1
                if context.update_progress and (i + 1) % 25 == 0:
                    context.update_progress(i + 1, total)
                continue

            if context.report_progress:
                context.report_progress(
                    scanned=i + 1, total=total,
                    log_line=f'No {"/".join(missing)} tag: {title} — {artist_name or "Unknown"}',
                    log_type='warning')

            if context.create_finding:
                try:
                    inserted = context.create_finding(
                        job_id=self.job_id,
                        finding_type='untagged_download',
                        severity='warning',
                        entity_type='file',
                        entity_id=str(history_id),
                        file_path=file_path,
                        title=f'Untagged: {title or os.path.basename(file_path)}',
                        description=(f'"{title}" by {artist_name or "Unknown"} has no '
                                     f'{", ".join(missing)} tag, so your media server '
                                     'files it under Unknown Album. Applying writes them '
                                     'back from the download record.'),
                        details={
                            'history_id': history_id,
                            'track_title': title,
                            'artist': artist_name,
                            'album_title': album_name,
                            'track_number': track_number_from_filename(file_path),
                            'missing': missing,
                            'file_path': file_path,
                        })
                    if inserted:
                        result.findings_created += 1
                    else:
                        result.findings_skipped_dedup += 1
                except Exception as e:
                    logger.debug("[Untagged Downloads] create finding failed for %s: %s", file_path, e)
                    result.errors += 1

            if context.update_progress and (i + 1) % 10 == 0:
                context.update_progress(i + 1, total)

        if context.update_progress:
            context.update_progress(total, total)
        logger.info("[Untagged Downloads] %d downloads checked, %d untagged",
                    result.scanned, result.findings_created)
        return result

    def estimate_scope(self, context: JobContext) -> int:
        conn = None
        try:
            conn = context.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COUNT(DISTINCT file_path) FROM library_history
                WHERE event_type = 'download' AND file_path IS NOT NULL AND file_path != ''
            """)
            row = cursor.fetchone()
            return row[0] if row else 0
        except Exception:
            return 0
        finally:
            if conn:
                conn.close()
