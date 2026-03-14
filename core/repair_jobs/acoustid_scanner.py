"""AcoustID Background Scanner Job — fingerprints tracks to detect wrong downloads."""

import os
import re
import time
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
    description = 'Fingerprints tracks to detect wrong downloads'
    icon = 'repair-icon-acoustid'
    default_enabled = False
    default_interval_hours = 168
    default_settings = {
        'fingerprint_threshold': 0.80,
        'title_similarity': 0.70,
        'artist_similarity': 0.60,
        'batch_size': 50,
    }
    auto_fix = False

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()

        settings = self._get_settings(context)
        fp_threshold = settings.get('fingerprint_threshold', 0.80)
        title_threshold = settings.get('title_similarity', 0.70)
        artist_threshold = settings.get('artist_similarity', 0.60)
        batch_size = settings.get('batch_size', 50)

        # Get AcoustID client
        acoustid_client = context.acoustid_client
        if not acoustid_client:
            try:
                from core.acoustid_client import AcoustIDClient
                acoustid_client = AcoustIDClient()
            except Exception as e:
                logger.warning("AcoustID client not available: %s", e)
                return result

        transfer = context.transfer_folder
        if not os.path.isdir(transfer):
            logger.warning("Transfer folder does not exist: %s", transfer)
            return result

        # Read checkpoint (last processed file path) to resume from
        checkpoint = None
        if context.config_manager:
            checkpoint = context.config_manager.get(
                f'repair.jobs.{self.job_id}.checkpoint', None
            )

        # Collect all audio files
        audio_files = []
        for root, _dirs, files in os.walk(transfer):
            if context.check_stop():
                return result
            for fname in sorted(files):
                ext = os.path.splitext(fname)[1].lower()
                if ext in AUDIO_EXTENSIONS:
                    audio_files.append(os.path.join(root, fname))

        # Sort for deterministic order (important for checkpoint)
        audio_files.sort()

        # Skip past checkpoint if resuming
        if checkpoint:
            try:
                idx = audio_files.index(checkpoint)
                audio_files = audio_files[idx + 1:]
                logger.info("Resuming AcoustID scan from checkpoint (%d files remaining)", len(audio_files))
            except ValueError:
                logger.debug("Checkpoint file not found, starting from beginning")

        total = len(audio_files)
        if context.update_progress:
            context.update_progress(0, total)

        # Build a lookup of known tracks from DB for comparison
        db_tracks = self._load_db_tracks(context)

        batch_count = 0
        for i, fpath in enumerate(audio_files):
            if context.check_stop():
                # Save checkpoint before stopping
                self._save_checkpoint(context, fpath)
                return result
            if i % 10 == 0 and context.wait_if_paused():
                self._save_checkpoint(context, fpath)
                return result

            result.scanned += 1
            batch_count += 1

            try:
                self._scan_file(
                    fpath, acoustid_client, db_tracks, context, result,
                    fp_threshold, title_threshold, artist_threshold
                )
            except Exception as e:
                logger.debug("Error scanning %s: %s", os.path.basename(fpath), e)
                result.errors += 1

            # Rate limit: pause between batches
            if batch_count >= batch_size:
                batch_count = 0
                self._save_checkpoint(context, fpath)
                time.sleep(2)

            if context.update_progress and (i + 1) % 10 == 0:
                context.update_progress(i + 1, total)

        # Clear checkpoint on completion
        self._save_checkpoint(context, None)

        if context.update_progress:
            context.update_progress(total, total)

        logger.info("AcoustID scan: %d files scanned, %d mismatches found, %d errors",
                     result.scanned, result.findings_created, result.errors)
        return result

    def _scan_file(self, fpath, acoustid_client, db_tracks, context, result,
                   fp_threshold, title_threshold, artist_threshold):
        """Fingerprint a single file and check for mismatches."""
        fname = os.path.basename(fpath)

        # Get expected title/artist from DB or filename
        expected = db_tracks.get(os.path.normpath(fpath))
        if not expected:
            # Try to extract from filename: "01 - Artist - Title.flac" or "01 Title.flac"
            base = os.path.splitext(fname)[0]
            # Strip leading track number
            base = re.sub(r'^\d{1,3}[\s.\-_]*', '', base)
            expected = {'title': base, 'artist': '', 'track_id': None}

        # Fingerprint the file
        try:
            fp_result = acoustid_client.fingerprint_and_lookup(fpath)
        except Exception as e:
            logger.debug("Fingerprint failed for %s: %s", fname, e)
            return

        if not fp_result or not fp_result.get('recordings'):
            # No match — could be a very rare/new track
            if context.create_finding:
                context.create_finding(
                    job_id=self.job_id,
                    finding_type='acoustid_no_match',
                    severity='info',
                    entity_type='track',
                    entity_id=str(expected.get('track_id', '')),
                    file_path=fpath,
                    title=f'No AcoustID match: {fname}',
                    description='File could not be identified by AcoustID fingerprint',
                    details={
                        'expected_title': expected['title'],
                        'expected_artist': expected['artist'],
                    }
                )
                result.findings_created += 1
            return

        # Check best recording match
        best_score = fp_result.get('best_score', 0)
        if best_score < fp_threshold:
            return  # Low confidence fingerprint, skip

        # Compare best AcoustID result against expected
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

        # If both title AND artist match well, no issue
        if title_sim >= title_threshold and artist_sim >= artist_threshold:
            return

        # Mismatch detected
        if context.create_finding:
            severity = 'warning' if best_score >= 0.90 else 'info'
            context.create_finding(
                job_id=self.job_id,
                finding_type='acoustid_mismatch',
                severity=severity,
                entity_type='track',
                entity_id=str(expected.get('track_id', '')),
                file_path=fpath,
                title=f'Possible wrong download: {fname}',
                description=(
                    f'Expected "{expected["title"]}" by {expected["artist"]}, '
                    f'but fingerprint matches "{aid_title}" by {aid_artist} '
                    f'(fp: {best_score:.0%}, title: {title_sim:.0%}, artist: {artist_sim:.0%})'
                ),
                details={
                    'expected_title': expected['title'],
                    'expected_artist': expected['artist'],
                    'acoustid_title': aid_title,
                    'acoustid_artist': aid_artist,
                    'fingerprint_score': round(best_score, 3),
                    'title_similarity': round(title_sim, 3),
                    'artist_similarity': round(artist_sim, 3),
                }
            )
            result.findings_created += 1

    def _load_db_tracks(self, context: JobContext) -> dict:
        """Load all tracks from DB keyed by normalized file_path."""
        tracks = {}
        conn = None
        try:
            conn = context.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, title, artist, file_path
                FROM tracks
                WHERE file_path IS NOT NULL AND file_path != ''
                  AND title IS NOT NULL AND title != ''
            """)
            for row in cursor.fetchall():
                track_id, title, artist, file_path = row
                tracks[os.path.normpath(file_path)] = {
                    'track_id': track_id,
                    'title': title or '',
                    'artist': artist or '',
                }
        except Exception as e:
            logger.error("Error loading tracks from DB: %s", e)
        finally:
            if conn:
                conn.close()
        return tracks

    def _save_checkpoint(self, context: JobContext, fpath):
        """Save or clear the scan checkpoint."""
        if context.config_manager:
            context.config_manager.set(
                f'repair.jobs.{self.job_id}.checkpoint',
                fpath
            )

    def _get_settings(self, context: JobContext) -> dict:
        if not context.config_manager:
            return self.default_settings.copy()
        cfg = context.config_manager.get(f'repair.jobs.{self.job_id}.settings', {})
        merged = self.default_settings.copy()
        merged.update(cfg)
        return merged

    def estimate_scope(self, context: JobContext) -> int:
        transfer = context.transfer_folder
        if not os.path.isdir(transfer):
            return 0
        count = 0
        for _root, _dirs, files in os.walk(transfer):
            for fname in files:
                if os.path.splitext(fname)[1].lower() in AUDIO_EXTENSIONS:
                    count += 1
        return count


def _normalize(text: str) -> str:
    t = text.lower()
    t = re.sub(r'\(.*?\)', '', t)
    t = re.sub(r'\[.*?\]', '', t)
    t = re.sub(r'[^a-z0-9 ]', '', t)
    return t.strip()
