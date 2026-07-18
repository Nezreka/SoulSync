"""Corrupt File Detector maintenance job (#1000).

Some tracks in a library can be physically damaged — the file plays with
skips/mutes or won't decode at all. Causes range from a bad slskd source to a
dying disk to an interrupted/in-place tag rewrite. Damaged FLACs are frame-level
corrupt: no amount of re-tagging fixes them; the audio data itself is gone, so
the only cure is a fresh download.

This scans library FLAC files and DECODE-TESTS each one — preferring ``flac -t``
(which also verifies the STREAMINFO MD5, exactly what a user runs by hand), and
falling back to a full ffmpeg decode. Any file that fails to decode cleanly is
surfaced as a finding.

Approving a finding (repair_worker._fix_corrupt_audio) deletes the corrupt file,
drops its DB row so the track goes missing, and re-adds it to the Wishlist so the
real version downloads again — same delete+re-download payload as the preview-clip
tool. The scan itself ONLY creates findings; nothing is deleted or wishlisted
without the user approving (auto_fix is off), and findings can be fixed in bulk or
one at a time from the findings list like every other job.

Decode-testing is real work (it decodes the whole file), so this is opt-in and
respects stop/pause per file. The optional ``only_modified_within_days`` setting
narrows the scan to recently-touched files for a fast, targeted pass.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from typing import Optional, Tuple

from core.library.path_resolver import resolve_library_file_path
from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair_jobs.audio_corruption")

# FLAC only for now: it's the format the damage was reported on and the one
# `flac -t` can MD5-verify. Other formats would need a looser ffmpeg-only check.
_CORRUPT_CHECK_EXTS = {'.flac'}
_DECODE_TIMEOUT_S = 600


def _resolve(file_path: str, context: JobContext) -> Optional[str]:
    """Resolve a stored library path to one this process can read.

    MUST pass the context's transfer folder + config_manager — a bare
    ``resolve_library_file_path(path)`` has no base directories to suffix-walk,
    so for Docker/NAS users (whose DB paths are the MEDIA SERVER's view) every
    single file silently resolved to None and the whole scan was skips (#1000
    follow-up: '6741 FLAC files decode-tested, 0 corrupt, 6741 skipped' in 0.1s)."""
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


def _first_error_line(text: Optional[str]) -> str:
    for line in (text or '').splitlines():
        line = line.strip()
        if line:
            return line[:200]
    return ''


def check_flac_integrity(path: str) -> Tuple[bool, str]:
    """Decode-test a FLAC. Returns (ok, reason).

    ``ok=True`` → decoded clean (or we couldn't run a test, in which case we
    NEVER flag — a false positive here leads to a delete). ``ok=False`` → real
    corruption, with a short reason. Prefers ``flac -t`` (verifies the STREAMINFO
    MD5); falls back to a full ffmpeg decode.
    """
    flac_bin = shutil.which('flac')
    if flac_bin:
        try:
            proc = subprocess.run([flac_bin, '-t', '-s', path],
                                  capture_output=True, text=True, timeout=_DECODE_TIMEOUT_S)
        except (subprocess.TimeoutExpired, OSError):
            return True, ''  # our own failure — don't flag a good file
        if proc.returncode != 0:
            return False, _first_error_line(proc.stderr) or 'flac -t reported errors'
        return True, ''

    ffmpeg_bin = shutil.which('ffmpeg')
    if ffmpeg_bin:
        try:
            proc = subprocess.run(
                [ffmpeg_bin, '-v', 'error', '-nostdin', '-i', path, '-f', 'null', '-'],
                capture_output=True, text=True, timeout=_DECODE_TIMEOUT_S)
        except (subprocess.TimeoutExpired, OSError):
            return True, ''
        err = (proc.stderr or '').strip()
        if proc.returncode != 0 or err:
            return False, _first_error_line(err) or 'ffmpeg decode errors'
        return True, ''

    # No decoder available → can't test → never flag.
    return True, ''


def _decoder_available() -> bool:
    return bool(shutil.which('flac') or shutil.which('ffmpeg'))


@register_job
class AudioCorruptionDetectorJob(RepairJob):
    job_id = 'audio_corruption_detector'
    display_name = 'Corrupt File Detector'
    description = 'Decode-tests library FLAC files and flags damaged ones to re-download'
    help_text = (
        'Scans your library and DECODE-TESTS each FLAC file to find ones that are '
        'physically damaged — audio that skips, mutes, or fails to decode. Damage '
        'can come from a bad download source, a failing disk, or an interrupted tag '
        'write. It uses `flac -t` when available (which also verifies the file\'s MD5 '
        'signature, the same check you\'d run by hand) and falls back to a full ffmpeg '
        'decode.\n\n'
        'A finding is created for each damaged file. Frame-corrupt audio cannot be '
        'repaired by re-tagging — the data itself is gone — so approving a finding '
        'DELETES the file, marks the track missing, and re-adds it to your Wishlist so '
        'the real version downloads again. You can fix findings one at a time or in bulk.\n\n'
        'This is opt-in and does real work (it decodes every file), so it can take a '
        'while on a large library. Use "Only modified within days" to run a fast, '
        'targeted pass over recently-touched files.\n\n'
        'Requires the flac or ffmpeg binary to run the decode test.\n\n'
        'Settings:\n'
        '  - only_modified_within_days: only test files modified in the last N days '
        '(0 = test everything).'
    )
    icon = 'repair-icon-lossless'
    default_enabled = False
    default_interval_hours = 168  # weekly
    default_settings = {
        'only_modified_within_days': 0,
    }
    setting_options: dict = {}
    auto_fix = False

    def _setting_int(self, context: JobContext, key: str, default: int) -> int:
        cm = getattr(context, 'config_manager', None)
        if cm is None:
            return default
        try:
            return int(cm.get(self.get_config_key(key), default) or default)
        except (TypeError, ValueError):
            return default

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()

        if not _decoder_available():
            logger.info("[Corrupt File Detector] neither flac nor ffmpeg on PATH — "
                        "skipping (a decode test can't run without one)")
            return result

        within_days = self._setting_int(context, 'only_modified_within_days', 0)
        cutoff_mtime = (time.time() - within_days * 86400) if within_days > 0 else None

        rows = []
        conn = None
        try:
            conn = context.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT t.id, t.title, ar.name AS artist_name, al.title AS album_title,
                       t.file_path
                FROM tracks t
                LEFT JOIN artists ar ON ar.id = t.artist_id
                LEFT JOIN albums al ON al.id = t.album_id
                WHERE t.file_path IS NOT NULL AND t.file_path != ''
            """)
            rows = [dict(r) for r in cursor.fetchall()]
        except Exception as e:
            logger.error("[Corrupt File Detector] error reading tracks: %s", e, exc_info=True)
            result.errors += 1
            return result
        finally:
            if conn:
                conn.close()

        # Native Library-v2 coverage: active files without a legacy backref.
        native_subjects = {}
        try:
            from core.library2.maintenance_sync import v2_uncovered_file_subjects

            for subject in v2_uncovered_file_subjects(
                context.db, context.config_manager,
            ):
                file_path = str(subject["path"])
                native_subjects[file_path] = subject
                rows.append({
                    'id': f"lib2:{subject['track_id']}",
                    'title': subject['title'],
                    'artist_name': subject['artist_name'],
                    'album_title': subject['album_title'],
                    'file_path': file_path,
                })
        except Exception as e:
            logger.warning("[Corrupt File Detector] V2 subject enumeration failed: %s", e)
            result.errors += 1

        # Narrow to FLAC up front so progress reflects the real work-list.
        rows = [r for r in rows
                if os.path.splitext(r['file_path'])[1].lower() in _CORRUPT_CHECK_EXTS]

        total = len(rows)
        if context.update_progress:
            context.update_progress(0, total)
        if context.report_progress:
            context.report_progress(phase=f'Decode-testing {total} FLAC files...', total=total)

        tested = 0
        unresolved = 0
        outside_window = 0
        for i, row in enumerate(rows):
            if context.check_stop():
                return result
            if i % 5 == 0 and context.wait_if_paused():
                return result

            result.scanned += 1
            title = row['title'] or 'Unknown'
            artist = row['artist_name'] or 'Unknown'
            subject = native_subjects.get(str(row['file_path']))
            if subject:
                from core.library2.paths import resolve_lib2_path

                resolved = row['file_path'] if os.path.isfile(row['file_path']) else (
                    resolve_lib2_path(row['file_path'],
                                      config_manager=context.config_manager))
            else:
                resolved = _resolve(row['file_path'], context)

            if not resolved:
                unresolved += 1
                result.skipped += 1
                continue

            # Optional "recently modified only" narrowing — cheap stat, big speedup.
            if cutoff_mtime is not None:
                try:
                    if os.path.getmtime(resolved) < cutoff_mtime:
                        outside_window += 1
                        result.skipped += 1
                        continue
                except OSError:
                    result.skipped += 1
                    continue

            if context.report_progress:
                context.report_progress(
                    scanned=i + 1, total=total,
                    phase=f'Decode-testing {i + 1}/{total}...',
                    log_line=f'{artist} — {title}', log_type='info')

            tested += 1
            try:
                ok, reason = check_flac_integrity(resolved)
            except Exception as e:
                logger.debug("[Corrupt File Detector] decode test errored for %s: %s",
                             os.path.basename(resolved), e)
                result.errors += 1
                if context.update_progress and (i + 1) % 5 == 0:
                    context.update_progress(i + 1, total)
                continue

            if ok:
                if context.update_progress and (i + 1) % 5 == 0:
                    context.update_progress(i + 1, total)
                continue

            if context.report_progress:
                context.report_progress(
                    log_line=f'Corrupt: {artist} — {title} ({reason})', log_type='error')

            if context.create_finding:
                try:
                    finding_details = {
                        'track_id': row['id'],
                        'title': row['title'],
                        'artist': row['artist_name'],
                        'album': row['album_title'],
                        'reason': reason,
                        'original_path': row['file_path'],
                    }
                    if subject:
                        from core.library2.maintenance_sync import v2_subject_details

                        finding_details.update(v2_subject_details(subject))
                    inserted = context.create_finding(
                        job_id=self.job_id,
                        finding_type='corrupt_audio',
                        severity='error',
                        entity_type='track',
                        entity_id=str(row['id']),
                        file_path=row['file_path'],
                        title=f'Corrupt file: {artist} - {title}',
                        description=(
                            f'"{title}" by {artist} failed a decode test '
                            f'({reason}). The audio is damaged and can\'t be repaired by '
                            're-tagging — approve to delete it and re-download the real version.'),
                        details=finding_details)
                    if inserted:
                        result.findings_created += 1
                    else:
                        result.findings_skipped_dedup += 1
                except Exception as e:
                    logger.debug("[Corrupt File Detector] create finding failed for track %s: %s",
                                 row['id'], e)
                    result.errors += 1

            if context.update_progress and (i + 1) % 5 == 0:
                context.update_progress(i + 1, total)

        if context.update_progress:
            context.update_progress(total, total)
        # An honest summary: 'decode-tested' means DECODED, not merely seen —
        # the old line reported every skip as tested, which hid the path-
        # resolution failure completely ('6741 decode-tested ... in 0.1s').
        logger.info(
            "[Corrupt File Detector] %d of %d FLAC files decode-tested, %d corrupt, "
            "%d path-unresolved, %d outside the modified window",
            tested, total, result.findings_created, unresolved, outside_window)
        if total and unresolved == total:
            # Every single path failed to resolve — that's a mapping problem,
            # not a healthy library. Say so where the user is looking.
            msg = ("No library paths could be resolved to readable files — the DB "
                   "stores your media server's paths. Check Settings → Library → "
                   "Music Paths (or your Docker mounts) so SoulSync can reach them.")
            logger.warning("[Corrupt File Detector] %s", msg)
            if context.report_progress:
                context.report_progress(phase='No library paths resolved',
                                        log_line=msg, log_type='error')
        return result

    def estimate_scope(self, context: JobContext) -> int:
        conn = None
        try:
            conn = context.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COUNT(*) FROM tracks
                WHERE file_path IS NOT NULL AND lower(file_path) LIKE '%.flac'
            """)
            row = cursor.fetchone()
            return row[0] if row else 0
        except Exception:
            return 0
        finally:
            if conn:
                conn.close()
