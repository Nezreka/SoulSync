"""Durable snapshots of bulk operations, addressed by their immutable ID."""

import json
import time
import uuid


def _table(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS repair_operation_reports (
        operation_id TEXT PRIMARY KEY, updated_at REAL NOT NULL, report TEXT NOT NULL)''')


def save_report(db, state):
    conn = db._get_connection()
    try:
        _table(conn)
        conn.execute('INSERT OR REPLACE INTO repair_operation_reports VALUES (?, ?, ?)',
                     (state['operation_id'], time.time(), json.dumps(state)))
        conn.execute('DELETE FROM repair_operation_reports WHERE updated_at < ?',
                     (time.time() - 30 * 86400,))
        conn.commit()
    finally:
        conn.close()


def load_report(db, operation_id):
    conn = db._get_connection()
    try:
        _table(conn)
        row = conn.execute('SELECT report FROM repair_operation_reports WHERE operation_id = ?',
                           (operation_id,)).fetchone()
        return json.loads(row[0]) if row else None
    finally:
        conn.close()


def record_case_left_as_is(conn, case_id):
    """Close recovery prompts without rewriting an earlier failure report."""
    _table(conn)
    report = {'operation_id': uuid.uuid4().hex, 'review_case_ids': [case_id],
              'review_scope': 'album', 'phase': 'left_as_is', 'running': False,
              'fixed': 0, 'failed': 0, 'error': None}
    conn.execute('INSERT INTO repair_operation_reports VALUES (?, ?, ?)',
                 (report['operation_id'], time.time(), json.dumps(report)))


def latest_case_report(db, case_id):
    conn = db._get_connection()
    try:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='repair_operation_reports'").fetchone():
            return None
        keys = {case_id}
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='reconcile_case_redirects'").fetchone():
            redirects = conn.execute('SELECT old_case_id,new_case_id FROM reconcile_case_redirects').fetchall()
            while True:
                parents = {old for old, new in redirects if new in keys} - keys
                if not parents:
                    break
                keys.update(parents)
        row = conn.execute('''SELECT report,updated_at FROM repair_operation_reports
            WHERE EXISTS (SELECT 1 FROM json_each(report, '$.review_case_ids')
                WHERE value IN (SELECT value FROM json_each(?)))
            ORDER BY updated_at DESC LIMIT 1''', (json.dumps(sorted(keys)),)).fetchone()
        return {**json.loads(row[0]), 'updated_at': row[1]} if row else None
    finally:
        conn.close()
