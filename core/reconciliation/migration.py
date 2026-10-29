"""Keep album review identity when a media server replaces its album ID."""
from __future__ import annotations

import json
import os
from core.reconciliation.stores import ALBUM_ID_STORES, DOCUMENT_STORES


class MigrationConflict(ValueError):
    """Two different current decisions require resolution before IDs can merge."""


def migrate_album(conn, old_id, new_id):
    if not conn.in_transaction:
        conn.execute('BEGIN')
    conn.execute('SAVEPOINT review_identity_migration')
    try:
        _migrate_album(conn, old_id, new_id)
    except Exception:
        conn.execute('ROLLBACK TO review_identity_migration')
        conn.execute('RELEASE review_identity_migration')
        raise
    conn.execute('RELEASE review_identity_migration')


def _migrate_album(conn, old_id, new_id):
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

    for table in ALBUM_ID_STORES:
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
            primary = [column[1] for column in conn.execute(f'PRAGMA table_info({table})') if column[5]]
            target = conn.execute(f'SELECT * FROM {table} WHERE ' + ' AND '.join(f'{key}=?' for key in primary),
                                  [row[key] for key in primary]).fetchone()
            if target:
                target = dict(zip(cols, target))
                if table in ('reconcile_album_plans', 'reconcile_fulfillment'):
                    # Evidence snapshots and acquisition progress can advance;
                    # they cannot silently change their release or profile.
                    identity = ('source', 'release_id', 'profile_id')
                    if any(row.get(key) != target.get(key) for key in identity):
                        raise MigrationConflict(f'{table}: conflicting release or profile for album {new_id}')
                    if str(target.get('updated_at') or '') >= str(row.get('updated_at') or ''):
                        continue
                elif any(row[key] != target[key] for key in cols if key not in ('updated_at', 'created_at')):
                    raise MigrationConflict(f'{table}: conflicting saved decisions for album {new_id}')
                else:
                    continue
                conn.execute(f'DELETE FROM {table} WHERE ' + ' AND '.join(f'{key}=?' for key in primary),
                             [row[key] for key in primary])
            conn.execute(f'INSERT INTO {table} ({",".join(cols)}) '
                         f'VALUES ({",".join("?" for _ in cols)})', [row[k] for k in cols])
        conn.execute(f'DELETE FROM {table} WHERE album_id=?', (old_id,))

    for table, identity, json_col, case_col in DOCUMENT_STORES:
        if table not in tables:
            continue
        columns = {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}
        if json_col not in columns:
            continue
        for fid, raw, key in conn.execute(
                f'SELECT {identity}, {json_col}, {case_col} FROM {table} '
                f'WHERE json_valid({json_col}) AND CAST(json_extract({json_col}, "$.album_id") AS TEXT)=?',
                (old_id,)).fetchall():
            conn.execute(f'UPDATE {table} SET {json_col}=?, {case_col}=? WHERE {identity}=?',
                         (json.dumps(document(json.loads(raw))), case_key(key), fid))
            signature_column = {'repair_findings': 'review_signature', 'reconcile_review_choices': 'approval_signature'}.get(table)
            if signature_column in columns:
                from core.reconciliation.signatures import signature
                conn.execute(f'UPDATE {table} SET {signature_column}=? WHERE {identity}=?',
                             (signature(document(json.loads(raw))), fid))
    if 'reconcile_reviews' in tables:
        from core.reconciliation.decisions import rebind_review
        for key, in conn.execute('SELECT case_id FROM reconcile_reviews WHERE album_id=?', (old_id,)).fetchall():
            rebind_review(conn, key, case_key(key), new_id)
    if 'reconcile_saved_plans' in tables:
        # Their signature binds the old IDs. Individual choices survive; bulk
        # approval must be rebuilt against the migrated inventory.
        conn.execute('DELETE FROM reconcile_saved_plans WHERE case_id LIKE ?',
                     (f'folder:%:{old_id}:%',))
        conn.execute('DELETE FROM reconcile_saved_plans WHERE case_id LIKE ?', (f'album:%:{old_id}',))


def upgrade_review_state(conn):
    if not conn.in_transaction:
        conn.execute('BEGIN')
    conn.execute('SAVEPOINT review_schema_migration')
    try:
        _upgrade_review_state(conn)
        _upgrade_signatures(conn)
    except Exception:
        conn.execute('ROLLBACK TO review_schema_migration')
        conn.execute('RELEASE review_schema_migration')
        raise
    conn.execute('RELEASE review_schema_migration')


def _upgrade_review_state(conn):
    """Versioned data migration at database startup, within its transaction."""
    from core.reconciliation import decisions
    conn.execute('CREATE TABLE IF NOT EXISTS reconcile_schema_versions(version INTEGER PRIMARY KEY)')
    if conn.execute('SELECT 1 FROM reconcile_schema_versions WHERE version=1').fetchone():
        return
    for case, album, profile, raw in conn.execute(
            'SELECT case_id,album_id,profile_id,plan_json FROM reconcile_album_plans').fetchall():
        decisions.register_review(conn, case, album, profile)
        plan = json.loads(raw)
        conn.execute('UPDATE reconcile_album_plans SET numbering_signature=? WHERE case_id=?',
                     (decisions.plan_numbering_signature(plan), case))
        votes = {choice for choice, evidence in conn.execute('''
            SELECT CASE WHEN c.category='numbering' THEN c.choice ELSE c.numbering_choice END,c.evidence_json
            FROM reconcile_review_choices c JOIN repair_findings f ON f.id=c.finding_id
            WHERE c.case_id=? AND c.evidence_json=f.details_json AND f.user_action IS NOT 'obsolete'
              AND (c.category='numbering' OR c.numbering_choice!='')''', (case,))
            if (json.loads(evidence).get('source'), json.loads(evidence).get('release_id')) ==
               (plan.get('source'), plan.get('release_id'))}
        if len(votes) == 1:
            decisions.save_numbering(conn, case, next(iter(votes)))
    # These obsolete blockers belong to the old immediate-removal flow. Only
    # unchanged, explicitly saved replacements qualify for this one-time repair.
    conn.execute('''UPDATE repair_findings SET last_error=NULL WHERE id IN (
        SELECT f.id FROM repair_findings f JOIN reconcile_review_choices c
        ON c.finding_id=f.id AND c.evidence_json=f.details_json
        WHERE f.status='pending' AND c.choice='fix' AND c.fix_action='replace'
        AND f.last_error IN ('This copy is in a playlist; review that reference first',
                             'The current file is in a playlist; review that reference first'))''')
    conn.execute('INSERT INTO reconcile_schema_versions VALUES(1)')


def _upgrade_signatures(conn):
    from core.reconciliation.signatures import signature
    if conn.execute('SELECT 1 FROM reconcile_schema_versions WHERE version=2').fetchone():
        return
    for fid, raw in conn.execute("SELECT id,details_json FROM repair_findings WHERE job_id='library_reconciliation' AND json_valid(details_json)").fetchall():
        conn.execute('UPDATE repair_findings SET review_signature=? WHERE id=?', (signature(json.loads(raw)), fid))
    for fid, raw in conn.execute('SELECT finding_id,evidence_json FROM reconcile_review_choices WHERE json_valid(evidence_json)').fetchall():
        conn.execute('UPDATE reconcile_review_choices SET approval_signature=? WHERE finding_id=?', (signature(json.loads(raw)), fid))
    conn.execute('INSERT INTO reconcile_schema_versions VALUES(2)')


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
