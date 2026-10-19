"""Draft decisions bound to the exact evidence a person reviewed.

Saving a choice never writes audio files or resolves a finding. Scanner updates
invalidate choices automatically when their evidence differs.
"""
from __future__ import annotations

from copy import deepcopy
import json

CREATE_TABLE = '''CREATE TABLE IF NOT EXISTS reconcile_review_choices (
    finding_id INTEGER PRIMARY KEY,
    case_id TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    category TEXT NOT NULL,
    choice TEXT NOT NULL CHECK(choice IN ('fix', 'dismiss')),
    fix_action TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)'''


def ensure_schema(conn):
    conn.execute(CREATE_TABLE)
    if 'fix_action' not in {row[1] for row in conn.execute('PRAGMA table_info(reconcile_review_choices)')}:
        conn.execute("ALTER TABLE reconcile_review_choices ADD COLUMN fix_action TEXT NOT NULL DEFAULT ''")
    conn.execute("""CREATE TABLE IF NOT EXISTS reconcile_saved_plans (
        case_id TEXT PRIMARY KEY, signature TEXT NOT NULL,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")


def load_choices(conn, finding_id=None, *, case_id=None):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='reconcile_review_choices'").fetchone():
        return {}
    action_column = 'fix_action' if 'fix_action' in {row[1] for row in conn.execute('PRAGMA table_info(reconcile_review_choices)')} else "''"
    sql = f'SELECT finding_id, case_id, evidence_json, category, choice, {action_column} FROM reconcile_review_choices'
    if finding_id is not None:
        sql, params = sql + ' WHERE finding_id=?', (finding_id,)
    elif case_id is not None:
        sql, params = sql + ' WHERE case_id=?', (case_id,)
    else:
        params = ()
    return {row[0]: tuple(row[1:]) for row in conn.execute(sql, params)}


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
    if choice == 'fix':
        result['plan_approved'] = True
    elif category == 'numbering':
        for key in ('track_number', 'disc_number'):
            for section in ('db_data', 'db_track_updates'):
                result.get(section, {}).pop(key, None)
            for change in (key, 'library_' + key):
                result.get('changes', {}).pop(change, None)
        # Keep the filename with its current position too. Other tag edits stay.
        result.pop('target_path', None)
        result.get('changes', {}).pop('filename', None)
        result['plan_keep'] = not bool(result.get('changes'))
    else:
        result['plan_keep'] = True
        result['changes'] = {}
    return result


def plan_signature(items):
    import hashlib
    payload = [{'id': item['id'], 'details': item['details'],
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
        for path, status, action, choice, raw in conn.execute('''
            SELECT f.file_path, f.status, f.user_action, c.choice, c.evidence_json
            FROM reconcile_review_choices c JOIN repair_findings f ON f.id=c.finding_id
            WHERE c.category='numbering' ORDER BY f.id DESC'''):
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
    fields = {'track_number', 'disc_number', 'library_track_number', 'library_disc_number'}
    before = {k: v for k, v in (previous.get('changes') or {}).items() if k in fields}
    after = {k: v for k, v in (current.get('changes') or {}).items() if k in fields}
    return bool(before) and before == after


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


def same_annotated_evidence(previous, current):
    """Adding explanations alone preserves an otherwise exact saved choice."""
    annotations = {'assignment_evidence', 'recording_identity',
                   'recommended_keeper', 'keeper_reason', 'tool_checks', 'match_reasons'}
    empty_defaults = {'metadata_review_reason', 'album_consistency', 'companion_count', 'recording_id_conflict'}
    def substantive(details):
        return {key: value for key, value in details.items()
                if key not in annotations and (key not in empty_defaults or value)}
    return bool(previous) and substantive(previous) == substantive(current)


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
