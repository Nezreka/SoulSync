"""Missing Lyrics maintenance job (Sokhi) — the lyrics sibling of the Cover
Art Filler.

Scans the library for tracks that have no ``.lrc`` sidecar, asks LRClib
whether lyrics actually exist for them (so instrumentals/interludes that
genuinely have no lyrics are never flagged — Option A), and creates a finding
for each fixable track. Applying a finding fetches + writes the ``.lrc`` and
embeds the lyrics, reusing the same LyricsClient the import pipeline uses.

Mirrors MissingCoverArtJob's "only surface actionable findings" design.
"""

from __future__ import annotations

import os

from core.library.path_resolver import resolve_library_file_path
from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair_jobs.missing_lyrics")

# Consecutive "unknown" LRCLib answers (429/503/timeout) after which the
# scan stops early instead of mis-recording the rest of the library (#1879).
MAX_CONSECUTIVE_UNKNOWN = 5


def _has_lrc_sidecar(file_path: str) -> bool:
    """True if a .lrc (or .txt lyrics) sidecar already sits next to the file."""
    if not file_path:
        return False
    base = os.path.splitext(file_path)[0]
    return os.path.exists(base + '.lrc') or os.path.exists(base + '.txt')


@register_job
class MissingLyricsJob(RepairJob):
    job_id = 'missing_lyrics'
    display_name = 'Lyrics Filler'
    description = 'Finds tracks with no .lrc lyrics and fetches synced lyrics from LRClib'
    help_text = (
        'Scans your library for tracks that have no .lrc lyrics file next to them. '
        'For each one it asks LRClib whether lyrics actually exist — tracks with no '
        'lyrics available (instrumentals, interludes) are skipped, so only fixable '
        'tracks are surfaced.\n\n'
        'When lyrics are found, a finding is created so you can review and apply it. '
        'Applying writes a synced .lrc sidecar (or plain text if no synced version '
        'exists) and embeds the lyrics in the file — the same way the import pipeline '
        'and the Library Re-tag tool do.\n\n'
        'Requires LRClib to be enabled (Settings > Metadata Enhancement).'
    )
    icon = 'repair-icon-lyrics'
    default_enabled = False
    default_interval_hours = 48
    default_settings = {}
    auto_fix = False

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()

        # Respect the same LRClib master toggle the import pipeline uses.
        if context.config_manager and context.config_manager.get(
                'metadata_enhancement.lrclib_enabled', True) is False:
            logger.info("[Lyrics Filler] LRClib disabled in settings — skipping scan")
            return result

        try:
            from core.lyrics_client import lyrics_client
        except Exception as e:
            logger.warning("[Lyrics Filler] lyrics client unavailable: %s", e)
            return result
        if not getattr(lyrics_client, 'api', None):
            logger.info("[Lyrics Filler] LRClib API not available — skipping scan")
            return result

        rows = []
        conn = None
        try:
            conn = context.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT t.id, t.title, ar.name, al.title, t.file_path, t.duration
                FROM tracks t
                LEFT JOIN albums al ON al.id = t.album_id
                LEFT JOIN artists ar ON ar.id = t.artist_id
                WHERE t.file_path IS NOT NULL AND t.file_path != ''
                  AND t.title IS NOT NULL AND t.title != ''
            """)
            rows = cursor.fetchall()
        except Exception as e:
            logger.error("[Lyrics Filler] Error reading tracks: %s", e, exc_info=True)
            result.errors += 1
            return result
        finally:
            if conn:
                conn.close()

        # The stored file_path may not exist as-is in this process's filesystem view (docker mounts,
        # a Plex/SoulSync path mismatch). The .lrc check below MUST run against the resolved on-disk
        # path — exactly like the apply (_fix_missing_lyrics) and the Cover Art sibling already do.
        # Checking the raw DB path made every path-mapped user's tracks read as ".lrc missing" even
        # though the sidecar was right there (issue #955).
        download_folder = (context.config_manager.get('soulseek.download_path', '')
                           if context.config_manager else '') or None

        total = len(rows)
        # Tri-state probing (#1879): an LRCLib lookup can come back
        # "unknown" (429/503/timeout) — that is NOT a definitive negative
        # and must never be counted as a skip. A run of consecutive
        # unknowns means LRCLib is pushing back; stop the scan early
        # (stopped_early) instead of burning the whole library against a
        # rate limit and recording thousands of false negatives.
        unknown_count = 0
        consecutive_unknown = 0
        last_unknown_reason = ''
        if context.update_progress:
            context.update_progress(0, total)
        if context.report_progress:
            context.report_progress(phase=f'Checking lyrics for {total} tracks...', total=total)

        for i, row in enumerate(rows):
            if context.check_stop():
                return result
            if i % 10 == 0 and context.wait_if_paused():
                return result

            track_id, title, artist_name, album_title, file_path, duration = row[:6]
            result.scanned += 1

            # Resolve the stored path to the real file before checking for the sidecar (see note
            # above). Fall back to the raw path when the resolver returns nothing — the common docker
            # case where the container path already equals the stored path.
            resolved_path = resolve_library_file_path(
                file_path,
                transfer_folder=getattr(context, 'transfer_folder', None),
                download_folder=download_folder,
                config_manager=context.config_manager,
            ) or file_path

            # Already has a sidecar on disk → nothing to do.
            if _has_lrc_sidecar(resolved_path):
                result.skipped += 1
                continue

            # Option A: only flag tracks LRClib actually has lyrics for. An
            # instrumental returns nothing here and is silently skipped (never
            # re-flagged on future scans).
            # tracks.duration is stored in MILLISECONDS (schema) — LRClib's
            # exact-match wants SECONDS, so convert (else exact-match never
            # hits and it silently falls back to a fuzzier search).
            try:
                duration_s = int(int(duration) / 1000) if duration else None
                if duration_s is not None and duration_s <= 0:
                    duration_s = None
            except (TypeError, ValueError):
                duration_s = None
            # Tri-state probe (#1879): prefer the detailed check when the
            # client offers it so the unknown reason is available for the
            # log / stopped_early text; fall back to has_remote_lyrics
            # (True = found, False = absent, None = unknown).
            probe_status = 'absent'
            probe_reason = ''
            probe_kind = ''
            try:
                check = getattr(lyrics_client, 'check_remote_lyrics', None)
                if callable(check):
                    outcome = check(title, artist_name or '', album_title, duration_s)
                    if hasattr(outcome, 'status'):
                        probe_status = outcome.status
                        probe_reason = getattr(outcome, 'reason', '') or ''
                        probe_kind = getattr(outcome, 'error_kind', '') or ''
                        if probe_status not in ('found', 'absent', 'unknown'):
                            # An unrecognized status string is a contract
                            # we don't understand — never record it as a
                            # definitive negative.
                            probe_reason = probe_reason or f'unrecognized probe status: {probe_status!r}'
                            probe_status = 'unknown'
                    else:
                        # A check that returns a bare bool/None instead of
                        # a result object: map it like has_remote_lyrics
                        # rather than guessing 'absent' for a truthy hit.
                        probe_status = ('found' if outcome else
                                        'unknown' if outcome is None else 'absent')
                else:
                    available = lyrics_client.has_remote_lyrics(
                        title, artist_name or '', album_title, duration_s)
                    probe_status = 'found' if available else ('unknown' if available is None else 'absent')
            except Exception as e:
                # A raising probe is an unknown, not a negative (#1879).
                # Warning, not debug: if the probe is raising for a
                # programming reason (not LRCLib), that must be visible —
                # and the consecutive-unknown abort below bounds this to
                # a handful of lines before the scan stops. The full
                # exception goes to the LOG; the user-facing reason (it
                # can land in stopped_early) names only the error type.
                logger.warning("[Lyrics Filler] availability check failed for '%s': %s", title, e)
                probe_status = 'unknown'
                probe_reason = f'LRCLib lookup failed ({type(e).__name__})'

            if probe_status == 'unknown':
                unknown_count += 1
                # Only TRANSIENT unknowns (429/5xx/timeout/network — or an
                # unclassified one, e.g. a raising probe) count toward the
                # early stop. A permanent query error (e.g. a 400 for one
                # album's bad metadata) says something about that track's
                # query, not about LRCLib's health: counting it would stop
                # a whole-library scan with a false "LRCLib stopped
                # answering" diagnosis over a cluster of bad rows. It does
                # RESET the streak, though — LRCLib answered, and
                # "consecutive" in the stop message should be literally
                # true (same rule as the bulk-fix breaker).
                if probe_kind == 'permanent':
                    consecutive_unknown = 0
                else:
                    consecutive_unknown += 1
                    last_unknown_reason = probe_reason
                logger.warning(
                    "[Lyrics Filler] LRCLib availability unknown for '%s' (%s) — not counted as missing lyrics",
                    title, probe_reason or 'lookup failed')
                if consecutive_unknown >= MAX_CONSECUTIVE_UNKNOWN:
                    result.stopped_early = (
                        f'LRCLib stopped answering reliably after {consecutive_unknown} '
                        f'consecutive failed lookups ({last_unknown_reason or "unknown error"}). '
                        f'Scan stopped early so the remaining tracks are not recorded as '
                        f'having no lyrics; retry later.')
                    logger.warning("[Lyrics Filler] %s", result.stopped_early)
                    if context.report_progress:
                        context.report_progress(
                            scanned=i + 1, total=total,
                            log_line=result.stopped_early,
                            log_type='warning')
                    if context.update_progress:
                        context.update_progress(i + 1, total)
                    logger.info("[Lyrics Filler] %d tracks checked, %d with lyrics found, "
                                "%d skipped, %d unknown (LRCLib unavailable)",
                                result.scanned, result.findings_created, result.skipped,
                                unknown_count)
                    return result
                if context.update_progress and (i + 1) % 10 == 0:
                    context.update_progress(i + 1, total)
                continue

            # A definitive answer (found OR absent) breaks the unknown run.
            consecutive_unknown = 0

            if probe_status != 'found':
                result.skipped += 1
                if context.update_progress and (i + 1) % 10 == 0:
                    context.update_progress(i + 1, total)
                continue

            if context.report_progress:
                context.report_progress(
                    scanned=i + 1, total=total,
                    log_line=f'Found lyrics: {title} — {artist_name or "Unknown"}',
                    log_type='success')

            if context.create_finding:
                try:
                    inserted = context.create_finding(
                        job_id=self.job_id,
                        finding_type='missing_lyrics',
                        severity='info',
                        entity_type='track',
                        entity_id=str(track_id),
                        file_path=file_path,
                        title=f'Missing lyrics: {title or "Unknown"}',
                        description=f'"{title}" by {artist_name or "Unknown"} has no .lrc — lyrics found on LRClib.',
                        details={
                            'track_id': track_id,
                            'track_title': title,
                            'artist': artist_name,
                            'album_title': album_title,
                            'file_path': file_path,
                            'duration': duration_s,
                        })
                    if inserted:
                        result.findings_created += 1
                    else:
                        result.findings_skipped_dedup += 1
                except Exception as e:
                    logger.debug("[Lyrics Filler] create finding failed for track %s: %s", track_id, e)
                    result.errors += 1

            if context.update_progress and (i + 1) % 5 == 0:
                context.update_progress(i + 1, total)

        if context.update_progress:
            context.update_progress(total, total)
        logger.info("[Lyrics Filler] %d tracks checked, %d with lyrics found, %d skipped, "
                    "%d unknown (LRCLib unavailable)",
                    result.scanned, result.findings_created, result.skipped, unknown_count)
        return result

    def estimate_scope(self, context: JobContext) -> int:
        conn = None
        try:
            conn = context.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COUNT(*) FROM tracks
                WHERE file_path IS NOT NULL AND file_path != ''
                  AND title IS NOT NULL AND title != ''
            """)
            row = cursor.fetchone()
            return row[0] if row else 0
        except Exception:
            return 0
        finally:
            if conn:
                conn.close()
