"""BPM Backfill Job — finds tracks missing BPM and fills it from Deezer or local analysis.

Issue #1476: lib2_tracks.bpm is only written at download time. Tracks already in
the library never get one. This job backfills BPM for existing tracks:

1. Deezer API first (via deezer_id) — fast, no local CPU cost
2. Local librosa analysis fallback — no API calls, works offline

Findings-first like metadata_gap_filler: results are reported for user review,
not auto-written. The existing _fix_metadata_gap handler applies the bpm field.
"""

import os

from core.metadata_service import get_client_for_source
from core.repair_jobs import register_job
from core.library2.maintenance_subjects import active_file_subjects, subject_details
from core.library2.paths import resolve_lib2_path
from core.repair_jobs.base import (
    JobContext, JobResult, RepairJob, drop_hand_tagged, scoped_file_subjects,
)
from core.repair_jobs.metadata_gap_filler import _gap_cursor
from utils.logging_config import get_logger

logger = get_logger("repair_job.bpm_backfill")


@register_job
class BpmBackfillJob(RepairJob):
    job_id = 'bpm_backfill'
    display_name = 'BPM Backfill'
    description = 'Finds tracks missing BPM and fills it from Deezer or local audio analysis'
    help_text = (
        'Searches for tracks in your library that are missing BPM (tempo) values. '
        'BPM is only written when a track is downloaded, so older library tracks never get one.\n\n'
        'For each track missing BPM, the job tries:\n'
        '1. Deezer API (via the track\'s Deezer ID) — fast, accurate\n'
        '2. Local audio analysis (librosa) — no API calls, works on any file\n\n'
        'Results are reported as findings for your review. The BPM is written to the '
        'database; file tags are updated if the deezer.tags.bpm setting is enabled.\n\n'
        'Settings:\n'
        '- Use Deezer: Look up BPM via the Deezer API first\n'
        '- Use local analysis: Fall back to analyzing the audio file locally'
    )
    icon = 'repair-icon-metadata'  # Reuse existing icon; no bpm-specific CSS class exists
    default_enabled = False
    default_interval_hours = 168  # Weekly — backfill is a slow, one-time-ish task
    default_settings = {
        'use_deezer': True,
        'use_local_analysis': True,
    }
    auto_fix = False
    supports_file_scope = True

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()

        settings = self._get_settings(context)
        use_deezer = settings.get('use_deezer', True)
        use_local = settings.get('use_local_analysis', True)

        if not use_deezer and not use_local:
            logger.info("BPM backfill: both sources disabled, nothing to do")
            return result

        # Probe backends upfront so we can warn if neither is available.
        deezer_client = None
        if use_deezer:
            try:
                deezer_client = get_client_for_source('deezer')
            except Exception as e:
                logger.debug("Could not get Deezer client: %s", e)

        local_available = False
        if use_local:
            try:
                from core.sample.analyze import analyze_track  # noqa: F401
                local_available = True
            except ImportError:
                logger.warning("BPM backfill: librosa not available, local analysis disabled")

        if not deezer_client and not local_available:
            logger.warning(
                "BPM backfill: no usable backend (Deezer client unavailable, "
                "librosa not installed). Nothing to do."
            )
            return result

        # Tracks missing BPM, one subject per track (its primary file when it
        # has several). A batch per run, continuing where the last one stopped:
        # a track no source can answer would otherwise hold its place in the
        # first batch on every run and the rest of the library never come up.
        try:
            missing = _missing_bpm_subjects(context)
        except Exception as e:
            logger.error("Error fetching tracks missing BPM: %s", e, exc_info=True)
            result.errors += 1
            return result
        cursor = _gap_cursor(context, _BPM_CURSOR_KEY)
        tracks = [s for s in missing if int(s['track_id']) > cursor][:_BPM_BATCH]
        if not tracks:
            tracks = missing[:_BPM_BATCH]

        total = len(tracks)
        if context.update_progress:
            context.update_progress(0, total)

        logger.info("Found %d tracks missing BPM", total)

        if context.report_progress:
            context.report_progress(phase=f'Finding BPM for {total} tracks...', total=total)

        for i, row in enumerate(tracks):
            if context.check_stop():
                return result
            if i % 20 == 0 and context.wait_if_paused():
                return result

            track_id = row['track_id']
            title, artist_name, album_title = row.get('title'), row.get('artist_name'), row.get('album_title')
            file_path = row.get('path')
            deezer_id = row.get('deezer_id')

            result.scanned += 1

            if context.report_progress:
                context.report_progress(
                    scanned=i + 1, total=total,
                    phase=f'Finding BPM {i + 1} / {total}',
                    log_line=f'Checking: {title or "Unknown"} — {artist_name or "Unknown"}',
                    log_type='info'
                )

            bpm_value = None
            bpm_source = None

            # 1. Try Deezer API first (same lookup as the download path in
            # core/metadata/source.py:563 — top-level 'bpm' key)
            if deezer_client and deezer_id:
                try:
                    track_data = deezer_client.get_track_details(deezer_id)
                    if track_data:
                        bpm_val = track_data.get('bpm')
                        if bpm_val and float(bpm_val) > 0:
                            bpm_value = round(float(bpm_val), 1)
                            bpm_source = 'deezer'
                except Exception as e:
                    logger.debug("Deezer BPM lookup failed for track %s: %s", track_id, e)

            # 2. Fall back to local analysis
            if bpm_value is None and local_available and file_path:
                try:
                    local_path = file_path if os.path.exists(file_path) else resolve_lib2_path(
                        file_path, config_manager=context.config_manager)
                    if local_path:
                        from core.sample.analyze import analyze_track
                        analysis = analyze_track(local_path)
                        bpm_val = analysis.get('bpm')
                        if bpm_val and float(bpm_val) > 0:
                            bpm_value = round(float(bpm_val), 1)
                            bpm_source = 'local'
                except Exception as e:
                    logger.debug("Local BPM analysis failed for track %s: %s", track_id, e)

            # Create finding for user review
            if bpm_value:
                if context.report_progress:
                    context.report_progress(
                        log_line=f'Found BPM {bpm_value} ({bpm_source}) for {title or "Unknown"}',
                        log_type='success'
                    )
                if context.create_finding:
                    try:
                        details = {
                            'track_id': f'lib2:{track_id}',
                            'title': title,
                            'artist': artist_name,
                            'album': album_title,
                            'bpm': bpm_value,
                            'bpm_source': bpm_source,
                            'found_fields': {'bpm': bpm_value},
                            'album_thumb_url': row.get('album_image') or None,
                            'artist_thumb_url': row.get('artist_image') or None,
                            'artist_id': row.get('artist_id'),
                        }
                        details.update(subject_details(row))
                        inserted = context.create_finding(
                            job_id=self.job_id,
                            finding_type='bpm_backfill',
                            severity='info',
                            entity_type='track',
                            entity_id=f'lib2:{track_id}',
                            file_path=file_path,
                            title=f'Missing BPM: {title or "Unknown"}',
                            description=(
                                f'Track "{title}" by {artist_name or "Unknown"} is missing BPM. '
                                f'Found {bpm_value} BPM via {bpm_source}.'
                            ),
                            details=details,
                        )
                        if inserted:
                            result.findings_created += 1
                        else:
                            result.findings_skipped_dedup += 1
                    except Exception as e:
                        logger.debug("Error creating BPM finding for track %s: %s", track_id, e)
                        result.errors += 1
            else:
                result.skipped += 1

            # Rate limit API calls; local analysis is CPU-bound so no sleep needed
            if bpm_source == 'deezer':
                if context.sleep_or_stop(0.5):
                    return result

            if context.update_progress and (i + 1) % 10 == 0:
                context.update_progress(i + 1, total)

        if context.update_progress:
            context.update_progress(total, total)
        if tracks:
            _gap_cursor(context, _BPM_CURSOR_KEY, int(tracks[-1]['track_id']))

        logger.info("BPM backfill scan: %d tracks checked, %d BPM found, %d skipped",
                    result.scanned, result.findings_created, result.skipped)
        return result

    def _get_settings(self, context: JobContext) -> dict:
        if not context.config_manager:
            return self.default_settings.copy()
        cfg = context.config_manager.get(f'repair.jobs.{self.job_id}.settings', {})
        merged = self.default_settings.copy()
        merged.update(cfg)
        return merged

    def estimate_scope(self, context: JobContext) -> int:
        try:
            return len(_missing_bpm_subjects(context))
        except Exception:
            return 0


_BPM_BATCH = 500
_BPM_CURSOR_KEY = 'repair.bpm_backfill.cursor'


def _missing_bpm_subjects(context: JobContext) -> list:
    """Library-v2 tracks without a BPM, by track id, hand-tagged files left out."""
    by_track: dict = {}
    for subject in drop_hand_tagged(context, scoped_file_subjects(
            context, active_file_subjects(context.db, context.config_manager))):
        if not str(subject.get('title') or '').strip():
            continue
        try:
            if subject.get('bpm') and float(subject['bpm']) > 0:
                continue
        except (TypeError, ValueError):
            pass
        track_id = int(subject['track_id'])
        if track_id in by_track and not subject.get('is_primary'):
            continue
        by_track[track_id] = subject
    return [by_track[k] for k in sorted(by_track)]
