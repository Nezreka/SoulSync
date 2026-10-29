"""Current accepted track decisions, independent of prunable operation history.

Each file and finding kind has one current record. New acceptance replaces it;
reopening removes it. The finding ID is provenance, never a foreign-key owner.
"""
from __future__ import annotations

import hashlib
import json

from core.reconciliation import decisions, signatures


def ensure_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS reconcile_settled_decisions (
        decision_id TEXT PRIMARY KEY, review_id TEXT NOT NULL, case_id TEXT NOT NULL,
        finding_id INTEGER NOT NULL, file_path TEXT, entity_id TEXT, finding_type TEXT NOT NULL,
        status TEXT NOT NULL, user_action TEXT, details_json TEXT NOT NULL,
        choice TEXT NOT NULL DEFAULT '', category TEXT NOT NULL DEFAULT '',
        numbering_choice TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
    columns = {row[1] for row in conn.execute('PRAGMA table_info(reconcile_settled_decisions)')}
    for field in ('fields_json', 'numbering_evidence_json'):
        if field not in columns:
            conn.execute(f"ALTER TABLE reconcile_settled_decisions ADD COLUMN {field} TEXT NOT NULL DEFAULT '{{}}'")
    conn.execute('CREATE INDEX IF NOT EXISTS idx_settled_case ON reconcile_settled_decisions(case_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_settled_finding ON reconcile_settled_decisions(finding_id)')


def record(conn, finding_id):
    candidate = conn.execute('SELECT job_id FROM repair_findings WHERE id=?', (finding_id,)).fetchone()
    if not candidate or candidate[0] != 'library_reconciliation':
        return
    row = conn.execute('''SELECT review_case_key,file_path,entity_id,finding_type,status,user_action,details_json
        FROM repair_findings WHERE id=? AND job_id='library_reconciliation'
        AND status IN ('resolved','dismissed') AND user_action IS NOT 'obsolete' ''', (finding_id,)).fetchone()
    if not row:
        return
    case, path, entity, kind, status, action, raw = row
    details = json.loads(raw or '{}')
    if not case:
        return
    album_id = details.get('album_id')
    if album_id is None:
        owner = conn.execute('SELECT album_id FROM reconcile_album_plans WHERE case_id=?', (case,)).fetchone()
        if not owner:
            return
        album_id = owner[0]
    ensure_schema(conn)
    review_id = decisions.register_review(conn, case, album_id, details.get('profile_id'))
    choice = conn.execute('SELECT evidence_json,choice,category,numbering_choice FROM reconcile_review_choices WHERE finding_id=?',
                          (finding_id,)).fetchone()
    accepted = choice[1:] if choice and signatures.matches(json.loads(choice[0]), details) else ('', '', '')
    identity = hashlib.sha256(json.dumps([review_id, path or str(entity), kind]).encode()).hexdigest()
    previous = conn.execute('SELECT fields_json,numbering_evidence_json,numbering_choice,category,choice FROM reconcile_settled_decisions WHERE decision_id=?',
                            (identity,)).fetchone()
    fields = json.loads(previous[0]) if previous else {}
    from core.reconciliation.fields import POSITION_FIELDS
    choice_value, category, numbering = accepted
    numbering = choice_value if category == 'numbering' else numbering
    for field, change in (details.get('changes') or {}).items():
        if not isinstance(change, dict):
            continue
        kept = (status == 'dismissed' or (numbering == 'dismiss' and field in POSITION_FIELDS | {'filename'}))
        fields[field] = change.get('old' if kept else 'new')
    has_positions = POSITION_FIELDS.intersection(details.get('changes') or {})
    numbering_evidence = raw if has_positions else previous[1] if previous else '{}'
    if previous and not has_positions and not numbering:
        # A later title/artist edit does not withdraw the accepted numbering.
        inherited = previous[4] if previous[3] == 'numbering' else previous[2]
        accepted = (accepted[0], accepted[1], inherited)
    conn.execute('''INSERT INTO reconcile_settled_decisions
        (decision_id,review_id,case_id,finding_id,file_path,entity_id,finding_type,status,user_action,
         details_json,choice,category,numbering_choice) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(decision_id) DO UPDATE SET finding_id=excluded.finding_id,
          case_id=excluded.case_id,status=excluded.status,user_action=excluded.user_action,
          details_json=excluded.details_json,choice=excluded.choice,category=excluded.category,
          numbering_choice=excluded.numbering_choice,updated_at=CURRENT_TIMESTAMP''',
        (identity, review_id, case, finding_id, path, entity, kind, status, action, raw, *accepted))
    conn.execute('UPDATE reconcile_settled_decisions SET fields_json=?,numbering_evidence_json=? WHERE decision_id=?',
                 (json.dumps(fields), numbering_evidence, identity))


def reopen(conn, finding_id):
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='reconcile_settled_decisions'").fetchone():
        conn.execute('DELETE FROM reconcile_settled_decisions WHERE finding_id=?', (finding_id,))


def suppresses(conn, case, kind, path, entity, details):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='reconcile_settled_decisions'").fetchone():
        return False
    rows = conn.execute('''SELECT details_json FROM reconcile_settled_decisions
        WHERE case_id=? AND finding_type=? AND (file_path=? OR (file_path IS NULL AND entity_id=?))''',
        (case, kind, path, str(entity) if entity is not None else None))
    return any(signatures.matches(json.loads(raw), details) for raw, in rows)


def authority_rows(conn, case_id):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='reconcile_settled_decisions'").fetchone():
        return []
    rows = conn.execute('''SELECT file_path,entity_id,status,user_action,details_json,details_json,
        choice,category,case_id,numbering_choice,fields_json FROM reconcile_settled_decisions
        WHERE case_id=? ORDER BY finding_id DESC''', (case_id,)).fetchall()
    result = []
    for row in rows:
        values = json.loads(row[10])
        details = json.loads(row[4])
        # Each field has its own accepted value; later unrelated edits do not
        # revoke previously kept values or turn them into new approvals.
        details['changes'] = {field: {'old': value, 'new': value} for field, value in values.items()}
        raw = json.dumps(details)
        result.append((*row[:4], raw, raw, row[6], row[7], row[8], row[9]))
    return result


def applied_numbering_choices(db):
    with db._get_connection() as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='reconcile_settled_decisions'").fetchone():
            return {}
        result, seen = {}, set()
        for path, choice, raw in conn.execute('''SELECT file_path,
            CASE WHEN category='numbering' THEN choice ELSE numbering_choice END,numbering_evidence_json
            FROM reconcile_settled_decisions WHERE category='numbering' OR numbering_choice!=''
            ORDER BY finding_id DESC'''):
            if path not in seen and choice == 'dismiss':
                result[path] = json.loads(raw)
            seen.add(path)
        return result


def numbering_recovery_evidence(db):
    with db._get_connection() as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='reconcile_settled_decisions'").fetchone():
            return {}
        from core.reconciliation.fields import POSITION_FIELDS
        history, seen = {}, set()
        for track_id, path, raw in conn.execute('''SELECT entity_id,file_path,numbering_evidence_json
            FROM reconcile_settled_decisions WHERE finding_type='reconciliation_metadata'
            AND status='resolved' AND user_action='reconciliation_metadata' ORDER BY finding_id DESC'''):
            details = json.loads(raw)
            if not POSITION_FIELDS.intersection(details.get('changes') or {}):
                continue
            identities = [('track', str(track_id))] if track_id else []
            if path:
                identities.append(('path', path))
            for identity in identities:
                if identity not in seen:
                    history[identity] = details
                    seen.add(identity)
            if all(details.get(key) for key in ('fingerprint_key', 'album_id', 'source', 'release_id')):
                history.setdefault(('audio', str(details['album_id']), details['source'],
                                    details['release_id'], details['fingerprint_key']), details)
        return history


def migrate_applied_releases(conn):
    """One-time import of unambiguous successful release acceptance."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='repair_operation_reports'").fetchone():
        return
    conn.execute('''CREATE TABLE IF NOT EXISTS reconcile_release_decisions (
        album_id TEXT PRIMARY KEY, source TEXT NOT NULL, release_id TEXT NOT NULL)''')
    for raw, in conn.execute('SELECT report FROM repair_operation_reports ORDER BY updated_at DESC').fetchall():
        report = json.loads(raw or '{}')
        if (not report.get('review_approved') or report.get('running') or report.get('failed')
                or report.get('error') or report.get('stopped') or not report.get('fixed')):
            continue
        for case in report.get('review_case_ids') or []:
            review = decisions.find_review(conn, case)
            current = review[4] if review else case
            for value, in conn.execute('''SELECT details_json FROM reconcile_settled_decisions
                WHERE case_id=? AND status='resolved' AND user_action IN
                  ('reconciliation_metadata','replacement_requested','wishlisted') ORDER BY finding_id DESC''', (current,)).fetchall():
                details = json.loads(value)
                album = conn.execute('SELECT canonical_locked,canonical_source,canonical_album_id FROM albums WHERE id=?',
                                     (str(details.get('album_id') or ''),)).fetchone()
                selected = (details.get('source'), str(details.get('release_id') or ''))
                if not album or not all(selected) or (album[0] and (album[1], str(album[2])) != selected):
                    continue
                conn.execute('INSERT OR IGNORE INTO reconcile_release_decisions VALUES(?,?,?)',
                             (str(details['album_id']), *selected))
