"""ReplayGain Filler maintenance job (#437) — the loudness sibling of the Lyrics
and Cover Art fillers.

Post-processing applies ReplayGain to slskd/WebUI downloads, but content that
enters the library another way — Lidarr, the REST API, manual adds — never gets
it, and there was no way to (re)apply RG to existing tracks or fix the ones where
analysis failed (a recurring ask on #437).

This scans the library for tracks with no ReplayGain track-gain tag and creates a
finding for each. Applying a finding runs the same ffmpeg ebur128 analysis the
import pipeline uses and writes the RG tags in place — no moves, no re-matching.

Scan only READS tags (cheap); the expensive ffmpeg analysis happens on apply.
Requires ffmpeg (RG analysis can't run without it), so the scan no-ops when ffmpeg
isn't on PATH rather than surfacing findings that could never be applied.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from core.library.path_resolver import resolve_library_file_path
from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair_jobs.replaygain_filler")


def needs_replaygain(rg_tags: Optional[Dict[str, Any]]) -> bool:
    """Pure decision: does this track need ReplayGain written?

    True when the track-gain tag is absent or blank. ``rg_tags`` is the dict from
    ``core.replaygain.read_replaygain_tags`` (keys: track_gain, track_peak, …).
    A present track_gain — even "+0.00 dB" — counts as already-tagged.
    """
    if not rg_tags:
        return True
    val = rg_tags.get('track_gain')
    return val is None or str(val).strip() == ''


def _resolve(file_path: str) -> Optional[str]:
    """Resolve a stored library path to one this process can read (Docker/host
    prefix mapping), falling back to the raw path if it's already a real file."""
    resolved = resolve_library_file_path(file_path) if file_path else None
    if not resolved and file_path and os.path.isfile(file_path):
        resolved = file_path
    return resolved


@register_job
class ReplayGainFillerJob(RepairJob):
    job_id = 'replaygain_filler'
    display_name = 'ReplayGain Filler'
    description = 'Finds tracks with no ReplayGain tag and analyzes + writes loudness tags'
    help_text = (
        'Scans your library for tracks that have no ReplayGain track-gain tag — '
        'common for albums added by Lidarr, the REST API, or by hand, which skip '
        "the download post-processing where ReplayGain normally runs.\n\n"
        'A finding is created for each. Applying one runs the same ffmpeg loudness '
        'analysis (EBU R128) the import pipeline uses and writes the ReplayGain '
        'tags in place — no files are moved or renamed. This also lets you re-fill '
        'tracks where the original analysis failed.\n\n'
        'Requires ffmpeg to be installed (the analysis cannot run without it).'
    )
    icon = 'repair-icon-replaygain'
    default_enabled = False
    default_interval_hours = 48
    default_settings = {}
    auto_fix = False

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()

        try:
            from core.replaygain import is_ffmpeg_available, read_replaygain_tags
        except Exception as e:
            logger.warning("[ReplayGain Filler] replaygain module unavailable: %s", e)
            return result
        if not is_ffmpeg_available():
            logger.info("[ReplayGain Filler] ffmpeg not available — skipping scan "
                        "(analysis cannot run without it)")
            return result

        rows = []
        conn = None
        try:
            conn = context.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT t.id, t.title, ar.name, t.file_path
                FROM tracks t
                LEFT JOIN artists ar ON ar.id = t.artist_id
                WHERE t.file_path IS NOT NULL AND t.file_path != ''
            """)
            rows = cursor.fetchall()
        except Exception as e:
            logger.error("[ReplayGain Filler] Error reading tracks: %s", e, exc_info=True)
            result.errors += 1
            return result
        finally:
            if conn:
                conn.close()

        total = len(rows)
        if context.update_progress:
            context.update_progress(0, total)
        if context.report_progress:
            context.report_progress(phase=f'Checking ReplayGain on {total} tracks...', total=total)

        for i, row in enumerate(rows):
            if context.check_stop():
                return result
            if i % 10 == 0 and context.wait_if_paused():
                return result

            track_id, title, artist_name, file_path = row[:4]
            result.scanned += 1

            resolved = _resolve(file_path)
            if not resolved:
                # Can't read the file from here → can't analyze it on apply either.
                result.skipped += 1
                continue

            try:
                rg = read_replaygain_tags(resolved)
            except Exception as e:
                logger.debug("[ReplayGain Filler] tag read failed for '%s': %s", title, e)
                result.skipped += 1
                continue

            if not needs_replaygain(rg):
                result.skipped += 1
                if context.update_progress and (i + 1) % 25 == 0:
                    context.update_progress(i + 1, total)
                continue

            if context.report_progress:
                context.report_progress(
                    scanned=i + 1, total=total,
                    log_line=f'No ReplayGain: {title} — {artist_name or "Unknown"}',
                    log_type='info')

            if context.create_finding:
                try:
                    inserted = context.create_finding(
                        job_id=self.job_id,
                        finding_type='missing_replaygain',
                        severity='info',
                        entity_type='track',
                        entity_id=str(track_id),
                        file_path=file_path,
                        title=f'No ReplayGain: {title or "Unknown"}',
                        description=(f'"{title}" by {artist_name or "Unknown"} has no '
                                     'ReplayGain tag — loudness can be analyzed + written.'),
                        details={
                            'track_id': track_id,
                            'track_title': title,
                            'artist': artist_name,
                            'file_path': file_path,
                        })
                    if inserted:
                        result.findings_created += 1
                    else:
                        result.findings_skipped_dedup += 1
                except Exception as e:
                    logger.debug("[ReplayGain Filler] create finding failed for track %s: %s", track_id, e)
                    result.errors += 1

            if context.update_progress and (i + 1) % 10 == 0:
                context.update_progress(i + 1, total)

        if context.update_progress:
            context.update_progress(total, total)
        logger.info("[ReplayGain Filler] %d tracks checked, %d missing ReplayGain, %d skipped",
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
            """)
            row = cursor.fetchone()
            return row[0] if row else 0
        except Exception:
            return 0
        finally:
            if conn:
                conn.close()
