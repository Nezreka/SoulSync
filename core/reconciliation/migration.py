"""Keep album review identity when a media server replaces its album ID."""
from __future__ import annotations

import json
import os


def migrate_album(conn, old_id, new_id):
    """Run inside the transaction that migrates the album and its tracks."""
    old_id, new_id = str(old_id), str(new_id)
    if old_id == new_id:
        return
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    def case_key(key):
        parts = str(key or '').split(':', 3)
        if len(parts) == 4 and parts[0] == 'folder' and parts[2] == old_id:
            parts[2] = new_id
            return ':'.join(parts)
        if len(parts) == 3 and parts[0] == 'album' and parts[2] == old_id:
            parts[2] = new_id
            return ':'.join(parts)
        return key

    def document(value, key=''):
        if isinstance(value, dict):
            return {(new_id if key == 'album_folder_counts' and str(k) == old_id else k):
                    document(v, k) for k, v in value.items()}
        if isinstance(value, list):
            return [document(v, 'album_id' if key == 'album_ids' else key) for v in value]
        if key in ('album_id', 'trusted_album_id') and str(value) == old_id:
            return new_id
        if key in ('case_id', 'review_case_key'):
            return case_key(value)
        return value

    for table in ('reconcile_album_intent', 'reconcile_wanted_slots',
                  'reconcile_release_decisions', 'reconcile_track_assignment',
                  'reconcile_fulfillment', 'reconcile_album_plans'):
        if table not in tables:
            continue
        cols = [row[1] for row in conn.execute(f'PRAGMA table_info({table})')]
        previous = conn.execute(f'SELECT * FROM {table} WHERE album_id=?', (old_id,)).fetchall()
        conn.execute(f'DELETE FROM {table} WHERE album_id=?', (old_id,))
        for raw in previous:
            row = dict(zip(cols, raw))
            old_case = row.get('case_id')
            row['album_id'] = new_id
            if old_case:
                row['case_id'] = case_key(old_case)
                conn.execute('CREATE TABLE IF NOT EXISTS reconcile_case_redirects '
                             '(old_case_id TEXT PRIMARY KEY, new_case_id TEXT NOT NULL)')
                if old_case != row['case_id']:
                    conn.execute('INSERT OR REPLACE INTO reconcile_case_redirects VALUES (?, ?)',
                                 (old_case, row['case_id']))
            if row.get('plan_json'):
                plan = document(json.loads(row['plan_json']))
                row['plan_json'] = json.dumps(plan)
                # Playlist-derived partial ownership can disappear when the
                # media server replaces track IDs too. Retain that existing
                # album scope, while respecting a newer explicit choice.
                if ('reconcile_album_intent' in tables and plan.get('intent_scope') in ('partial', 'complete')
                        and plan.get('source') and plan.get('release_id')):
                    conn.execute('INSERT OR IGNORE INTO reconcile_album_intent '
                        '(album_id,scope,source,release_id) VALUES(?,?,?,?)',
                        (new_id, plan['intent_scope'], plan['source'], plan['release_id']))
            existing = conn.execute(f'SELECT * FROM {table} WHERE album_id=?', (new_id,)).fetchall()
            if table == 'reconcile_wanted_slots' and existing:
                prior = dict(zip(cols, existing[0]))
                if (prior['source'], prior['release_id']) == (row['source'], row['release_id']):
                    row['slots_json'] = json.dumps(sorted(set(json.loads(prior['slots_json'])) |
                                                         set(json.loads(row['slots_json']))))
                    conn.execute(f'DELETE FROM {table} WHERE album_id=?', (new_id,))
            # Newer records at the destination win any exact identity collision.
            conn.execute(f'INSERT OR IGNORE INTO {table} ({",".join(cols)}) '
                         f'VALUES ({",".join("?" for _ in cols)})', [row[k] for k in cols])
        conn.execute(f'DELETE FROM {table} WHERE album_id=?', (old_id,))

    for table, identity, json_col in (
            ('repair_findings', 'id', 'details_json'),
            ('reconcile_review_choices', 'finding_id', 'evidence_json')):
        if table not in tables:
            continue
        columns = {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}
        if json_col not in columns:
            continue
        case_col = 'review_case_key' if table == 'repair_findings' else 'case_id'
        for fid, raw, key in conn.execute(
                f'SELECT {identity}, {json_col}, {case_col} FROM {table} '
                f'WHERE json_valid({json_col}) AND CAST(json_extract({json_col}, "$.album_id") AS TEXT)=?',
                (old_id,)).fetchall():
            conn.execute(f'UPDATE {table} SET {json_col}=?, {case_col}=? WHERE {identity}=?',
                         (json.dumps(document(json.loads(raw))), case_key(key), fid))
    if 'reconcile_saved_plans' in tables:
        # Their signature binds the old IDs. Individual choices survive; bulk
        # approval must be rebuilt against the migrated inventory.
        conn.execute('DELETE FROM reconcile_saved_plans WHERE case_id LIKE ?',
                     (f'folder:%:{old_id}:%',))


def repair_legacy_ids(db):
    """Recover old records only when surviving inventory proves one new owner."""
    conn = db._get_connection()
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'reconcile_album_plans', 'albums', 'tracks'} <= tables:
            return
        plans = conn.execute('SELECT album_id, profile_id, plan_json FROM reconcile_album_plans '
                             'WHERE album_id NOT IN (SELECT id FROM albums)').fetchall()
        for old_id, profile, raw in plans:
            inventory = json.loads(raw or '{}').get('inventory') or {}
            ids = inventory.get('track_ids') or []
            owners = conn.execute('SELECT DISTINCT t.album_id, a.owner_profile_id FROM tracks t '
                'JOIN albums a ON a.id=t.album_id WHERE t.id IN (SELECT value FROM json_each(?))',
                (json.dumps(ids),)).fetchall() if ids else []
            if not owners and old_id:
                # Some servers replace track IDs together with the album ID.
                # The file must still be the same inode through either mount.
                found = set()
                for path in inventory.get('paths') or []:
                    if not os.path.isfile(path):
                        continue
                    name = os.path.basename(path).replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
                    candidates = conn.execute("SELECT t.album_id, a.owner_profile_id, t.file_path "
                        "FROM tracks t JOIN albums a ON a.id=t.album_id "
                        "WHERE t.file_path=? OR t.file_path LIKE ? ESCAPE '\\'",
                        (path, '%/' + name)).fetchall()
                    for album, owner, candidate in candidates:
                        try:
                            if os.path.samefile(path, candidate):
                                found.add((album, owner))
                        except (OSError, TypeError):
                            continue
                owners = list(found)
            if len(owners) == 1 and int(owners[0][1] or 1) == int(profile or 1):
                migrate_album(conn, old_id, owners[0][0])
        conn.commit()
    finally:
        conn.close()
