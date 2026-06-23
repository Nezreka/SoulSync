"""Quality Upgrade Scanner Job — flags library tracks below the user's profile.

Walks the music folders ON DISK (transfer + download + every configured library
path) exactly like the Orphan / Fake-Lossless detectors — those reliably "see"
files because they os.walk real directories instead of trying to resolve the
DB's stored (often relative) paths. For each audio file it probes the ACTUAL
measured audio quality (bit depth / sample rate / bitrate via the same
`probe_audio_quality` the download import guard uses) and checks it against the
user's v3 ranked targets with `quality_meets_profile` (strict — fallback
ignored, that's a download-time concession, not a definition of "good enough").

Every file that satisfies none of the targets becomes a finding the user can:
  - 'redownload': add the track to the wishlist and delete the low-quality file
  - 'delete': remove the low-quality file (+ DB row when known)
  - 'ignore': dismiss the finding (handled in the UI via the dismiss endpoint)

Each walked file is matched back to its DB track (by path suffix) so the finding
carries the real title/artist/album + track id; when no DB row matches, the
file's own tags are used and the finding is filed as a loose 'file'.
"""

import os

from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair_job.quality_upgrade")

AUDIO_EXTENSIONS = {'.mp3', '.flac', '.ogg', '.opus', '.m4a', '.aac', '.wav', '.wma', '.aiff', '.aif'}


@register_job
class QualityUpgradeScannerJob(RepairJob):
    job_id = 'quality_upgrade_scanner'
    display_name = 'Quality Check (flag only — you decide per finding)'
    description = 'Flags library tracks below your quality profile; you choose re-download / delete / ignore per finding'
    help_text = (
        'FLAG-ONLY quality job. Walks your music library folder on disk (so it also '
        'catches loose files not in the DB) and checks every track against your v3 '
        'quality profile — then just FLAGS what is below profile. Unlike the active '
        '"Quality Upgrade Finder", it does NOT search a replacement; you decide what '
        'to do per finding: Re-download / Delete / Ignore.\n\n'
        'Two-stage check (same as the download/import pipeline):\n'
        '1. Real-audio guard (optional, ffmpeg) — decodes the file (truncation + '
        'silence detection) to catch broken/incomplete audio the header hides.\n'
        '2. Quality gate — measured bit depth / sample rate / bitrate vs your '
        'profile targets.\n\n'
        'Settings:\n'
        '- Deep audio verify (default OFF): run the ffmpeg decode guard. Off = fast '
        'header-only quality pass (milliseconds/track). On = full decode '
        '(seconds/track, CPU-heavy) but catches broken/silent audio.\n'
        '- library_tracks_only (default off): only check files matched to a '
        'library DB track (skip loose/orphan files).\n\n'
        'The scan only reports — it never deletes or re-downloads on its own. '
        'Use the sibling "Quality Upgrade Finder" instead if you want it to actively '
        'find and queue a better version for you.'
    )
    icon = 'repair-icon-lossless'
    default_enabled = False
    default_interval_hours = 168
    # library_tracks_only: when ON, only check files that match a library DB
    # track (skips loose/orphan files). Default OFF — the scan checks EVERY
    # audio file in the Music Library output folder, which is what users expect
    # ("check my library folder"). DB matching after a reset is unreliable and
    # would wrongly skip everything. Turn ON to ignore non-DB files.
    #
    # deep_audio_verify default OFF: the ffmpeg decode is the CPU-heavy step. Most
    # users want the fast header-only quality pass; turn it on for a deep scan that
    # also catches broken/silent audio. (Matches the download pipeline's default.)
    default_settings = {'library_tracks_only': False, 'deep_audio_verify': False, 'require_top_target': False}
    setting_options = {'library_tracks_only': [True, False],
                       'deep_audio_verify': [True, False],
                       'require_top_target': [True, False]}
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
        # Same real-file AudioGuard the download/import pipeline runs: ffmpeg
        # DECODES the file (astats + silencedetect) to catch truncated or
        # mostly-silent audio the header can't reveal.
        from core.imports.silence import detect_broken_audio

        # --- Collect the music folders to walk (real dirs, abspath'd) ---
        base_dirs = self._collect_music_dirs(context)
        if not base_dirs:
            logger.warning(
                "[QualityScan] No existing music folder to walk (transfer=%r, cwd=%r). "
                "Set soulseek.transfer_path to the real mount or add your library under "
                "Settings → Library → Music Paths.",
                context.transfer_folder, os.getcwd())
            return result
        logger.info("[QualityScan] Walking %d folder(s): %r", len(base_dirs), base_dirs)

        # --- Gather audio files (dedup by real path) ---
        audio_files = []
        seen = set()
        for base in base_dirs:
            for root, _dirs, files in os.walk(base):
                if context.check_stop():
                    return result
                for fname in files:
                    if os.path.splitext(fname)[1].lower() in AUDIO_EXTENSIONS:
                        fpath = os.path.join(root, fname)
                        rp = os.path.realpath(fpath)
                        if rp in seen:
                            continue
                        seen.add(rp)
                        audio_files.append(fpath)

        total = len(audio_files)
        logger.info("[QualityScan] Found %d audio file(s) to check", total)
        if context.report_progress:
            context.report_progress(phase=f'Checking {total} files...', total=total)
        if context.update_progress:
            context.update_progress(0, total)

        # --- DB suffix index so a walked file maps back to its track row ---
        db_index = self._build_db_suffix_index(context)
        # Only check files that are part of the LIBRARY (have a DB track row).
        # The transfer/download folders also hold pre-import leftovers (e.g.
        # residue after a DB reset) — those are orphans, not library tracks, and
        # belong to the Orphan File Detector, not a quality upgrade scan. Default
        # ON so the scan reflects the user's actual library, not download junk.
        _settings = self._get_settings(context)
        library_only = _settings.get('library_tracks_only', False)
        # Deep verify = run the ffmpeg AudioGuard (real decode) per file, exactly
        # like the download pipeline. Slower than a header read (seconds vs ms) but
        # it verifies the REAL audio, not just the metadata. OFF by default (the
        # decode is the CPU-heavy step); turn on for a deep scan.
        deep_verify = _settings.get('deep_audio_verify', False)
        # require_top_target: flag files that meet a lower target but not the
        # highest-priority one (e.g. 16-bit FLAC when 24-bit is preferred).
        require_top = _settings.get('require_top_target', False)
        check_targets = targets[:1] if require_top and len(targets) > 1 else targets

        probe_failed = 0
        not_in_library = 0
        for i, fpath in enumerate(audio_files):
            if context.check_stop():
                return result
            if i % 20 == 0 and context.wait_if_paused():
                return result

            fname = os.path.basename(fpath)

            # Map to a DB track up front (cheap suffix lookup). When scoping to
            # the library, skip anything with no DB row BEFORE probing — no point
            # reading hundreds of orphan files.
            meta = self._match_db(fpath, db_index)
            if library_only and meta is None:
                not_in_library += 1
                result.skipped += 1
                continue
            if meta is None:
                meta = self._read_file_tags(fpath)

            result.scanned += 1
            if context.report_progress and i % 25 == 0:
                context.report_progress(
                    scanned=i + 1, total=total,
                    phase=f'Checking {i + 1} / {total}',
                    log_line=f'Checking: {fname}',
                    log_type='info',
                )

            # === Real-file verification — the SAME two stages the download /
            # import pipeline runs on every file ===
            #   1) AudioGuard: ffmpeg DECODES the audio (astats / silencedetect)
            #      to catch truncated or mostly-silent files the header hides.
            #   2) Quality gate: measured quality (mutagen) vs the ranked profile.
            try:
                broken_reason = detect_broken_audio(fpath) if deep_verify else None
            except Exception as e:
                logger.debug("AudioGuard failed for %s: %s", fname, e)
                broken_reason = None

            try:
                aq = probe_audio_quality(fpath)
            except Exception as e:
                logger.debug("Probe failed for %s: %s", fname, e)
                aq = None

            if broken_reason:
                issue = 'broken_audio'
                current_label = aq.label() if aq is not None else 'unknown'
            elif aq is None:
                # Header unreadable → can't judge quality; leave it unflagged.
                probe_failed += 1
                result.skipped += 1
                continue
            elif not quality_meets_profile(aq, check_targets):
                issue = 'below_profile'
                current_label = aq.label()
            else:
                # Decodes fully AND meets the profile → genuinely good.
                if context.update_progress and (i + 1) % 25 == 0:
                    context.update_progress(i + 1, total)
                continue

            # Build the finding (broken audio OR below profile).
            target_labels = [t.label for t in targets]
            disp_title = meta.get('title') or os.path.splitext(fname)[0]
            disp_artist = meta.get('artist') or 'Unknown'
            if issue == 'broken_audio':
                _title = f'Broken/incomplete audio: {disp_title}'
                _desc = (f'"{disp_title}" by {disp_artist} failed real-audio '
                         f'verification (ffmpeg): {broken_reason}')
                _severity = 'warning'
            else:
                _pref = targets[0].label if require_top and len(targets) > 1 else None
                _title = f'{"Upgradeable" if _pref else "Below quality"}: {disp_title} ({current_label})'
                _desc = (f'"{disp_title}" by {disp_artist} is {current_label}'
                         + (f', below your preferred quality ({_pref}).' if _pref else
                            f', which does not meet your quality profile '
                            f'({", ".join(target_labels[:3])}'
                            f'{"…" if len(target_labels) > 3 else ""}).'))
                _severity = 'info'

            if context.report_progress:
                context.report_progress(log_line=_title, log_type='error')
            if context.create_finding:
                inserted = context.create_finding(
                    job_id=self.job_id,
                    finding_type='quality_upgrade',
                    severity=_severity,
                    entity_type='track' if meta.get('track_id') else 'file',
                    entity_id=str(meta['track_id']) if meta.get('track_id') else None,
                    file_path=fpath,
                    title=_title,
                    description=_desc,
                    details={
                        'quality_issue': issue,
                        'broken_audio_reason': broken_reason or '',
                        'current_quality': current_label,
                        'current_format': aq.format if aq is not None else '',
                        'current_bitrate': aq.bitrate if aq is not None else None,
                        'current_sample_rate': aq.sample_rate if aq is not None else None,
                        'current_bit_depth': aq.bit_depth if aq is not None else None,
                        'target_qualities': target_labels,
                        'expected_title': disp_title,
                        'expected_artist': disp_artist,
                        'album_title': meta.get('album', ''),
                        'track_number': meta.get('track_number'),
                        'album_thumb_url': meta.get('album_thumb_url'),
                        'artist_thumb_url': meta.get('artist_thumb_url'),
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
            logger.warning("[QualityScan] %d/%d files could not be probed (unreadable)",
                           probe_failed, total)
        if not_in_library:
            logger.info(
                "[QualityScan] %d/%d files skipped — not in the library DB (orphan "
                "leftovers in transfer/downloads; disable 'library_tracks_only' to "
                "include them)", not_in_library, total)
        logger.info("Quality upgrade scan: %d checked, %d below profile, %d skipped",
                    result.scanned, result.findings_created, result.skipped)
        return result

    def _get_settings(self, context: JobContext) -> dict:
        merged = dict(self.default_settings)
        if context.config_manager:
            try:
                cfg = context.config_manager.get(f'repair.jobs.{self.job_id}.settings', {})
                if isinstance(cfg, dict):
                    merged.update(cfg)
            except Exception as e:
                logger.debug("settings read failed: %s", e)
        return merged

    def _collect_music_dirs(self, context: JobContext) -> list:
        """The music-library directories to walk, as absolute paths (dedup).

        Only the user's MUSIC LIBRARY is scanned — that's the "Output Folder
        (Music Library)" setting (soulseek.transfer_path) plus any custom
        library paths (library.music_paths, for media-server setups). The
        download/staging folders are deliberately NOT walked: they hold raw,
        pre-import downloads and leftovers, not the finished library, and the
        user expects quality checks to run on their library only. Whatever
        custom path the user configured for the output folder is respected,
        because it's read live from config here.
        """
        cm = context.config_manager
        raw = [context.transfer_folder]
        if cm:
            try:
                raw.append(cm.get('soulseek.transfer_path', './Transfer'))
                mp = cm.get('library.music_paths', []) or []
                if isinstance(mp, list):
                    raw.extend([p for p in mp if isinstance(p, str) and p.strip()])
            except Exception as e:
                logger.debug("music dir config read failed: %s", e)
        out, seen = [], set()
        for d in raw:
            if not d:
                continue
            ad = os.path.abspath(d)
            if ad in seen:
                continue
            seen.add(ad)
            if os.path.isdir(ad):
                out.append(ad)
        return out

    def _build_db_suffix_index(self, context: JobContext) -> dict:
        """Map normalized path suffixes (last 1-3 components, lowercased) →
        track metadata, so a walked absolute file can be matched to its DB row
        even when the DB stores a different (relative) path prefix."""
        index = {}
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
                fp = (row[3] or '').replace('\\', '/')
                if not fp:
                    continue
                parts = fp.split('/')
                meta = {
                    'track_id': row[0],
                    'title': row[1] or '',
                    'artist': row[2] or '',
                    'track_number': row[4],
                    'album': row[5] or '',
                    'album_thumb_url': row[6] or None,
                    'artist_thumb_url': row[7] or None,
                }
                for depth in range(1, min(4, len(parts) + 1)):
                    suffix = '/'.join(parts[-depth:]).lower()
                    index.setdefault(suffix, meta)
        except Exception as e:
            logger.error("Error building DB suffix index: %s", e)
        finally:
            if conn:
                conn.close()
        return index

    def _match_db(self, fpath: str, db_index: dict):
        """Match a walked file to a DB track via path suffix. Returns the track
        meta dict, or None when the file isn't part of the library."""
        parts = fpath.replace('\\', '/').split('/')
        for depth in range(min(3, len(parts)), 0, -1):
            suffix = '/'.join(parts[-depth:]).lower()
            hit = db_index.get(suffix)
            if hit:
                return hit
        return None

    def _read_file_tags(self, fpath: str) -> dict:
        """Read title/artist/album from the file's own tags (for loose files
        when library_tracks_only is off)."""
        meta = {'track_id': None}
        try:
            from mutagen import File as MutagenFile
            audio = MutagenFile(fpath, easy=True)
            if audio:
                meta['title'] = (audio.get('title') or [None])[0] or ''
                meta['artist'] = (audio.get('artist') or audio.get('albumartist') or [None])[0] or ''
                meta['album'] = (audio.get('album') or [None])[0] or ''
        except Exception as e:
            logger.debug("tag read failed for %s: %s", os.path.basename(fpath), e)
        return meta

    def estimate_scope(self, context: JobContext) -> int:
        count = 0
        for base in self._collect_music_dirs(context):
            for _root, _dirs, files in os.walk(base):
                for fname in files:
                    if os.path.splitext(fname)[1].lower() in AUDIO_EXTENSIONS:
                        count += 1
        return count
