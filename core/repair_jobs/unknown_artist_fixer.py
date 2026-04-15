"""Unknown Artist Fixer Job — finds tracks tagged as 'Unknown Artist' and corrects metadata.

Resolves the correct artist/album/track metadata from file tags or metadata API,
re-tags the audio file, moves it to the correct folder, and updates the database.
"""

import os
import re
import shutil
import sys
import time

from core.metadata_service import get_client_for_source, get_primary_client, get_primary_source
from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair_job.unknown_artist_fixer")

_UNKNOWN_NAMES = {'unknown artist', 'unknown', ''}

# Sidecar extensions to move alongside audio files
_SIDECAR_EXTS = {'.lrc', '.jpg', '.jpeg', '.png', '.nfo', '.txt', '.cue'}


@register_job
class UnknownArtistFixerJob(RepairJob):
    job_id = 'unknown_artist_fixer'
    display_name = 'Fix Unknown Artists'
    description = 'Finds tracks tagged as "Unknown Artist" and corrects metadata, tags, and file paths'
    help_text = (
        'Scans your library for tracks filed under "Unknown Artist" — a common result of '
        'incomplete metadata during playlist pipeline downloads.\n\n'
        'For each affected track, the job resolves the correct artist, album, and track number by:\n'
        '1. Reading embedded file tags (if the file itself has correct metadata)\n'
        '2. Looking up the track by ID on your configured metadata source\n'
        '3. Searching by track title as a last resort\n\n'
        'When a match is found, the job can re-tag the file, move it to the correct folder, '
        'and update the database.\n\n'
        'Settings:\n'
        '- Dry Run: Preview changes without applying them (default: on)\n'
        '- Fix file tags: Write corrected metadata to audio file tags\n'
        '- Reorganize files: Move files to the correct folder structure'
    )
    icon = 'repair-icon-artist'
    default_enabled = False
    default_interval_hours = 168  # Weekly
    default_settings = {
        'dry_run': True,
        'fix_tags': True,
        'reorganize_files': True,
    }
    auto_fix = True

    def estimate_scope(self, context: JobContext) -> int:
        try:
            conn = context.db._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT COUNT(*) FROM tracks t
                    JOIN artists ar ON ar.id = t.artist_id
                    WHERE LOWER(TRIM(ar.name)) IN ('unknown artist', 'unknown', '')
                      AND t.file_path IS NOT NULL AND t.file_path != ''
                """)
                return cursor.fetchone()[0]
            finally:
                conn.close()
        except Exception:
            return 0

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()
        settings = self._get_settings(context)
        dry_run = settings.get('dry_run', True)
        fix_tags = settings.get('fix_tags', True)
        reorganize_files = settings.get('reorganize_files', True)

        mode_label = 'DRY RUN' if dry_run else 'LIVE'
        if context.report_progress:
            context.report_progress(phase=f'Scanning ({mode_label})...',
                                    log_line=f'Mode: {mode_label}', log_type='info')

        # Query all tracks under Unknown Artist
        conn = context.db._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT t.id, t.title, t.file_path, t.track_number, t.duration,
                       ar.id as artist_id, ar.name as artist_name,
                       al.id as album_id, al.title as album_title, al.year,
                       al.thumb_url as album_thumb,
                       t.spotify_track_id, t.itunes_track_id, t.deezer_track_id
                FROM tracks t
                JOIN artists ar ON ar.id = t.artist_id
                JOIN albums al ON al.id = t.album_id
                WHERE LOWER(TRIM(ar.name)) IN ('unknown artist', 'unknown', '')
                  AND t.file_path IS NOT NULL AND t.file_path != ''
                ORDER BY al.title, t.track_number
                LIMIT 500
            """)
            tracks = [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

        total = len(tracks)
        if total == 0:
            if context.report_progress:
                context.report_progress(phase='No Unknown Artist tracks found',
                                        log_line='No tracks to fix', log_type='success')
            return result

        if context.report_progress:
            context.report_progress(phase=f'Found {total} Unknown Artist tracks',
                                    total=total, log_line=f'Processing {total} tracks...',
                                    log_type='info')

        # Get file path templates for reorganization
        transfer = context.transfer_folder
        templates = {}
        if context.config_manager:
            templates = context.config_manager.get('file_organization.templates', {})
        album_template = templates.get('album_path', '$albumartist/$albumartist - $album/$track - $title')

        for i, track in enumerate(tracks):
            if context.check_stop():
                return result
            if i % 20 == 0 and context.wait_if_paused():
                return result

            result.scanned += 1
            track_id = track['id']
            title = track['title'] or ''
            file_path = track['file_path']

            # Resolve actual file on disk
            from core.repair_worker import _resolve_file_path
            resolved = _resolve_file_path(file_path, transfer)
            if not resolved or not os.path.exists(resolved):
                result.skipped += 1
                continue

            # Try to resolve correct metadata
            corrected = self._resolve_metadata(context, track, resolved)
            if not corrected:
                result.skipped += 1
                if context.report_progress:
                    context.report_progress(
                        scanned=i + 1, total=total,
                        log_line=f'Could not resolve: {title}', log_type='warning')
                continue

            # Compute expected file path
            expected_rel = None
            if reorganize_files and corrected.get('artist') and corrected.get('album'):
                from core.repair_jobs.library_reorganize import _build_path_from_template, _get_audio_quality
                quality = _get_audio_quality(resolved)
                tmpl_ctx = {
                    'artist': corrected['artist'],
                    'albumartist': corrected['artist'],
                    'album': corrected['album'],
                    'title': corrected.get('title', title),
                    'track_number': corrected.get('track_number', 1),
                    'disc_number': corrected.get('disc_number', 1),
                    'year': corrected.get('year', ''),
                    'quality': quality,
                    'albumtype': 'Album',
                }
                folder, fname_base = _build_path_from_template(album_template, tmpl_ctx)
                file_ext = os.path.splitext(resolved)[1]
                if quality and f'[{quality}]' not in fname_base:
                    fname_base = f"{fname_base} [{quality}]"
                expected_rel = os.path.join(folder, fname_base + file_ext)

            if dry_run:
                # Create finding for review
                desc_parts = [f'Artist: Unknown Artist → {corrected["artist"]}']
                if corrected.get('album'):
                    desc_parts.append(f'Album: {track.get("album_title", "?")} → {corrected["album"]}')
                if corrected.get('track_number'):
                    desc_parts.append(f'Track #: {track.get("track_number", "?")} → {corrected["track_number"]}')
                if expected_rel:
                    desc_parts.append(f'Path: → {expected_rel}')

                if context.create_finding:
                    context.create_finding(
                        job_id=self.job_id,
                        finding_type='unknown_artist',
                        severity='warning',
                        entity_type='track',
                        entity_id=str(track_id),
                        file_path=file_path,
                        title=f'{corrected["artist"]} - {corrected.get("title", title)}',
                        description='\n'.join(desc_parts),
                        details={
                            'track_id': track_id,
                            'artist_id': track['artist_id'],
                            'album_id': track['album_id'],
                            'current_artist': track['artist_name'],
                            'corrected_artist': corrected['artist'],
                            'corrected_album': corrected.get('album', ''),
                            'corrected_track_number': corrected.get('track_number'),
                            'corrected_year': corrected.get('year', ''),
                            'corrected_title': corrected.get('title', title),
                            'source': corrected.get('source', ''),
                            'confidence': corrected.get('confidence', 0),
                            'file_path': resolved,
                            'expected_path': expected_rel,
                            'album_thumb_url': corrected.get('image_url') or track.get('album_thumb'),
                            'cover_url': corrected.get('image_url', ''),
                        }
                    )
                    result.findings_created += 1
            else:
                # Live mode — apply fix
                try:
                    fixed = self._apply_fix(context, track, corrected, resolved,
                                            expected_rel, transfer, fix_tags, reorganize_files)
                    if fixed:
                        result.auto_fixed += 1
                    else:
                        result.errors += 1
                except Exception as e:
                    logger.error(f"Failed to fix track {track_id}: {e}")
                    result.errors += 1

            if context.report_progress:
                context.report_progress(
                    scanned=i + 1, total=total,
                    log_line=f'{"[Preview]" if dry_run else "[Fixed]"} {corrected["artist"]} - {corrected.get("title", title)}',
                    log_type='info' if dry_run else 'success')

        if context.report_progress:
            if dry_run:
                context.report_progress(
                    phase=f'Preview complete — {result.findings_created} fixable tracks',
                    log_line=f'Done: {result.findings_created} can be fixed, {result.skipped} unresolvable',
                    log_type='success')
            else:
                context.report_progress(
                    phase=f'Fixed {result.auto_fixed} tracks',
                    log_line=f'Done: {result.auto_fixed} fixed, {result.errors} errors, {result.skipped} skipped',
                    log_type='success')

        return result

    def _resolve_metadata(self, context, track, resolved_path):
        """Try to resolve correct metadata for an Unknown Artist track.
        Returns dict with artist, album, track_number, year, etc. or None."""

        title = track['title'] or ''

        # Priority 1: Read embedded file tags
        try:
            from core.tag_writer import read_file_tags
            tags = read_file_tags(resolved_path)
            tag_artist = tags.get('artist') or tags.get('album_artist')
            if tag_artist and tag_artist.strip().lower() not in _UNKNOWN_NAMES:
                return {
                    'artist': tag_artist.strip(),
                    'album': (tags.get('album') or '').strip() or track.get('album_title', ''),
                    'title': (tags.get('title') or '').strip() or title,
                    'track_number': tags.get('track_number') or track.get('track_number'),
                    'disc_number': tags.get('disc_number') or 1,
                    'year': (tags.get('year') or '').strip(),
                    'source': 'file_tags',
                    'confidence': 1.0,
                }
        except Exception as e:
            logger.debug(f"Failed to read tags from {resolved_path}: {e}")

        # Priority 2: Look up by source track ID using the appropriate client.
        # Try the primary source's ID first, then fall back to any available ID
        # with its matching client so we never pass a Deezer/iTunes ID to Spotify
        # (or vice-versa).
        _primary = get_primary_source()
        _id_candidates = []
        for _src in [_primary] + [s for s in ('spotify', 'deezer', 'itunes') if s != _primary]:
            _tid = track.get(f'{_src}_track_id')
            if _tid:
                _id_candidates.append((_src, _tid))
        source_id = None
        _lookup_client = None
        for _src, _tid in _id_candidates:
            _c = get_client_for_source(_src)
            if _c:
                source_id = _tid
                _lookup_client = _c
                break
        if source_id and _lookup_client:
            try:
                details = _lookup_client.get_track_details(str(source_id))
                if details and details.get('primary_artist'):
                    artist = details['primary_artist']
                    if artist.lower() not in _UNKNOWN_NAMES:
                        album = details.get('album', {})
                        album_name = album.get('name', '') if isinstance(album, dict) else str(album)
                        return {
                            'artist': artist,
                            'album': album_name,
                            'title': details.get('name', title),
                            'track_number': details.get('track_number'),
                            'disc_number': details.get('disc_number', 1),
                            'year': (album.get('release_date', '') or '')[:4] if isinstance(album, dict) else '',
                            'image_url': album.get('images', [{}])[0].get('url', '') if isinstance(album, dict) and album.get('images') else '',
                            'source': 'track_id_lookup',
                            'confidence': 0.95,
                        }
            except Exception as e:
                logger.debug(f"Track ID lookup failed for {source_id}: {e}")

        # Priority 3: Search by title using the configured primary metadata source
        _search_client = get_primary_client()
        if title and _search_client:
            try:
                results = _search_client.search_tracks(title, limit=5)
                if results:
                    # Score candidates
                    from difflib import SequenceMatcher
                    best = None
                    best_score = 0
                    for r in results:
                        name_sim = SequenceMatcher(None, title.lower(), r.name.lower()).ratio()
                        # Boost if album matches
                        album_name = r.album if hasattr(r, 'album') else ''
                        if album_name and track.get('album_title'):
                            album_sim = SequenceMatcher(None, track['album_title'].lower(), album_name.lower()).ratio()
                            name_sim = (name_sim * 0.7) + (album_sim * 0.3)
                        if name_sim > best_score:
                            best_score = name_sim
                            best = r

                    if best and best_score >= 0.7:
                        artist = best.artists[0] if best.artists else ''
                        if artist and artist.lower() not in _UNKNOWN_NAMES:
                            # Get full details for track_number
                            full_details = None
                            try:
                                full_details = _search_client.get_track_details(best.id)
                            except Exception:
                                pass
                            album_data = full_details.get('album', {}) if full_details else {}
                            return {
                                'artist': artist,
                                'album': best.album if hasattr(best, 'album') else '',
                                'title': best.name,
                                'track_number': full_details.get('track_number') if full_details else None,
                                'disc_number': full_details.get('disc_number', 1) if full_details else 1,
                                'year': (album_data.get('release_date', '') or '')[:4] if isinstance(album_data, dict) else '',
                                'image_url': getattr(best, 'image_url', '') or '',
                                'source': 'title_search',
                                'confidence': round(best_score, 3),
                            }
            except Exception as e:
                logger.debug(f"Title search failed for '{title}': {e}")
            # Rate limit courtesy
            if context.sleep_or_stop(0.2):
                return None

        return None

    def _apply_fix(self, context, track, corrected, resolved_path,
                   expected_rel, transfer, fix_tags, reorganize_files):
        """Apply the fix: re-tag file, move to correct path, update DB."""
        track_id = track['id']

        # Step 1: Write corrected tags to file
        if fix_tags:
            try:
                from core.tag_writer import write_tags_to_file
                db_data = {
                    'title': corrected.get('title', track['title']),
                    'artist_name': corrected['artist'],
                    'album_title': corrected.get('album', ''),
                    'year': corrected.get('year', ''),
                    'track_number': corrected.get('track_number'),
                    'disc_number': corrected.get('disc_number', 1),
                }
                tag_result = write_tags_to_file(
                    resolved_path, db_data,
                    embed_cover=True,
                    cover_url=corrected.get('image_url') or None
                )
                if tag_result.get('success'):
                    logger.info(f"Re-tagged: {corrected['artist']} - {corrected.get('title', track['title'])}")
                else:
                    logger.warning(f"Tag write failed for track {track_id}: {tag_result.get('error')}")
            except Exception as e:
                logger.error(f"Tag write error for track {track_id}: {e}")

        # Step 2: Move file to correct location
        final_path = resolved_path
        if reorganize_files and expected_rel:
            expected_abs = os.path.normpath(os.path.join(transfer, expected_rel))
            current_norm = os.path.normpath(resolved_path)

            if current_norm.lower() != expected_abs.lower():
                try:
                    os.makedirs(os.path.dirname(expected_abs), exist_ok=True)

                    # Handle case rename on case-insensitive FS
                    if sys.platform in ('win32', 'darwin') and os.path.exists(expected_abs):
                        tmp = expected_abs + '.tmp_rename'
                        shutil.move(current_norm, tmp)
                        shutil.move(tmp, expected_abs)
                    else:
                        shutil.move(current_norm, expected_abs)

                    final_path = expected_abs
                    logger.info(f"Moved: {os.path.basename(current_norm)} → {expected_rel}")

                    # Move sidecars
                    src_dir = os.path.dirname(current_norm)
                    dst_dir = os.path.dirname(expected_abs)
                    src_stem = os.path.splitext(os.path.basename(current_norm))[0]
                    dst_stem = os.path.splitext(os.path.basename(expected_abs))[0]
                    for ext in _SIDECAR_EXTS:
                        sidecar_src = os.path.join(src_dir, src_stem + ext)
                        if os.path.isfile(sidecar_src):
                            sidecar_dst = os.path.join(dst_dir, dst_stem + ext)
                            if not os.path.exists(sidecar_dst):
                                try:
                                    shutil.move(sidecar_src, sidecar_dst)
                                except Exception:
                                    pass

                    # Also move cover.jpg from old album folder
                    cover_src = os.path.join(src_dir, 'cover.jpg')
                    cover_dst = os.path.join(dst_dir, 'cover.jpg')
                    if os.path.isfile(cover_src) and not os.path.exists(cover_dst):
                        try:
                            shutil.copy2(cover_src, cover_dst)
                        except Exception:
                            pass

                    # Clean up empty directories
                    parent = os.path.dirname(current_norm)
                    transfer_norm = os.path.normpath(transfer)
                    for _ in range(5):
                        if (parent and os.path.isdir(parent)
                                and os.path.normpath(parent) != transfer_norm
                                and not os.listdir(parent)):
                            os.rmdir(parent)
                            parent = os.path.dirname(parent)
                        else:
                            break

                except Exception as e:
                    logger.error(f"File move failed for track {track_id}: {e}")
                    # Continue with DB update even if move failed

        # Step 3: Update database
        try:
            conn = context.db._get_connection()
            try:
                cursor = conn.cursor()

                # Find or create the correct artist
                corrected_artist = corrected['artist']
                cursor.execute("SELECT id FROM artists WHERE LOWER(name) = LOWER(?)",
                               (corrected_artist,))
                artist_row = cursor.fetchone()
                if artist_row:
                    new_artist_id = artist_row[0]
                else:
                    cursor.execute("INSERT INTO artists (name) VALUES (?)", (corrected_artist,))
                    new_artist_id = cursor.lastrowid

                # Update track's artist_id and file_path
                cursor.execute("""
                    UPDATE tracks SET artist_id = ?, file_path = ?
                    WHERE id = ?
                """, (new_artist_id, final_path, track_id))

                # Update track_number if we have it
                if corrected.get('track_number'):
                    cursor.execute("UPDATE tracks SET track_number = ? WHERE id = ?",
                                   (corrected['track_number'], track_id))

                # Update album title if corrected
                if corrected.get('album') and corrected['album'] != track.get('album_title'):
                    cursor.execute("UPDATE albums SET title = ? WHERE id = ?",
                                   (corrected['album'], track['album_id']))

                # Update album year if we have it
                if corrected.get('year') and corrected['year'].isdigit():
                    cursor.execute("UPDATE albums SET year = ? WHERE id = ?",
                                   (int(corrected['year']), track['album_id']))

                # Update album artist_id to match
                cursor.execute("UPDATE albums SET artist_id = ? WHERE id = ?",
                               (new_artist_id, track['album_id']))

                conn.commit()
                logger.info(f"DB updated: track {track_id} → artist '{corrected_artist}'")
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"DB update failed for track {track_id}: {e}")
            return False

        return True

    def _get_settings(self, context):
        if not context.config_manager:
            return self.default_settings.copy()
        cfg = context.config_manager.get(f'repair.jobs.{self.job_id}.settings', {})
        merged = self.default_settings.copy()
        if isinstance(cfg, dict):
            merged.update(cfg)
        return merged

    def _get_setting(self, context, key, default=None):
        return self._get_settings(context).get(key, default)
