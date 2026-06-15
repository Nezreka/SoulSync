"""Quality Upgrade Scanner Job — flags library tracks below the user's profile.

Scans the whole library (not just Transfer) by resolving each track's DB file
path to a real file on disk, probing its ACTUAL measured audio quality (bit
depth / sample rate / bitrate via mutagen — the SAME `probe_audio_quality` the
download import guard uses), and checking it against the user's v3 ranked
targets with `quality_meets_profile` (strict; fallback ignored — that's a
download-time concession, not a definition of "good enough").

Every track that satisfies none of the targets becomes a finding the user can:
  - 'redownload': add the track to the wishlist and delete the low-quality file
  - 'delete': remove the low-quality file and its DB record
  - 'ignore': dismiss the finding (handled in the UI via the dismiss endpoint)

This unifies the library quality check onto the exact same pipeline as the
download quality gate — no more extension-only tier guessing.
"""

import os

from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair_job.quality_upgrade")


@register_job
class QualityUpgradeScannerJob(RepairJob):
    job_id = 'quality_upgrade_scanner'
    display_name = 'Quality Upgrade Scanner'
    description = 'Flags library tracks below your quality profile'
    help_text = (
        'Scans your music library and checks every track\'s REAL audio quality '
        '(bit depth, sample rate, bitrate — read from the file itself, not just '
        'the extension) against your configured quality profile. This is the same '
        'check the download pipeline runs, so a track flagged here is one the '
        'downloader would also reject.\n\n'
        'Each below-profile track is reported as a finding. You can:\n'
        '• Re-download — add the track to your wishlist and remove the low-quality file\n'
        '• Delete — remove the low-quality file entirely\n'
        '• Ignore — dismiss the finding and keep the file\n\n'
        'The scan only reports — it never deletes or re-downloads on its own. '
        'Profile targets and fallback come straight from Settings → Quality, so '
        'adjusting your profile changes what counts as "below quality" here.'
    )
    icon = 'repair-icon-lossless'
    default_enabled = False
    default_interval_hours = 168
    default_settings = {}
    auto_fix = False  # User chooses fix action per finding

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()

        # Load the user's v3 ranked targets — the SAME definition the download
        # import guard uses. Strict: a track is below-profile when its measured
        # quality satisfies NONE of the targets (fallback is not consulted).
        from core.quality.selection import targets_from_profile, quality_meets_profile
        try:
            profile = context.db.get_quality_profile()
        except Exception as e:
            logger.warning("Could not load quality profile: %s", e)
            return result
        targets, _fallback = targets_from_profile(profile)
        if not targets:
            logger.info("Quality profile has no targets — nothing to check against")
            return result

        logger.info("Quality upgrade scan — profile targets (strict): %s",
                    [t.label for t in targets])

        from core.imports.file_ops import probe_audio_quality

        db_tracks = self._load_db_tracks(context)
        if not db_tracks:
            logger.info("No library tracks with file paths found")
            return result

        track_list = sorted(db_tracks.items(), key=lambda x: str(x[0]))
        total = len(track_list)
        if context.report_progress:
            context.report_progress(phase=f'Scanning {total} library tracks...', total=total)
        if context.update_progress:
            context.update_progress(0, total)

        probe_failed = 0
        _diag_logged = False
        for i, (track_id, info) in enumerate(track_list):
            if context.check_stop():
                return result
            if i % 20 == 0 and context.wait_if_paused():
                return result

            raw_fp = info.get('file_path', '')
            resolved = self._resolve_path(raw_fp, context)
            if not resolved:
                # One-shot diagnostic on the first unresolved track — logs EXACTLY
                # what the resolver tried (base dirs, cwd) so a path/mount mismatch
                # is diagnosable instead of a silent "all skipped".
                if not _diag_logged:
                    _diag_logged = True
                    self._log_resolve_diag(raw_fp, context)
                result.skipped += 1
                probe_failed += 1
                continue

            result.scanned += 1
            fname = os.path.basename(resolved)
            if context.report_progress and i % 25 == 0:
                context.report_progress(
                    scanned=i + 1, total=total,
                    phase=f'Checking {i + 1} / {total}',
                    log_line=f'Checking: {fname}',
                    log_type='info',
                )

            try:
                aq = probe_audio_quality(resolved)
            except Exception as e:
                logger.debug("Probe failed for %s: %s", fname, e)
                aq = None

            if aq is None:
                # File couldn't be read — can't judge quality, leave unflagged.
                probe_failed += 1
                result.skipped += 1
                continue

            # Strict profile check — identical to the download guard.
            if quality_meets_profile(aq, targets):
                if context.update_progress and (i + 1) % 25 == 0:
                    context.update_progress(i + 1, total)
                continue

            # Below profile → create a finding.
            current_label = aq.label()
            target_labels = [t.label for t in targets]
            if context.report_progress:
                context.report_progress(
                    log_line=f'Below quality: {fname} — {current_label}',
                    log_type='error',
                )
            if context.create_finding:
                inserted = context.create_finding(
                    job_id=self.job_id,
                    finding_type='quality_upgrade',
                    severity='info',
                    entity_type='track',
                    entity_id=str(track_id),
                    file_path=resolved,
                    title=f'Below quality: {info.get("title") or fname} ({current_label})',
                    description=(
                        f'"{info.get("title") or fname}" by '
                        f'{info.get("artist") or "Unknown"} is {current_label}, '
                        f'which does not meet your quality profile '
                        f'({", ".join(target_labels[:3])}'
                        f'{"…" if len(target_labels) > 3 else ""}).'
                    ),
                    details={
                        'current_quality': current_label,
                        'current_format': aq.format,
                        'current_bitrate': aq.bitrate,
                        'current_sample_rate': aq.sample_rate,
                        'current_bit_depth': aq.bit_depth,
                        'target_qualities': target_labels,
                        'expected_title': info.get('title', ''),
                        'expected_artist': info.get('artist', ''),
                        'album_title': info.get('album_title', ''),
                        'track_number': info.get('track_number'),
                        'album_thumb_url': info.get('album_thumb_url'),
                        'artist_thumb_url': info.get('artist_thumb_url'),
                    },
                )
                if inserted:
                    result.findings_created += 1
                else:
                    result.findings_skipped_dedup += 1

            if context.update_progress and (i + 1) % 25 == 0:
                context.update_progress(i + 1, total)

        if context.update_progress:
            context.update_progress(total, total)

        if probe_failed:
            logger.warning(
                "Quality upgrade scan: %d/%d tracks could not be read/resolved and "
                "were left unflagged (their quality couldn't be verified).",
                probe_failed, total)
        logger.info("Quality upgrade scan: %d scanned, %d below profile, %d skipped",
                    result.scanned, result.findings_created, result.skipped)
        return result

    def _load_db_tracks(self, context: JobContext) -> dict:
        """Load all library tracks keyed by ID (mirrors AcoustIDScannerJob)."""
        tracks = {}
        conn = None
        try:
            conn = context.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT t.id, t.title,
                       COALESCE(NULLIF(t.track_artist, ''), ar.name) AS artist,
                       t.file_path, t.track_number,
                       al.title AS album_title, al.thumb_url, ar.thumb_url
                FROM tracks t
                LEFT JOIN artists ar ON ar.id = t.artist_id
                LEFT JOIN albums al ON al.id = t.album_id
                WHERE t.file_path IS NOT NULL AND t.file_path != ''
            """)
            for row in cursor.fetchall():
                track_id = row[0]
                if track_id is None:
                    continue
                tracks[str(track_id)] = {
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
        """Resolve a DB file path to an actual file on disk via the shared
        resolver (picks up library.music_paths + Plex locations too)."""
        if not file_path:
            return None
        if os.path.exists(file_path):
            return file_path
        from core.library.path_resolver import resolve_library_file_path
        return resolve_library_file_path(
            file_path,
            transfer_folder=context.transfer_folder,
            config_manager=context.config_manager,
        )

    def _log_resolve_diag(self, file_path, context):
        """Log a detailed diagnostic for the first track whose path can't be
        resolved — the only reliable way to tell apart a CWD problem, a wrong
        transfer mount, or genuinely-missing files in this container."""
        from core.library.path_resolver import resolve_library_file_path_with_diagnostic
        try:
            _, attempt = resolve_library_file_path_with_diagnostic(
                file_path,
                transfer_folder=context.transfer_folder,
                config_manager=context.config_manager,
            )
            tf = context.transfer_folder
            abs_tf = os.path.abspath(tf) if tf else None
            logger.warning(
                "[QualityResolve] unresolved db_path=%r | cwd=%r | transfer_folder=%r "
                "(abspath=%r, isdir=%s) | base_dirs_tried=%r | abs_join_exists=%s",
                file_path, os.getcwd(), tf, abs_tf,
                os.path.isdir(abs_tf) if abs_tf else None,
                attempt.base_dirs_tried,
                os.path.exists(os.path.join(abs_tf, file_path)) if abs_tf else None,
            )
        except Exception as e:
            logger.warning("[QualityResolve] diagnostic failed: %s", e)

    def estimate_scope(self, context: JobContext) -> int:
        conn = None
        try:
            conn = context.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COUNT(*) FROM tracks
                WHERE file_path IS NOT NULL AND file_path != ''
            """)
            return cursor.fetchone()[0]
        except Exception:
            return 0
        finally:
            if conn:
                conn.close()
