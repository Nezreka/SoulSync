"""AcoustID Scanner Job — fingerprints library tracks to detect wrong downloads.

Scans the entire library (not just Transfer) by resolving DB file paths to
actual files on disk. Creates actionable findings that can be fixed:
  - 'retag': Update DB metadata to match what the file actually is
  - 'redownload': Add the expected track to wishlist and delete the wrong file
  - 'delete': Remove the wrong file and its DB record
"""

import os
import re
from difflib import SequenceMatcher
from typing import Optional

from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair_job.acoustid")

AUDIO_EXTENSIONS = {'.mp3', '.flac', '.ogg', '.opus', '.m4a', '.aac', '.wav', '.wma', '.aiff', '.aif'}


@register_job
class AcoustIDScannerJob(RepairJob):
    job_id = 'acoustid_scanner'
    display_name = 'AcoustID Scanner'
    description = 'Fingerprints library tracks to detect wrong downloads'
    help_text = (
        'Scans your music library by fingerprinting audio files and comparing '
        'them against the AcoustID database. Detects cases where the wrong song '
        'was downloaded — even if the filename and tags look correct.\n\n'
        'When a mismatch is found, you can:\n'
        '• Retag — update the DB record to match the actual audio content\n'
        '• Redownload — add the correct track to your wishlist and remove the wrong file\n'
        '• Delete — remove the wrong file entirely\n\n'
        'The job processes tracks in batches with checkpointing so it resumes '
        'where it left off across runs. Requires an AcoustID API key (Settings).\n\n'
        'Settings:\n'
        '- Fingerprint Threshold: Minimum AcoustID match confidence (0.0–1.0)\n'
        '- Title Similarity: How closely the identified title must match\n'
        '- Artist Similarity: How closely the identified artist must match\n'
        '- Batch Size: Tracks per scan run (checkpoint saved between batches)'
    )
    icon = 'repair-icon-acoustid'
    default_enabled = True
    default_interval_hours = 24
    default_settings = {
        'fingerprint_threshold': 0.80,
        'title_similarity': 0.70,
        'artist_similarity': 0.60,
        'batch_size': 200,
    }
    auto_fix = False  # User chooses fix action per finding

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()

        settings = self._get_settings(context)
        fp_threshold = settings.get('fingerprint_threshold', 0.80)
        title_threshold = settings.get('title_similarity', 0.70)
        artist_threshold = settings.get('artist_similarity', 0.60)
        batch_size = settings.get('batch_size', 200)

        # Get AcoustID client
        acoustid_client = context.acoustid_client
        if not acoustid_client:
            try:
                from core.acoustid_client import AcoustIDClient
                acoustid_client = AcoustIDClient()
            except Exception as e:
                logger.warning("AcoustID client not available: %s", e)
                return result

        # Load all library tracks from DB with their file paths
        db_tracks = self._load_db_tracks(context)
        if not db_tracks:
            logger.info("No library tracks with file paths found")
            return result

        # Read checkpoint (last processed track ID) to resume from
        checkpoint_id = None
        if context.config_manager:
            checkpoint_id = context.config_manager.get(
                f'repair.jobs.{self.job_id}.checkpoint_id', None
            )

        # Build ordered list of (track_id, info) sorted by ID for deterministic order
        track_list = sorted(db_tracks.items(), key=lambda x: x[0])

        # Skip past checkpoint if resuming
        if checkpoint_id is not None:
            original_len = len(track_list)
            track_list = [(tid, info) for tid, info in track_list if tid > checkpoint_id]
            if len(track_list) < original_len:
                logger.info("Resuming AcoustID scan from checkpoint ID %s (%d tracks remaining)",
                            checkpoint_id, len(track_list))

        total = len(track_list)
        if context.report_progress:
            context.report_progress(phase=f'Scanning {total} library tracks...', total=total)
        if context.update_progress:
            context.update_progress(0, total)

        batch_count = 0
        for i, (track_id, track_info) in enumerate(track_list):
            if context.check_stop():
                self._save_checkpoint_id(context, track_id)
                return result
            if i % 10 == 0 and context.wait_if_paused():
                self._save_checkpoint_id(context, track_id)
                return result

            # Resolve the DB path to an actual file on disk
            file_path = track_info.get('file_path', '')
            resolved = self._resolve_path(file_path, context)
            if not resolved:
                result.skipped += 1
                continue

            result.scanned += 1
            batch_count += 1

            fname = os.path.basename(resolved)
            if context.report_progress:
                context.report_progress(
                    scanned=i + 1, total=total,
                    phase=f'Fingerprinting {i + 1} / {total}',
                    log_line=f'Scanning: {fname}',
                    log_type='info'
                )

            try:
                self._scan_file(
                    resolved, track_id, track_info, acoustid_client, context, result,
                    fp_threshold, title_threshold, artist_threshold
                )
            except Exception as e:
                logger.debug("Error scanning %s: %s", fname, e)
                result.errors += 1

            # Rate limit: pause between batches to avoid hammering AcoustID API
            if batch_count >= batch_size:
                batch_count = 0
                self._save_checkpoint_id(context, track_id)
                if context.sleep_or_stop(2):
                    return result

            if context.update_progress and (i + 1) % 10 == 0:
                context.update_progress(i + 1, total)

        # Clear checkpoint on full completion
        self._save_checkpoint_id(context, None)

        if context.update_progress:
            context.update_progress(total, total)

        logger.info("AcoustID scan: %d scanned, %d skipped, %d mismatches, %d errors",
                     result.scanned, result.skipped, result.findings_created, result.errors)
        return result

    def _scan_file(self, fpath, track_id, expected, acoustid_client, context, result,
                   fp_threshold, title_threshold, artist_threshold):
        """Fingerprint a single file and check for mismatches."""
        fname = os.path.basename(fpath)

        # Fingerprint the file
        try:
            fp_result = acoustid_client.fingerprint_and_lookup(fpath)
        except Exception as e:
            logger.debug("Fingerprint failed for %s: %s", fname, e)
            result.errors += 1
            if context.report_progress:
                context.report_progress(log_line=f'Error: {fname} — {e}', log_type='error')
            return

        if not fp_result or not fp_result.get('recordings'):
            if context.report_progress:
                context.report_progress(log_line=f'No match: {fname}', log_type='skip')
            return

        best_score = fp_result.get('best_score', 0)
        if best_score < fp_threshold:
            return

        best_recording = fp_result['recordings'][0]
        aid_title = best_recording.get('title', '')
        aid_artist = best_recording.get('artist', '')

        if not aid_title:
            return

        # Normalize and compare
        norm_expected_title = _normalize(expected['title'])
        norm_aid_title = _normalize(aid_title)
        norm_expected_artist = _normalize(expected['artist'])
        norm_aid_artist = _normalize(aid_artist)

        title_sim = SequenceMatcher(None, norm_expected_title, norm_aid_title).ratio()
        artist_sim = SequenceMatcher(None, norm_expected_artist, norm_aid_artist).ratio() if norm_expected_artist else 1.0

        if title_sim >= title_threshold and artist_sim >= artist_threshold:
            return

        # Mismatch detected
        if context.report_progress:
            context.report_progress(
                log_line=f'Mismatch: {fname} — expected "{expected["title"]}", got "{aid_title}"',
                log_type='error'
            )
        if context.create_finding:
            severity = 'warning' if best_score >= 0.90 else 'info'
            context.create_finding(
                job_id=self.job_id,
                finding_type='acoustid_mismatch',
                severity=severity,
                entity_type='track',
                entity_id=str(track_id),
                file_path=fpath,
                title=f'Wrong download: "{expected["title"]}" is actually "{aid_title}"',
                description=(
                    f'Expected "{expected["title"]}" by {expected["artist"]}, '
                    f'but audio fingerprint matches "{aid_title}" by {aid_artist} '
                    f'(fingerprint: {best_score:.0%}, title match: {title_sim:.0%}, '
                    f'artist match: {artist_sim:.0%})'
                ),
                details={
                    'expected_title': expected['title'],
                    'expected_artist': expected['artist'],
                    'acoustid_title': aid_title,
                    'acoustid_artist': aid_artist,
                    'fingerprint_score': round(best_score, 3),
                    'title_similarity': round(title_sim, 3),
                    'artist_similarity': round(artist_sim, 3),
                    'album_thumb_url': expected.get('album_thumb_url'),
                    'artist_thumb_url': expected.get('artist_thumb_url'),
                    'album_title': expected.get('album_title', ''),
                    'track_number': expected.get('track_number'),
                }
            )
            result.findings_created += 1

    def _load_db_tracks(self, context: JobContext) -> dict:
        """Load all tracks from DB keyed by track ID."""
        tracks = {}
        conn = None
        try:
            conn = context.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT t.id, t.title, ar.name, t.file_path, t.track_number,
                       al.title AS album_title, al.thumb_url, ar.thumb_url
                FROM tracks t
                LEFT JOIN artists ar ON ar.id = t.artist_id
                LEFT JOIN albums al ON al.id = t.album_id
                WHERE t.file_path IS NOT NULL AND t.file_path != ''
                  AND t.title IS NOT NULL AND t.title != ''
            """)
            for row in cursor.fetchall():
                track_id = row[0]
                tracks[track_id] = {
                    'title': row[1] or '',
                    'artist': row[2] or '',
                    'file_path': row[3] or '',
                    'track_number': row[4],
                    'album_title': row[5] or '',
                    'album_thumb_url': row[6] or None,
                    'artist_thumb_url': row[7] or None,
                }
        except Exception as e:
            logger.error("Error loading tracks from DB: %s", e)
        finally:
            if conn:
                conn.close()
        return tracks

    def _resolve_path(self, file_path, context):
        """Resolve a DB file path to an actual file on disk."""
        if not file_path:
            return None
        if os.path.exists(file_path):
            return file_path
        # Try the repair_worker's resolver
        from core.repair_worker import _resolve_file_path
        return _resolve_file_path(file_path, context.transfer_folder)

    def _save_checkpoint_id(self, context: JobContext, track_id):
        """Save or clear the scan checkpoint by track ID."""
        if context.config_manager:
            context.config_manager.set(
                f'repair.jobs.{self.job_id}.checkpoint_id', track_id
            )

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
            cursor.execute("""
                SELECT COUNT(*) FROM tracks
                WHERE file_path IS NOT NULL AND file_path != ''
                  AND title IS NOT NULL AND title != ''
            """)
            return cursor.fetchone()[0]
        except Exception:
            return 0
        finally:
            if conn:
                conn.close()


def _normalize(text: str) -> str:
    t = text.lower()
    t = re.sub(r'\(.*?\)', '', t)
    t = re.sub(r'\[.*?\]', '', t)
    t = re.sub(r'[^a-z0-9 ]', '', t)
    return t.strip()
