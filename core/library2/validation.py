"""Read-only metadata checks and the shared, transactional finding boundary."""

import json
import logging
from datetime import datetime, timezone

_change_emitter = None


def notify_changes(file_ids=()):
    """Called only after commit; notification failure cannot undo persistence."""
    if _change_emitter:
        try:
            _change_emitter({'file_ids': list(file_ids)})
        except Exception:
            logging.getLogger(__name__).debug('Library change notification failed', exc_info=True)


def edition_reference(conn, track_id, data):
    """Use a concrete owning edition; an ambiguous release is never guessed."""
    rows = conn.execute(
        "SELECT rt.*, e.track_count, e.disc_count, e.title AS edition_title FROM lib2_release_tracks rt "
        "JOIN lib2_release_editions e ON e.id=rt.release_edition_id WHERE rt.track_id=?", (track_id,),
    ).fetchall()
    if len(rows) > 1:
        data.update(track_number=None, disc_number=None, track_count=None, total_discs=None, edition_status='unknown')
    elif rows:
        row = rows[0]
        data.update(track_number=row['track_number'], disc_number=row['disc_number'],
                    total_discs=row['disc_count'], edition_status='correct', edition_id=row['release_edition_id'])
        counts = conn.execute(
            "SELECT COUNT(*), SUM(CASE WHEN disc_number=? THEN 1 ELSE 0 END) "
            "FROM lib2_release_tracks WHERE release_edition_id=?", (row['disc_number'], row['release_edition_id']),
        ).fetchone()
        data['track_count'] = counts[1] if row['track_count'] and counts[0] == row['track_count'] else None
        if row['title_override']:
            data['title'] = row['title_override']
        if row['edition_title']:
            data['album_title'] = row['edition_title']
    else:
        album = conn.execute('SELECT album_id FROM lib2_tracks WHERE id=?', (track_id,)).fetchone()
        ambiguous = album and conn.execute('SELECT 1 FROM lib2_release_editions WHERE release_group_id=?', (album[0],)).fetchone()
        data['edition_status'] = 'unknown' if ambiguous else 'not_checked'
        if ambiguous:
            data.update(track_number=None, disc_number=None, track_count=None)
    return data


def import_reference(context):
    """A Library-bound download uses the same payload as retag and validation."""
    from contextlib import closing
    from core.downloads.origin import _parse_source_info
    from core.imports.context import get_import_track_info
    from core.library2.retag import _track_rows, _db_data_for_row
    track = get_import_track_info(context)
    native = context.get('lib2_entity') or track.get('lib2_entity') or {}
    track_id = native.get('track_id') or _parse_source_info(track.get('source_info')).get('lib2_track_id')
    if not track_id:
        return None
    from database.music_database import get_database
    with closing(get_database()._get_connection()) as conn:
        rows = _track_rows(conn, [int(track_id)])
        return _db_data_for_row(conn, rows[0]) if rows else None


def refresh_imported_metadata(database, paths):
    """Read the final published files, including retained companion copies."""
    from contextlib import closing
    from core.library2.scan import rescan_files
    from core.library2.status import _coerce_tags
    with closing(database._get_connection()) as conn:
        ids = [row[0] for path in paths for row in conn.execute('SELECT id FROM lib2_track_files WHERE path=?', (str(path),))]
    rescan_files(database, file_ids=ids, source='import')
    with closing(database._get_connection()) as conn:
        states = [(_coerce_tags(row[0]).get('_validation') or {}).get('status', 'unknown') for fid in ids
                  for row in conn.execute('SELECT tags_json FROM lib2_track_files WHERE id=?', (fid,))]
    return 'issues' if 'issues' in states else 'correct' if states and all(s == 'correct' for s in states) else 'unknown'


def artwork_checks(image_url, tags, config):
    enabled = lambda key: config.get('metadata_enhancement.' + key, True)
    return {
        'artwork_database': 'correct' if image_url else 'missing',
        'cover': 'not_required' if not enabled('embed_album_art') else 'not_checked' if 'has_cover_art' not in tags
                 else 'correct' if tags.get('has_cover_art') else 'missing',
        'artwork_sidecar': 'not_required' if not enabled('cover_art_download') else 'not_checked' if 'cover_sidecar' not in tags
                           else 'correct' if tags.get('cover_sidecar') else 'missing',
    }


def metadata_states(database, paths):
    """Download history reads the same persisted file verdict as Library."""
    from contextlib import closing
    from core.library2.status import _coerce_tags
    paths, result = list(dict.fromkeys(paths)), {}
    with closing(database._get_connection()) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='lib2_track_files'").fetchone():
            return result
        for start in range(0, len(paths), 500):
            chunk = paths[start:start + 500]
            for row in conn.execute(f"SELECT path,tags_json FROM lib2_track_files WHERE path IN ({','.join('?' for _ in chunk)}) AND COALESCE(file_state,'active')<>'deleted'", chunk):
                result[row[0]] = (_coerce_tags(row[1]).get('_validation') or {}).get('status', 'unknown')
    return result


def validate_tags(conn, file_id, tags, config):
    from core.library2.retag import _track_rows, _db_data_for_row
    from core.tag_writer import build_tag_diff
    from core.library2.status import EXPECTED_TAGS

    file = conn.execute('SELECT track_id, owner_profile_id FROM lib2_track_files WHERE id=?', (file_id,)).fetchone()
    rows = _track_rows(conn, [file['track_id']]) if file and file['track_id'] else []
    if not rows:
        return {'status': 'unknown', 'checks': {}, 'source': 'file', 'checked_at': datetime.now(timezone.utc).isoformat()}
    data = _db_data_for_row(conn, rows[0])
    checks = {}
    for diff in build_tag_diff(tags, data):
        key = 'albumartist' if diff['file_key'] == 'album_artist' else diff['file_key']
        if key not in EXPECTED_TAGS or key == 'cover':
            continue
        checks[key] = ('missing' if not diff['file_value'] else 'unknown' if not diff['db_value'] or diff.get('protected')
                       else 'mismatch' if diff['changed'] else 'correct')
    for key, expected in (('total_tracks', data.get('track_count')), ('total_discs', data.get('total_discs'))):
        actual = tags.get(key)
        checks[key] = 'unknown' if not expected else 'not_checked' if not actual else 'correct' if actual == expected else 'mismatch'
    checks.update(artwork_checks(rows[0]['album_image_url'], tags, config))
    checks['edition'] = data.get('edition_status', 'not_checked')
    return {'status': 'issues' if any(v in ('missing', 'mismatch') for v in checks.values())
            else 'unknown' if any(v in ('unknown', 'not_checked') for v in checks.values()) else 'correct',
            'checks': checks, 'reference': data, 'source': 'file', 'owner_profile_id': file['owner_profile_id'],
            'file_id': file_id, 'track_id': file['track_id'], 'album_id': rows[0]['album_id'],
            'checked_at': datetime.now(timezone.utc).isoformat()}


def linked_findings_many(conn, files):
    """Resolve findings in bounded batches; retain file and library ownership."""
    files = {int(f['id']): f for f in files if f}
    result = {fid: [] for fid in files}
    if not files or not conn.execute("SELECT 1 FROM sqlite_master WHERE name='repair_findings'").fetchone():
        return result
    ids = list(files)
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        rows = conn.execute(
            f"SELECT rf.*, tf.id AS subject_file_id FROM lib2_track_files tf "
            "JOIN lib2_tracks t ON t.id=tf.track_id JOIN lib2_albums a ON a.id=t.album_id "
            "JOIN repair_findings rf ON rf.status='pending' AND (rf.file_path=tf.path OR "
            "(json_valid(rf.details_json) AND (json_extract(rf.details_json,'$.library_v2.file_id')=tf.id OR "
            "json_extract(rf.details_json,'$.validation.file_id')=tf.id)) OR "
            "(rf.file_path IS NULL AND ((rf.entity_type='file' AND rf.entity_id='lib2:'||tf.id) OR "
            "(rf.entity_type='track' AND rf.entity_id='lib2:'||t.id))) OR "
            "(rf.entity_type='album' AND rf.entity_id='lib2:'||t.album_id) OR "
            "(rf.entity_type='artist' AND rf.entity_id='lib2:'||a.primary_artist_id)) "
            f"WHERE tf.id IN ({','.join('?' for _ in chunk)})", chunk,
        ).fetchall()
        for row in rows:
            finding = dict(row)
            fid = finding.pop('subject_file_id')
            try:
                details = json.loads(finding.get('details_json') or '{}')
            except (TypeError, ValueError):
                details = {}
            owner = details.get('library_owner_id', details.get('owner_profile_id'))
            if owner is None:
                owner = (details.get('validation') or {}).get('owner_profile_id')
            if owner is None and ('library_owner_id' in details or 'owner_profile_id' in (details.get('validation') or {})):
                owner = 1
            if owner is not None and int(owner) != int(files[fid].get('owner_profile_id') or 1):
                continue
            finding['details'] = details
            result[fid].append(finding)
    return result


def linked_findings(conn, file):
    return linked_findings_many(conn, [file]).get(file['id'], []) if file else []


def persist_validation(conn, file_id, validation):
    """Called inside the same transaction as file observations; no file writes."""
    file = dict(conn.execute('SELECT * FROM lib2_track_files WHERE id=?', (file_id,)).fetchone())
    findings = linked_findings(conn, file)
    issues = {k: v for k, v in validation['checks'].items() if v in ('missing', 'mismatch')}
    current = [f for f in findings if f['details'].get('validation')]
    for finding in current:
        if not issues and finding_verified(conn, finding, [file]):
            conn.execute("UPDATE repair_findings SET status='resolved', user_action='validated', "
                         "resolved_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP WHERE id=?", (finding['id'],))
        elif issues:
            required = finding['details'].get('required_fields') or [k for k, v in finding['details']['validation']['checks'].items() if v in ('missing', 'mismatch')]
            conn.execute('UPDATE repair_findings SET details_json=?, updated_at=CURRENT_TIMESTAMP WHERE id=?',
                         (json.dumps({'validation': validation, 'required_fields': list(set(required) | set(issues))}), finding['id']))
    for finding in findings:
        if finding in current or finding.get('file_path') != file['path']:
            continue
        if finding_verified(conn, finding, [file]):
            conn.execute("UPDATE repair_findings SET status='resolved', user_action='validated', "
                         "resolved_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP WHERE id=?", (finding['id'],))
    if issues and not current and not any(f['finding_type'] == 'library_retag' for f in findings) and conn.execute("SELECT 1 FROM sqlite_master WHERE name='repair_findings'").fetchone():
        dismissed = conn.execute("SELECT 1 FROM repair_findings WHERE finding_type='library_retag' AND file_path=? AND status='dismissed'", (file['path'],)).fetchone()
        if not dismissed:
            conn.execute(
                "INSERT INTO repair_findings(job_id,finding_type,severity,entity_type,entity_id,file_path,title,description,details_json) "
                "VALUES('library_retag','library_retag','info','track',?,?,?,?,?)",
                (f"lib2:{file['track_id']}", file['path'], 'Metadata issues', ', '.join(f'{k}: {v}' for k, v in issues.items()),
                 json.dumps({'validation': validation})),
            )


def finding_summary(conn, file, findings=None):
    audio_types = {'acoustid_mismatch', 'corrupt_audio', 'fake_lossless', 'short_preview_track', 'dead_file', 'path_mismatch', 'stale_index_path'}
    findings = linked_findings(conn, file) if findings is None else findings
    return [{k: f.get(k) for k in ('id', 'finding_type', 'job_id', 'title', 'last_error', 'updated_at')}
            | {'category': 'check' if f['finding_type'] in audio_types or f['finding_type'].startswith('quality_') else 'metadata'} for f in findings]


def finding_verified(conn, finding, files):
    """Only close a supported issue on positive evidence, never on a skipped read."""
    kind = finding['finding_type']
    if kind not in ('library_retag', 'missing_cover_art', 'track_number_mismatch'):
        return None
    for file in files:
        tags = json.loads(file['tags_json'] or '{}')
        validation = tags.get('_validation') or {}
        checks = validation.get('checks') or {}
        if not checks:
            return False
        if kind == 'library_retag':
            details = finding.get('details') or {}
            required = details.get('required_fields') or [k for k, v in (details.get('validation') or {}).get('checks', {}).items() if v in ('missing', 'mismatch')]
            if not required:
                required = [('albumartist' if d.get('file_key') == 'album_artist' else 'cover' if d.get('file_key') == 'cover_art' else d.get('file_key'))
                            for d in details.get('diff', []) if d.get('changed')]
            if not required:
                return None
            if any(checks.get(k) not in ('correct', 'not_required') for k in required):
                return False
        elif kind == 'missing_cover_art':
            if any(checks.get(k) not in ('correct', 'not_required') for k in ('artwork_database', 'cover', 'artwork_sidecar')):
                return False
        elif kind == 'track_number_mismatch':
            if any(checks.get(k) != 'correct' for k in ('track_number', 'disc_number')) or checks.get('total_tracks') == 'mismatch':
                return False
            if (finding.get('details') or {}).get('total_tracks') and checks.get('total_tracks') != 'correct':
                return False
            target = finding.get('details', {}).get('new_filename')
            if target:
                import os
                if os.path.basename(file['path']) != target:
                    return False
        else:
            return None
    return bool(files)


def verify_finding_change(database, kind, entity_type, entity_id, path, details, config):
    from contextlib import closing
    from core.library2.maintenance_sync import _resolve_links
    with closing(database._get_connection()) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='lib2_track_files'").fetchone():
            return None
        links = _resolve_links(conn, entity_type=entity_type, entity_id=entity_id, file_path=path,
                               details=details, config_manager=config, artist_files=False)
        ids = links['files'] if kind == 'missing_cover_art' else links.get('direct_files') or links['files']
        owner = details.get('library_owner_id', (details.get('validation') or {}).get('owner_profile_id'))
        if owner is None and ('library_owner_id' in details or 'owner_profile_id' in (details.get('validation') or {})):
            owner = 1
        files = [dict(row) for row in conn.execute(
            f"SELECT * FROM lib2_track_files WHERE id IN ({','.join('?' for _ in ids)}) AND file_state<>'deleted'", ids,
        )] if ids else []
        if owner is not None:
            files = [f for f in files if int(f.get('owner_profile_id') or 1) == int(owner)]
        if not files:
            return False if kind in ('track_number_mismatch', 'missing_cover_art') or details.get('validation') else None
        return finding_verified(conn, {'finding_type': kind, 'details': details}, files)
