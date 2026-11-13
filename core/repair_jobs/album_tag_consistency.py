"""Album Tag Consistency Job — finds albums where tracks have inconsistent tags.

When tracks in the same album have different artist names, album names, or
MusicBrainz release IDs, media servers like Navidrome split them into separate
albums. This job detects these inconsistencies and offers to fix them by
normalizing all tracks to the canonical (majority) value.
"""

import json
import os
import re
from contextlib import closing
from typing import Dict
from core.library2.maintenance_subjects import active_file_subjects
from collections import Counter

from mutagen import File as MutagenFile

from core.repair_jobs import register_job
from core.repair_jobs.base import (
    get_scope_artist, JobContext, JobResult, RepairJob, artist_scoped_subjects, drop_hand_tagged,
)
from utils.logging_config import get_logger

logger = get_logger("repair_job.album_tag_consistency")


def _read_tag(audio, tag_name):
    """Compatibility adapter onto the shared partial-field codec."""
    from core.tag_writer import read_tag_field
    return read_tag_field(audio, tag_name)


MISSING = '(missing)'

_CHECK_FIELDS = (
    ('check_album_name', 'album', 'album_tag'),
    ('check_album_artist', 'albumartist', 'albumartist_tag'),
    ('check_mb_release_id', 'musicbrainz_albumid', 'mbid_tag'),
)


def _detect_inconsistencies(tag_data, check_album, check_artist, check_mbid):
    """Majority-vote inconsistency detection over per-file tag snapshots.

    A track MISSING a tag the others carry is a variant, not a pass. Upstream
    d97a9b3d6 found the hole: navidrome keys an album on album + album artist
    + musicbrainz release id, so one file without the id splits the album
    exactly like one carrying the wrong id, and the version that only compared
    tracks which HAD a value reported that album consistent. A field nobody
    has is still left alone - there is nothing to normalize to.
    """
    enabled = {
        'check_album_name': check_album,
        'check_album_artist': check_artist,
        'check_mb_release_id': check_mbid,
    }
    inconsistencies = []
    for setting_key, field, tag_key in _CHECK_FIELDS:
        if not enabled.get(setting_key, True):
            continue
        present = [t[tag_key] for t in tag_data if t.get(tag_key)]
        if not present:
            continue
        values = [t.get(tag_key) or MISSING for t in tag_data]
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


def _write_tag(audio, tag_name, value):
    """Set one field; callers must confirm the atomic save before reporting it."""
    from core.tag_writer import set_tag_fields
    if audio is None or value is None:
        return False
    try:
        return bool(set_tag_fields(audio, {tag_name: value}))
    except Exception as exc:
        logger.debug("Failed to set tag %s: %s", tag_name, exc)
        return False


def split_group_key(artist_name, album_title):
    """Upstream split detection folds punctuation but retains edition qualifiers."""
    return tuple(' '.join(re.sub(r'[^\w\s]', ' ', str(v or '').casefold()).split())
                 for v in (artist_name, album_title))


def split_catalogue_state(conn, album_ids):
    """Snapshot the native release identities a reviewed split was based on."""
    state = []
    for album_id in sorted(set(album_ids)):
        row = conn.execute('SELECT id,primary_artist_id,title,year,release_date,canonical_locked,canonical_source,canonical_album_id '
                           'FROM lib2_albums WHERE id=?', (int(album_id),)).fetchone()
        if row is None:
            raise ValueError('Reviewed album no longer exists')
        editions = conn.execute('SELECT id,title,spotify_id,musicbrainz_id,external_ids,is_default '
                                'FROM lib2_release_editions WHERE release_group_id=? ORDER BY id', (int(album_id),)).fetchall()
        state.append({**dict(row), 'editions': [dict(e) for e in editions]})
    return state


def _compatible_split(state, tags):
    """Reject positive evidence of distinct releases; unknown facts may coexist."""
    years = {str(r.get('year') or r.get('release_date') or '')[:4] for r in state}
    years.discard('')
    years.update(str(t['date_tag'])[:4] for t in tags if t.get('date_tag') and str(t['date_tag'])[:4].isdigit())
    if len(years) > 1 or len({t['rg_tag'] for t in tags if t.get('rg_tag')}) > 1:
        return False
    signatures = []
    from core.library2.native_enrich import _stored_source_ids
    for row in state:
        ids = {}
        for edition in row['editions']:
            if edition['is_default']:
                ids = _stored_source_ids(edition)
        if row['canonical_locked'] and row['canonical_source'] and row['canonical_album_id']:
            ids = {row['canonical_source']: str(row['canonical_album_id'])}
        if ids:
            signatures.append(ids)
    for index, first in enumerate(signatures):
        for second in signatures[index + 1:]:
            common = set(first) & set(second)
            if not common or any(first[source] != second[source] for source in common):
                return False
    return True


VARIOUS_ARTISTS = 'Various Artists'
#: same-titled albums the release-id pass will read the tags of. a title this
#: many albums share is a generic one, and reading every file of it is the cost
RELEASE_GROUP_LIMIT = 40
_DISC_FOLDER = re.compile(r'^(cd|disc|disk)\s*\d+$', re.IGNORECASE)


def release_folder(path):
    """The folder a release lives in: a file's own, or the one above a disc folder."""
    folder = os.path.dirname(str(path or '').replace('\\', '/').rstrip('/'))
    if _DISC_FOLDER.match(os.path.basename(folder)):
        folder = os.path.dirname(folder)
    return folder


def folder_split_album_artist(tags):
    """The album artist a folder of same-titled files should share, or None.

    A compilation ripped with no album artist tag lands on the server as one
    album per track artist (#2072: Wipeout Pulse as sixteen one-track albums).
    When most files already name one album artist, that is it. When nobody
    agrees and three or more artists are in the folder, it is a compilation:
    Various Artists. Two artists splitting a folder evenly is not enough to
    say which.
    """
    named = [t['albumartist_tag'] for t in tags if t.get('albumartist_tag')]
    if named:
        value, count = Counter(named).most_common(1)[0]
        if count * 2 > len(tags):
            return value
    seen = {(t.get('albumartist_tag') or t.get('artist_tag') or '').casefold() for t in tags}
    seen.discard('')
    return VARIOUS_ARTISTS if len(seen) >= 3 else None


def _read_subject_tags(subjects, context):
    from core.library2.paths import resolve_lib2_path
    tags = []
    for subject in subjects:
        raw = str(subject.get('path') or '')
        path = raw if os.path.isfile(raw) else resolve_lib2_path(raw, config_manager=context.config_manager)
        if not path:
            continue
        try:
            audio = MutagenFile(path, easy=False)
            if audio is not None:
                tags.append({'track_id': f"lib2:{subject['track_id']}", 'file_id': subject['file_id'],
                    'album_id': subject['album_id'], 'owner_profile_id': subject.get('owner_profile_id'),
                    'track_title': subject.get('title'), 'file_path': raw, 'resolved_path': path,
                    'album_tag': _read_tag(audio, 'album'), 'albumartist_tag': _read_tag(audio, 'albumartist'),
                    'mbid_tag': _read_tag(audio, 'musicbrainz_albumid'),
                    'artist_tag': _read_tag(audio, 'artist'),
                    'rg_tag': _read_tag(audio, 'musicbrainz_releasegroupid'), 'date_tag': _read_tag(audio, 'date')})
        except Exception as exc:
            logger.debug('Unable to read consistency file %s: %s', raw, exc)
    return tags


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
        'A compilation with no album artist tag, filed as one album per track artist, '
        'is reported too: one folder, one title, several album artists, or the same '
        'title in three or more artist folders whose track numbers fit together as one record, '
        'or same-titled albums whose files carry one MusicBrainz release id wherever they sit.\n\n'
        'Also reviews native album rows with the same artist and title that may '
        'have split on your server. Conflicting years, release identities, editions '
        'and library owners are kept separate. Apply only changes the listed files; '
        'catalogue rows are never merged, and manual fields, hand tags and pins are protected.\n\n'
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
    supports_file_scope = True

    def _get_settings(self, context: JobContext) -> dict:
        """Get job settings from config, merged with defaults."""
        cfg = context.config_manager.get(f'repair.jobs.{self.job_id}.settings', {})
        merged = dict(self.default_settings)
        if isinstance(cfg, dict):
            merged.update(cfg)
        return merged

    def estimate_scope(self, context: JobContext) -> int:
        try:
            counts: Dict[int, int] = {}
            for subject in artist_scoped_subjects(context, active_file_subjects(context.db, context.config_manager)):
                counts[int(subject["album_id"])] = counts.get(int(subject["album_id"]), 0) + 1
            return sum(1 for count in counts.values() if count >= 2)
        except Exception:
            return 0

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()
        settings = self._get_settings(context)
        check_album = settings.get("check_album_name", True)
        check_artist = settings.get("check_album_artist", True)
        check_mbid = settings.get("check_mb_release_id", True)
        if any((check_album, check_artist, check_mbid)):
            try:
                subjects = drop_hand_tagged(context, artist_scoped_subjects(
                    context, active_file_subjects(context.db, context.config_manager)))
            except Exception as e:
                logger.warning("V2 subject enumeration failed: %s", e)
                result.errors += 1
                return result
            self._scan_native_albums(
                context, result, subjects, check_album, check_artist, check_mbid,
            )
            if not context.check_stop():
                self._scan_split_groups(context, result, subjects, check_album, check_artist, check_mbid)
            if check_artist and not context.check_stop():
                self._scan_folder_splits(context, result, subjects, check_album, check_mbid)
        return result

    def _scan_split_groups(self, context, result, subjects, check_album, check_artist, check_mbid):
        """Review same-name native rows as a group, without merging the catalogue."""
        try:
            groups = {}
            for subject in subjects:
                # one album split across rows shares its ALBUM artist; the
                # first track credit differs on every guest spot and split
                # the album's tracks into groups that never compared
                album_artist = subject.get('album_artist_name') or subject.get('artist_name')
                key = (*split_group_key(album_artist, subject.get('album_title')), subject.get('owner_profile_id'))
                if key[0] and key[1]:
                    groups.setdefault(key, []).append(subject)
            for subjects in groups.values():
                if context.check_stop() or context.wait_if_paused():
                    return
                album_ids = sorted({s['album_id'] for s in subjects})
                if len(album_ids) < 2:
                    continue
                tags = _read_subject_tags(subjects, context)
                if len({t['album_id'] for t in tags}) < 2:
                    continue
                with closing(context.db._get_connection()) as conn:
                    state = split_catalogue_state(conn, album_ids)
                if not _compatible_split(state, tags):
                    continue
                result.scanned += 1
                inconsistencies = _detect_inconsistencies(tags, check_album, check_artist, check_mbid)
                if not inconsistencies or not context.create_finding:
                    continue
                album_title = subjects[0].get('album_title')
                artist_name = subjects[0].get('album_artist_name') or subjects[0].get('artist_name')
                files = sorted({t['file_id'] for t in tags})
                inserted = context.create_finding(job_id=self.job_id, finding_type='album_tag_inconsistency',
                    severity='warning', entity_type='album', entity_id=f'lib2:{album_ids[0]}', file_path=None,
                    title=f'Split on your server: {album_title} by {artist_name}',
                    description=f'{len(album_ids)} catalogue rows with matching artist/title have inconsistent file tags. Review the listed files.',
                    details={'album_id': f'lib2:{album_ids[0]}', 'album_ids': album_ids, 'server_split': True,
                        'split_catalogue_state': state, 'library_v2_native': True,
                        'library_owner_id': subjects[0].get('owner_profile_id') or 1,
                        'album_title': album_title, 'artist_name': artist_name, 'inconsistencies': inconsistencies,
                        'track_count': len(tags), 'tracks': [{'id': t['track_id'], 'track_id': t['track_id'],
                            'file_id': t['file_id'], 'album_id': t['album_id'], 'owner_profile_id': t['owner_profile_id'],
                            'title': t['track_title'], 'file_path': t['file_path']} for t in tags],
                        'library_v2': {'album_id': album_ids[0], 'album_ids': album_ids, 'artist_id': subjects[0].get('artist_id'),
                            'track_ids': sorted({s['track_id'] for s in subjects}), 'file_ids': files}})
                if inserted:
                    result.findings_created += 1
                else:
                    result.findings_skipped_dedup += 1
        except Exception as exc:
            logger.warning('Native split album scan failed: %s', exc)
            result.errors += 1

    def _scan_folder_splits(self, context, result, subjects, check_album, check_mbid):
        """One folder, one album title, several album artists (#2072).

        The same-artist pass above cannot see these: a compilation with no
        album artist tag is filed under each track's own artist, so no two of
        its rows share an artist. What they share is the folder and the title.

        Or the tags say so themselves: the same MusicBrainz release id on files
        of same-titled albums, wherever they are kept.

        Or the title and the shelf: an organiser that filed by track artist
        leaves one track in each of ``Skream/Wipeout Pulse``,
        ``Kraftwerk/Wipeout Pulse`` and so on. Those are grouped by the folder
        above the artist folders, and because every artist has a "Greatest
        Hits", that group has to look like one record: three or more albums,
        and no two files holding the same disc and track number.
        """
        try:
            groups, shelves, titled = {}, {}, {}
            for subject in subjects:
                folder = release_folder(subject.get('path'))
                title = split_group_key('', subject.get('album_title'))[1]
                if folder and title:
                    owner = subject.get('owner_profile_id')
                    groups.setdefault((folder.casefold(), title, owner), []).append(subject)
                    titled.setdefault((title, owner), []).append(subject)
                    shelf = os.path.dirname(os.path.dirname(folder))
                    if shelf:
                        shelves.setdefault((shelf.casefold(), title, owner), []).append(subject)
            reported = set()

            def by_release():
                """Wherever the files sit: albums of one title under different
                album artists whose files carry the same MusicBrainz release id
                are one release, on the tags' own word (#2072, Storm: "I
                thought this was metadata based rather than folder based").
                A title half the library shares ("Greatest Hits") is not read
                through: past RELEASE_GROUP_LIMIT albums the folder passes
                above are the ones that apply."""
                for same_title in titled.values():
                    albums = {s['album_id'] for s in same_title}
                    if not 2 <= len(albums) <= RELEASE_GROUP_LIMIT or albums <= reported:
                        continue
                    if len({str(s.get('album_artist_name') or s.get('artist_name') or '').casefold()
                            for s in same_title}) < 2:
                        continue
                    releases = {}
                    for tag in _read_subject_tags(same_title, context):
                        if tag.get('mbid_tag'):
                            releases.setdefault(tag['mbid_tag'], set()).add(tag['file_id'])
                    for file_ids in releases.values():
                        yield [s for s in same_title if s['file_id'] in file_ids], 'release'

            def candidates():
                for members in groups.values():
                    yield members, 'folder'
                for members in shelves.values():
                    yield members, 'shelf'
                yield from by_release()

            for members, kind in candidates():
                if context.check_stop() or context.wait_if_paused():
                    return
                across_folders = kind == 'shelf'
                if kind == 'release' and {s['album_id'] for s in members} <= reported:
                    continue
                if across_folders:
                    folders = {release_folder(s.get('path')).casefold() for s in members}
                    places = [(int(s.get('disc_number') or 1), int(s.get('track_number') or 0))
                              for s in members]
                    if (len(folders) < 3 or len({s['album_id'] for s in members}) < 3
                            or any(track <= 0 for _disc, track in places)
                            or len(set(places)) != len(places)
                            or {s['album_id'] for s in members} <= reported):
                        continue
                album_ids = sorted({s['album_id'] for s in members})
                artists = {str(s.get('album_artist_name') or s.get('artist_name') or '').casefold()
                           for s in members}
                if len(album_ids) < 2 or len(artists) < 2:
                    continue
                tags = _read_subject_tags(members, context)
                if len({t['album_id'] for t in tags}) < 2:
                    continue
                # positive signs of two releases sharing a folder keep them apart
                years = {str(t['date_tag'])[:4] for t in tags
                         if t.get('date_tag') and str(t['date_tag'])[:4].isdigit()}
                if len(years) > 1 or len({t['rg_tag'] for t in tags if t.get('rg_tag')}) > 1:
                    continue
                result.scanned += 1
                album_artist = folder_split_album_artist(tags)
                if not album_artist or not context.create_finding:
                    continue
                outliers = [t for t in tags if (t.get('albumartist_tag') or '') != album_artist]
                if not outliers:
                    continue
                variants = sorted({t.get('albumartist_tag') or MISSING for t in tags},
                                  key=lambda v: (v == MISSING, v))
                inconsistencies = [{'field': 'albumartist', 'canonical': album_artist,
                                    'variants': variants, 'outlier_count': len(outliers)}]
                inconsistencies += _detect_inconsistencies(tags, check_album, False, check_mbid)
                with closing(context.db._get_connection()) as conn:
                    state = split_catalogue_state(conn, album_ids)
                album_title = members[0].get('album_title')
                reported.update(album_ids)
                where = {'shelf': 'sit in separate artist folders',
                         'release': 'carry the same MusicBrainz release id'}.get(
                             kind, 'sit in one folder')
                inserted = context.create_finding(
                    job_id=self.job_id, finding_type='album_tag_inconsistency',
                    severity='warning', entity_type='album', entity_id=f'lib2:{album_ids[0]}',
                    file_path=None,
                    title=f'Split by artist on your server: {album_title}',
                    description=(
                        f'{len(album_ids)} albums named "{album_title}" {where} under '
                        f'{len(artists)} different album artists. Unify Tags sets the album '
                        f'artist to "{album_artist}" on every file so the server shows one album.'),
                    details={'album_id': f'lib2:{album_ids[0]}', 'album_ids': album_ids,
                        'server_split': True, 'folder_split': True, 'split_evidence': kind,
                        'split_catalogue_state': state, 'library_v2_native': True,
                        'library_owner_id': members[0].get('owner_profile_id') or 1,
                        'album_title': album_title, 'artist_name': album_artist,
                        'inconsistencies': inconsistencies, 'track_count': len(tags),
                        'tracks': [{'id': t['track_id'], 'track_id': t['track_id'],
                            'file_id': t['file_id'], 'album_id': t['album_id'],
                            'owner_profile_id': t['owner_profile_id'],
                            'title': t['track_title'], 'file_path': t['file_path']} for t in tags],
                        'library_v2': {'album_id': album_ids[0], 'album_ids': album_ids,
                            'artist_id': members[0].get('artist_id'),
                            'track_ids': sorted({s['track_id'] for s in members}),
                            'file_ids': sorted({t['file_id'] for t in tags})}})
                if inserted:
                    result.findings_created += 1
                else:
                    result.findings_skipped_dedup += 1
                if context.report_progress:
                    context.report_progress(
                        log_line=f'Found: {album_title}, split across {len(artists)} album artists',
                        log_type='warning')
        except Exception as exc:
            logger.warning('Folder split album scan failed: %s', exc)
            result.errors += 1

    def _scan_native_albums(self, context: JobContext, result: JobResult, file_subjects,
                            check_album: bool, check_artist: bool, check_mbid: bool):
        """Library-v2 releases grouped by their native file subjects."""
        # One finding per release: its id is the dedup key, so a per-library
        # split would let one library's finding overwrite the other's. The
        # fix still checks each listed file's owner.
        albums = {}
        for subject in file_subjects:
            albums.setdefault(subject['album_id'], []).append(subject)

        # The skip breakdown, ported from the legacy projection this replaced.
        # It is not decoration: an album with two tracks and no stored file paths is
        # the Navidrome-shaped gap, and without the breakdown "Scanned: 1" against a
        # three-album library reads as a broken scan rather than as two deliberate
        # exclusions.
        # The scoped artist's releases, as upstream's album-artist filter: a
        # guest-led first track does not hide one, a guest spot is not one.
        scope_artist = get_scope_artist(context)
        eligible, single_track, no_paths = [], 0, 0
        for album_id, subjects in albums.items():
            if scope_artist and (subjects[0].get('album_artist_name') or '').casefold() != scope_artist.casefold():
                continue
            if len(subjects) < 2:
                single_track += 1
            elif not [s for s in subjects if str(s.get('path') or '').strip()]:
                no_paths += 1
            else:
                eligible.append((album_id, subjects))
        total = len(eligible) + single_track + no_paths
        if context.report_progress:
            context.report_progress(
                phase=f'Checking {len(eligible)} of {total} albums...',
                total=len(eligible),
                log_line=(
                    f'{total} albums in the database — {len(eligible)} eligible, '
                    f'{single_track} single-track, {no_paths} without stored file paths'
                ),
                log_type='info')
        if context.update_progress:
            context.update_progress(0, len(eligible))

        unreadable = 0
        for idx, (album_id, subjects) in enumerate(eligible):
            if context.check_stop() or context.wait_if_paused():
                return
            result.scanned += 1
            # G11-8: the bar sat at 0/N for the whole run; 3.6.1 moved it and
            # named an album every 20
            if context.update_progress:
                context.update_progress(idx + 1, len(eligible))
            if context.report_progress and idx % 20 == 0:
                context.report_progress(
                    scanned=idx + 1, total=len(eligible),
                    phase=f'Scanning {idx + 1} / {len(eligible)}',
                    log_line=f"{subjects[0].get('artist_name')} — {subjects[0].get('album_title')}",
                    log_type='info')

            tag_data = _read_subject_tags(subjects, context)

            if len(tag_data) < 2:
                # Eligible on paper, but the files are not there to read. Counted and
                # reported rather than silently skipped — this is what tells a user
                # their paths have drifted.
                unreadable += 1
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
                    'tracks': [{'id': t['track_id'], 'track_id': t['track_id'], 'file_id': t['file_id'],
                                'album_id': album_id, 'owner_profile_id': t['owner_profile_id'],
                                'title': t['track_title'], 'file_path': t['file_path']} for t in tag_data],
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
            if context.report_progress:
                context.report_progress(log_line=f'Found: {album_title} — {fields_affected}',
                                        log_type='warning')

        if unreadable and context.report_progress:
            context.report_progress(
                log_line=(f'{unreadable} album(s) skipped because their files '
                          f'could not be read'),
                log_type='warning')

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
