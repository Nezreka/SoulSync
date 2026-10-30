"""Read-only metadata checks and the shared, transactional finding boundary."""

import json
import logging
import time
from datetime import datetime, timezone

_change_emitter = None


def notify_changes(file_ids=()):
    """Called only after commit; notification failure cannot undo persistence."""
    if _change_emitter:
        try:
            _change_emitter({'file_ids': list(file_ids)})
        except Exception:
            logging.getLogger(__name__).debug('Library change notification failed', exc_info=True)


def edition_reference_rows(conn, track_ids):
    """Batch the edition bindings shared by read-only metadata lookups."""
    marks = ','.join('?' for _ in track_ids)
    rows = {}
    for row in conn.execute(
        "SELECT rt.*, e.track_count, e.disc_count, e.title AS edition_title FROM lib2_release_tracks rt "
        f"JOIN lib2_release_editions e ON e.id=rt.release_edition_id WHERE rt.track_id IN ({marks})", track_ids):
        rows.setdefault(int(row['track_id']), []).append(row)
    bound = {int(row[0]) for row in conn.execute(
        f"SELECT t.id FROM lib2_tracks t WHERE t.id IN ({marks}) AND EXISTS "
        "(SELECT 1 FROM lib2_release_editions e WHERE e.release_group_id=t.album_id)", track_ids)}
    return rows, bound


def edition_reference(conn, track_id, data, *, rows=None, has_edition=None, lookup_only=False):
    """Use a concrete owning edition; an ambiguous release is never guessed."""
    rows = rows if rows is not None else conn.execute(
        "SELECT rt.*, e.track_count, e.disc_count, e.title AS edition_title FROM lib2_release_tracks rt "
        "JOIN lib2_release_editions e ON e.id=rt.release_edition_id WHERE rt.track_id=?", (track_id,),
    ).fetchall()
    positions = {(r['track_number'], r['disc_number'] or 1) for r in rows}
    discs = {r['disc_count'] for r in rows}
    if len(rows) > 1 and len(positions) == 1 and rows[0]['track_number']:  # same slot on every edition
        (track, disc), = positions
        data.update(track_number=track, disc_number=disc, track_count=None,
                    total_discs=discs.pop() if len(discs) == 1 else None, edition_status='correct')
    elif len(rows) > 1:
        data.update(track_number=None, disc_number=None, track_count=None, total_discs=None, edition_status='unknown')
    elif rows:
        row = rows[0]
        data.update(track_number=row['track_number'], disc_number=row['disc_number'],
                    total_discs=row['disc_count'], edition_status='correct', edition_id=row['release_edition_id'])
        if not lookup_only:
            # A row without a position (a second copy from an earlier single
            # download) is no slot: complete when the count matches every row
            # or every numbered one, and only numbered rows make a disc's total.
            counts = conn.execute(
                "SELECT COUNT(*), COUNT(track_number), SUM(CASE WHEN disc_number=? AND track_number IS NOT NULL "
                "THEN 1 ELSE 0 END) FROM lib2_release_tracks WHERE release_edition_id=?",
                (row['disc_number'], row['release_edition_id']),
            ).fetchone()
            data['track_count'] = counts[2] if row['track_count'] in (counts[0], counts[1]) and row['track_count'] else None
        if row['title_override']:
            data['title'] = row['title_override']
        if row['edition_title']:
            data['album_title'] = row['edition_title']
    else:
        ambiguous = has_edition
        if ambiguous is None:
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
        # Empty in the file AND the catalogue: a retag has nothing to write, so
        # "missing" would raise a finding whose fix can only fail its re-check.
        checks[key] = ('not_required' if not diff['file_value'] and not diff['db_value']
                       else 'missing' if not diff['file_value'] else 'unknown' if not diff['db_value'] or diff.get('protected')
                       else 'mismatch' if diff['changed'] else 'correct')
    for key, number, expected in (('total_tracks', 'track_number', data.get('track_count')),
                                  ('total_discs', 'disc_number', data.get('total_discs'))):
        actual = tags.get(key)
        # A known total the file lacks is written like any other tag; it needs a
        # number to sit beside, else nothing could ever write it (build_tag_diff).
        checks[key] = ('unknown' if not expected else 'correct' if actual == expected else 'mismatch' if actual
                       else 'missing' if tags.get(number) or data.get(number) else 'unknown')
    checks.update(artwork_checks(rows[0]['album_image_url'], tags, config))
    checks['edition'] = data.get('edition_status', 'not_checked')
    return {'status': 'issues' if any(v in ('missing', 'mismatch') for v in checks.values())
            else 'unknown' if any(v in ('unknown', 'not_checked') for v in checks.values()) else 'correct',
            'checks': checks, 'reference': data, 'source': 'file', 'owner_profile_id': file['owner_profile_id'],
            'file_id': file_id, 'track_id': file['track_id'], 'album_id': rows[0]['album_id'],
            'checked_at': datetime.now(timezone.utc).isoformat()}


_JSON_LINKS = {'key': None, 'at': 0.0, 'links': {}}
_JSON_LINKS_TTL = 60.0


def _json_file_links(conn):
    """``{file_id: {finding ids}}`` for pending findings that name a file in
    their details (``library_v2.file_id`` / ``validation.file_id``).

    reading that means parsing every pending finding's details, 16mb on a
    library with eight thousand retag findings, so it's built once and reused
    while the pending set is the same (count and newest id) and for at most a
    minute. a file id kept as a json string counts too, as it did when this was
    compared against the integer column.
    """
    db_file = conn.execute("PRAGMA database_list").fetchone()[2]
    count, newest = conn.execute(
        "SELECT (SELECT COUNT(*) FROM repair_findings WHERE status='pending'), "
        "(SELECT MAX(id) FROM repair_findings)").fetchone()
    key = (db_file, count, newest)
    now = time.monotonic()
    if _JSON_LINKS['key'] == key and now - _JSON_LINKS['at'] < _JSON_LINKS_TTL:
        return _JSON_LINKS['links']
    links = {}
    for finding_id, lv2, val in conn.execute(
            "SELECT id, json_extract(details_json,'$.library_v2.file_id'), "
            "json_extract(details_json,'$.validation.file_id') FROM repair_findings "
            "WHERE status='pending' AND details_json LIKE '%file_id%' "
            "AND json_valid(details_json)"):
        for value in (lv2, val):
            if value is not None:
                links.setdefault(str(value).strip(), set()).add(finding_id)
    _JSON_LINKS.update(key=key, at=now, links=links)
    return links


def _pending_findings_for_files(conn, file_ids):
    """``[{**finding, 'subject_file_id': id}, ...]``: each pending finding that
    names one of these files, by path, by file/track/album/artist id, or by the
    file id in its details.

    one indexed lookup per kind of link. it used to be a single join with all
    of them ORed, which matched every file against every pending finding with
    json parsing in between: seconds an album, a minute for an artist's
    appears-on page.
    """
    marks = ','.join('?' for _ in file_ids)
    subjects = conn.execute(
        f"SELECT tf.id, tf.path, t.id AS track_id, t.album_id, a.primary_artist_id "
        f"FROM lib2_track_files tf JOIN lib2_tracks t ON t.id=tf.track_id "
        f"JOIN lib2_albums a ON a.id=t.album_id WHERE tf.id IN ({marks})", list(file_ids),
    ).fetchall()
    if not subjects:
        return []
    by_path, by_key = {}, {}
    for row in subjects:
        fid = int(row['id'])
        if row['path']:
            by_path.setdefault(row['path'], set()).add(fid)
        # file and track ids only count for a finding with no path of its own
        by_key.setdefault(('file', f"lib2:{fid}", True), set()).add(fid)
        by_key.setdefault(('track', f"lib2:{row['track_id']}", True), set()).add(fid)
        by_key.setdefault(('album', f"lib2:{row['album_id']}", False), set()).add(fid)
        by_key.setdefault(('artist', f"lib2:{row['primary_artist_id']}", False), set()).add(fid)

    pairs = {}

    def _take(finding_rows, fids_of):
        for found in finding_rows:
            for fid in fids_of(found):
                pairs.setdefault((found['id'], fid), found)

    paths = list(by_path)
    for i in range(0, len(paths), 500):
        part = paths[i:i + 500]
        _take(conn.execute(
            f"SELECT * FROM repair_findings WHERE status='pending' AND file_path IN "
            f"({','.join('?' for _ in part)})", part).fetchall(),
            lambda f: by_path.get(f['file_path'], ()))
    for entity_type in ('file', 'track', 'album', 'artist'):
        keys = [k for k in by_key if k[0] == entity_type]
        for i in range(0, len(keys), 500):
            part = keys[i:i + 500]
            needs_no_path = part[0][2]
            _take(conn.execute(
                f"SELECT * FROM repair_findings WHERE status='pending' AND entity_type=? "
                f"AND entity_id IN ({','.join('?' for _ in part)})"
                + (" AND file_path IS NULL" if needs_no_path else ""),
                [entity_type, *[k[1] for k in part]]).fetchall(),
                lambda f, t=entity_type, n=needs_no_path: by_key.get((t, f['entity_id'], n), ()))
    links = _json_file_links(conn)
    by_finding = {}
    for fid in file_ids:
        for finding_id in links.get(str(fid), ()):
            by_finding.setdefault(finding_id, set()).add(int(fid))
    found_ids = list(by_finding)
    for i in range(0, len(found_ids), 500):
        part = found_ids[i:i + 500]
        _take(conn.execute(
            f"SELECT * FROM repair_findings WHERE status='pending' AND id IN "
            f"({','.join('?' for _ in part)})", part).fetchall(),
            lambda f: by_finding.get(f['id'], ()))

    out = []
    for (_finding_id, fid), found in pairs.items():
        finding = dict(found)
        finding['subject_file_id'] = fid
        out.append(finding)
    return out


def linked_findings_many(conn, files):
    """Resolve findings in bounded batches; retain file and library ownership."""
    files = {int(f['id']): f for f in files if f}
    result = {fid: [] for fid in files}
    if not files or not conn.execute("SELECT 1 FROM sqlite_master WHERE name='repair_findings'").fetchone():
        return result
    ids = list(files)
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        rows = _pending_findings_for_files(conn, chunk)
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
            required = finding['details'].get('required_fields') or [k for k, v in (finding['details']['validation'].get('checks') or {}).items() if v in ('missing', 'mismatch')]
            details = {**finding['details'], 'validation': validation, 'required_fields': sorted(set(required) | set(issues))}
            conn.execute('UPDATE repair_findings SET details_json=?, updated_at=CURRENT_TIMESTAMP WHERE id=?',
                         (json.dumps(details), finding['id']))  # merge: keeps diff, manual flags, owner
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
            # Judge only what the fix wrote: requiring a correct disc tag the
            # fix never touches kept every disc-less file's finding open forever.
            details = finding.get('details') or {}
            wrote = {'disc_number': not details.get('disc_ok', True) and details.get('disc_number'),
                     'total_tracks': details.get('total_tracks')}
            if checks.get('track_number') != 'correct' or any(
                    v and checks.get(k) in ('missing', 'mismatch') for k, v in wrote.items()):
                return False
            # A finding that only corrects the total needs it positively confirmed.
            if wrote['total_tracks'] and details.get('current_track_num') == details.get(
                    'correct_track_num') and checks.get('total_tracks') != 'correct':
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
