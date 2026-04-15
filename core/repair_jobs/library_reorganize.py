"""Library Reorganize Job — moves files to match the current file organization template.

Safety design:
- Dry run mode is ON by default. The job only creates findings (reports) showing
  what WOULD move. The user must explicitly disable dry_run in job settings.
- The job is disabled by default (default_enabled=False) so it never runs
  automatically unless the user explicitly enables it.
- Case-insensitive path comparison on Windows prevents false moves.
- Destination collision check prevents overwriting existing files.
- Files without usable tags are skipped, not guessed at.
- Moves are always within the transfer folder; cannot escape to parent dirs.
"""

import json
import os
import re
import shutil
import sys

from core.metadata_service import get_client_for_source, get_primary_source, get_source_priority
from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair_job.library_reorganize")

AUDIO_EXTENSIONS = {'.mp3', '.flac', '.ogg', '.opus', '.m4a', '.aac', '.wav', '.wma', '.aiff', '.aif'}
SIDECAR_EXTENSIONS = {'.lrc', '.jpg', '.jpeg', '.png', '.nfo', '.txt', '.cue'}
ALBUM_SIDECAR_NAMES = {
    'cover.jpg', 'cover.jpeg', 'cover.png',
    'folder.jpg', 'folder.jpeg', 'folder.png',
    'album.jpg', 'album.jpeg', 'album.png',
    'front.jpg', 'front.jpeg', 'front.png',
    'thumb.jpg', 'thumb.png',
}

# Windows and macOS use case-insensitive filesystems by default
_CASE_INSENSITIVE = sys.platform in ('win32', 'darwin')


def _paths_equivalent(path_a: str, path_b: str) -> bool:
    """Compare two paths, case-insensitive on Windows/macOS."""
    a = os.path.normpath(path_a)
    b = os.path.normpath(path_b)
    if _CASE_INSENSITIVE:
        return a.lower() == b.lower()
    return a == b


def _sanitize_filename(filename: str) -> str:
    """Sanitize filename for file system compatibility."""
    sanitized = re.sub(r'[<>:"/\\|?*]', '_', filename)
    sanitized = re.sub(r'\s+', ' ', sanitized).strip()
    sanitized = sanitized.rstrip('. ') or '_'
    if re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\.|$)', sanitized, re.IGNORECASE):
        sanitized = '_' + sanitized
    return sanitized[:200]


def _sanitize_context_values(context: dict) -> dict:
    """Sanitize all string values for path safety.

    Empty strings are preserved so that template cleanup regexes can
    remove surrounding decorators (e.g. ``($year)`` → ``()`` → removed).
    """
    sanitized = {}
    for key, value in context.items():
        if isinstance(value, str):
            sanitized[key] = _sanitize_filename(value) if value else ''
        else:
            sanitized[key] = value
    return sanitized


def _apply_path_template(template: str, context: dict) -> str:
    """Apply template variables to build a path string."""
    clean = _sanitize_context_values(context)
    result = template
    result = result.replace('$albumartist', clean.get('albumartist', clean.get('artist', 'Unknown Artist')))
    result = result.replace('$albumtype', clean.get('albumtype', 'Album'))
    result = result.replace('$playlist', clean.get('playlist_name', ''))
    result = result.replace('$artistletter', (clean.get('artist', 'U') or 'U')[0].upper())
    result = result.replace('$artist', clean.get('artist', 'Unknown Artist'))
    result = result.replace('$album', clean.get('album', 'Unknown Album'))
    result = result.replace('$title', clean.get('title', 'Unknown Track'))
    result = result.replace('$track', f"{clean.get('track_number', 1):02d}")
    result = result.replace('$year', str(clean.get('year', '')))
    result = re.sub(r'\s+', ' ', result)
    result = re.sub(r'\s*-\s*-\s*', ' - ', result)
    return result.strip()


def _build_path_from_template(template: str, context: dict) -> tuple:
    """Build (folder_path, filename_base) from a template string and context."""
    full_path = _apply_path_template(template, context)
    quality_value = context.get('quality', '')
    disc_value = f"{context.get('disc_number', 1):02d}"

    path_parts = full_path.split('/')
    if len(path_parts) > 1:
        folder_parts = path_parts[:-1]
        filename_base = path_parts[-1]

        cleaned_folders = []
        for part in folder_parts:
            part = part.replace('$quality', '')
            part = part.replace('$disc', '')
            part = re.sub(r'\s*\[\s*\]', '', part)
            part = re.sub(r'\s*\(\s*\)', '', part)
            part = re.sub(r'\s*\{\s*\}', '', part)
            part = re.sub(r'\s*-\s*$', '', part)
            part = re.sub(r'^\s*-\s*', '', part)
            part = re.sub(r'\s+', ' ', part).strip()
            if part:
                cleaned_folders.append(part)

        filename_base = filename_base.replace('$quality', quality_value)
        filename_base = filename_base.replace('$disc', disc_value)
        filename_base = re.sub(r'\s*\[\s*\]', '', filename_base)
        filename_base = re.sub(r'\s*\(\s*\)', '', filename_base)
        filename_base = re.sub(r'\s*\{\s*\}', '', filename_base)
        filename_base = re.sub(r'\s*-\s*$', '', filename_base)
        filename_base = re.sub(r'\s+', ' ', filename_base).strip()

        sanitized_folders = [_sanitize_filename(p) for p in cleaned_folders]
        folder_path = os.path.join(*sanitized_folders) if sanitized_folders else ''
        return folder_path, _sanitize_filename(filename_base)
    else:
        full_path = full_path.replace('$quality', quality_value)
        full_path = full_path.replace('$disc', disc_value)
        full_path = re.sub(r'\s*\[\s*\]', '', full_path)
        full_path = re.sub(r'\s*\(\s*\)', '', full_path)
        full_path = re.sub(r'\s*\{\s*\}', '', full_path)
        full_path = re.sub(r'\s*-\s*$', '', full_path)
        full_path = re.sub(r'\s+', ' ', full_path).strip()
        return '', _sanitize_filename(full_path)


def _get_audio_quality(file_path: str) -> str:
    """Read audio file and return a quality descriptor string."""
    try:
        ext = os.path.splitext(file_path)[1].lower()
        if ext == '.flac':
            from mutagen.flac import FLAC
            audio = FLAC(file_path)
            bits = audio.info.bits_per_sample
            return f"FLAC {bits}bit"
        elif ext == '.mp3':
            from mutagen.mp3 import MP3, BitrateMode
            audio = MP3(file_path)
            kbps = audio.info.bitrate // 1000
            if audio.info.bitrate_mode == BitrateMode.VBR:
                return "MP3-VBR"
            return f"MP3-{kbps}"
        elif ext in ('.m4a', '.aac', '.mp4'):
            from mutagen.mp4 import MP4
            audio = MP4(file_path)
            kbps = audio.info.bitrate // 1000
            return f"M4A-{kbps}"
        elif ext == '.ogg':
            from mutagen.oggvorbis import OggVorbis
            audio = OggVorbis(file_path)
            kbps = audio.info.bitrate // 1000
            return f"OGG-{kbps}"
        elif ext == '.opus':
            from mutagen.oggopus import OggOpus
            audio = OggOpus(file_path)
            kbps = audio.info.bitrate // 1000
            return f"OPUS-{kbps}"
        return ''
    except Exception:
        return ''


def _read_tag_metadata(file_path: str) -> dict:
    """Read artist, album, title, track_number, disc_number, year from file tags."""
    try:
        from mutagen import File as MutagenFile
        audio = MutagenFile(file_path, easy=True)
        if audio is None:
            return {}

        def first(tag_list):
            if isinstance(tag_list, list) and tag_list:
                return str(tag_list[0])
            if isinstance(tag_list, str):
                return tag_list
            return ''

        meta = {}
        meta['artist'] = first(audio.get('artist', ['']))
        meta['albumartist'] = first(audio.get('albumartist', [''])) or meta['artist']
        meta['album'] = first(audio.get('album', ['']))
        meta['title'] = first(audio.get('title', ['']))

        # Track number: may be "3/12" format
        raw_track = first(audio.get('tracknumber', ['1']))
        try:
            meta['track_number'] = int(raw_track.split('/')[0])
        except (ValueError, IndexError):
            meta['track_number'] = 1

        # Disc number: may be "1/2" format
        raw_disc = first(audio.get('discnumber', ['1']))
        try:
            meta['disc_number'] = int(raw_disc.split('/')[0])
        except (ValueError, IndexError):
            meta['disc_number'] = 1

        # Year
        raw_date = first(audio.get('date', ['']))
        meta['year'] = raw_date[:4] if raw_date and len(raw_date) >= 4 else ''

        return meta
    except Exception as e:
        logger.debug("Failed to read tags from %s: %s", file_path, e)
        return {}


def _remove_empty_dirs(directory: str, root: str):
    """Remove empty directories up to root. Never removes root itself."""
    directory = os.path.normpath(directory)
    root = os.path.normpath(root)
    while directory != root and len(directory) > len(root) and directory.startswith(root):
        try:
            if os.path.isdir(directory) and not os.listdir(directory):
                os.rmdir(directory)
                directory = os.path.dirname(directory)
            else:
                break
        except OSError:
            break


@register_job
class LibraryReorganizeJob(RepairJob):
    job_id = 'library_reorganize'
    display_name = 'Library Reorganize'
    description = 'Moves files to match the current file organization template (dry run by default)'
    help_text = (
        'Scans your transfer folder and reads each audio file\'s tags (artist, album, title, '
        'track number, disc number) to compute the expected file path based on your current '
        'file organization template from Settings.\n\n'
        'Any file whose actual path doesn\'t match the expected template gets flagged. In dry '
        'run mode (default), a finding is created showing the current and expected paths. '
        'Disable dry run to have the job move files automatically.\n\n'
        'Safety features: case-insensitive path comparison on Windows/macOS, collision '
        'detection, path escape prevention, and sidecar file handling (.lrc, .nfo, etc.).\n\n'
        'Settings:\n'
        '- Dry Run: When enabled, only reports what would change without moving files\n'
        '- Move Sidecars: Also move associated files (.lrc, .jpg, .nfo) alongside audio files'
    )
    icon = 'repair-icon-reorganize'
    default_enabled = False
    default_interval_hours = 168  # Weekly — but disabled by default so won't auto-run
    default_settings = {
        'dry_run': True,
        'move_sidecars': True,
    }
    auto_fix = True

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()
        transfer = context.transfer_folder
        if not os.path.isdir(transfer):
            logger.warning("Transfer folder does not exist: %s", transfer)
            return result

        # Get template config
        cm = context.config_manager
        if not cm:
            logger.error("No config manager available")
            return result

        if not cm.get('file_organization.enabled', True):
            logger.info("File organization is disabled — skipping reorganize")
            if context.report_progress:
                context.report_progress(phase='Skipped — file organization disabled',
                                        log_line='File organization is disabled in settings',
                                        log_type='skip')
            return result

        templates = cm.get('file_organization.templates', {})
        album_template = templates.get('album_path', '$albumartist/$albumartist - $album/$track - $title')
        single_template = templates.get('single_path', '$artist/$artist - $title/$title')
        disc_label = cm.get('file_organization.disc_label', 'Disc')
        logger.info(f"Library Reorganize templates — album: '{album_template}', single: '{single_template}' (raw config: {templates})")

        dry_run = self._get_setting(context, 'dry_run', True)
        move_sidecars = self._get_setting(context, 'move_sidecars', True)

        if context.report_progress:
            mode_label = 'DRY RUN' if dry_run else 'LIVE'
            context.report_progress(phase=f'Scanning files ({mode_label})...',
                                    log_line=f'Mode: {mode_label} — Scanning {transfer}',
                                    log_type='info')

        # Collect all audio files
        audio_files = []
        for root_dir, _dirs, files in os.walk(transfer):
            if context.check_stop():
                return result
            for fname in files:
                ext = os.path.splitext(fname)[1].lower()
                if ext in AUDIO_EXTENSIONS:
                    audio_files.append(os.path.join(root_dir, fname))

        total = len(audio_files)
        if total == 0:
            logger.info("No audio files found in transfer folder")
            if context.report_progress:
                context.report_progress(phase='No files found', log_line='No audio files in transfer folder',
                                        log_type='info')
            return result

        if context.report_progress:
            context.report_progress(phase=f'Reading tags from {total} files...',
                                    log_line=f'Found {total} audio files',
                                    log_type='info', scanned=0, total=total)

        # Pre-read all tags and group by album for multi-disc detection
        file_tags = {}  # fpath -> tags dict
        album_groups = {}  # (albumartist, album) -> [fpath, ...]
        for fpath in audio_files:
            tags = _read_tag_metadata(fpath)
            file_tags[fpath] = tags
            key = (tags.get('albumartist', '') or tags.get('artist', ''),
                   tags.get('album', ''))
            if key not in album_groups:
                album_groups[key] = []
            album_groups[key].append(fpath)

        # Compute total_discs per album group
        album_total_discs = {}
        for key, fpaths in album_groups.items():
            max_disc = max((file_tags[fp].get('disc_number', 1) for fp in fpaths), default=1)
            album_total_discs[key] = max_disc

        # Pre-load album years from DB for files missing year tags
        db_album_years = {}  # (artist, album) -> year string
        needs_year = '$year' in (album_template + single_template)
        if needs_year:
            db_album_years = self._load_album_years(context.db)

        # API fallback: find (artist, album) pairs still missing year, batch-lookup
        if needs_year and db_album_years is not None:
            missing_pairs = set()
            for fpath, tags in file_tags.items():
                year = tags.get('year', '')
                if year:
                    continue
                artist = tags.get('artist', '') or tags.get('albumartist', '')
                album = tags.get('album', '') or tags.get('title', '')
                if not artist or not album:
                    continue
                key = (artist.lower(), album.lower())
                if key not in db_album_years:
                    missing_pairs.add((artist, album))

            if missing_pairs:
                api_years = self._lookup_years_from_api(context, missing_pairs)
                db_album_years.update(api_years)

        # Track claimed destinations to detect in-batch collisions
        claimed_destinations = set()
        # Track src_dir -> dest_dir for post-pass sidecar cleanup
        moved_dirs = {}  # src_dir -> dest_dir (last used destination)

        for i, fpath in enumerate(audio_files):
            if context.check_stop():
                return result
            if i % 50 == 0 and context.wait_if_paused():
                return result

            result.scanned += 1
            fname = os.path.basename(fpath)
            file_ext = os.path.splitext(fname)[1]

            tags = file_tags.get(fpath, {})

            # Skip files without minimum usable tags
            title = tags.get('title', '') or ''
            artist = tags.get('artist', '') or ''
            if not title and not artist:
                result.skipped += 1
                if context.report_progress and i % 20 == 0:
                    context.report_progress(scanned=i + 1, total=total,
                                            phase=f'Processing ({i+1}/{total})...')
                continue

            # Use defaults only when tags exist but are empty
            artist = artist or 'Unknown Artist'
            albumartist = tags.get('albumartist', '') or artist
            album = tags.get('album', '') or ''
            title = title or 'Unknown Track'
            track_number = tags.get('track_number', 1) or 1
            disc_number = tags.get('disc_number', 1) or 1
            year = tags.get('year', '')

            # Fallback: if file tags have no year, try the DB album year
            if not year and db_album_years:
                year = db_album_years.get((artist.lower(), (album or title).lower()), '')
                if not year:
                    year = db_album_years.get((albumartist.lower(), (album or title).lower()), '')

            # Read quality for $quality template variable
            quality = _get_audio_quality(fpath)

            # Determine template type: album or single
            album_key = (albumartist, album)
            group_size = len(album_groups.get(album_key, []))
            is_album = bool(album) and group_size > 1
            total_discs = album_total_discs.get(album_key, 1)

            template_context = {
                'artist': artist,
                'albumartist': albumartist,
                'album': album or title,
                'title': title,
                'track_number': track_number,
                'disc_number': disc_number,
                'year': year,
                'quality': quality,
                'albumtype': 'Album',
            }

            if is_album:
                template = album_template
                user_controls_disc = '$disc' in template
                folder_path, filename_base = _build_path_from_template(template, template_context)
                if folder_path and filename_base:
                    if total_discs > 1 and not user_controls_disc:
                        disc_folder = f"{disc_label} {disc_number}"
                        expected = os.path.join(transfer, folder_path, disc_folder, filename_base + file_ext)
                    else:
                        expected = os.path.join(transfer, folder_path, filename_base + file_ext)
                else:
                    result.skipped += 1
                    continue
            else:
                template = single_template
                folder_path, filename_base = _build_path_from_template(template, template_context)
                if folder_path and filename_base:
                    expected = os.path.join(transfer, folder_path, filename_base + file_ext)
                else:
                    result.skipped += 1
                    continue

            # Safety: verify destination is still inside transfer folder
            expected_norm = os.path.normpath(expected)
            transfer_norm = os.path.normpath(transfer)
            if not expected_norm.startswith(transfer_norm + os.sep) and expected_norm != transfer_norm:
                logger.warning("Computed path escapes transfer folder, skipping: %s", expected_norm)
                result.skipped += 1
                continue

            actual_norm = os.path.normpath(fpath)

            # Case-insensitive comparison on Windows/macOS
            if _paths_equivalent(actual_norm, expected_norm):
                if context.report_progress and i % 20 == 0:
                    context.report_progress(scanned=i + 1, total=total,
                                            phase=f'Processing ({i+1}/{total})...')
                continue

            # Check for in-batch destination collision
            dest_key = expected_norm.lower() if _CASE_INSENSITIVE else expected_norm
            if dest_key in claimed_destinations:
                result.skipped += 1
                if context.report_progress:
                    context.report_progress(
                        scanned=i + 1, total=total,
                        log_line=f'SKIP (duplicate dest): {os.path.basename(fpath)}',
                        log_type='skip'
                    )
                continue
            claimed_destinations.add(dest_key)

            # File needs to move
            if dry_run:
                rel_actual = os.path.relpath(actual_norm, transfer)
                rel_expected = os.path.relpath(expected_norm, transfer)
                if context.create_finding:
                    context.create_finding(
                        job_id=self.job_id,
                        finding_type='path_mismatch',
                        severity='info',
                        entity_type='file',
                        entity_id=None,
                        file_path=fpath,
                        title=f'Would move: {os.path.basename(fpath)}',
                        description=f'From: {rel_actual}\nTo: {rel_expected}',
                        details={'from': rel_actual, 'to': rel_expected}
                    )
                    result.findings_created += 1
                if context.report_progress:
                    context.report_progress(
                        scanned=i + 1, total=total,
                        phase=f'Dry run ({i+1}/{total})...',
                        log_line=f'[DRY] {os.path.basename(fpath)} -> {os.path.relpath(expected_norm, transfer)}',
                        log_type='info'
                    )
            else:
                # Actually move the file
                try:
                    dest_dir = os.path.dirname(expected_norm)
                    os.makedirs(dest_dir, exist_ok=True)

                    # Collision: skip if destination already exists and is a different file
                    if os.path.exists(expected_norm):
                        # On case-insensitive FS, check if it's the same file (case rename)
                        try:
                            same_file = os.path.samefile(actual_norm, expected_norm)
                        except (OSError, ValueError):
                            same_file = False

                        if not same_file:
                            result.skipped += 1
                            if context.report_progress:
                                context.report_progress(
                                    scanned=i + 1, total=total,
                                    log_line=f'SKIP (exists): {os.path.basename(fpath)}',
                                    log_type='skip'
                                )
                            continue

                        # Same file, different case — use two-step rename to avoid
                        # OS refusing rename to "same" path on case-insensitive FS
                        if _CASE_INSENSITIVE:
                            tmp_path = expected_norm + '.tmp_rename'
                            shutil.move(actual_norm, tmp_path)
                            shutil.move(tmp_path, expected_norm)
                        else:
                            shutil.move(actual_norm, expected_norm)
                    else:
                        shutil.move(actual_norm, expected_norm)

                    result.auto_fixed += 1
                    # Record src->dest for post-pass sidecar cleanup
                    # For multi-disc, use album root (parent of Disc N/) as destination
                    sidecar_dest = dest_dir
                    if is_album and total_discs > 1 and '$disc' not in album_template:
                        sidecar_dest = os.path.dirname(dest_dir)
                    moved_dirs[os.path.dirname(actual_norm)] = sidecar_dest

                    # Move sidecar files (LRC, cover art, etc.)
                    if move_sidecars:
                        stem = os.path.splitext(os.path.basename(actual_norm))[0]
                        src_dir = os.path.dirname(actual_norm)
                        # Track-level sidecars (same stem as audio file)
                        for sidecar_ext in SIDECAR_EXTENSIONS:
                            sidecar_src = os.path.join(src_dir, stem + sidecar_ext)
                            if os.path.isfile(sidecar_src):
                                new_stem = os.path.splitext(os.path.basename(expected_norm))[0]
                                sidecar_dst = os.path.join(dest_dir, new_stem + sidecar_ext)
                                try:
                                    shutil.move(sidecar_src, sidecar_dst)
                                except Exception as se:
                                    logger.debug("Failed to move sidecar %s: %s", sidecar_src, se)
                        # Album-level sidecars (cover.jpg, folder.jpg, etc.)
                        # For multi-disc, place in the album root (parent of Disc N/)
                        album_dest = dest_dir
                        if is_album and total_discs > 1 and '$disc' not in album_template:
                            album_dest = os.path.dirname(dest_dir)
                            os.makedirs(album_dest, exist_ok=True)
                        for album_sidecar in ALBUM_SIDECAR_NAMES:
                            sidecar_src = os.path.join(src_dir, album_sidecar)
                            if os.path.isfile(sidecar_src):
                                sidecar_dst = os.path.join(album_dest, album_sidecar)
                                if not os.path.exists(sidecar_dst):
                                    try:
                                        shutil.move(sidecar_src, sidecar_dst)
                                    except Exception as se:
                                        logger.debug("Failed to move album sidecar %s: %s", sidecar_src, se)

                    # Update DB file_path if there's a matching track
                    self._update_db_path(context.db, actual_norm, expected_norm, transfer)

                    # Clean up empty source directories
                    _remove_empty_dirs(os.path.dirname(actual_norm), transfer)

                    if context.report_progress:
                        context.report_progress(
                            scanned=i + 1, total=total,
                            phase=f'Moving ({i+1}/{total})...',
                            log_line=f'Moved: {os.path.basename(fpath)}',
                            log_type='success'
                        )
                except Exception as e:
                    logger.error("Failed to move %s -> %s: %s", fpath, expected_norm, e)
                    result.errors += 1
                    if context.report_progress:
                        context.report_progress(
                            scanned=i + 1, total=total,
                            log_line=f'ERROR: {os.path.basename(fpath)} -- {e}',
                            log_type='error'
                        )

            if context.update_progress and (i + 1) % 10 == 0:
                context.update_progress(i + 1, total)

        if context.update_progress:
            context.update_progress(total, total)

        # Post-pass: move leftover sidecar files from directories that lost all audio
        if not dry_run and move_sidecars and moved_dirs:
            for src_dir, dest_dir in moved_dirs.items():
                if not os.path.isdir(src_dir):
                    continue
                # Check if any audio files remain in this directory
                has_audio = any(
                    os.path.splitext(f)[1].lower() in AUDIO_EXTENSIONS
                    for f in os.listdir(src_dir)
                    if os.path.isfile(os.path.join(src_dir, f))
                )
                if has_audio:
                    continue
                # Move all remaining sidecar-type files to the destination
                for f in os.listdir(src_dir):
                    fpath_full = os.path.join(src_dir, f)
                    if not os.path.isfile(fpath_full):
                        continue
                    ext = os.path.splitext(f)[1].lower()
                    if ext in SIDECAR_EXTENSIONS:
                        dst = os.path.join(dest_dir, f)
                        if not os.path.exists(dst):
                            try:
                                shutil.move(fpath_full, dst)
                            except Exception:
                                pass
                # Try cleaning up the now-potentially-empty directory
                _remove_empty_dirs(src_dir, transfer)

        mode_text = 'Dry run' if dry_run else 'Reorganize'
        summary = f"{mode_text} complete: {result.scanned} scanned, {result.auto_fixed} moved, {result.findings_created} findings, {result.skipped} skipped, {result.errors} errors"
        logger.info(summary)
        if context.report_progress:
            context.report_progress(
                phase='Complete',
                log_line=summary,
                log_type='success',
                scanned=total, total=total
            )

        return result

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

    def _get_setting(self, context: JobContext, key: str, default):
        """Read a job-specific setting from config."""
        if context.config_manager:
            return context.config_manager.get(f'repair.jobs.{self.job_id}.settings.{key}', default)
        return default

    def _load_album_years(self, db) -> dict:
        """Load all album years from DB. Returns {(artist_lower, album_lower): year_str}.

        Checks the main albums table first, then falls back to discovery_pool
        release_date for tracks that were playlist-synced without year metadata.
        """
        years = {}
        conn = None
        try:
            conn = db._get_connection()
            cursor = conn.cursor()

            # Source 1: albums table (most authoritative)
            cursor.execute("""
                SELECT ar.name, al.title, al.year
                FROM albums al
                JOIN artists ar ON ar.id = al.artist_id
                WHERE al.year IS NOT NULL AND al.year != 0
            """)
            for row in cursor.fetchall():
                artist_name, album_title, year = row
                if artist_name and album_title and year:
                    key = (artist_name.lower(), album_title.lower())
                    years[key] = str(year)[:4]

            # Source 2: discovery_pool release_date (covers playlist-synced tracks)
            try:
                cursor.execute("""
                    SELECT artist_name, album_name, release_date
                    FROM discovery_pool
                    WHERE release_date IS NOT NULL AND release_date != ''
                """)
                for row in cursor.fetchall():
                    artist_name, album_name, release_date = row
                    if artist_name and album_name and release_date:
                        key = (artist_name.lower(), album_name.lower())
                        if key not in years:  # Don't override albums table
                            year_str = str(release_date)[:4]
                            if len(year_str) == 4 and year_str.isdigit():
                                years[key] = year_str
            except Exception:
                pass  # discovery_pool may not exist on all installs

            # Source 3: recent_releases (watchlist artist releases)
            try:
                cursor.execute("""
                    SELECT wa.artist_name, rr.album_name, rr.release_date
                    FROM recent_releases rr
                    JOIN watchlist_artists wa ON wa.id = rr.watchlist_artist_id
                    WHERE rr.release_date IS NOT NULL AND rr.release_date != ''
                """)
                for row in cursor.fetchall():
                    artist_name, album_name, release_date = row
                    if artist_name and album_name and release_date:
                        key = (artist_name.lower(), album_name.lower())
                        if key not in years:
                            year_str = str(release_date)[:4]
                            if len(year_str) == 4 and year_str.isdigit():
                                years[key] = year_str
            except Exception:
                pass  # recent_releases may not exist on all installs

            # Source 4: wishlist tracks (spotify_data JSON contains release date)
            try:
                cursor.execute("""
                    SELECT spotify_data FROM wishlist_tracks
                    WHERE spotify_data IS NOT NULL AND spotify_data != ''
                """)
                for row in cursor.fetchall():
                    try:
                        data = json.loads(row[0])
                        artist_name = ''
                        if data.get('artists'):
                            a = data['artists'][0]
                            artist_name = a.get('name', '') if isinstance(a, dict) else str(a)
                        album_data = data.get('album', {})
                        album_name = album_data.get('name', '') if isinstance(album_data, dict) else ''
                        release_date = album_data.get('release_date', '') if isinstance(album_data, dict) else ''
                        if artist_name and album_name and release_date:
                            key = (artist_name.lower(), album_name.lower())
                            if key not in years:
                                year_str = str(release_date)[:4]
                                if len(year_str) == 4 and year_str.isdigit():
                                    years[key] = year_str
                    except (ValueError, KeyError, TypeError):
                        continue
            except Exception:
                pass  # wishlist_tracks may not exist or have different schema

        except Exception as e:
            logger.debug("Failed to load album years from DB: %s", e)
        finally:
            if conn:
                conn.close()
        return years

    def _lookup_years_from_api(self, context, missing_pairs) -> dict:
        """Batch-lookup release years from the configured metadata providers for albums not found in DB.

        Args:
            context: JobContext with config_manager
            missing_pairs: set of (artist, album) tuples needing year lookup

        Returns:
            dict of {(artist_lower, album_lower): year_str}
        """
        years = {}
        if not missing_pairs:
            return years

        primary_source = get_primary_source()
        source_priority = get_source_priority(primary_source)

        # Cap lookups to avoid excessive API calls
        max_lookups = 200
        pairs_list = list(missing_pairs)[:max_lookups]
        logger.info("Looking up %d album years from configured metadata providers", len(pairs_list))

        if context.report_progress:
            context.report_progress(
                phase=f'Looking up {len(pairs_list)} album years from metadata providers...',
                log_line=f'Fetching release years for {len(pairs_list)} albums',
                log_type='info'
            )

        for artist, album in pairs_list:
            if context.check_stop():
                break
            key = (artist.lower(), album.lower())
            for source_name in source_priority:
                try:
                    search_client = get_client_for_source(source_name)
                    if not search_client or not hasattr(search_client, 'search_albums'):
                        continue
                    results = search_client.search_albums(f"{artist} {album}", limit=3)
                    if results:
                        for r in results:
                            release_date = getattr(r, 'release_date', '') or ''
                            if release_date and len(release_date) >= 4:
                                year_str = release_date[:4]
                                if year_str.isdigit():
                                    years[key] = year_str
                                    break
                        if key in years:
                            break
                    if context.sleep_or_stop(0.1):  # Rate limit courtesy
                        return years
                except Exception as e:
                    logger.debug("API year lookup failed for %s - %s via %s: %s", artist, album, source_name, e)

        logger.info("API year lookup: found %d/%d years", len(years), len(pairs_list))
        return years

    def _update_db_path(self, db, old_path: str, new_path: str, transfer_folder: str = ''):
        """Update file_path in the tracks table when a file is moved.

        DB may store server-side paths (e.g. /mnt/musicBackup/Artist/Album/track.flac)
        while local paths use the transfer folder (e.g. H:\\Music\\Artist\\Album\\track.flac).
        Falls back to suffix matching when exact match fails.
        """
        conn = None
        try:
            conn = db._get_connection()
            cursor = conn.cursor()

            # Try exact match first
            cursor.execute("UPDATE tracks SET file_path = ? WHERE file_path = ?",
                           (new_path, old_path))
            if cursor.rowcount > 0:
                conn.commit()
                return

            # Try normalized path match
            cursor.execute("UPDATE tracks SET file_path = ? WHERE file_path = ?",
                           (new_path, os.path.normpath(old_path)))
            if cursor.rowcount > 0:
                conn.commit()
                return

            # Suffix match: compute path relative to transfer folder and match
            # against DB paths that may use a different base prefix
            if transfer_folder:
                try:
                    rel_suffix = os.path.relpath(old_path, transfer_folder).replace('\\', '/')
                    # Escape LIKE wildcards (% _ ^) so artist/album names are literal
                    escaped = rel_suffix.replace('^', '^^').replace('%', '^%').replace('_', '^_')
                    cursor.execute(
                        "UPDATE tracks SET file_path = ? WHERE file_path LIKE ? ESCAPE '^'",
                        (new_path, '%/' + escaped)
                    )
                    if cursor.rowcount > 0:
                        conn.commit()
                        return
                    # Also try with backslash separators (Windows DB paths)
                    escaped_bs = escaped.replace('/', '\\')
                    cursor.execute(
                        "UPDATE tracks SET file_path = ? WHERE file_path LIKE ? ESCAPE '^'",
                        (new_path, '%\\' + escaped_bs)
                    )
                except Exception:
                    pass

            conn.commit()
        except Exception as e:
            logger.debug("DB path update failed for %s: %s", old_path, e)
        finally:
            if conn:
                conn.close()
