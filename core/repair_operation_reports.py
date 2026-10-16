"""Durable snapshots of bulk operations, addressed by their immutable ID."""

import json
import time


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
