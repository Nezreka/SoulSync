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

from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair_jobs.missing_lyrics")


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

        total = len(rows)
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

            # Already has a sidecar on disk → nothing to do.
            if _has_lrc_sidecar(file_path):
                result.skipped += 1
                continue

            # Option A: only flag tracks LRClib actually has lyrics for. An
            # instrumental returns nothing here and is silently skipped (never
            # re-flagged on future scans).
            try:
                duration_s = int(duration) if duration else None
            except (TypeError, ValueError):
                duration_s = None
            try:
                available = lyrics_client.has_remote_lyrics(
                    title, artist_name or '', album_title, duration_s)
            except Exception as e:
                logger.debug("[Lyrics Filler] availability check failed for '%s': %s", title, e)
                available = False

            if not available:
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
        logger.info("[Lyrics Filler] %d tracks checked, %d with lyrics found, %d skipped",
                    result.scanned, result.findings_created, result.skipped)
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
