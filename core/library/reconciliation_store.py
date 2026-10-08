"""Persist the latest complete album reconciliation view.

Repair findings remain the actionable write model.  This store keeps the
successful assignments and album context that findings intentionally omit, so
reviewing one exception never makes the rest of its album disappear.
"""

from __future__ import annotations

import json
from typing import Any

from core.repair_review_identity import review_case_key


CREATE_TABLE = '''
CREATE TABLE IF NOT EXISTS reconcile_album_plans (
    case_id TEXT PRIMARY KEY,
    album_id TEXT,
    profile_id INTEGER NOT NULL DEFAULT 1,
    album_title TEXT NOT NULL DEFAULT '',
    artist_name TEXT NOT NULL DEFAULT '',
    album_folder TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    release_id TEXT NOT NULL DEFAULT '',
    intent_scope TEXT NOT NULL DEFAULT '',
    evidence_signature TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'review',
    confirmed_count INTEGER NOT NULL DEFAULT 0,
    changed_count INTEGER NOT NULL DEFAULT 0,
    uncertain_count INTEGER NOT NULL DEFAULT 0,
    missing_count INTEGER NOT NULL DEFAULT 0,
    extra_count INTEGER NOT NULL DEFAULT 0,
    categories_json TEXT NOT NULL DEFAULT '[]',
    plan_json TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
'''


def ensure_schema(conn) -> None:
    # Keep this safe inside the database bootstrap transaction. executescript
    # commits implicitly in sqlite3, which would split unrelated migrations.
    conn.execute(CREATE_TABLE)
    conn.execute('''CREATE INDEX IF NOT EXISTS idx_reconcile_plans_state
                    ON reconcile_album_plans(state, updated_at)''')
    conn.execute('''CREATE INDEX IF NOT EXISTS idx_reconcile_plans_album
                    ON reconcile_album_plans(profile_id, album_id)''')


def persist_album_plan(db, header: dict[str, Any], payload: dict[str, Any]) -> str:
    """Upsert one complete album snapshot and return its stable case ID."""
    case_id = review_case_key(header, None)
    assignments = payload.get('assignments') or []
    counts = {
        'confirmed': sum(row.get('status') == 'confirmed' for row in assignments),
        'changed': sum(row.get('status') == 'change' for row in assignments),
        'uncertain': sum(row.get('status') == 'uncertain' for row in assignments),
        'missing': sum(row.get('status') == 'missing' for row in assignments),
        'not_owned': sum(row.get('status') == 'not_owned' for row in assignments),
        'extra': sum(row.get('status') in ('extra', 'duplicate') for row in assignments),
    }
    categories = sorted(set(payload.get('categories') or []))
    state = str(payload.get('state') or (
        'review' if counts['uncertain'] or counts['missing'] or counts['extra']
        else 'changes' if counts['changed'] else 'confirmed'))
    document = {**payload, 'case_id': case_id, 'counts': counts,
                'categories': categories, 'state': state}
    conn = db._get_connection()
    try:
        ensure_schema(conn)
        conn.execute('''
            INSERT INTO reconcile_album_plans (
                case_id, album_id, profile_id, album_title, artist_name,
                album_folder, source, release_id, intent_scope,
                evidence_signature, state, confirmed_count, changed_count,
                uncertain_count, missing_count, extra_count, categories_json,
                plan_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                      CURRENT_TIMESTAMP)
            ON CONFLICT(case_id) DO UPDATE SET
                album_id=excluded.album_id, profile_id=excluded.profile_id,
                album_title=excluded.album_title, artist_name=excluded.artist_name,
                album_folder=excluded.album_folder, source=excluded.source,
                release_id=excluded.release_id, intent_scope=excluded.intent_scope,
                evidence_signature=excluded.evidence_signature, state=excluded.state,
                confirmed_count=excluded.confirmed_count,
                changed_count=excluded.changed_count,
                uncertain_count=excluded.uncertain_count,
                missing_count=excluded.missing_count, extra_count=excluded.extra_count,
                categories_json=excluded.categories_json,
                plan_json=excluded.plan_json, updated_at=CURRENT_TIMESTAMP
        ''', (
            case_id, str(header.get('album_id') or ''),
            int(header.get('profile_id') or 1), str(header.get('album_title') or ''),
            str(header.get('artist_name') or ''), str(header.get('album_folder') or ''),
            str(payload.get('source') or ''), str(payload.get('release_id') or ''),
            str(payload.get('intent_scope') or ''),
            str(header.get('evidence_signature') or ''), state,
            counts['confirmed'], counts['changed'], counts['uncertain'],
            counts['missing'], counts['extra'], json.dumps(categories),
            json.dumps(document, ensure_ascii=False, default=str),
        ))
        conn.commit()
        return case_id
    finally:
        conn.close()


def load_album_plan(conn, case_id: str) -> dict[str, Any] | None:
    """Load a plan when the installation has run the new scanner."""
    try:
        row = conn.execute(
            'SELECT plan_json FROM reconcile_album_plans WHERE case_id = ?',
            (case_id,),
        ).fetchone()
    except Exception:
        return None
    if not row:
        return None
    try:
        value = json.loads(row[0])
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def retire_unseen_plans(db, observed_case_ids: set[str]) -> None:
    """Remove snapshots for albums absent from a completed scan."""
    conn = db._get_connection()
    try:
        ensure_schema(conn)
        existing = {str(row[0]) for row in conn.execute(
            'SELECT case_id FROM reconcile_album_plans')}
        stale = existing - observed_case_ids
        if stale:
            # Avoid SQLite's host-parameter limit on large libraries.
            conn.executemany('DELETE FROM reconcile_album_plans WHERE case_id = ?',
                             [(case_id,) for case_id in stale])
        conn.commit()
    finally:
        conn.close()
