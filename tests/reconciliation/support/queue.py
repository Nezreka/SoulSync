"""Minimal queue persistence for projection and policy tests."""
import json
import sqlite3

from core.reconciliation.identity import review_case_key
from core.reconciliation.queue import (
    ready_batch_preview,
)


class _Connection(sqlite3.Connection):
    def close(self):
        pass


class _Database:
    def __init__(self):
        self.conn = sqlite3.connect(':memory:', factory=_Connection)
        self.conn.execute('''
            CREATE TABLE repair_findings (
                id INTEGER PRIMARY KEY, job_id TEXT, finding_type TEXT,
                severity TEXT, status TEXT, entity_type TEXT, entity_id TEXT,
                file_path TEXT, title TEXT, description TEXT, details_json TEXT,
                user_action TEXT, created_at TEXT, updated_at TEXT, resolved_at TEXT,
                last_error TEXT, review_case_key TEXT
            )
        ''')
        self.conn.execute('CREATE INDEX idx_review ON repair_findings(status, review_case_key)')
        from core.reconciliation.choices import ensure_schema
        ensure_schema(self.conn)

    def _get_connection(self):
        return self.conn

    def add(self, finding_id, path, details, *, status='pending', kind='acoustid_mismatch',
            job_id='acoustid_scanner'):
        self.conn.execute('''
            INSERT INTO repair_findings
            (id, job_id, finding_type, severity, status, file_path, title,
             details_json, created_at, updated_at, review_case_key)
            VALUES (?, ?, ?, 'warning', ?, ?, ?, ?,
                    '2026-10-05', '2026-10-05', ?)
        ''', (finding_id, job_id, kind, status, path, f'Finding {finding_id}',
              json.dumps(details), review_case_key(details, path, finding_id)))
        self.conn.commit()


def _ready_selection(db, *, case_id=''):
    preview = ready_batch_preview(db, case_id=case_id)
    return ([action['id'] for album in preview['albums'] for action in album['actions']],
            [album['case_id'] for album in preview['albums']])
