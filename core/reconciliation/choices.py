"""Draft decisions bound to the exact evidence a person reviewed.

Saving a choice never writes audio files or resolves a finding. Scanner updates
invalidate choices automatically when their evidence differs.
"""
from __future__ import annotations

from copy import deepcopy
import json
from core.reconciliation.fields import POSITION_FIELDS, FILE_POSITION_FIELDS
from core.reconciliation.signatures import signature, matches

CREATE_TABLE = '''CREATE TABLE IF NOT EXISTS reconcile_review_choices (
    finding_id INTEGER PRIMARY KEY,
    case_id TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    category TEXT NOT NULL,
    choice TEXT NOT NULL CHECK(choice IN ('fix', 'dismiss')),
    fix_action TEXT NOT NULL DEFAULT '',
    numbering_choice TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)'''


def ensure_schema(conn):
    conn.execute(CREATE_TABLE)
    if 'approval_signature' not in {row[1] for row in conn.execute('PRAGMA table_info(reconcile_review_choices)')}:
        conn.execute("ALTER TABLE reconcile_review_choices ADD COLUMN approval_signature TEXT NOT NULL DEFAULT ''")
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='repair_findings'").fetchone():
        if 'review_signature' not in {row[1] for row in conn.execute('PRAGMA table_info(repair_findings)')}:
            conn.execute("ALTER TABLE repair_findings ADD COLUMN review_signature TEXT NOT NULL DEFAULT ''")
    if 'fix_action' not in {row[1] for row in conn.execute('PRAGMA table_info(reconcile_review_choices)')}:
        conn.execute("ALTER TABLE reconcile_review_choices ADD COLUMN fix_action TEXT NOT NULL DEFAULT ''")
    if 'numbering_choice' not in {row[1] for row in conn.execute('PRAGMA table_info(reconcile_review_choices)')}:
        conn.execute("ALTER TABLE reconcile_review_choices ADD COLUMN numbering_choice TEXT NOT NULL DEFAULT ''")
    if 'playlist_override' not in {row[1] for row in conn.execute('PRAGMA table_info(reconcile_review_choices)')}:
        conn.execute("ALTER TABLE reconcile_review_choices ADD COLUMN playlist_override TEXT NOT NULL DEFAULT ''")
    conn.execute("""CREATE TABLE IF NOT EXISTS reconcile_saved_plans (
        case_id TEXT PRIMARY KEY, signature TEXT NOT NULL,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")

def load_choices(conn, finding_id=None, *, case_id=None):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='reconcile_review_choices'").fetchone():
        return {}
    columns = {row[1] for row in conn.execute('PRAGMA table_info(reconcile_review_choices)')}
    action_column = 'fix_action' if 'fix_action' in columns else "''"
    numbering_column = 'numbering_choice' if 'numbering_choice' in columns else "''"
    override_column = 'playlist_override' if 'playlist_override' in columns else "''"
    sql = f'SELECT finding_id, case_id, evidence_json, category, choice, {action_column}, {numbering_column}, {override_column} FROM reconcile_review_choices'
    if finding_id is not None:
        sql, params = sql + ' WHERE finding_id=?', (finding_id,)
    elif case_id is not None:
        sql, params = sql + ' WHERE case_id=?', (case_id,)
    else:
        params = ()
    result = {row[0]: tuple(row[1:]) for row in conn.execute(sql, params)}
    # Rebind only the read projection. Immutable approval evidence remains an
    # audit snapshot; execution still checks the current file and target.
    if result and 'approval_signature' in columns:
        query = '''SELECT c.finding_id,f.details_json FROM reconcile_review_choices c
            JOIN repair_findings f ON f.id=c.finding_id AND c.case_id=f.review_case_key
            WHERE c.approval_signature!='' AND c.approval_signature=f.review_signature'''
        if finding_id is not None:
            query += ' AND c.finding_id=?'
        elif case_id is not None:
            query += ' AND c.case_id=?'
        for fid, raw in conn.execute(query, params):
            result[fid] = (result[fid][0], raw, *result[fid][2:])
    from core.reconciliation.decisions import numbering_choices
    numbering = numbering_choices(conn)
    if numbering:
        query = "SELECT id,review_case_key,details_json FROM repair_findings WHERE finding_type='reconciliation_metadata' AND status='pending'"
        query_params = ()
        if finding_id is not None:
            query += ' AND id=?'
            query_params = (finding_id,)
        elif case_id is not None:
            query += ' AND review_case_key=?'
            query_params = (case_id,)
        for fid, key, raw in conn.execute(query, query_params):
            choice = numbering.get(key)
            if not choice:
                continue
            previous = result.get(fid)
            if previous and previous[0] == key and previous[1] == raw and previous[2] != 'numbering':
                result[fid] = (*previous[:5], choice, *previous[6:])
            else:
                result[fid] = (key, raw, 'numbering', choice, '', choice, '')
    return result


def project(details, saved, *, case_id=None, raw_json=None):
    """Return the effective proposal, preserving the immutable scan evidence."""
    if not saved or (case_id is not None and saved[0] != case_id):
        return details
    if raw_json is not None and raw_json != saved[1]:
        return details
    try:
        if json.loads(saved[1]) != details:
            return details
    except (ValueError, TypeError):
        return details
    result = deepcopy(details)
    category, choice = saved[2:4]
    result['planned_choice'] = {'category': category, 'action': choice, 'fix_action': saved[4] if len(saved) > 4 else ''}
    if len(saved) > 6 and saved[6] and choice == 'fix':
        result['playlist_override'] = json.loads(saved[6])
    numbering = choice if category == 'numbering' else saved[5] if len(saved) > 5 else ''
    if numbering:
        result['numbering_choice'] = numbering
    if numbering == 'dismiss':
        result.get('db_data', {}).pop('track_count', None)
        for key in FILE_POSITION_FIELDS:
            for section in ('db_data', 'db_track_updates'):
                result.get(section, {}).pop(key, None)
            for change in (key, 'library_' + key):
                result.get('changes', {}).pop(change, None)
        # Keep the filename with its current position too. Other tag edits stay.
        result.pop('target_path', None)
        result.get('changes', {}).pop('filename', None)
        result['plan_keep'] = not bool(result.get('changes'))
    if category != 'numbering' and choice == 'fix':
        result['plan_approved'] = True
        if result['planned_choice']['fix_action'] in ('quarantine', 'replace', 'request_missing') or result['planned_choice']['fix_action'].startswith('existing:'):
            result['changes'] = {}
    elif category != 'numbering' and choice == 'dismiss':
        result['plan_keep'] = True
        result['changes'] = {}
    return result


def plan_signature(items):
    import hashlib
    payload = [{'id': item['id'], 'details': signature(item['details']),
                'type': item['finding_type'], 'error': item.get('last_error')}
               for item in sorted(items, key=lambda row: row['id'])]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def applied_numbering_choices(db):
    """Keep an applied rejection of unchanged numbering across metadata repairs."""
    conn = db._get_connection()
    try:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='reconcile_review_choices'").fetchone():
            return {}
        result, seen = {}, set()
        ensure_schema(conn)
        for path, status, action, choice, raw in conn.execute('''
            SELECT f.file_path, f.status, f.user_action,
                CASE WHEN c.category='numbering' THEN c.choice ELSE c.numbering_choice END,
                c.evidence_json
            FROM reconcile_review_choices c JOIN repair_findings f ON f.id=c.finding_id
            WHERE c.category='numbering' OR c.numbering_choice != '' ORDER BY f.id DESC'''):
            if path in seen:
                continue
            seen.add(path)
            if status in ('resolved', 'dismissed') and action != 'obsolete' and choice == 'dismiss':
                result[path] = json.loads(raw)
        return result
    finally:
        conn.close()


def retains_numbering(previous, current):
    if not previous or not previous.get('fingerprint_key'):
        return False
    if any(previous.get(key) != current.get(key) for key in
           ('source', 'release_id', 'fingerprint_key', 'expected_title')):
        return False
    fields = POSITION_FIELDS
    before = {k: v for k, v in (previous.get('changes') or {}).items() if k in fields}
    after = {k: v for k, v in (current.get('changes') or {}).items() if k in fields}
    return bool(before) and before == after


def flattened_numbering_history(db):
    """Read latest numbering repairs, including those superseding legacy fixes."""
    conn = db._get_connection()
    try:
        history, seen = {}, set()
        for track_id, path, raw in conn.execute('''SELECT entity_id, file_path, details_json
                FROM repair_findings WHERE job_id='library_reconciliation'
                AND finding_type='reconciliation_metadata' AND status='resolved'
                AND user_action='reconciliation_metadata' ORDER BY id DESC'''):
            details = json.loads(raw or '{}')
            positions = {'track_number', 'disc_number', 'total_discs', 'library_track_number', 'library_disc_number'}
            if not positions.intersection(details.get('changes') or {}):
                continue
            identities = [('track', str(track_id))] if track_id else []
            if path:
                identities.append(('path', path))
            for identity in identities:
                if identity in seen:
                    continue
                seen.add(identity)
                history[identity] = details
            if (details.get('fingerprint_key') and details.get('album_id')
                    and details.get('source') and details.get('release_id')):
                identity = ('audio', str(details['album_id']), details['source'],
                            details['release_id'], details['fingerprint_key'])
                history.setdefault(identity, details)
        return history
    finally:
        conn.close()


def recovers_flattened_numbering(history, file, *, album_id=None, source='', release_id=''):
    # A library rescan can replace a track row while leaving the file intact.
    # A moved and reindexed file needs the same album, release and fingerprint.
    previous = (history.get(('track', str((file.get('db') or {}).get('id'))))
                or history.get(('path', file.get('path')))
                or history.get(('audio', str(album_id), source, release_id,
                                (file.get('acoustid') or {}).get('fingerprint_key'))))
    return bool(previous and previous.get('numbering_mode') == 'digital'
                and (previous.get('release_disc_count') or 0) > 1
                and previous.get('fingerprint_key')
                and previous['fingerprint_key'] == (file.get('acoustid') or {}).get('fingerprint_key'))


def same_relocated_evidence(previous, current):
    """A verified path change alone does not revoke a reviewed decision."""
    if not previous.get('fingerprint_key') or previous.get('fingerprint_key') != current.get('fingerprint_key'):
        return False
    if previous.get('album_folder') == current.get('album_folder'):
        return False
    volatile = {'album_folder', 'album_folders', 'folder_audio_path_digest',
                'evidence_signature', 'mtime_ns', 'target_path'}
    return ({k: v for k, v in previous.items() if k not in volatile} ==
            {k: v for k, v in current.items() if k not in volatile})


def remember_release_choice(db, album_id, source, release_id):
    """Distinguish a reviewer's edition choice from an older canonical lock."""
    conn = db._get_connection()
    try:
        conn.execute('''CREATE TABLE IF NOT EXISTS reconcile_release_decisions (
            album_id TEXT PRIMARY KEY, source TEXT NOT NULL, release_id TEXT NOT NULL)''')
        conn.execute('INSERT OR REPLACE INTO reconcile_release_decisions VALUES (?, ?, ?)',
                     (str(album_id), source, str(release_id)))
        conn.commit()
    finally:
        conn.close()


def load_release_choices(db):
    conn = db._get_connection()
    try:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='reconcile_release_decisions'").fetchone():
            return {}
        return {row[0]: (row[1], row[2]) for row in conn.execute(
            'SELECT album_id, source, release_id FROM reconcile_release_decisions')}
    finally:
        conn.close()


def same_annotated_evidence(previous, current):
    """Adding explanations alone preserves an otherwise exact saved choice."""
    annotations = {'library_root', 'pairing_checks', 'assignment_evidence', 'recording_identity', 'numbering_mode',
                   'recommended_keeper', 'keeper_reason', 'tool_checks', 'match_reasons',
                   'expected_artist_names', 'artist_aliases', 'routine_numbering',
                   'numbering_recovery', 'title_difference_kind', 'competitors', 'current_release'}
    for added in ('replacement_files', 'replacement_track'):
        if previous and added not in previous:
            annotations.add(added)
    if previous and not previous.get('file_identity'):
        annotations.add('file_identity')
    if previous and previous.get('duplicate_copies'):
        annotations.update(key for key in ('expected_title', 'expected_duration_ms', 'expected_track_number', 'expected_disc_number') if key not in previous)
    empty_defaults = {'metadata_review_reason', 'album_consistency', 'companion_count',
                      'recording_id_conflict', 'catalogue_disagreement'}
    def substantive(details):
        result = {key: value for key, value in details.items()
                  if key not in annotations and (key not in empty_defaults or value)}
        # These labels explain existing targets; the option identities and
        # actual old/new values still have to match exactly.
        if 'current_tags' in result:
            result['current_tags'] = {k: v for k, v in result['current_tags'].items()
                                      if (k != 'artists_list' or v) and
                                      (k != 'total_discs' or 'total_discs' in (previous.get('current_tags') or {}))}
        if 'assignment_options' in result:
            result['assignment_options'] = [
                {key: value for key, value in option.items() if key != 'artist'}
                for option in result['assignment_options']]
        if 'changes' in result:
            result['changes'] = {
                field: {key: value for key, value in change.items()
                        if key not in {'old_label', 'new_label'}}
                for field, change in result['changes'].items()}
        return result
    return matches(previous, current) or bool(previous) and substantive(previous) == substantive(current)


def assignment_for_file(overrides, album_id, source, release_id, file, files, *, db=None):
    """Retain an assignment after a unique, verified move of the same audio."""
    import os
    prefix = (str(album_id), source, release_id)
    exact = overrides.get((*prefix, file['path']))
    if exact:
        return exact
    fingerprint = (file.get('acoustid') or {}).get('fingerprint_key')
    if not fingerprint:
        return None
    matches = [(key, value) for key, value in overrides.items()
               if key[:3] == prefix and not os.path.exists(key[3])
               and value[1] == file['size_bytes'] and value[3] == fingerprint]
    copies = [row for row in files if row['size_bytes'] == file['size_bytes']
              and (row.get('acoustid') or {}).get('fingerprint_key') == fingerprint]
    if len(matches) == len(copies) == 1:
        old_key, previous = matches[0]
        slot, size, _, fingerprint_key = previous
        recovered = (slot, size, file['mtime_ns'], fingerprint_key)
        if db is not None:
            from core.library.maintenance_lock import mutation_lock
            with mutation_lock:
                # All consumers (reset, reopen, authority and slot ownership)
                # must see the same verified move. Do not overwrite a choice
                # that changed after this scan loaded its assignment snapshot.
                if os.path.exists(old_key[3]):
                    return None
                conn = db._get_connection()
                try:
                    changed = conn.execute('''UPDATE reconcile_track_assignment
                        SET file_path=?, mtime_ns=? WHERE album_id=? AND source=?
                        AND release_id=? AND file_path=? AND slot_key=?
                        AND size_bytes=? AND mtime_ns=? AND fingerprint_key=?''',
                        (file['path'], file['mtime_ns'], *old_key, *previous)).rowcount
                    if changed != 1:
                        conn.rollback()
                        return None
                    conn.commit()
                finally:
                    conn.close()
                overrides.pop(old_key, None)
                overrides[(*prefix, file['path'])] = recovered
        return recovered
    return None


def remember_applied_releases(db, details):
    """Applying a reviewed plan also accepts its release context."""
    selected = {}
    for item in details:
        if item.get('album_id') is not None and item.get('source') and item.get('release_id'):
            selected[str(item['album_id'])] = (item['source'], str(item['release_id']))
    for album_id, (source, release_id) in selected.items():
        remember_release_choice(db, album_id, source, release_id)


def backfill_applied_releases(db):
    """Recover release acceptance from successful, explicitly approved operations."""
    conn = db._get_connection()
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'repair_operation_reports', 'repair_findings', 'albums'} <= tables:
            return
        existing = load_release_choices(db)
        accepted = []
        for raw, in conn.execute('SELECT report FROM repair_operation_reports ORDER BY updated_at DESC'):
            report = json.loads(raw or '{}')
            if (not report.get('review_approved') or report.get('running') or report.get('failed')
                    or report.get('error') or report.get('stopped') or not report.get('fixed')):
                continue
            for key in report.get('review_case_ids') or []:
                from core.reconciliation.redirects import resolve_case
                key = resolve_case(db, key)
                rows = conn.execute("SELECT details_json FROM repair_findings WHERE review_case_key=? "
                    "AND status='resolved' AND user_action IN ('reconciliation_metadata', 'replacement_requested', 'wishlisted') "
                    "ORDER BY id DESC", (key,)).fetchall()
                for value, in rows:
                    item = json.loads(value or '{}')
                    album_id = str(item.get('album_id') or '')
                    if not album_id or album_id in existing or not item.get('source') or not item.get('release_id'):
                        continue
                    album = conn.execute('SELECT canonical_locked, canonical_source, canonical_album_id '
                                         'FROM albums WHERE id=?', (album_id,)).fetchone()
                    if not album or (album[0] and (album[1], str(album[2])) != (item['source'], str(item['release_id']))):
                        continue
                    accepted.append(item)
                    existing[album_id] = (item['source'], str(item['release_id']))
    finally:
        conn.close()
    remember_applied_releases(db, accepted)
