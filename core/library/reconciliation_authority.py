"""Respect existing review choices and pins at legacy maintenance boundaries.

The finding journal remains the authority for accepted field decisions; this
module does not create another preference store or silently dismiss findings.
"""
from __future__ import annotations

import json
import os

ALIASES = {
    'library_title': 'title', 'library_track_artist': 'artist', 'track_artist': 'artist',
    'artist_name': 'artist',
    'library_track_number': 'track_number', 'library_disc_number': 'disc_number',
    'library_recording_mbid': 'musicbrainz_recording_id',
    'library_album_title': 'album', 'album_title': 'album',
    'albumartist': 'album_artist', 'musicbrainz_albumid': 'musicbrainz_release_id',
    'musicbrainz_album_id': 'musicbrainz_release_id',
    'library_recording_disambiguation': 'recording_disambiguation',
    'release_date': 'date', 'track_count': 'track_total',
}
IDENTITY = {'title', 'artist', 'album', 'album_artist', 'track_number', 'disc_number',
            'musicbrainz_recording_id', 'musicbrainz_release_id', 'filename',
            'recording_disambiguation', 'year', 'date', 'track_total', 'disc_total'}
FIELDS = {
    'mbid_mismatch': {'musicbrainz_recording_id'},
    'album_mbid_mismatch': {'musicbrainz_release_id'},
    'track_number_mismatch': {'track_number', 'disc_number', 'filename'},
    'canonical_version': {'release', 'musicbrainz_release_id'}, 'incomplete_album': {'intent', 'files'},
    'album_tag_inconsistency': IDENTITY, 'library_retag': IDENTITY,
    'metadata_gap': IDENTITY, 'comma_artist_split': {'artist'},
    'unknown_artist': IDENTITY | {'files'}, 'suspect_album_tag': IDENTITY | {'files'},
    'acoustid_mismatch': IDENTITY | {'files'}, 'album_release_year_mismatch': {'release', 'files', 'year', 'date'},
    'duplicate_tracks': {'files'}, 'single_album_redundant': {'files'},
    'path_mismatch': {'files'}, 'orphan_file': {'files'}, 'dead_file': {'files'},
    'corrupt_audio': {'files'}, 'short_preview_track': {'files'},
    'expired_download': {'files'}, 'unwanted_content': {'files'},
    'quality_upgrade': {'files'}, 'fake_lossless': {'files'},
}


def _table(conn, name):
    return bool(conn.execute('SELECT 1 FROM sqlite_master WHERE name=?', (name,)).fetchone())


def _canonical(values):
    result = {ALIASES.get(k, k): v for k, v in values.items()}
    if result.get('date') and 'year' not in result:
        result['year'] = str(result['date']).split('-', 1)[0]
    return result


def _proposed(kind, details):
    values = dict(details.get('db_data') or details.get('found_fields') or {})
    changes = details.get('changes')
    if isinstance(changes, dict):
        values.update({k: v.get('new') for k, v in changes.items() if isinstance(v, dict)})
    if kind == 'canonical_version' and details.get('source') == 'musicbrainz':
        values['musicbrainz_release_id'] = details.get('album_id')
    if kind == 'mbid_mismatch':
        values['musicbrainz_recording_id'] = ''
    if kind == 'album_mbid_mismatch':
        values['musicbrainz_release_id'] = details.get('consensus_mbid')
    if kind == 'track_number_mismatch':
        for key in ('track_number', 'disc_number'):
            if key in details:
                values[key] = details[key]
        if 'correct_track_num' in details:
            values['track_number'] = details['correct_track_num']
        if details.get('new_filename'):
            values['filename'] = details['new_filename']
    if kind == 'album_tag_inconsistency':
        values.update({r['field']: r['canonical'] for r in details.get('inconsistencies', [])})
    return _canonical(values)


def conflict(db, kind, *, entity_type='', entity_id=None, file_path=None, details=None, connection=None):
    """Return a review link and reason, or None for a compatible legacy action.

    A supplied connection stays open and shares the caller's read transaction.
    """
    if kind not in FIELDS or db is None or not callable(getattr(db, '_get_connection', None)):
        return None
    details = details or {}
    if kind == 'library_retag' and details.get('tracks'):
        for row in details['tracks']:
            blocked = conflict(db, kind, entity_type='track', entity_id=row.get('track_id') or row.get('id'),
                               file_path=row.get('file_path'), details={**{k: v for k, v in details.items() if k != 'tracks'}, **row},
                               connection=connection)
            if blocked:
                return blocked
        return None
    proposed = _proposed(kind, details)
    touched = FIELDS[kind]
    if kind in ('metadata_gap', 'library_retag', 'album_tag_inconsistency') and proposed:
        touched = touched.intersection(proposed)
    if not touched:
        return None
    conn = connection if connection is not None else db._get_connection()
    try:
        if not _table(conn, 'reconcile_album_plans'):
            return None
        album_ids = {str(details['album_id'])} if details.get('album_id') is not None else set()
        paths = {file_path} if file_path else set()
        for value in details.get('all_files') or details.get('files') or []:
            path = value.get('file_path') if isinstance(value, dict) else value
            if isinstance(path, str):
                paths.add(path)
        for path in list(paths):
            alias = ('/music/' + path[len('/app/Music/library/'):] if path.startswith('/app/Music/library/') else
                     '/app/Music/library/' + path[len('/music/'):] if path.startswith('/music/') else None)
            if alias:
                try:
                    if os.path.samefile(path, alias):
                        paths.add(alias)
                except OSError:
                    pass
        track_ids = {str(entity_id)} if entity_type == 'track' and entity_id is not None else set()
        if entity_type == 'album' and entity_id is not None:
            album_ids.add(str(entity_id))
        for row in details.get('tracks') or details.get('duplicate_copies') or []:
            if row.get('file_path'):
                paths.add(row['file_path'])
            if row.get('id') is not None:
                track_ids.add(str(row['id']))
        if _table(conn, 'tracks'):
            for tid, aid, path in conn.execute(
                    'SELECT id, album_id, file_path FROM tracks WHERE id IN (SELECT value FROM json_each(?)) '
                    'OR file_path IN (SELECT value FROM json_each(?))', (json.dumps(list(track_ids)), json.dumps(list(paths)))):
                track_ids.add(str(tid))
                if aid is not None:
                    album_ids.add(str(aid))
                if path:
                    paths.add(path)
        # Folder containment is used only to locate candidate plans; accepted
        # file decisions below still require an exact path or track identity.
        plans = conn.execute('SELECT case_id, album_id, album_folder FROM reconcile_album_plans WHERE '
                             'album_id IN (SELECT value FROM json_each(?)) OR album_folder IN (SELECT value FROM json_each(?))',
                             (json.dumps(list(album_ids)), json.dumps([os.path.dirname(p) for p in paths]))).fetchall()
        for case_id, album_id, _folder in plans:
            def blocked(reason, case_id=case_id):
                return {'success': False, 'review_conflict': True, 'review_case_key': case_id,
                        'error': reason + ' Review this album plan before changing that decision.'}
            # Active plans own file membership until their work is resolved.
            if 'files' in touched and conn.execute(
                    "SELECT 1 FROM repair_findings WHERE review_case_key=? AND job_id='library_reconciliation' AND status='pending' LIMIT 1", (case_id,)).fetchone():
                return blocked('This file action overlaps an active album plan.')
            if kind == 'incomplete_album' and _table(conn, 'reconcile_album_intent'):
                if conn.execute('SELECT 1 FROM reconcile_album_intent WHERE album_id=?', (str(album_id),)).fetchone():
                    return blocked('Album completeness and wanted tracks were deliberately chosen in Review.')
            if (kind == 'canonical_version' or 'musicbrainz_release_id' in proposed) and hasattr(db, 'get_album_canonical'):
                pin = db.get_album_canonical(album_id)
                if pin and pin.get('locked'):
                    target = (details.get('source'), str(details.get('album_id') or '')) if kind == 'canonical_version' else ('musicbrainz', str(proposed.get('musicbrainz_release_id') or ''))
                    if target != (pin['source'], str(pin['album_id'])):
                        return blocked('This action conflicts with the pinned release.')
            if touched.intersection(IDENTITY) and paths and _table(conn, 'reconcile_track_assignment'):
                if conn.execute('SELECT 1 FROM reconcile_track_assignment WHERE album_id=? AND file_path IN (SELECT value FROM json_each(?)) LIMIT 1', (str(album_id), json.dumps(list(paths)))).fetchone():
                    return blocked('This file has a manually chosen release-track assignment.')
            if not _table(conn, 'reconcile_review_choices'):
                continue
            rows = conn.execute("""SELECT f.file_path, f.entity_id, f.status, f.user_action,
                    f.details_json, c.evidence_json, c.choice, c.category, c.case_id
                FROM repair_findings f LEFT JOIN reconcile_review_choices c ON c.finding_id=f.id
                WHERE f.review_case_key=? AND f.job_id='library_reconciliation'
                AND f.status IN ('pending','resolved','dismissed') ORDER BY f.id DESC""", (case_id,))
            seen = set()
            for path, tid, status, action, raw, saved, choice, category, choice_case in rows:
                if action == 'obsolete':
                    continue
                same_file = not (paths or track_ids) or path in paths or str(tid) in track_ids
                if paths or track_ids:
                    if path not in paths and str(tid) not in track_ids:
                        # Album-wide fields can affect every member.
                        if not touched.intersection({'album', 'album_artist', 'musicbrainz_release_id'}):
                            continue
                d = json.loads(raw or '{}')
                approved = bool(saved and saved == raw and choice_case == case_id)
                if status == 'pending' and not approved:
                    continue
                if same_file and 'files' in touched and approved and (choice == 'dismiss' or status == 'pending'):
                    return blocked('This file has an accepted decision in the album plan.')
                for field, change in (d.get('changes') or {}).items():
                    field = ALIASES.get(field, field)
                    if not same_file and field not in {'album', 'album_artist', 'musicbrainz_release_id'}:
                        continue
                    if field not in touched or not isinstance(change, dict) or (path, field) in seen:
                        continue
                    seen.add((path, field))
                    kept = choice == 'dismiss' and (category != 'numbering' or field in ('track_number', 'disc_number', 'filename'))
                    expected = change.get('old' if kept or status == 'dismissed' else 'new')
                    if field not in proposed or str(proposed[field] or '') != str(expected or ''):
                        return blocked(f'This action conflicts with the accepted {field.replace("_", " ")} decision.')
        return None
    finally:
        if connection is None:
            conn.close()


def enrichment_tag_guard(db, file_path):
    """Check provider results at their write boundary, including unplanned IDs.

    Retag callers hold the maintenance lock through provider lookup and save.
    Unrelated enrichment remains available when an identity field is protected.
    """
    fields = {
        'MUSICBRAINZ_RECORDING_ID': 'musicbrainz_recording_id',
        'MUSICBRAINZ_RELEASE_ID': 'musicbrainz_release_id',
        'MUSICBRAINZ_TRACKCOMMENT': 'recording_disambiguation',
        'TITLE': 'title', 'ARTIST': 'artist', 'ARTISTS': 'artist',
        'ALBUM': 'album', 'ALBUMARTIST': 'album_artist',
        'TRACKNUMBER': 'track_number', 'DISCNUMBER': 'disc_number',
        'TOTALTRACKS': 'track_total', 'TOTALDISCS': 'disc_total',
        'DATE': 'date',
    }

    def allowed(tag, value):
        field = fields.get(tag)
        if not field:
            return True
        proposed = {field: value}
        if field == 'date':
            proposed['year'] = str(value).split('-', 1)[0]
        return conflict(db, 'library_retag', file_path=file_path,
                        details={'db_data': proposed}) is None

    return allowed
