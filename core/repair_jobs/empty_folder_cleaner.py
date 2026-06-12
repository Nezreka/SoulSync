"""Empty Folder Cleaner maintenance job (corruption's request).

After imports, relocations, and deletions, the music library accumulates empty
artist/album folders (and folders left holding only OS junk like .DS_Store). This
scans the library root and flags folders that are safe to remove, so the library
stays tidy.

Safety is the whole point — deleting directories is destructive:
  - only TRULY empty folders (no real files) are ever flagged; a folder with a
    cover.jpg or any audio is never touched,
  - optionally folders holding *only* OS-junk files (.DS_Store, Thumbs.db, …),
  - the library root itself is never removed, nor symlinked directories,
  - it walks bottom-up so a parent left empty by its (removable) children cascades,
  - and the apply handler RE-CHECKS emptiness at delete time, so anything that
    gained a file between scan and apply is left alone.

``dir_is_removable`` is the pure decision seam — unit-tested independent of the FS.
"""

from __future__ import annotations

import os
from typing import Iterable, List

from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair_jobs.empty_folder_cleaner")

# Files that don't count as real content — safe to delete along with the folder.
JUNK_FILES = {'.ds_store', 'thumbs.db', 'desktop.ini', '.directory', 'album.nfo~'}


def is_junk(name: str) -> bool:
    return (name or '').lower() in JUNK_FILES


def dir_is_removable(files: Iterable[str], surviving_subdirs: Iterable[str],
                     *, ignore_junk: bool = True) -> bool:
    """Pure: is a directory safe to remove?

    Removable iff it has **no surviving subdirectories** and **no real files** —
    where "no real files" means literally empty, or (when ``ignore_junk``) only
    OS-junk files. ``surviving_subdirs`` is the list of child dirs that are NOT
    themselves being removed (i.e. still hold content).
    """
    if list(surviving_subdirs):
        return False
    files = list(files)
    if not files:
        return True
    if not ignore_junk:
        return False
    return all(is_junk(f) for f in files)


@register_job
class EmptyFolderCleanerJob(RepairJob):
    job_id = 'empty_folder_cleaner'
    display_name = 'Empty Folder Cleaner'
    description = 'Finds empty (or junk-only) folders in the library and removes them'
    help_text = (
        'Scans your music library for empty folders left behind after imports, '
        'relocations, and deletions — empty artist/album folders, or folders that '
        'hold only OS junk like .DS_Store / Thumbs.db.\n\n'
        'A finding is created for each. Applying one deletes the folder (after '
        're-checking it is still empty). Folders that contain any real file — a '
        'cover image, an audio track, anything — are never touched, the library '
        'root is never removed, and it cascades: a folder left empty once its '
        'empty children are removed is cleaned too.'
    )
    icon = 'repair-icon-folder'
    default_enabled = False
    default_interval_hours = 168  # weekly — empties accrue slowly
    default_settings = {'remove_junk_files': True}
    auto_fix = False

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()

        root = context.transfer_folder
        if not root or not os.path.isdir(root):
            logger.info("[Empty Folder Cleaner] library root not available — skipping")
            return result
        root = os.path.realpath(root)

        ignore_junk = True
        try:
            if context.config_manager:
                ignore_junk = bool(context.config_manager.get(
                    'repair.jobs.empty_folder_cleaner.remove_junk_files', True))
        except Exception:  # noqa: S110 — setting read is best-effort; defaults to True
            pass

        flagged = set()   # dir paths we'd remove → a parent sees them as "gone"

        # topdown=False ⇒ deepest first, so children are decided before parents.
        for dirpath, dirnames, filenames in os.walk(root, topdown=False):
            if context.check_stop():
                return result
            real = os.path.realpath(dirpath)
            if real == root:
                continue                      # never the library root itself
            if os.path.islink(dirpath):
                continue                      # don't delete symlinked dirs
            result.scanned += 1

            surviving = [d for d in dirnames
                         if os.path.join(dirpath, d) not in flagged]
            if not dir_is_removable(filenames, surviving, ignore_junk=ignore_junk):
                result.skipped += 1
                continue

            flagged.add(dirpath)
            junk = [f for f in filenames if is_junk(f)]
            rel = os.path.relpath(dirpath, root)
            if context.report_progress:
                context.report_progress(log_line=f'Empty folder: {rel}', log_type='info')
            if context.create_finding:
                try:
                    inserted = context.create_finding(
                        job_id=self.job_id,
                        finding_type='empty_folder',
                        severity='info',
                        entity_type='folder',
                        entity_id=dirpath,
                        file_path=dirpath,
                        title=f'Empty folder: {os.path.basename(dirpath) or rel}',
                        description=(f'"{rel}" holds no music'
                                     + (f' (only {len(junk)} junk file(s))' if junk else '')
                                     + ' — safe to remove.'),
                        details={
                            'folder_path': dirpath,
                            'junk_files': junk,
                            'remove_junk': ignore_junk,
                        })
                    if inserted:
                        result.findings_created += 1
                    else:
                        result.findings_skipped_dedup += 1
                except Exception as e:
                    logger.debug("[Empty Folder Cleaner] create finding failed for %s: %s", dirpath, e)
                    result.errors += 1

        logger.info("[Empty Folder Cleaner] %d folders scanned, %d empty flagged",
                    result.scanned, result.findings_created)
        return result

    def estimate_scope(self, context: JobContext) -> int:
        root = context.transfer_folder
        if not root or not os.path.isdir(root):
            return 0
        total = 0
        for _dp, dirnames, _f in os.walk(root):
            total += len(dirnames)
        return total


def remove_empty_folder(folder_path: str, *, junk_files: List[str], remove_junk: bool,
                        root: str, listdir, isdir, islink, remove_file, rmdir) -> dict:
    """Pure-ish orchestration for the apply handler — RE-CHECKS the folder is still
    removable, then deletes any junk + the folder. Effects injected for testing.

    Returns ``{'removed': bool, 'error': str|None}``. Refuses to touch the root, a
    symlink, a non-dir, or a folder that gained real content since the scan.
    """
    if not folder_path or not isdir(folder_path):
        return {'removed': False, 'error': 'Folder no longer exists'}
    if islink(folder_path):
        return {'removed': False, 'error': 'Refusing to remove a symlinked folder'}
    if root and os.path.realpath(folder_path) == os.path.realpath(root):
        return {'removed': False, 'error': 'Refusing to remove the library root'}

    # Re-check at apply time: only junk/empty now? (Anything else = leave it.)
    entries = list(listdir(folder_path))
    real_entries = [e for e in entries if not (remove_junk and is_junk(e))]
    if real_entries:
        return {'removed': False, 'error': 'Folder is no longer empty — left untouched'}

    if remove_junk:
        for j in entries:
            if is_junk(j):
                try:
                    remove_file(os.path.join(folder_path, j))
                except Exception:  # noqa: S110 — junk best-effort; rmdir below fails loudly if blocked
                    pass
    try:
        rmdir(folder_path)
    except Exception as e:
        return {'removed': False, 'error': f'Could not remove folder: {e}'}
    return {'removed': True, 'error': None}


__all__ = ['dir_is_removable', 'is_junk', 'remove_empty_folder', 'EmptyFolderCleanerJob']
