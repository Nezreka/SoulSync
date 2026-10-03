"""BPM Backfill Job — finds tracks missing BPM and fills it from Deezer or local analysis.

Issue #1476: tracks.bpm is only written at download time. Tracks already in
the library never get one. This job backfills BPM for existing tracks:

1. Deezer API first (via deezer_id) — fast, no local CPU cost
2. Local librosa analysis fallback — no API calls, works offline

Findings-first like metadata_gap_filler: results are reported for user review,
not auto-written. The existing _fix_metadata_gap handler applies the bpm field.
"""

import os

from core.metadata_service import get_client_for_source
from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob, not_locked_sql
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

        # Find tracks missing BPM
        tracks = []
        conn = None
        try:
            conn = context.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(tracks)")
            track_columns = {column[1] for column in cursor.fetchall()}

            select_cols = [
                "t.id",
                "t.title",
                "ar.name",
                "al.title",
                "t.file_path",
                "al.thumb_url",
                "ar.thumb_url",
                "ar.id",
            ]
            # Deezer ID for API lookup (column is deezer_id, not deezer_track_id)
            if "deezer_id" in track_columns:
                select_cols.append("t.deezer_id AS deezer_id")
                deezer_idx = len(select_cols) - 1
            else:
                deezer_idx = None

            locked_filter = not_locked_sql(cursor, 'tracks', 't') + not_locked_sql(cursor, 'albums', 'al')
            # Order Deezer-matched tracks first (faster lookups)
            order_by = (
                "ORDER BY CASE WHEN t.deezer_id IS NOT NULL AND t.deezer_id != '' THEN 0 ELSE 1 END"
                if "deezer_id" in track_columns else ""
            )
            cursor.execute(f"""
                SELECT {', '.join(select_cols)}
                FROM tracks t
                LEFT JOIN artists ar ON ar.id = t.artist_id
                LEFT JOIN albums al ON al.id = t.album_id
                WHERE t.title IS NOT NULL AND t.title != ''
                  AND (t.bpm IS NULL OR t.bpm = 0){locked_filter}
                {order_by}
                LIMIT 500
            """)
            tracks = cursor.fetchall()
        except Exception as e:
            logger.error("Error fetching tracks missing BPM: %s", e, exc_info=True)
            result.errors += 1
            return result
        finally:
            if conn:
                conn.close()

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

            track_id, title, artist_name, album_title = row[0], row[1], row[2], row[3]
            file_path = row[4]
            album_thumb, artist_thumb, artist_id = row[5], row[6], row[7]
            deezer_id = row[deezer_idx] if deezer_idx is not None else None

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
                    if os.path.exists(file_path):
                        from core.sample.analyze import analyze_track
                        analysis = analyze_track(file_path)
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
                        inserted = context.create_finding(
                            job_id=self.job_id,
                            finding_type='bpm_backfill',
                            severity='info',
                            entity_type='track',
                            entity_id=str(track_id),
                            file_path=file_path,
                            title=f'Missing BPM: {title or "Unknown"}',
                            description=(
                                f'Track "{title}" by {artist_name or "Unknown"} is missing BPM. '
                                f'Found {bpm_value} BPM via {bpm_source}.'
                            ),
                            details={
                                'track_id': track_id,
                                'title': title,
                                'artist': artist_name,
                                'album': album_title,
                                'bpm': bpm_value,
                                'bpm_source': bpm_source,
                                'found_fields': {'bpm': bpm_value},
                                'album_thumb_url': album_thumb or None,
                                'artist_thumb_url': artist_thumb or None,
                                'artist_id': artist_id,
                            }
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
        conn = None
        try:
            conn = context.db._get_connection()
            cursor = conn.cursor()
            # Note: does not apply not_locked_sql filters; may overcount slightly
            # vs. what scan() will process. Acceptable for an estimate.
            cursor.execute("""
                SELECT COUNT(*) FROM tracks
                WHERE title IS NOT NULL AND title != ''
                  AND (bpm IS NULL OR bpm = 0)
            """)
            row = cursor.fetchone()
            return min(row[0], 500) if row else 0
        except Exception:
            return 0
        finally:
            if conn:
                conn.close()
