"""Album Tag Consistency Job — finds albums where tracks have inconsistent tags.

When tracks in the same album have different artist names, album names, or
MusicBrainz release IDs, media servers like Navidrome split them into separate
albums. This job detects these inconsistencies and offers to fix them by
normalizing all tracks to the canonical (majority) value.
"""

import json
import os
import re
from collections import Counter

from mutagen import File as MutagenFile
from mutagen.id3 import ID3
from mutagen.flac import FLAC
from mutagen.oggvorbis import OggVorbis
from mutagen.mp4 import MP4

from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
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


MISSING = '(missing)'

_CHECK_FIELDS = (
    ('check_album_name', 'album', 'album_tag'),
    ('check_album_artist', 'albumartist', 'albumartist_tag'),
    ('check_mb_release_id', 'musicbrainz_albumid', 'mbid_tag'),
)


def split_group_key(artist_name, album_title):
    """the key two server-split rows of one album share.

    case, whitespace and punctuation are folded; edition qualifiers are NOT.
    "Album (Deluxe)" and "Album" are different releases and must stay apart,
    or the fix would stamp one release id across two real albums."""
    def fold(value):
        value = (value or '').casefold()
        value = re.sub(r"[^\w\s]", ' ', value)
        return ' '.join(value.split())
    return fold(artist_name), fold(album_title)


def find_inconsistencies(tag_data, settings):
    """the fields whose values disagree across tag_data, majority first.

    a track missing a tag the others carry is a variant, not a pass: navidrome
    keys an album on album + album artist + musicbrainz release id, so one
    file without the id splits exactly like one with the wrong id. a field
    nobody has is left alone (nothing to normalize to)."""
    inconsistencies = []
    for setting_key, field, tag_key in _CHECK_FIELDS:
        if not settings.get(setting_key, True):
            continue
        present = [t[tag_key] for t in tag_data if t.get(tag_key)]
        if not present:
            continue
        values = [t[tag_key] or MISSING for t in tag_data]
        if len(set(values)) <= 1:
            continue
        # majority among the tracks that HAVE a value; a missing tag never wins
        majority = Counter(present).most_common(1)[0][0]
        outliers = [t for t in tag_data if (t.get(tag_key) or MISSING) != majority]
        inconsistencies.append({
            'field': field,
            'canonical': majority,
            'variants': sorted(set(values), key=lambda v: (v == MISSING, v)),
            'outlier_count': len(outliers),
        })
    return inconsistencies


@register_job
class AlbumTagConsistencyJob(RepairJob):
    job_id = 'album_tag_consistency'
    display_name = 'Album Tag Consistency'
    description = 'Finds albums where tracks have inconsistent tags causing media server splits'
    help_text = (
        'Scans your library for albums where tracks have mismatched metadata — '
        'different album names, artist names, or MusicBrainz release IDs across '
        'tracks that belong to the same album.\n\n'
        'These inconsistencies cause media servers like Navidrome to split one album '
        'into multiple entries (e.g. "Simulation Theory" and "Simulation Theory (Super Deluxe)").\n\n'
        'Albums your server has ALREADY split are checked too: rows that share an artist and '
        'title are treated as one album and their tracks compared together, so two "Album X" '
        'entries with one track each still get caught. A track missing a tag the others carry '
        '(often the MusicBrainz release id) counts as a mismatch, since servers key on it.\n\n'
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

        try:
            conn = context.db._get_connection()
            cursor = conn.cursor()

            # Get all albums with 2+ tracks that have file paths
            cursor.execute("""
                SELECT al.id, al.title, ar.name as artist_name,
                       COUNT(t.id) as track_count
                FROM albums al
                JOIN artists ar ON ar.id = al.artist_id
                JOIN tracks t ON t.album_id = al.id
                WHERE t.file_path IS NOT NULL AND t.file_path != ''
                GROUP BY al.id
                HAVING COUNT(t.id) >= 2
                ORDER BY ar.name, al.title
            """)
            albums = cursor.fetchall()
            total = len(albums)

            # Eligibility breakdown — users compare the scanned count to their
            # server's album count ("only scans 1300 of my 4000 albums") and
            # read the gap as a truncation bug. Say up front what was excluded
            # and why instead of reporting a bare, smaller number.
            total_albums = single_track = missing_paths = no_artist = 0
            try:
                cursor.execute("""
                    SELECT COUNT(*) AS total_albums,
                           SUM(CASE WHEN track_total < 2 THEN 1 ELSE 0 END) AS single_track,
                           SUM(CASE WHEN track_total >= 2 AND with_path < 2 THEN 1 ELSE 0 END) AS missing_paths
                    FROM (
                        SELECT al.id,
                               COUNT(t.id) AS track_total,
                               SUM(CASE WHEN t.file_path IS NOT NULL AND t.file_path != ''
                                        THEN 1 ELSE 0 END) AS with_path
                        FROM albums al
                        LEFT JOIN tracks t ON t.album_id = al.id
                        GROUP BY al.id
                    )
                """)
                row = cursor.fetchone()
                if row:
                    total_albums = row['total_albums'] or 0
                    single_track = row['single_track'] or 0
                    missing_paths = row['missing_paths'] or 0
                cursor.execute("""
                    SELECT COUNT(*) FROM albums al
                    LEFT JOIN artists ar ON ar.id = al.artist_id
                    WHERE ar.id IS NULL
                """)
                no_artist = cursor.fetchone()[0] or 0
            except Exception as e:
                logger.debug("eligibility breakdown query failed: %s", e)

            if context.report_progress:
                context.report_progress(
                    phase=f'Scanning {total} of {total_albums} albums for tag consistency...',
                    total=total,
                )
                if total_albums > total:
                    reasons = []
                    if single_track:
                        reasons.append(f'{single_track} single-track')
                    if missing_paths:
                        reasons.append(f'{missing_paths} without stored file paths (re-run a library scan to populate)')
                    if no_artist:
                        reasons.append(f'{no_artist} without an artist link')
                    context.report_progress(
                        log_line=(
                            f'{total_albums} albums in the database, {total} eligible '
                            f'(need 2+ tracks with file paths)'
                            + (f' — excluded: {", ".join(reasons)}' if reasons else '')
                        ),
                        log_type='info',
                    )

            # Albums that were eligible but had <2 files actually readable from
            # SoulSync's filesystem — a scanned album silently producing no
            # finding is indistinguishable from a healthy one to the user, so
            # count these and say so at the end (Docker mount mismatch is the
            # usual cause: the server's "/music/..." path isn't mounted here).
            unreadable_albums = 0

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

                tag_data = self._read_track_tags(tracks, context)

                if len(tag_data) < 2:
                    # Eligible on paper (2+ tracks with paths) but the files
                    # themselves weren't readable/parseable from here.
                    unreadable_albums += 1
                    continue

                inconsistencies = find_inconsistencies(tag_data, settings)

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

            # albums the server has already split: they reach us as two or
            # more album rows with the same artist and title, each too small
            # for the loop above. achilles4: two tracks from one album, two
            # albums in navidrome, "the SoulSync tools have not picked up on
            # these discrepancies" — this is the pass that picks them up.
            if not context.check_stop():
                split_findings = self._scan_split_groups(cursor, context, settings, result)
                if split_findings and context.report_progress:
                    context.report_progress(
                        log_line=f'{split_findings} album(s) your server has split across several entries',
                        log_type='warning',
                    )

            if unreadable_albums and context.report_progress:
                context.report_progress(
                    log_line=(
                        f'{unreadable_albums} album(s) skipped: their audio files '
                        f'could not be read from SoulSync\'s filesystem — if your '
                        f'media server runs elsewhere (e.g. Docker), mount the music '
                        f'folder here at the same path the server reports'
                    ),
                    log_type='warning',
                )

            conn.close()

        except Exception as e:
            logger.error(f"Album tag consistency scan error: {e}")
            result.errors += 1

        return result

    def _read_track_tags(self, tracks, context):
        """one dict per track whose file could be read from here."""
        tag_data = []
        for track in tracks:
            file_path = track['file_path']
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
        return tag_data

    def _scan_split_groups(self, cursor, context, settings, result) -> int:
        """album rows that share an artist and title, compared as one album.

        returns the number of findings created. one finding per group,
        keyed on the smallest album id so a re-run dedups against it."""
        cursor.execute("""
            SELECT al.id, al.title, ar.name AS artist_name
            FROM albums al
            JOIN artists ar ON ar.id = al.artist_id
            WHERE EXISTS (
                SELECT 1 FROM tracks t
                WHERE t.album_id = al.id AND t.file_path IS NOT NULL AND t.file_path != ''
            )
        """)
        groups = {}
        for row in cursor.fetchall():
            key = split_group_key(row['artist_name'], row['title'])
            if not key[0] or not key[1]:
                continue
            groups.setdefault(key, []).append(row)
        split = {key: rows for key, rows in groups.items() if len(rows) >= 2}
        if not split:
            return 0

        created = 0
        for rows in split.values():
            if context.check_stop():
                break
            album_ids = sorted(str(r['id']) for r in rows)
            placeholders = ','.join('?' * len(album_ids))
            cursor.execute(f"""
                SELECT id, title, file_path, album_id FROM tracks
                WHERE album_id IN ({placeholders}) AND file_path IS NOT NULL AND file_path != ''
            """, album_ids)
            tracks = cursor.fetchall()
            tag_data = self._read_track_tags(tracks, context)
            if len(tag_data) < 2:
                continue
            result.scanned += 1
            inconsistencies = find_inconsistencies(tag_data, settings)
            if not inconsistencies:
                continue

            album_title = rows[0]['title']
            artist_name = rows[0]['artist_name']
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
                entity_id=album_ids[0],
                file_path=None,
                title=f'Split on your server: {album_title} by {artist_name}',
                description=(
                    f'Your server shows this album as {len(rows)} entries. '
                    f'{total_outliers} track(s) have mismatched {fields_affected}. '
                    + '; '.join(desc_parts)
                ),
                details={
                    'album_id': album_ids[0],
                    'album_ids': album_ids,
                    'album_title': album_title,
                    'artist_name': artist_name,
                    'server_split': True,
                    'inconsistencies': inconsistencies,
                    'track_count': len(tag_data),
                    'tracks': [{'id': t['track_id'], 'title': t['track_title'],
                                'file_path': t['file_path']} for t in tag_data],
                },
            )
            if inserted:
                result.findings_created += 1
                created += 1
            else:
                result.findings_skipped_dedup += 1
            if context.report_progress:
                context.report_progress(
                    log_line=f'Split: {album_title} ({len(rows)} entries) — {fields_affected}',
                    log_type='warning',
                )
        return created

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
