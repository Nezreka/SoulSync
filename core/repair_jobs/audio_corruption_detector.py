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

Passing files are remembered by size and mtime (``audio_integrity_checks``), so a
later run only decodes what changed, what failed, or what has not been re-tested
for ``recheck_after_days``. Decodes run on ``workers`` threads; findings,
progress and every database write stay on the scanning thread.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Dict, Iterable, Optional, Tuple

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


def _file_identity(path: str):
    """``(size, mtime_ns)`` of ``path``, or ``None`` when it is not there."""
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return (stat.st_size, stat.st_mtime_ns)


def _decoder_available() -> bool:
    return bool(shutil.which('flac') or shutil.which('ffmpeg'))


class _IntegrityMemory:
    """Which files passed a decode test, and as what (size, mtime_ns).

    Its own small table, created on first use, so it needs nothing from the
    music database's migrations. Keyed on the RESOLVED path: the one that was
    actually decoded. Failures are never remembered; a damaged file is tested
    again every run until it is replaced. Any database problem disables the
    memory for the run instead of failing the scan.
    """

    def __init__(self, conn):
        self._conn = conn
        # Buffered and written in one short transaction per flush: holding a
        # write transaction open across dozens of decodes would lock the
        # music database for everything else SoulSync is doing.
        self._passed = []
        self._forgotten = []

    @classmethod
    def open(cls, db) -> Optional['_IntegrityMemory']:
        try:
            conn = db._get_connection()
            conn.execute("""
                CREATE TABLE IF NOT EXISTS audio_integrity_checks (
                    path TEXT PRIMARY KEY,
                    size INTEGER NOT NULL,
                    mtime_ns INTEGER NOT NULL,
                    checked_at REAL NOT NULL
                )
            """)
            conn.commit()
            return cls(conn)
        except Exception as e:
            logger.debug("[Corrupt File Detector] no result memory this run: %s", e)
            return None

    def load(self) -> Dict[str, Tuple[int, int, float]]:
        try:
            return {row[0]: (row[1], row[2], row[3]) for row in self._conn.execute(
                "SELECT path, size, mtime_ns, checked_at FROM audio_integrity_checks")}
        except Exception as e:
            logger.debug("[Corrupt File Detector] could not read result memory: %s", e)
            return {}

    def passed(self, path: str, identity: Tuple[int, int]) -> None:
        self._passed.append((path, identity[0], identity[1], time.time()))

    def forget(self, path: str) -> None:
        self._forgotten.append((path,))

    def prune(self, keep: Iterable[str], known: Iterable[str]) -> int:
        gone = [(path,) for path in set(known) - set(keep)]
        self._forgotten.extend(gone)
        return len(gone)

    def commit(self) -> None:
        if not (self._passed or self._forgotten):
            return
        try:
            with self._conn:
                if self._passed:
                    self._conn.executemany(
                        "INSERT OR REPLACE INTO audio_integrity_checks "
                        "(path, size, mtime_ns, checked_at) VALUES (?, ?, ?, ?)", self._passed)
                if self._forgotten:
                    self._conn.executemany(
                        "DELETE FROM audio_integrity_checks WHERE path = ?", self._forgotten)
        except Exception as e:
            logger.debug("[Corrupt File Detector] could not save result memory: %s", e)
        self._passed, self._forgotten = [], []

    def close(self) -> None:
        self.commit()
        try:
            self._conn.close()
        except Exception:
            pass


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
        'This is opt-in and does real work (it decodes every file). Files that pass '
        'are remembered, so later runs only decode files that are new, changed, '
        'failed before, or have not been re-tested in a while.\n\n'
        'Requires the flac or ffmpeg binary to run the decode test.\n\n'
        'Settings:\n'
        '  - only_modified_within_days: only test files modified in the last N days '
        '(0 = test everything).\n'
        '  - recheck_after_days: re-test an unchanged file that passed once it is this '
        'many days old (0 = re-test every file every run).\n'
        '  - workers: how many files to decode at once (1-8).'
    )
    icon = 'repair-icon-lossless'
    default_enabled = False
    default_interval_hours = 168  # weekly
    default_settings = {
        'only_modified_within_days': 0,
        'recheck_after_days': 30,
        'workers': 2,
    }
    setting_options: dict = {}
    auto_fix = False

    def _setting_int(self, context: JobContext, key: str, default: int) -> int:
        """A numeric setting. The Tools page saves job settings as one dict under
        ``repair.jobs.<id>.settings``; this job used to read only the flat
        ``repair.jobs.<id>.<key>``, so a value set in the UI never applied.
        Both are read, the dict first."""
        cm = getattr(context, 'config_manager', None)
        if cm is None:
            return default
        saved = cm.get(self.get_config_key('settings'), {})
        value = saved.get(key) if isinstance(saved, dict) else None
        if value is None:
            value = cm.get(self.get_config_key(key), None)
        try:
            return default if value is None else int(value)
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

        # Narrow to FLAC up front so progress reflects the real work-list.
        rows = [r for r in rows
                if os.path.splitext(r['file_path'])[1].lower() in _CORRUPT_CHECK_EXTS]

        total = len(rows)
        if context.update_progress:
            context.update_progress(0, total)
        if context.report_progress:
            context.report_progress(phase=f'Decode-testing {total} FLAC files...', total=total)

        recheck_days = max(0, self._setting_int(context, 'recheck_after_days', 30))
        workers = min(8, max(1, self._setting_int(context, 'workers', 2)))
        memory = _IntegrityMemory.open(context.db)
        remembered_rows = memory.load() if memory else {}

        # Pass 1, cheap: resolve, stat, and decide what actually needs a decode.
        unresolved = outside_window = remembered = 0
        seen_paths = set()
        work = []
        now = time.time()
        for i, row in enumerate(rows):
            if context.check_stop():
                if memory:
                    memory.close()
                return result
            result.scanned += 1
            resolved = _resolve(row['file_path'], context)
            if not resolved:
                unresolved += 1
                result.skipped += 1
                continue
            identity = _file_identity(resolved)
            if identity is None:
                result.skipped += 1
                continue
            seen_paths.add(resolved)
            # Optional "recently modified only" narrowing — cheap stat, big speedup.
            if cutoff_mtime is not None and identity[1] / 1e9 < cutoff_mtime:
                outside_window += 1
                result.skipped += 1
                continue
            known = remembered_rows.get(resolved)
            if (recheck_days and known and (known[0], known[1]) == identity
                    and now - known[2] < recheck_days * 86400):
                remembered += 1
                result.skipped += 1
                continue
            work.append((row, resolved, identity))

        if context.report_progress:
            context.report_progress(
                phase=f'Decode-testing {len(work)} of {total} FLAC files '
                      f'({remembered} passed recently)...',
                scanned=total - len(work), total=total)
        if context.update_progress:
            context.update_progress(total - len(work), total)

        # Pass 2: decode. Only the tests run on the pool; everything they
        # report is handled here, on this thread.
        tested = 0
        done = total - len(work)
        stopped = False
        pending = {}
        queue = iter(work)
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix='flac-test') as pool:
            def top_up():
                while len(pending) < workers * 2:
                    item = next(queue, None)
                    if item is None:
                        return
                    pending[pool.submit(check_flac_integrity, item[1])] = item

            top_up()
            while pending:
                finished, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in finished:
                    row, resolved, identity_before = pending.pop(future)
                    done += 1
                    tested += 1
                    self._handle_verdict(context, result, memory, row, resolved,
                                         identity_before, future, done, total)
                if memory and done % 50 == 0:
                    memory.commit()
                if context.check_stop() or context.wait_if_paused():
                    stopped = True
                    for future in pending:
                        future.cancel()
                    break
                top_up()

        if memory:
            if not stopped and cutoff_mtime is None:
                # a full pass saw every library FLAC: forget files that left it
                memory.prune(seen_paths, remembered_rows)
            memory.close()
        if stopped:
            return result

        if context.update_progress:
            context.update_progress(total, total)
        # An honest summary: 'decode-tested' means DECODED, not merely seen —
        # the old line reported every skip as tested, which hid the path-
        # resolution failure completely ('6741 decode-tested ... in 0.1s').
        logger.info(
            "[Corrupt File Detector] %d of %d FLAC files decode-tested, %d corrupt, "
            "%d passed recently, %d path-unresolved, %d outside the modified window",
            tested, total, result.findings_created, remembered, unresolved, outside_window)
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

    def _handle_verdict(self, context, result, memory, row, resolved,
                        identity_before, future, done, total) -> None:
        title = row['title'] or 'Unknown'
        artist = row['artist_name'] or 'Unknown'
        if context.report_progress:
            context.report_progress(
                scanned=done, total=total,
                phase=f'Decode-testing {done}/{total}...',
                log_line=f'{artist} — {title}', log_type='info')
        if context.update_progress and done % 5 == 0:
            context.update_progress(done, total)
        try:
            ok, reason = future.result()
        except Exception as e:
            logger.debug("[Corrupt File Detector] decode test errored for %s: %s",
                         os.path.basename(resolved), e)
            result.errors += 1
            return

        # A decode verdict only means something if the bytes under it held
        # still. The scan walks the library while the import pipeline is
        # still moving files into it, and a cross-device move is
        # copy-then-delete — so `flac -t` reading a half-written FLAC
        # reports exactly what a genuinely damaged one does. The finding was
        # then written against a path that had already moved on.
        identity_after = _file_identity(resolved)
        if identity_after is None or identity_after != identity_before:
            # Moved, replaced or still growing under the test — not evidence.
            logger.debug(
                "[Corrupt File Detector] %s changed during the decode test "
                "(%s -> %s) — not flagging",
                os.path.basename(resolved), identity_before, identity_after)
            result.skipped += 1
            if context.report_progress:
                context.report_progress(
                    log_line=(f'Skipped {artist} — {title}: the file changed '
                              f'while it was being tested'), log_type='info')
            return

        if ok:
            if memory:
                memory.passed(resolved, identity_after)
            return
        if memory:
            memory.forget(resolved)

        if context.report_progress:
            context.report_progress(
                log_line=f'Corrupt: {artist} — {title} ({reason})', log_type='error')

        if context.create_finding:
            try:
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
                    details={
                        'track_id': row['id'],
                        'title': row['title'],
                        'artist': row['artist_name'],
                        'album': row['album_title'],
                        'reason': reason,
                        'original_path': row['file_path'],
                    })
                if inserted:
                    result.findings_created += 1
                else:
                    result.findings_skipped_dedup += 1
            except Exception as e:
                logger.debug("[Corrupt File Detector] create finding failed for track %s: %s",
                             row['id'], e)
                result.errors += 1

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
