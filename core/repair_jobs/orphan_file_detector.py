"""Orphan File Detector Job — finds files not tracked in the DB.

Always scans the transfer/staging folder. Optionally (opt-in setting
``scan_library_folder``, default off so existing installations keep their
current scan cost) also scans every root the admin has explicitly configured
in ``library.music_paths`` (Settings → Music Library Paths) — closing review A4:
the retired legacy ``quality_upgrade_scanner`` used to walk the whole music
library and quality-check files with no DB match (failed imports, files
placed outside SoulSync); its native replacement only reads already-imported
rows via SQL and never touches the disk, so those files stopped getting
checked at all.

``library.music_paths`` — not ``soulseek.download_path``/``transfer_path`` —
is deliberately the ONLY source used for "the real library folder(s)" here:
those two are download-pipeline staging areas, and which folder a given
installation actually treats as its finished library varies (some point
media servers straight at the transfer or staging folder). This mirrors the
existing convention other Library-v2 modules already use for the same
question (``core.library2.file_delete._library_roots``,
``core.library.path_resolver``, ``core.library2.paths
.missing_path_root_is_healthy``) — an explicit, user-declared list, never
guessed from an unrelated download-pipeline path. Empty by default, so this
opt-in setting is a genuine no-op until the admin configures at least one
root.

Rather than add a second job for the same "audio file with no DB match"
concept, every orphan finding (either source) now also carries the file's
real measured quality (mutagen — format/bitrate/sample_rate/bit_depth), so
you can judge what you have before deciding to import or remove it.
"""

import os
import re
import time

from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob, skip_deleted_quarantine
from utils.logging_config import get_logger

logger = get_logger("repair_job.orphan_files")

AUDIO_EXTENSIONS = {'.mp3', '.flac', '.ogg', '.opus', '.m4a', '.aac', '.wav', '.wma', '.aiff', '.aif'}


def _library_folder_setting(context: JobContext) -> bool:
    try:
        settings = context.config_manager.get(
            'repair.jobs.orphan_file_detector.settings', {}) or {}
        return bool(settings.get('scan_library_folder', False)) if isinstance(settings, dict) else False
    except Exception:  # noqa: BLE001
        return False


def _configured_music_roots(config_manager) -> list:
    """Every existing, canonical root the admin explicitly configured in
    ``library.music_paths`` — same read+resolve pattern as
    ``core.library2.file_delete._library_roots``."""
    try:
        configured = config_manager.get("library.music_paths", []) or []
    except Exception:  # noqa: BLE001
        configured = []
    if isinstance(configured, str):
        configured = [configured]

    from core.imports.paths import docker_resolve_path

    roots: list = []
    for raw in configured:
        if not isinstance(raw, str) or not raw.strip():
            continue
        resolved = os.path.realpath(
            os.path.abspath(os.path.expanduser(docker_resolve_path(raw.strip())))
        )
        if os.path.isdir(resolved) and resolved not in roots:
            roots.append(resolved)
    return roots


def _scan_roots(context: JobContext) -> list:
    """Folders to walk: transfer always, every configured library.music_paths
    root too when the opt-in setting is on. De-duplicated."""
    roots = []
    transfer = context.transfer_folder
    if transfer and os.path.isdir(transfer):
        roots.append(os.path.realpath(transfer))
    else:
        logger.warning("Transfer folder does not exist: %s", transfer)

    if _library_folder_setting(context) and context.config_manager is not None:
        for resolved in _configured_music_roots(context.config_manager):
            if resolved not in roots:
                roots.append(resolved)

    return roots


@register_job
class OrphanFileDetectorJob(RepairJob):
    job_id = 'orphan_file_detector'
    display_name = 'Orphan File Detector'
    description = 'Finds audio files not tracked in the database'
    help_text = (
        'Walks your transfer folder looking for audio files (FLAC, MP3, M4A, OGG, WAV, etc.) '
        'that exist on disk but have no matching entry in the SoulSync database. Each finding '
        "also carries the file's real measured quality (format/bitrate/sample rate/bit depth), "
        'so you know what you have before deciding.\n\n'
        "Optional setting 'scan_library_folder' (off by default) also walks every folder "
        'configured under Settings → Music Library Paths (library.music_paths), not just the '
        'transfer/staging area — catches a failed or partial import, or a file placed there '
        'outside SoulSync entirely. A no-op until at least one library path is configured '
        'there.\n\n'
        'Orphan files can appear after manual folder edits, interrupted downloads, or database '
        'issues. Each orphan is reported as a finding so you can decide whether to import it '
        'into your library or remove it.\n\n'
        'This job only scans and reports — it never moves or deletes files on its own.'
    )
    icon = 'repair-icon-orphan'
    default_enabled = True
    default_interval_hours = 24
    default_settings = {'scan_library_folder': False}
    auto_fix = False

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()

        if (
            context.config_manager is None
            or context.config_manager.get("features.library_v2", True) is not True
        ):
            return result

        roots = _scan_roots(context)
        if not roots:
            return result

        # Build set of known file-path suffixes from DB.
        # DB may store paths with a different base prefix than the local filesystem
        # (e.g. DB has /mnt/musicBackup/Artist/Album/track.mp3, local disk is
        # H:\Music\Artist\Album\track.mp3).  We compare using suffix fragments
        # of depth 1-3 (filename, album/filename, artist/album/filename) which
        # covers all realistic path-prefix mismatches.
        known_suffixes = set()
        known_titles = set()       # (title_lower, artist_lower) for exact match
        known_titles_clean = set()  # (clean_title, clean_artist) for normalized match
        def _strip_extras(s):
            """Strip parentheticals/brackets for normalized comparison."""
            return re.sub(r'\s*[\(\[][^)\]]*[\)\]]', '', s).strip()

        try:
            from core.library2.maintenance_subjects import active_file_subjects

            for subject in active_file_subjects(
                context.db, context.config_manager, include_missing=True,
            ):
                parts = str(subject["path"]).replace('\\', '/').split('/')
                for depth in range(1, min(5, len(parts) + 1)):
                    known_suffixes.add('/'.join(parts[-depth:]).lower())

            conn = context.db._get_connection()
            try:
                rows = conn.execute(
                    """SELECT t.title, credit.name, primary_artist.name
                         FROM lib2_tracks t
                         JOIN lib2_albums al ON al.id=t.album_id
                    LEFT JOIN lib2_artists primary_artist
                           ON primary_artist.id=al.primary_artist_id
                    LEFT JOIN lib2_track_artists ta ON ta.track_id=t.id
                    LEFT JOIN lib2_artists credit ON credit.id=ta.artist_id
                        WHERE t.title IS NOT NULL AND t.title<>''"""
                ).fetchall()
            finally:
                conn.close()
            for title_value, credit_name, primary_name in rows:
                title = (title_value or '').lower().strip()
                if not title:
                    continue
                for artist_value in (credit_name, primary_name):
                    artist = (artist_value or '').lower().strip()
                    known_titles.add((title, artist))
                    clean_t = _strip_extras(title)
                    if clean_t:
                        known_titles_clean.add((clean_t, _strip_extras(artist)))
        except Exception as e:
            logger.error("Error reading known file paths from DB: %s", e, exc_info=True)
            result.errors += 1
            return result

        # Walk every scan root and find orphans
        audio_files = []
        for root_dir in roots:
            for root, dirs, files in os.walk(root_dir):
                skip_deleted_quarantine(root, dirs, root_dir)
                if context.check_stop():
                    return result
                for fname in files:
                    ext = os.path.splitext(fname)[1].lower()
                    if ext in AUDIO_EXTENSIONS:
                        audio_files.append(os.path.join(root, fname))

        total = len(audio_files)
        if context.update_progress:
            context.update_progress(0, total)
        if context.report_progress:
            context.report_progress(phase=f'Checking {total} files...', total=total)

        orphan_files = []

        for i, fpath in enumerate(audio_files):
            if context.check_stop():
                return result
            if i % 100 == 0 and context.wait_if_paused():
                return result

            result.scanned += 1

            if context.report_progress and i % 50 == 0:
                context.report_progress(
                    scanned=i + 1, total=total,
                    phase=f'Checking {i + 1} / {total}',
                    log_line=f'Checking: {os.path.basename(fpath)}',
                    log_type='info'
                )

            # Check if this file matches any known DB path via suffix matching
            fpath_parts = fpath.replace('\\', '/').split('/')
            is_known = False
            for depth in range(1, min(5, len(fpath_parts) + 1)):
                suffix = '/'.join(fpath_parts[-depth:]).lower()
                if suffix in known_suffixes:
                    is_known = True
                    break

            # Fallback: read file tags and check if title+artist exists in DB
            # Catches path mismatches where the file is tracked but under a different path.
            # Uses both exact and normalized comparison to handle feat. suffixes, etc.
            if not is_known and known_titles:
                try:
                    from mutagen import File as MutagenFile
                    audio = MutagenFile(fpath, easy=True)
                    if audio:
                        file_title = ((audio.get('title') or [None])[0] or '').lower().strip()
                        file_artist = ((audio.get('artist') or [None])[0] or '').lower().strip()
                        file_albumartist = (
                            (audio.get('albumartist') or audio.get('album_artist') or [None])[0]
                            or ''
                        ).lower().strip()
                        if file_title:
                            file_artists = [a for a in (file_artist, file_albumartist) if a]
                            if not file_artists:
                                file_artists = ['']
                            # Exact match first (fast path)
                            if any((file_title, artist) in known_titles for artist in file_artists):
                                is_known = True
                            else:
                                # Normalized match: strip (feat. X), [FLAC 16bit], etc.
                                clean_title = _strip_extras(file_title)
                                clean_artist = _strip_extras(file_artists[0])
                                # Also try first artist only (handles "Gorillaz, Dennis Hopper" → "Gorillaz")
                                first_artist = clean_artist.split(',')[0].strip() if clean_artist else ''
                                if clean_title and (
                                    (clean_title, clean_artist) in known_titles_clean or
                                    (first_artist and (clean_title, first_artist) in known_titles_clean)
                                ):
                                    is_known = True
                                if clean_title and not is_known:
                                    for artist in file_artists[1:]:
                                        clean_artist = _strip_extras(artist)
                                        first_artist = clean_artist.split(',')[0].strip() if clean_artist else ''
                                        if (
                                            (clean_title, clean_artist) in known_titles_clean or
                                            (first_artist and (clean_title, first_artist) in known_titles_clean)
                                        ):
                                            is_known = True
                                            break
                except Exception as e:
                    logger.debug("tag-based orphan check: %s", e)

            # Last resort: parse title from filename pattern "NN - Title [Quality].ext"
            # and match against known titles. Catches files with unreadable tags.
            if not is_known and known_titles:
                try:
                    fname_base = os.path.splitext(os.path.basename(fpath))[0]
                    # Strip quality tags like [FLAC 16bit], [MP3-320]
                    fname_clean = re.sub(r'\s*\[.*?\]\s*$', '', fname_base).strip()
                    # Strip leading track number: "01 - Title" → "Title"
                    fname_clean = re.sub(r'^\d{1,3}\s*[-–.]\s*', '', fname_clean).strip()
                    if fname_clean:
                        fname_lower = fname_clean.lower()
                        # Extract artist from parent folder
                        parent_folder = os.path.basename(os.path.dirname(fpath)).lower().strip()
                        # Try artist from grandparent (Artist/Album/track.flac)
                        grandparent = os.path.basename(os.path.dirname(os.path.dirname(fpath))).lower().strip()
                        for folder_artist in [parent_folder, grandparent]:
                            if (fname_lower, folder_artist) in known_titles:
                                is_known = True
                                break
                            clean_fn = _strip_extras(fname_lower)
                            clean_fa = _strip_extras(folder_artist)
                            if clean_fn and (clean_fn, clean_fa) in known_titles_clean:
                                is_known = True
                                break
                except Exception as e:
                    logger.debug("filename-pattern orphan check: %s", e)

            if not is_known:
                orphan_files.append(fpath)

            if context.update_progress and (i + 1) % 50 == 0:
                context.update_progress(i + 1, total)

        # Safety: if most files look like orphans, it's almost certainly a path
        # mismatch between the DB and filesystem (remount / Docker volume change),
        # NOT real orphans. Creating findings anyway is dangerous — a user batch-
        # applying "move to staging" / "delete" on them would relocate or wipe the
        # whole library. So we create NO findings here, the same hard skip the
        # stale-removal paths use. Fix the path mismatch and real orphans surface.
        from core.library.stale_guard import is_implausible_orphan_flood
        if is_implausible_orphan_flood(len(orphan_files), total):
            pct = (len(orphan_files) / total * 100) if total else 0
            logger.warning(
                "Mass orphan guard: %d of %d files (%.0f%%) flagged as orphans — "
                "almost certainly a DB↔filesystem path mismatch, not real orphans. "
                "Creating no findings so a batch move/delete can't wipe the library.",
                len(orphan_files), total, pct,
            )
            if context.report_progress:
                context.report_progress(
                    log_line=(f'Skipped: {len(orphan_files)} of {total} files look '
                              'orphaned — likely a DB path mismatch, not real orphans. '
                              'No findings created.'),
                    log_type='skip',
                )
            if context.update_progress:
                context.update_progress(total, total)
            logger.info("Orphan file scan: %d files scanned, mass-orphan guard tripped "
                        "(0 findings)", result.scanned)
            return result

        from core.imports.file_ops import probe_audio_quality
        from core.library2.status import quality_tier

        for fpath in orphan_files:
            if context.report_progress:
                context.report_progress(
                    log_line=f'Orphan: {os.path.basename(fpath)}',
                    log_type='skip'
                )
            try:
                stat = os.stat(fpath)
                ext = os.path.splitext(fpath)[1].lower().lstrip('.')
                quality = probe_audio_quality(fpath)
                tier = quality_tier(
                    quality.format if quality else None,
                    quality.bitrate if quality else None,
                    quality.bit_depth if quality else None,
                )
                if context.create_finding:
                    inserted = context.create_finding(
                        job_id=self.job_id,
                        finding_type='orphan_file',
                        severity='info',
                        entity_type='file',
                        entity_id=None,
                        file_path=fpath,
                        title=f'Orphan file: {os.path.basename(fpath)}',
                        description=(
                            'Audio file is not tracked in the database. '
                            f'Measured quality: {tier.replace("_", " ")}.'
                        ),
                        details={
                            'file_size': stat.st_size,
                            'format': quality.format if quality else ext,
                            'bitrate': quality.bitrate if quality else None,
                            'sample_rate': quality.sample_rate if quality else None,
                            'bit_depth': quality.bit_depth if quality else None,
                            'quality_tier': tier,
                            'modified': time.strftime('%Y-%m-%d %H:%M:%S',
                                                      time.localtime(stat.st_mtime)),
                            'folder': os.path.dirname(fpath),
                        }
                    )
                    if inserted:
                        result.findings_created += 1
                    else:
                        result.findings_skipped_dedup += 1
            except Exception as e:
                logger.debug("Error creating orphan finding for %s: %s", fpath, e)
                result.errors += 1

        if context.update_progress:
            context.update_progress(total, total)

        logger.info("Orphan file scan: %d files scanned, %d orphans found",
                     result.scanned, result.findings_created)
        return result

    def estimate_scope(self, context: JobContext) -> int:
        roots = _scan_roots(context)
        count = 0
        for root_dir in roots:
            for root, dirs, files in os.walk(root_dir):
                skip_deleted_quarantine(root, dirs, root_dir)
                for fname in files:
                    if os.path.splitext(fname)[1].lower() in AUDIO_EXTENSIONS:
                        count += 1
        return count
