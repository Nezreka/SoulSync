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
from core.matching.audio_verification import evaluate, Decision
from core.matching.acoustid_candidates import duration_mismatches_strongly
from core.acoustid_verification import _resolve_expected_artist_aliases

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
        # Skip tracks the user force-imported via the version-mismatch
        # fallback (they are expected to mismatch; default: still scan them
        # but report as informational).
        'skip_force_imported': False,
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
        if checkpoint_id is not None:
            checkpoint_id = str(checkpoint_id)

        # Build ordered list of (track_id, info) sorted by ID for deterministic order
        track_list = sorted(db_tracks.items(), key=lambda x: str(x[0]))

        # Skip past checkpoint if resuming
        if checkpoint_id is not None:
            original_len = len(track_list)
            track_list = [(tid, info) for tid, info in track_list if str(tid) > checkpoint_id]
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

        # Resolve which artist value to compare against, in priority order:
        #   1. DB `track_artist` (per-track, manually curated or scanner-
        #      populated) — trust it when populated. Respects user edits
        #      from the enhanced library view.
        #   2. File's ARTIST tag — ground truth for what's on disk.
        #      Catches legacy compilation tracks where `track_artist`
        #      column is NULL because they were downloaded before that
        #      column existed; the file itself has the correct per-
        #      track artist (Tidal/Spotify/Deezer all write it).
        #   3. Album artist — final fallback for files without proper
        #      ARTIST tags AND no DB track_artist.
        track_artist = (expected.get('track_artist') or '').strip()
        if track_artist:
            expected_artist = track_artist
        else:
            file_artist = None
            try:
                from core.tag_writer import read_file_tags
                file_tags = read_file_tags(fpath)
                file_artist = (file_tags.get('artist') or '').strip() or None
            except Exception as e:
                logger.debug("file-tag artist read failed for %s: %s", fname, e)
            expected_artist = (
                file_artist
                or (expected.get('album_artist') or '').strip()
                or expected['artist']
            )

        # Verification status from the embedded SOULSYNC_VERIFICATION tag.
        # force_imported = user accepted this file as best candidate after the
        # retry budget was exhausted — a mismatch here is EXPECTED. Either skip
        # (job setting) or downgrade the finding to informational below.
        file_verif_status = None
        try:
            from core.tag_writer import read_file_tags as _rft
            file_verif_status = (_rft(fpath) or {}).get('verification_status')
        except Exception:
            pass
        if file_verif_status == 'human_verified':
            # The user explicitly confirmed this file via the review queue —
            # never second-guess a human decision.
            if context.report_progress:
                context.report_progress(
                    log_line=f'Skipped (human-verified): {fname}', log_type='skip')
            return
        if file_verif_status == 'force_imported' and \
                self._get_settings(context).get('skip_force_imported', False):
            if context.report_progress:
                context.report_progress(
                    log_line=f'Skipped (force-imported fallback): {fname}',
                    log_type='skip')
            return

        # Fingerprint-collision guard: when the TOP recording's length is wildly
        # different from the file, the fingerprint hit is a hash collision (the
        # 17-min mashup → 5-min track case), not a real match — skip BEFORE any
        # title/artist/version analysis so it can't surface as a false finding.
        try:
            file_duration_s = (expected.get('duration_ms') or 0) / 1000.0
        except Exception:
            file_duration_s = 0.0
        cand_duration_s = best_recording.get('duration') or best_recording.get('length')
        if file_duration_s and duration_mismatches_strongly(file_duration_s, cand_duration_s):
            if context.report_progress:
                context.report_progress(
                    log_line=(f'Skipped (duration mismatch suggests fingerprint '
                              f'collision): {fname}'),
                    log_type='skip')
            return

        # Decision via the shared verification core — identical logic to import-
        # time verification (alias-aware artist match + cross-script SKIP), so the
        # scan no longer false-flags correct cross-script tracks. Only a FAIL
        # produces a "Wrong download" finding.
        _alias_cache = {}

        def _aliases():
            if 'v' not in _alias_cache:
                try:
                    _alias_cache['v'] = _resolve_expected_artist_aliases(expected_artist)
                except Exception:
                    _alias_cache['v'] = []
            return _alias_cache['v']

        outcome = evaluate(
            expected['title'], expected_artist, fp_result['recordings'],
            fingerprint_score=best_score,
            aliases_provider=_aliases,
        )

        # Refresh the DB column from the file tag (the tag travels with the
        # file and survives DB resets; the tracks row is the UI-facing cache).
        if file_verif_status:
            try:
                conn = context.db._get_connection()
                conn.cursor().execute(
                    "UPDATE tracks SET verification_status = ? WHERE id = ?",
                    (file_verif_status, track_id))
                getattr(conn, 'commit', lambda: None)()
            except Exception as e:
                logger.debug("verification_status refresh failed for %s: %s", track_id, e)

        if outcome.decision != Decision.FAIL:
            if context.report_progress:
                context.report_progress(
                    log_line=f'OK ({outcome.decision.value}): {fname} — {outcome.reason}',
                    log_type='ok',
                )
            return

        title_sim = outcome.title_sim
        artist_sim = outcome.artist_sim
        matched_title = outcome.matched_title or aid_title
        matched_artist = outcome.matched_artist or aid_artist

        # Mismatch (FAIL) — create finding.
        if context.report_progress:
            context.report_progress(
                log_line=f'Mismatch: {fname} — expected "{expected["title"]}", got "{matched_title}"',
                log_type='error'
            )
        if context.create_finding:
            _is_force = file_verif_status == 'force_imported'
            severity = 'info' if _is_force else ('warning' if best_score >= 0.90 else 'info')
            _title = (
                f'Force-imported (fallback): "{expected["title"]}" is actually "{matched_title}"'
                if _is_force else
                f'Wrong download: "{expected["title"]}" is actually "{matched_title}"'
            )
            inserted = context.create_finding(
                job_id=self.job_id,
                finding_type='acoustid_mismatch',
                severity=severity,
                entity_type='track',
                entity_id=str(track_id),
                file_path=fpath,
                title=_title,
                description=(
                    f'Expected "{expected["title"]}" by {expected_artist}, '
                    f'but audio fingerprint matches "{matched_title}" by {matched_artist} '
                    f'(fingerprint: {best_score:.0%}, title match: {title_sim:.0%}, '
                    f'artist match: {artist_sim:.0%})'
                ),
                details={
                    'expected_title': expected['title'],
                    'expected_artist': expected_artist,
                    'acoustid_title': matched_title,
                    'acoustid_artist': matched_artist,
                    'fingerprint_score': round(best_score, 3),
                    'title_similarity': round(title_sim, 3),
                    'artist_similarity': round(artist_sim, 3),
                    'album_thumb_url': expected.get('album_thumb_url'),
                    'artist_thumb_url': expected.get('artist_thumb_url'),
                    'album_title': expected.get('album_title', ''),
                    'track_number': expected.get('track_number'),
                    'force_imported': file_verif_status == 'force_imported',
                }
            )
            if inserted:
                result.findings_created += 1
            else:
                result.findings_skipped_dedup += 1

    def _load_db_tracks(self, context: JobContext) -> dict:
        """Load all tracks from DB keyed by track ID."""
        tracks = {}
        conn = None
        try:
            conn = context.db._get_connection()
            cursor = conn.cursor()
            # Discord report (Skowl): compilation albums like "High Tea
            # Music: Vol 1" have a different artist per track but the
            # `tracks.artist_id` foreign key points at the ALBUM artist
            # (curator / label-name applied to every track). AcoustID
            # returns the actual per-track artist → 12% similarity →
            # Wrong Song flag. Fix: prefer `tracks.track_artist` (the
            # per-track artist, populated by every server-scan + auto-
            # import path when different from album artist) and fall
            # back to the album artist only when the per-track column
            # is NULL or empty (legacy rows / single-artist albums).
            # Load `track_artist` (raw, may be empty) AND `album_artist`
            # separately so `_scan_file` can tell the difference between
            # 'DB has a curated per-track value' and 'DB fell back to
            # album artist'. The COALESCE'd `artist` field is kept as a
            # convenience for the existing `expected['artist']` consumers
            # that want a single resolved value, but the resolution
            # priority that actually drives the comparison is reproduced
            # in `_scan_file`: track_artist → file tag → album_artist.
            cursor.execute("""
                SELECT t.id, t.title,
                       COALESCE(NULLIF(t.track_artist, ''), ar.name) AS artist,
                       t.file_path, t.track_number,
                       al.title AS album_title, al.thumb_url, ar.thumb_url,
                       NULLIF(t.track_artist, '') AS track_artist,
                       ar.name AS album_artist,
                       t.duration
                FROM tracks t
                LEFT JOIN artists ar ON ar.id = t.artist_id
                LEFT JOIN albums al ON al.id = t.album_id
                WHERE t.file_path IS NOT NULL AND t.file_path != ''
                  AND t.title IS NOT NULL AND t.title != ''
            """)
            for row in cursor.fetchall():
                track_id = row[0]
                if track_id is None:
                    logger.warning(
                        "Skipping track row with null ID while loading AcoustID scan candidates: %s",
                        row[3] or "<unknown file>",
                    )
                    continue
                track_id = str(track_id)
                tracks[track_id] = {
                    'title': row[1] or '',
                    'artist': row[2] or '',
                    'file_path': row[3] or '',
                    'track_number': row[4],
                    'album_title': row[5] or '',
                    'album_thumb_url': row[6] or None,
                    'artist_thumb_url': row[7] or None,
                    'track_artist': row[8] or '',  # raw (may be empty)
                    'album_artist': row[9] or '',
                    # Duration in MS (DB stores ms). Used by the
                    # duration-mismatch guard to spot fingerprint
                    # collisions where the matched recording is a
                    # totally different length.
                    'duration_ms': row[10] or 0,
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
        # Use the shared library-path resolver — picks up
        # library.music_paths and Plex library locations too.
        from core.library.path_resolver import resolve_library_file_path
        return resolve_library_file_path(
            file_path,
            transfer_folder=context.transfer_folder,
            config_manager=context.config_manager,
        )

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
