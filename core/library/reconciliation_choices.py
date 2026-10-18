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


def load_choices(conn, finding_id=None):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='reconcile_review_choices'").fetchone():
        return {}
    action_column = 'fix_action' if 'fix_action' in {row[1] for row in conn.execute('PRAGMA table_info(reconcile_review_choices)')} else "''"
    sql = f'SELECT finding_id, case_id, evidence_json, category, choice, {action_column} FROM reconcile_review_choices'
    return {row[0]: tuple(row[1:]) for row in conn.execute(
        sql + (' WHERE finding_id=?' if finding_id is not None else ''),
        (finding_id,) if finding_id is not None else ())}


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
