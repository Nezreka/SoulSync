"""Album Tag Consistency Job — finds albums where tracks have inconsistent tags.

When tracks in the same album have different artist names, album names, or
MusicBrainz release IDs, media servers like Navidrome split them into separate
albums. This job detects these inconsistencies and offers to fix them by
normalizing all tracks to the canonical (majority) value.
"""

import json
import os
from collections import Counter

from mutagen import File as MutagenFile
from mutagen.id3 import ID3
from mutagen.flac import FLAC
from mutagen.oggvorbis import OggVorbis
from mutagen.mp4 import MP4

from core.repair_jobs import register_job
from core.repair_jobs.base import get_scope_artist, JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair_job.album_tag_consistency")


def _read_tag(audio, tag_name):
    """Read a tag value from a Mutagen file object, handling format differences."""
    if audio is None:
        return None
    try:
        if isinstance(audio.tags, ID3):
            # MP3
            if tag_name == 'album':
                frame = audio.tags.get('TALB')
                return str(frame) if frame else None
            elif tag_name == 'artist':
                frame = audio.tags.get('TPE1')
                return str(frame) if frame else None
            elif tag_name == 'albumartist':
                frame = audio.tags.get('TPE2')
                return str(frame) if frame else None
            elif tag_name == 'musicbrainz_albumid':
                for key in audio.tags:
                    if key.startswith('TXXX:') and 'MusicBrainz Album Id' in key:
                        return str(audio.tags[key])
                return None
        elif isinstance(audio, (FLAC, OggVorbis)):
            vals = audio.get(tag_name.upper(), [])
            return vals[0] if vals else None
        elif isinstance(audio, MP4):
            tag_map = {
                'album': '\xa9alb',
                'artist': '\xa9ART',
                'albumartist': 'aART',
            }
            key = tag_map.get(tag_name)
            if key:
                vals = audio.get(key, [])
                return vals[0] if vals else None
            if tag_name == 'musicbrainz_albumid':
                vals = audio.get('----:com.apple.iTunes:MusicBrainz Album Id', [])
                if vals:
                    return vals[0].decode('utf-8') if isinstance(vals[0], bytes) else str(vals[0])
                return None
    except Exception as e:
        logger.debug("read tag value failed: %s", e)
    return None


def _detect_inconsistencies(tag_data, check_album, check_artist, check_mbid):
    """Majority-vote inconsistency detection over per-file tag snapshots."""
    inconsistencies = []
    checks = (
        ('album', 'album_tag', check_album),
        ('albumartist', 'albumartist_tag', check_artist),
        ('musicbrainz_albumid', 'mbid_tag', check_mbid),
    )
    for field, key, enabled in checks:
        if not enabled:
            continue
        values = [t[key] for t in tag_data if t[key]]
        if values and len(set(values)) > 1:
            majority = Counter(values).most_common(1)[0][0]
            outliers = [t for t in tag_data if t[key] and t[key] != majority]
            inconsistencies.append({
                'field': field,
                'canonical': majority,
                'variants': list(set(values)),
                'outlier_count': len(outliers),
            })
    return inconsistencies


def _write_tag(audio, tag_name, value):
    """Write a tag value to a Mutagen file object, handling format differences."""
    if audio is None or value is None:
        return False
    try:
        if isinstance(audio.tags, ID3):
            from mutagen.id3 import TALB, TPE1, TPE2, TXXX
            if tag_name == 'album':
                audio.tags.delall('TALB')
                audio.tags.add(TALB(encoding=3, text=[value]))
            elif tag_name == 'artist':
                audio.tags.delall('TPE1')
                audio.tags.add(TPE1(encoding=3, text=[value]))
            elif tag_name == 'albumartist':
                audio.tags.delall('TPE2')
                audio.tags.add(TPE2(encoding=3, text=[value]))
            elif tag_name == 'musicbrainz_albumid':
                # Remove existing
                to_remove = [k for k in audio.tags if k.startswith('TXXX:') and 'MusicBrainz Album Id' in k]
                for k in to_remove:
                    del audio.tags[k]
                audio.tags.add(TXXX(encoding=3, desc='MusicBrainz Album Id', text=[value]))
            return True
        elif isinstance(audio, (FLAC, OggVorbis)):
            audio[tag_name.upper()] = [value]
            return True
        elif isinstance(audio, MP4):
            tag_map = {
                'album': '\xa9alb',
                'artist': '\xa9ART',
                'albumartist': 'aART',
            }
            key = tag_map.get(tag_name)
            if key:
                audio[key] = [value]
                return True
            if tag_name == 'musicbrainz_albumid':
                from mutagen.mp4 import MP4FreeForm
                audio['----:com.apple.iTunes:MusicBrainz Album Id'] = [
                    MP4FreeForm(value.encode('utf-8'))
                ]
                return True
    except Exception as e:
        logger.debug(f"Failed to write tag {tag_name}: {e}")
    return False


@register_job
class AlbumTagConsistencyJob(RepairJob):
    job_id = 'album_tag_consistency'
    supports_artist_scope = True
    display_name = 'Album Tag Consistency'
    description = 'Finds albums where tracks have inconsistent tags causing media server splits'
    help_text = (
        'Scans your library for albums where tracks have mismatched metadata — '
        'different album names, artist names, or MusicBrainz release IDs across '
        'tracks that belong to the same album.\n\n'
        'These inconsistencies cause media servers like Navidrome to split one album '
        'into multiple entries (e.g. "Simulation Theory" and "Simulation Theory (Super Deluxe)").\n\n'
        'The fix normalizes all tracks in the album to the most common (majority) value, '
        'then writes the corrected tags to the actual audio files.\n\n'
        'Settings:\n'
        '- Check album name: Detect inconsistent album title tags\n'
        '- Check album artist: Detect inconsistent album artist tags\n'
        '- Check MB release ID: Detect inconsistent MusicBrainz Album IDs'
    )
    icon = 'repair-icon-consistency'
    default_enabled = False
    default_interval_hours = 168  # Weekly
    default_settings = {
        'check_album_name': True,
        'check_album_artist': True,
        'check_mb_release_id': True,
    }
    auto_fix = False

    def _get_settings(self, context: JobContext) -> dict:
        """Get job settings from config, merged with defaults."""
        cfg = context.config_manager.get(f'repair.jobs.{self.job_id}.settings', {})
        merged = dict(self.default_settings)
        if isinstance(cfg, dict):
            merged.update(cfg)
        return merged

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()
        settings = self._get_settings(context)
        check_album = settings.get('check_album_name', True)
        check_artist = settings.get('check_album_artist', True)
        check_mbid = settings.get('check_mb_release_id', True)

        if not any([check_album, check_artist, check_mbid]):
            return result

        scope_artist = get_scope_artist(context)
        scope_clause = "AND lower(ar.name) = lower(?)" if scope_artist else ""
        scope_params = (scope_artist,) if scope_artist else ()

        try:
            conn = context.db._get_connection()
            cursor = conn.cursor()

            # Get all albums with 2+ tracks that have file paths
            cursor.execute(f"""
                SELECT al.id, al.title, ar.name as artist_name,
                       COUNT(t.id) as track_count
                FROM albums al
                JOIN artists ar ON ar.id = al.artist_id
                JOIN tracks t ON t.album_id = al.id
                WHERE t.file_path IS NOT NULL AND t.file_path != ''
                  {scope_clause}
                GROUP BY al.id
                HAVING COUNT(t.id) >= 2
                ORDER BY ar.name, al.title
            """, scope_params)
            albums = cursor.fetchall()
            total = len(albums)

            if context.report_progress:
                context.report_progress(phase=f'Scanning {total} albums for tag consistency...', total=total)

            for idx, album_row in enumerate(albums):
                if context.check_stop():
                    break
                if idx % 10 == 0 and context.wait_if_paused():
                    break

                album_id = album_row['id']
                album_title = album_row['title']
                artist_name = album_row['artist_name']
                result.scanned += 1

                if context.report_progress and idx % 20 == 0:
                    context.report_progress(
                        scanned=idx + 1, total=total,
                        phase=f'Scanning {idx + 1} / {total}',
                        log_line=f'{artist_name} — {album_title}',
                        log_type='info'
                    )

                # Get all tracks in this album with file paths
                cursor.execute("""
                    SELECT id, title, file_path FROM tracks
                    WHERE album_id = ? AND file_path IS NOT NULL AND file_path != ''
                """, (album_id,))
                tracks = cursor.fetchall()

                if len(tracks) < 2:
                    continue

                # Read tags from each file
                tag_data = []
                for track in tracks:
                    file_path = track['file_path']
                    # Resolve path
                    resolved = self._resolve_path(file_path, context)
                    if not resolved or not os.path.exists(resolved):
                        continue

                    try:
                        audio = MutagenFile(resolved, easy=False)
                        if audio is None:
                            continue
                        tag_data.append({
                            'track_id': track['id'],
                            'track_title': track['title'],
                            'file_path': file_path,
                            'resolved_path': resolved,
                            'album_tag': _read_tag(audio, 'album'),
                            'albumartist_tag': _read_tag(audio, 'albumartist'),
                            'mbid_tag': _read_tag(audio, 'musicbrainz_albumid'),
                        })
                    except Exception:
                        continue

                if len(tag_data) < 2:
                    continue

                inconsistencies = _detect_inconsistencies(
                    tag_data, check_album, check_artist, check_mbid)

                if inconsistencies:
                    fields_affected = ', '.join(i['field'] for i in inconsistencies)
                    total_outliers = sum(i['outlier_count'] for i in inconsistencies)

                    # Build description with specifics
                    desc_parts = []
                    for inc in inconsistencies:
                        variants_str = ' vs '.join(f'"{v}"' for v in inc['variants'][:3])
                        desc_parts.append(f"{inc['field']}: {variants_str}")

                    inserted = context.create_finding(
                        job_id=self.job_id,
                        finding_type='album_tag_inconsistency',
                        severity='warning',
                        entity_type='album',
                        entity_id=str(album_id),
                        file_path=None,
                        title=f'Inconsistent tags: {album_title} by {artist_name}',
                        description=f'{total_outliers} track(s) have mismatched {fields_affected}. ' + '; '.join(desc_parts),
                        details={
                            'album_id': album_id,
                            'album_title': album_title,
                            'artist_name': artist_name,
                            'inconsistencies': inconsistencies,
                            'track_count': len(tag_data),
                            'tracks': [{'id': t['track_id'], 'title': t['track_title'],
                                        'file_path': t['file_path']} for t in tag_data],
                        }
                    )
                    if inserted:
                        result.findings_created += 1
                    else:
                        result.findings_skipped_dedup += 1

                    if context.report_progress:
                        context.report_progress(
                            log_line=f'Found: {album_title} — {fields_affected}',
                            log_type='warning'
                        )

            conn.close()

        except Exception as e:
            logger.error(f"Album tag consistency scan error: {e}")
            result.errors += 1

        self._scan_native_albums(context, result, check_album, check_artist, check_mbid)
        return result

    def _scan_native_albums(self, context: JobContext, result: JobResult,
                            check_album: bool, check_artist: bool, check_mbid: bool):
        """Native Library-v2 coverage: releases without a legacy backref, read
        via the shared V2 file-subject enumerator (grouped per album)."""
        try:
            from core.library2.maintenance_sync import v2_uncovered_file_subjects
            from core.library2.paths import resolve_lib2_path

            albums = {}
            for subject in v2_uncovered_file_subjects(
                context.db, context.config_manager,
            ):
                albums.setdefault(subject['album_id'], []).append(subject)
        except Exception as e:
            logger.warning("V2 subject enumeration failed: %s", e)
            result.errors += 1
            return

        scope_artist = get_scope_artist(context)
        for album_id, subjects in albums.items():
            if context.check_stop() or context.wait_if_paused():
                return
            if len(subjects) < 2:
                continue
            if scope_artist and (subjects[0].get('artist_name') or '').lower() != scope_artist.lower():
                continue
            result.scanned += 1

            tag_data = []
            for subject in subjects:
                raw = str(subject['path'])
                resolved = raw if os.path.exists(raw) else resolve_lib2_path(
                    raw, config_manager=context.config_manager)
                if not resolved or not os.path.exists(resolved):
                    continue
                try:
                    audio = MutagenFile(resolved, easy=False)
                    if audio is None:
                        continue
                    tag_data.append({
                        'track_id': f"lib2:{subject['track_id']}",
                        'track_title': subject['title'],
                        'file_path': raw,
                        'resolved_path': resolved,
                        'album_tag': _read_tag(audio, 'album'),
                        'albumartist_tag': _read_tag(audio, 'albumartist'),
                        'mbid_tag': _read_tag(audio, 'musicbrainz_albumid'),
                    })
                except Exception:
                    continue

            if len(tag_data) < 2:
                continue

            inconsistencies = _detect_inconsistencies(
                tag_data, check_album, check_artist, check_mbid)
            if not inconsistencies or not context.create_finding:
                continue

            album_title = subjects[0].get('album_title')
            artist_name = subjects[0].get('artist_name')
            fields_affected = ', '.join(i['field'] for i in inconsistencies)
            total_outliers = sum(i['outlier_count'] for i in inconsistencies)
            desc_parts = []
            for inc in inconsistencies:
                variants_str = ' vs '.join(f'"{v}"' for v in inc['variants'][:3])
                desc_parts.append(f"{inc['field']}: {variants_str}")

            inserted = context.create_finding(
                job_id=self.job_id,
                finding_type='album_tag_inconsistency',
                severity='warning',
                entity_type='album',
                entity_id=f"lib2:{album_id}",
                file_path=None,
                title=f'Inconsistent tags: {album_title} by {artist_name}',
                description=f'{total_outliers} track(s) have mismatched {fields_affected}. ' + '; '.join(desc_parts),
                details={
                    'album_id': f"lib2:{album_id}",
                    'album_title': album_title,
                    'artist_name': artist_name,
                    'inconsistencies': inconsistencies,
                    'track_count': len(tag_data),
                    'tracks': [{'id': t['track_id'], 'title': t['track_title'],
                                'file_path': t['file_path']} for t in tag_data],
                    'library_v2_native': True,
                    'library_v2': {
                        'artist_id': subjects[0].get('artist_id'),
                        'album_id': album_id,
                        'track_id': None,
                        'file_id': None,
                        'artist_ids': [subjects[0]['artist_id']] if subjects[0].get('artist_id') else [],
                        'album_ids': [album_id],
                        'track_ids': sorted({s['track_id'] for s in subjects}),
                        'file_ids': sorted({s['file_id'] for s in subjects}),
                    },
                },
            )
            if inserted:
                result.findings_created += 1
            else:
                result.findings_skipped_dedup += 1

    def _resolve_path(self, file_path, context):
        """Resolve a DB file path to an actual filesystem path."""
        if not file_path:
            return None
        # Try as-is first
        if os.path.exists(file_path):
            return file_path
        # Try relative to transfer folder
        if context.transfer_folder:
            joined = os.path.join(context.transfer_folder, file_path)
            if os.path.exists(joined):
                return joined
        # Try with download path
        download_path = context.config_manager.get('soulseek.download_path', '') if context.config_manager else ''
        if download_path:
            joined = os.path.join(download_path, file_path)
            if os.path.exists(joined):
                return joined
        return None
