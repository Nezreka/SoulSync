"""Lossy Converter Job — finds lossless files that don't have a lossy copy.

Scans the library for lossless files without a corresponding lossy copy alongside
them, and creates a finding for each. The fix action converts the file using
ffmpeg with the user's configured codec/bitrate settings.
"""

import os

from core.imports.file_ops import m4a_codec
from core.library.path_resolver import resolve_library_file_path
from core.quality.lossless import (
    LOSSLESS_CANDIDATE_EXTENSIONS,
    is_lossless_audio_path,
    lossy_output_would_overwrite_source,
)
from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob, scoped_file_subjects
from core.quality.selection import load_profile_by_id
from utils.logging_config import get_logger

logger = get_logger("repair_job.lossy_converter")

CODEC_MAP = {
    'mp3':  '.mp3',
    'opus': '.opus',
    'aac':  '.m4a',
}


def _profile_lossy_settings(context: JobContext, profile_id=None, track_id=None) -> dict:
    """Resolve the live lossy policy for one track, with legacy fallback."""
    profile = None
    try:
        if track_id is not None:
            from core.library2.quality_eval import effective_track_profile

            conn = context.db._get_connection()
            try:
                profile = effective_track_profile(conn, int(track_id))
            finally:
                conn.close()
        elif profile_id:
            profile = load_profile_by_id(profile_id)
        elif context.db and hasattr(context.db, 'get_quality_profile'):
            profile = context.db.get_quality_profile()
    except Exception as exc:  # noqa: BLE001 - legacy DB/config remains usable
        logger.debug("Could not resolve quality profile %r: %s", profile_id, exc)
    if isinstance(profile, dict) and 'lossy_copy_enabled' in profile:
        return {
            'profile_id': profile.get('id'),
            # A global/default result is inherited rather than assigned. Keep
            # that distinction in findings so legacy apply paths also resolve
            # the then-current default instead of pinning today's row id.
            'assigned_profile_id': (
                profile.get('id') if profile.get('explicit', True) else None
            ),
            'profile_name': profile.get('name') or profile.get('preset') or 'default',
            'enabled': bool(profile.get('lossy_copy_enabled')),
            'codec': str(profile.get('lossy_copy_codec') or 'mp3').lower(),
            'bitrate': str(profile.get('lossy_copy_bitrate') or '320'),
            'delete_original': bool(profile.get('lossy_copy_delete_original')),
        }
    cfg = context.config_manager
    return {
        'profile_id': profile_id,
        'assigned_profile_id': profile_id,
        'profile_name': 'legacy settings',
        'enabled': bool(cfg and cfg.get('lossy_copy.enabled', False)),
        'codec': str(cfg.get('lossy_copy.codec', 'mp3') if cfg else 'mp3').lower(),
        'bitrate': str(cfg.get('lossy_copy.bitrate', '320') if cfg else '320'),
        'delete_original': bool(cfg and cfg.get('lossy_copy.delete_original', False)),
    }


def _lossless_ext_where(col: str) -> str:
    """SQL pre-filter matching files whose extension *might* be lossless. The
    final decision (including ALAC-in-.m4a, which needs a codec probe) is made
    per-file by is_lossless_audio_path. Extensions are trusted constants from the
    quality model, never user input — safe to interpolate."""
    return '(' + ' OR '.join(
        f"LOWER({col}) LIKE '%{ext}'" for ext in sorted(LOSSLESS_CANDIDATE_EXTENSIONS)
    ) + ')'


def _resolve_file_path(file_path, transfer_folder, download_folder=None, config_manager=None):
    """Backwards-compat wrapper. Use ``resolve_library_file_path`` directly."""
    return resolve_library_file_path(
        file_path,
        transfer_folder=transfer_folder,
        download_folder=download_folder,
        config_manager=config_manager,
    )


@register_job
class LossyConverterJob(RepairJob):
    job_id = 'lossy_converter'
    display_name = 'Lossy Converter'
    description = 'Finds lossless files without a lossy copy'
    help_text = (
        'Scans your library for lossless files (FLAC/ALAC/WAV/AIFF/DSD) that don\'t already have a lossy copy '
        '(MP3, Opus, or AAC) alongside them.\n\n'
        'Uses each track\'s assigned Quality Profile, including its codec, bitrate, '
        'and whether the lossless source should be retained. Enable Lossy Copy on '
        'the relevant profile first.\n\n'
        'Each finding can be fixed individually or in bulk — the fix action converts '
        'the lossless file using ffmpeg at your configured bitrate.\n\n'
        'Requires ffmpeg to be installed.'
    )
    icon = 'repair-icon-lossy'
    default_enabled = False
    default_interval_hours = 0  # Manual only
    default_settings = {}
    auto_fix = False
    supports_file_scope = True

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()

        if not context.config_manager:
            logger.warning("Config manager not available")
            return result

        # Enumerate the authoritative native file index.
        tracks = []
        native_subjects = {}
        try:
            from core.library2.maintenance_subjects import active_file_subjects

            for subject in scoped_file_subjects(context, active_file_subjects(
                context.db, context.config_manager,
            )):
                if not subject.get("is_primary"):
                    continue
                file_path = str(subject["path"])
                ext = os.path.splitext(file_path)[1].lower()
                if ext not in LOSSLESS_CANDIDATE_EXTENSIONS:
                    continue
                native_subjects[file_path] = subject
                tracks.append((
                    f"lib2:{subject['track_id']}", subject["title"],
                    subject["artist_name"], subject["album_title"], file_path,
                    subject.get("album_image"), subject.get("artist_image"),
                    subject.get("artist_id"),
                ))
        except Exception as e:
            logger.warning("V2 subject enumeration failed: %s", e)
            result.errors += 1
        from core.repair_jobs.filesystem_subjects import filesystem_audio_files

        for file_path in filesystem_audio_files(
            context, extensions=LOSSLESS_CANDIDATE_EXTENSIONS,
        ):
            tracks.append((
                None, os.path.splitext(os.path.basename(file_path))[0],
                None, os.path.basename(os.path.dirname(file_path)), file_path,
                None, None, None,
            ))

        total = len(tracks)
        if context.update_progress:
            context.update_progress(0, total)
        if context.report_progress:
            context.report_progress(
                phase=f'Scanning {total} lossless files for profile-defined lossy copies...',
                total=total
            )

        download_folder = None
        if context.config_manager:
            download_folder = context.config_manager.get('soulseek.download_path', '')

        # Files silently dropped from the scan. Surfaced at the end so a library
        # whose DB paths don't resolve on disk reads as "N skipped" instead of
        # looking like the job just missed lossless files (#995).
        skipped_missing = 0        # DB path could not be located on disk
        skipped_not_lossless = 0   # extension matched but the codec probe says lossy

        for i, row in enumerate(tracks):
            if context.check_stop():
                return result
            if i % 200 == 0 and context.wait_if_paused():
                return result

            (
                track_id, title, artist_name, album_title, file_path,
                album_thumb, artist_thumb, artist_id,
            ) = row
            result.scanned += 1

            subject = native_subjects.get(str(file_path))
            policy = _profile_lossy_settings(
                context,
                track_id=subject.get("track_id") if subject else None,
            )
            if not policy['enabled']:
                result.skipped += 1
                continue
            codec = policy['codec']
            bitrate = policy['bitrate']
            out_ext = CODEC_MAP.get(codec, '.mp3')
            quality_label = f'{codec.upper()}-{bitrate}'

            if context.report_progress and i % 50 == 0:
                context.report_progress(
                    scanned=i + 1, total=total,
                    phase=f'Scanning {i + 1} / {total}',
                    log_line=f'Checking: {title or "Unknown"} — {artist_name or "Unknown"}',
                    log_type='info'
                )

            # Resolve path
            if subject:
                if os.path.isfile(file_path):
                    resolved = file_path
                else:
                    from core.library2.paths import resolve_lib2_path

                    resolved = resolve_lib2_path(
                        file_path, config_manager=context.config_manager)
            else:
                resolved = _resolve_file_path(file_path, context.transfer_folder, download_folder,
                                              config_manager=context.config_manager)
            if not resolved or not os.path.exists(resolved):
                skipped_missing += 1
                continue

            # Confirm it's actually lossless — the SQL pre-filter lets .m4a through,
            # which is ALAC (lossless) OR AAC (lossy); only a codec probe decides.
            if not is_lossless_audio_path(resolved, probe_codec=m4a_codec):
                skipped_not_lossless += 1
                continue

            # Check if lossy copy already exists
            out_path = os.path.splitext(resolved)[0] + out_ext
            # Never offer to convert a file onto itself (e.g. .m4a ALAC + AAC target
            # lands on the same path) — that conversion would destroy the original.
            if lossy_output_would_overwrite_source(resolved, out_path):
                continue
            if os.path.exists(out_path):
                continue

            # Create finding
            if context.report_progress:
                context.report_progress(
                    log_line=f'Missing {quality_label}: {title or "Unknown"} — {artist_name or "Unknown"}',
                    log_type='skip'
                )

            if context.create_finding:
                try:
                    file_size = os.path.getsize(resolved)
                    finding_details = {
                        'track_id': track_id,
                        'title': title,
                        'artist': artist_name,
                        'album': album_title,
                        'file_path': file_path,
                        'resolved_path': resolved,
                        'codec': codec,
                        'bitrate': bitrate,
                        'quality_label': quality_label,
                        'file_size': file_size,
                        'album_thumb_url': album_thumb or None,
                        'artist_thumb_url': artist_thumb or None,
                        'artist_id': artist_id,
                        'quality_profile_id': policy.get('assigned_profile_id'),
                        'quality_profile_name': policy.get('profile_name'),
                        'delete_original': policy.get('delete_original', False),
                    }
                    if subject:
                        from core.library2.maintenance_subjects import subject_details

                        finding_details.update(subject_details(subject))
                    inserted = context.create_finding(
                        job_id=self.job_id,
                        finding_type='missing_lossy_copy',
                        severity='info',
                        entity_type='track' if subject else 'file',
                        entity_id=str(track_id) if track_id is not None else None,
                        file_path=file_path,
                        title=f'No {quality_label} copy: {title or "Unknown"}',
                        description=(
                            f'Lossless file "{title}" by {artist_name or "Unknown"} does not have '
                            f'a {quality_label} copy alongside it'
                        ),
                        details=finding_details
                    )
                    if inserted:
                        result.findings_created += 1
                    else:
                        result.findings_skipped_dedup += 1
                except Exception as e:
                    logger.debug("Error creating finding for track %s: %s", track_id, e)
                    result.errors += 1

            if context.update_progress and (i + 1) % 100 == 0:
                context.update_progress(i + 1, total)

        if context.update_progress:
            context.update_progress(total, total)

        if context.report_progress:
            summary = f'Found {result.findings_created} lossless files without their profile-defined lossy copy'
            if skipped_missing:
                summary += f'; {skipped_missing} tracks could not be located on disk (skipped)'
            context.report_progress(
                scanned=total, total=total,
                phase='Complete',
                log_line=summary,
                log_type='success' if result.findings_created == 0 else 'info'
            )

        logger.info("Lossy converter scan: %d scanned, %d missing lossy copies, "
                     "%d unresolved/missing on disk, %d probed-not-lossless",
                     result.scanned, result.findings_created, skipped_missing, skipped_not_lossless)
        return result

    def estimate_scope(self, context: JobContext) -> int:
        count = 0
        try:
            from core.library2.maintenance_subjects import active_file_subjects

            count += sum(
                1 for subject in scoped_file_subjects(context, active_file_subjects(
                    context.db, context.config_manager,
                )) if os.path.splitext(str(subject.get("path") or ""))[1].lower()
                in LOSSLESS_CANDIDATE_EXTENSIONS
            )
        except Exception as exc:
            logger.debug("Could not estimate indexed conversion scope: %s", exc)
        try:
            from core.repair_jobs.filesystem_subjects import filesystem_audio_files

            count += sum(
                1 for path in filesystem_audio_files(
                    context, extensions=LOSSLESS_CANDIDATE_EXTENSIONS,
                ) if os.path.splitext(path)[1].lower() in LOSSLESS_CANDIDATE_EXTENSIONS
            )
        except Exception as exc:
            logger.debug("Could not estimate filesystem conversion scope: %s", exc)
        return count
