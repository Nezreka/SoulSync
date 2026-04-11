"""Orphan File Detector Job — finds files in the transfer folder not tracked in the DB."""

import os
import re
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

        # Build set of known file-path suffixes from DB.
        # DB may store paths with a different base prefix than the local filesystem
        # (e.g. DB has /mnt/musicBackup/Artist/Album/track.mp3, local disk is
        # H:\Music\Artist\Album\track.mp3).  We compare using suffix fragments
        # of depth 1-3 (filename, album/filename, artist/album/filename) which
        # covers all realistic path-prefix mismatches.
        known_suffixes = set()
        known_titles = set()       # (title_lower, artist_lower) for exact match
        known_titles_clean = set()  # (clean_title, clean_artist) for normalized match
        conn = None

        def _strip_extras(s):
            """Strip parentheticals/brackets for normalized comparison."""
            return re.sub(r'\s*[\(\[][^)\]]*[\)\]]', '', s).strip()

        try:
            conn = context.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT file_path FROM tracks WHERE file_path IS NOT NULL AND file_path != ''")
            for row in cursor.fetchall():
                parts = row[0].replace('\\', '/').split('/')
                # Store last 1-4 path components as lowercase suffixes.
                # Depth 4 covers Genre/Artist/Album/track.flac scenarios.
                for depth in range(1, min(5, len(parts) + 1)):
                    suffix = '/'.join(parts[-depth:]).lower()
                    known_suffixes.add(suffix)

            # Build title+artist sets for tag-based fallback matching
            cursor.execute("""
                SELECT t.title, ar.name FROM tracks t
                LEFT JOIN artists ar ON ar.id = t.artist_id
                WHERE t.title IS NOT NULL AND t.title != ''
            """)
            for row in cursor.fetchall():
                title = (row[0] or '').lower().strip()
                artist = (row[1] or '').lower().strip()
                if title:
                    known_titles.add((title, artist))
                    # Also store normalized version (stripped of feat., parentheticals, etc.)
                    clean_t = _strip_extras(title)
                    clean_a = _strip_extras(artist)
                    if clean_t:
                        known_titles_clean.add((clean_t, clean_a))
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
        if context.report_progress:
            context.report_progress(phase=f'Checking {total} files...', total=total)

        orphan_files = []

        for i, fpath in enumerate(audio_files):
            if context.check_stop():
                return result
            if i % 100 == 0 and context.wait_if_paused():
                return result

            result.scanned += 1

            if context.report_progress and i % 50 == 0:
                context.report_progress(
                    scanned=i + 1, total=total,
                    phase=f'Checking {i + 1} / {total}',
                    log_line=f'Checking: {os.path.basename(fpath)}',
                    log_type='info'
                )

            # Check if this file matches any known DB path via suffix matching
            fpath_parts = fpath.replace('\\', '/').split('/')
            is_known = False
            for depth in range(1, min(5, len(fpath_parts) + 1)):
                suffix = '/'.join(fpath_parts[-depth:]).lower()
                if suffix in known_suffixes:
                    is_known = True
                    break

            # Fallback: read file tags and check if title+artist exists in DB
            # Catches path mismatches where the file is tracked but under a different path.
            # Uses both exact and normalized comparison to handle feat. suffixes, etc.
            if not is_known and known_titles:
                try:
                    from mutagen import File as MutagenFile
                    audio = MutagenFile(fpath, easy=True)
                    if audio:
                        file_title = ((audio.get('title') or [None])[0] or '').lower().strip()
                        file_artist = ((audio.get('artist') or [None])[0] or '').lower().strip()
                        if file_title:
                            # Exact match first (fast path)
                            if (file_title, file_artist) in known_titles:
                                is_known = True
                            else:
                                # Normalized match: strip (feat. X), [FLAC 16bit], etc.
                                clean_title = _strip_extras(file_title)
                                clean_artist = _strip_extras(file_artist)
                                # Also try first artist only (handles "Gorillaz, Dennis Hopper" → "Gorillaz")
                                first_artist = clean_artist.split(',')[0].strip() if clean_artist else ''
                                if clean_title and (
                                    (clean_title, clean_artist) in known_titles_clean or
                                    (first_artist and (clean_title, first_artist) in known_titles_clean)
                                ):
                                    is_known = True
                except Exception:
                    pass

            # Last resort: parse title from filename pattern "NN - Title [Quality].ext"
            # and match against known titles. Catches files with unreadable tags.
            if not is_known and known_titles:
                try:
                    fname_base = os.path.splitext(os.path.basename(fpath))[0]
                    # Strip quality tags like [FLAC 16bit], [MP3-320]
                    fname_clean = re.sub(r'\s*\[.*?\]\s*$', '', fname_base).strip()
                    # Strip leading track number: "01 - Title" → "Title"
                    fname_clean = re.sub(r'^\d{1,3}\s*[-–.]\s*', '', fname_clean).strip()
                    if fname_clean:
                        fname_lower = fname_clean.lower()
                        # Extract artist from parent folder
                        parent_folder = os.path.basename(os.path.dirname(fpath)).lower().strip()
                        # Try artist from grandparent (Artist/Album/track.flac)
                        grandparent = os.path.basename(os.path.dirname(os.path.dirname(fpath))).lower().strip()
                        for folder_artist in [parent_folder, grandparent]:
                            if (fname_lower, folder_artist) in known_titles:
                                is_known = True
                                break
                            clean_fn = _strip_extras(fname_lower)
                            clean_fa = _strip_extras(folder_artist)
                            if clean_fn and (clean_fn, clean_fa) in known_titles_clean:
                                is_known = True
                                break
                except Exception:
                    pass

            if not is_known:
                orphan_files.append(fpath)

            if context.update_progress and (i + 1) % 50 == 0:
                context.update_progress(i + 1, total)

        # Safety check: if most files look like orphans, it's probably a path
        # mismatch between the DB and filesystem — not actual orphans.
        orphan_ratio = len(orphan_files) / total if total else 0
        mass_orphan = orphan_ratio > 0.5 and len(orphan_files) > 20

        if mass_orphan:
            logger.warning(
                "Mass orphan warning: %d of %d files (%.0f%%) flagged as orphans — "
                "this likely indicates a DB path mismatch, not actual orphans",
                len(orphan_files), total, orphan_ratio * 100
            )

        for fpath in orphan_files:
            if context.report_progress:
                context.report_progress(
                    log_line=f'Orphan: {os.path.basename(fpath)}',
                    log_type='skip'
                )
            try:
                stat = os.stat(fpath)
                ext = os.path.splitext(fpath)[1].lower().lstrip('.')
                if context.create_finding:
                    context.create_finding(
                        job_id=self.job_id,
                        finding_type='orphan_file',
                        severity='warning' if mass_orphan else 'info',
                        entity_type='file',
                        entity_id=None,
                        file_path=fpath,
                        title=f'Orphan file: {os.path.basename(fpath)}',
                        description=(
                            'Audio file in transfer folder is not tracked in the database. '
                            'WARNING: Mass orphan detection triggered — this may be a path '
                            'mismatch, not actual orphans. Verify before deleting!'
                        ) if mass_orphan else (
                            'Audio file in transfer folder is not tracked in the database'
                        ),
                        details={
                            'file_size': stat.st_size,
                            'format': ext,
                            'modified': time.strftime('%Y-%m-%d %H:%M:%S',
                                                      time.localtime(stat.st_mtime)),
                            'folder': os.path.dirname(fpath),
                            'mass_orphan': mass_orphan,
                        }
                    )
                    result.findings_created += 1
            except Exception as e:
                logger.debug("Error creating orphan finding for %s: %s", fpath, e)
                result.errors += 1

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
