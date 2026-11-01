"""Zero-Length AAC Fixer maintenance job (Skowl's report).

Tidal (and the HiFi instances in front of it) send AAC as stream segments, and
SoulSync used to save them as they came: a fragmented MP4 whose header says it
has no length. It plays, but Navidrome and Feishin show 0:00 and can't seek.
New downloads are remuxed on the way in now; this fixes the ones already in
the library.

The scan only reads each .m4a/.mp4 header (cheap) and creates a finding for a
file that reads no length. Applying one remuxes it into a plain MP4 (the audio
is copied, not re-encoded) and puts every tag back as it was, cover and
MusicBrainz ids included. The work happens on a copy, and the original is only
replaced once the copy has its tags and reads its real length.

Requires ffmpeg, so the scan does nothing when ffmpeg isn't installed rather
than surfacing findings that could never be applied.
"""

from __future__ import annotations

import os
import shutil
from typing import Optional

from core.library.path_resolver import resolve_library_file_path
from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob, scoped_file_subjects
from utils.logging_config import get_logger

logger = get_logger("repair_jobs.streamed_aac_repair")

_MP4_EXTS = {'.m4a', '.mp4'}


def ffmpeg_path() -> Optional[str]:
    found = shutil.which('ffmpeg')
    if found:
        return found
    local = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'tools', 'ffmpeg')
    return local if os.path.isfile(local) else None


def _resolve(file_path: str, context: JobContext) -> Optional[str]:
    """Resolve a stored library path to one this process can read (the same
    suffix walk every file job needs for Docker/NAS paths)."""
    if not file_path:
        return None
    resolved = resolve_library_file_path(
        file_path,
        transfer_folder=context.transfer_folder,
        config_manager=context.config_manager,
    )
    if not resolved and os.path.isfile(file_path):
        resolved = file_path
    return resolved


@register_job
class StreamedAacRepairJob(RepairJob):
    job_id = 'streamed_aac_repair'
    display_name = 'Zero-Length AAC Fixer'
    description = 'Finds AAC files that show 0:00 and can\'t seek, and fixes them without re-downloading'
    help_text = (
        'Tidal sends AAC in streamed pieces, and older SoulSync versions saved those files as '
        'they came. They play fine, but the file says it has no length, so Navidrome, Feishin '
        'and other players show 0:00 and can\'t seek.\n\n'
        'This scans your .m4a files and creates a finding for each one that reads no length. '
        'Fixing one rewrites the file into a normal MP4: the audio is copied, not re-encoded, '
        'and every tag is put back as it was, cover art and MusicBrainz ids included. The work '
        'is done on a copy and the original is only replaced once the copy checks out.\n\n'
        'Use Fix All to do the whole library at once. Afterwards, rescan your media server so it '
        'reads the new length.\n\n'
        'Requires ffmpeg to be installed.'
    )
    icon = 'repair-icon-replaygain'
    default_enabled = False
    default_interval_hours = 168
    default_settings: dict = {}
    auto_fix = False
    supports_file_scope = True

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()
        if not ffmpeg_path():
            logger.info("[Zero-Length AAC] ffmpeg not available, skipping scan (nothing could be fixed)")
            return result
        from core.downloads.mp4_remux import reads_zero_length

        rows = []
        native_subjects = {}
        try:
            from core.library2.maintenance_subjects import active_file_subjects

            for subject in scoped_file_subjects(context, active_file_subjects(
                context.db, context.config_manager,
            )):
                file_path = str(subject["path"])
                if os.path.splitext(file_path)[1].lower() not in _MP4_EXTS:
                    continue
                native_subjects[file_path] = subject
                rows.append((f"lib2:{subject['track_id']}", subject["title"],
                             subject["artist_name"], file_path))
        except Exception as e:
            logger.warning("[Zero-Length AAC] subject enumeration failed: %s", e)
        from core.repair_jobs.filesystem_subjects import filesystem_audio_files

        for file_path in filesystem_audio_files(context):
            if os.path.splitext(file_path)[1].lower() in _MP4_EXTS:
                rows.append((None, os.path.splitext(os.path.basename(file_path))[0], None, file_path))

        total = len(rows)
        if context.update_progress:
            context.update_progress(0, total)
        if context.report_progress:
            context.report_progress(phase=f'Checking the length of {total} AAC files...', total=total)

        for i, (track_id, title, artist_name, file_path) in enumerate(rows):
            if context.check_stop():
                return result
            if i % 10 == 0 and context.wait_if_paused():
                return result
            result.scanned += 1
            resolved = _resolve(file_path, context)
            if not resolved:
                result.skipped += 1
                continue
            if not reads_zero_length(resolved):
                if context.update_progress and (i + 1) % 25 == 0:
                    context.update_progress(i + 1, total)
                continue
            if context.report_progress:
                context.report_progress(scanned=i + 1, total=total,
                                        log_line=f'Shows 0:00: {title} by {artist_name or "Unknown"}',
                                        log_type='info')
            if context.create_finding:
                try:
                    details = {'track_id': track_id, 'track_title': title,
                               'artist': artist_name, 'file_path': resolved}
                    subject = native_subjects.get(str(file_path))
                    if subject:
                        from core.library2.maintenance_subjects import subject_details

                        details.update(subject_details(subject))
                    inserted = context.create_finding(
                        job_id=self.job_id,
                        finding_type='zero_length_mp4',
                        severity='warning',
                        entity_type='track' if subject else 'file',
                        entity_id=str(track_id) if track_id is not None else None,
                        file_path=file_path,
                        title=f'Shows 0:00: {title or "Unknown"}',
                        description=(f'"{title}" by {artist_name or "Unknown"} plays but reads no '
                                     'length, so players show 0:00 and can\'t seek. Fixing it keeps '
                                     'the audio and every tag.'),
                        details=details)
                    if inserted:
                        result.findings_created += 1
                    else:
                        result.findings_skipped_dedup += 1
                except Exception as e:
                    logger.debug("[Zero-Length AAC] create finding failed for %s: %s", file_path, e)
                    result.errors += 1
            if context.update_progress and (i + 1) % 10 == 0:
                context.update_progress(i + 1, total)

        if context.update_progress:
            context.update_progress(total, total)
        logger.info("[Zero-Length AAC] %d AAC files checked, %d read no length, %d skipped",
                    result.scanned, result.findings_created, result.skipped)
        return result

    def estimate_scope(self, context: JobContext) -> int:
        try:
            from core.library2.maintenance_subjects import count_active_files

            return count_active_files(context.db, context.config_manager)
        except Exception as exc:
            logger.debug("Could not estimate the Zero-Length AAC scope: %s", exc)
            return 0
