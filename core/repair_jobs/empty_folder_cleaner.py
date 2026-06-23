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

from core.library.residual_files import JUNK_FILES, is_disposable, is_junk  # noqa: F401 — JUNK_FILES/is_junk re-exported
from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair_jobs.empty_folder_cleaner")


def dir_is_removable(files: Iterable[str], surviving_subdirs: Iterable[str],
                     *, ignore_junk: bool = True, ignore_disposable: bool = False) -> bool:
    """Pure: is a directory safe to remove?

    Removable iff it has **no surviving subdirectories** and **no real files** —
    where "no real files" means literally empty, or (when ``ignore_junk``) only
    OS-junk files, or (when ``ignore_disposable`` — #891) only *residual* files:
    junk + cover/scan images + lyric/metadata sidecars. ``ignore_disposable`` is the
    broader opt-in that clears the cover.jpg-only folders a reorganize leaves behind.
    ``surviving_subdirs`` is the list of child dirs that are NOT themselves being
    removed (i.e. still hold content).
    """
    if list(surviving_subdirs):
        return False
    files = list(files)
    if not files:
        return True
    if ignore_disposable:
        return all(is_disposable(f) for f in files)
    if ignore_junk:
        return all(is_junk(f) for f in files)
    return False


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
        're-checking it is still empty). Folders that contain any real file — an '
        'audio track, a booklet, anything not recognized as a leftover — are never '
        'touched, the library root is never removed, and it cascades: a folder left '
        'empty once its empty children are removed is cleaned too.\n\n'
        'Enable "Also remove image/sidecar-only folders" to clear the cover.jpg / '
        '.lrc leftovers a Library Reorganize leaves behind — folders whose only '
        'remaining files are cover/scan images or lyric/metadata sidecars.'
    )
    icon = 'repair-icon-folder'
    default_enabled = False
    default_interval_hours = 168  # weekly — empties accrue slowly
    default_settings = {'remove_junk_files': True, 'remove_residual_files': False}
    auto_fix = False

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()

        root = context.transfer_folder
        if not root or not os.path.isdir(root):
            logger.info("[Empty Folder Cleaner] library root not available — skipping")
            return result
        root = os.path.realpath(root)

        ignore_junk = True
        ignore_disposable = False
        try:
            if context.config_manager:
                # #912: job settings are persisted as a nested dict under
                # `repair.jobs.<id>.settings` (see RepairWorker.set_job_settings / get_job_config).
                # The old flat-key reads ('repair.jobs.empty_folder_cleaner.remove_residual_files')
                # never matched what the UI saves, so the #891 opt-in toggle silently did nothing —
                # the scan always fell back to the False default and skipped every image/.lrc folder.
                job_settings = context.config_manager.get(
                    'repair.jobs.empty_folder_cleaner.settings', {}) or {}
                if isinstance(job_settings, dict):
                    ignore_junk = bool(job_settings.get('remove_junk_files', True))
                    # #891: also clear folders left holding only images / .lrc / sidecars
                    # (what a reorganize leaves behind). Opt-in — default off.
                    ignore_disposable = bool(job_settings.get('remove_residual_files', False))
        except Exception:  # noqa: S110 — setting read is best-effort; defaults to junk-only
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
            if not dir_is_removable(filenames, surviving,
                                    ignore_junk=ignore_junk, ignore_disposable=ignore_disposable):
                result.skipped += 1
                continue

            flagged.add(dirpath)
            junk = [f for f in filenames if is_junk(f)]
            # Files that will be swept along with the folder (junk always; images/
            # sidecars only when the residual option is on).
            purgeable = [f for f in filenames
                         if is_junk(f) or (ignore_disposable and is_disposable(f))]
            residual = [f for f in purgeable if not is_junk(f)]
            rel = os.path.relpath(dirpath, root)
            if context.report_progress:
                context.report_progress(log_line=f'Empty folder: {rel}', log_type='info')
            if context.create_finding:
                try:
                    if residual:
                        extra = f' (only {len(residual)} leftover image/sidecar file(s))'
                    elif junk:
                        extra = f' (only {len(junk)} junk file(s))'
                    else:
                        extra = ''
                    inserted = context.create_finding(
                        job_id=self.job_id,
                        finding_type='empty_folder',
                        severity='info',
                        entity_type='folder',
                        entity_id=dirpath,
                        file_path=dirpath,
                        title=f'Empty folder: {os.path.basename(dirpath) or rel}',
                        description=(f'"{rel}" holds no music' + extra + ' — safe to remove.'),
                        details={
                            'folder_path': dirpath,
                            'junk_files': junk,
                            'purgeable_files': purgeable,
                            'remove_junk': ignore_junk,
                            'remove_disposable': ignore_disposable,
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
                        root: str, listdir, isdir, islink, remove_file, rmdir,
                        remove_disposable: bool = False) -> dict:
    """Pure-ish orchestration for the apply handler — RE-CHECKS the folder is still
    removable, then deletes any purgeable leftovers + the folder. Effects injected
    for testing.

    With ``remove_disposable`` (#891) the re-check also treats cover images and
    lyric/metadata sidecars as removable, and sweeps them before rmdir. Returns
    ``{'removed': bool, 'error': str|None}``. Refuses to touch the root, a symlink, a
    non-dir, or a folder that gained REAL content (audio, a booklet, anything not
    recognized as residual) since the scan.
    """
    if not folder_path or not isdir(folder_path):
        return {'removed': False, 'error': 'Folder no longer exists'}
    if islink(folder_path):
        return {'removed': False, 'error': 'Refusing to remove a symlinked folder'}
    if root and os.path.realpath(folder_path) == os.path.realpath(root):
        return {'removed': False, 'error': 'Refusing to remove the library root'}

    def _purgeable(e: str) -> bool:
        return (remove_junk and is_junk(e)) or (remove_disposable and is_disposable(e))

    # Re-check at apply time: only purgeable leftovers now? (Anything else = leave it.)
    entries = list(listdir(folder_path))
    real_entries = [e for e in entries if not _purgeable(e)]
    if real_entries:
        return {'removed': False, 'error': 'Folder is no longer empty — left untouched'}

    for e in entries:
        if _purgeable(e):
            try:
                remove_file(os.path.join(folder_path, e))
            except Exception:  # noqa: S110 — leftover best-effort; rmdir below fails loudly if blocked
                pass
    try:
        rmdir(folder_path)
    except Exception as e:
        return {'removed': False, 'error': f'Could not remove folder: {e}'}
    return {'removed': True, 'error': None}


__all__ = ['dir_is_removable', 'is_junk', 'remove_empty_folder', 'EmptyFolderCleanerJob']
