"""Durable album decisions, independent of findings and media-server IDs.

Writers supply a transaction. Readers never initialize schema or repair data.
Legacy case keys remain public aliases; verified migrations preserve review_id.
"""
from __future__ import annotations

import hashlib
import json
import uuid


class StaleDecision(ValueError):
    """Another command has advanced this review's decision revision."""


def ensure_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS reconcile_reviews (
        review_id TEXT PRIMARY KEY, profile_id INTEGER NOT NULL,
        album_id TEXT NOT NULL, case_id TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS reconcile_review_aliases (
        case_id TEXT PRIMARY KEY, review_id TEXT NOT NULL REFERENCES reconcile_reviews(review_id))''')
    conn.execute('''CREATE TABLE IF NOT EXISTS reconcile_album_decisions (
        review_id TEXT NOT NULL REFERENCES reconcile_reviews(review_id), kind TEXT NOT NULL,
        value_json TEXT NOT NULL, dependencies_json TEXT NOT NULL,
        revision INTEGER NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(review_id,kind))''')


def find_review(conn, case_id):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='reconcile_review_aliases'").fetchone():
        return None
    return conn.execute('''SELECT r.review_id,r.revision,r.profile_id,r.album_id,r.case_id
        FROM reconcile_reviews r JOIN reconcile_review_aliases a USING(review_id)
        WHERE a.case_id=?''', (case_id,)).fetchone()


def register_review(conn, case_id, album_id, profile_id=1):
    ensure_schema(conn)
    row = find_review(conn, case_id)
    if row:
        if row[2] != int(profile_id or 1) or row[3] != str(album_id or ''):
            raise ValueError('Review identity belongs to a different album or profile')
        return row[0]
    identity = uuid.uuid4().hex
    conn.execute('INSERT INTO reconcile_reviews(review_id,profile_id,album_id,case_id) VALUES(?,?,?,?)',
                 (identity, int(profile_id or 1), str(album_id or ''), case_id))
    conn.execute('INSERT INTO reconcile_review_aliases VALUES(?,?)', (case_id, identity))
    return identity


def save(conn, case_id, kind, value, dependencies, *, expected_revision=None):
    row = find_review(conn, case_id)
    if not row:
        raise ValueError('The album has no current review identity')
    revision = row[1] if expected_revision is None else expected_revision
    if revision != row[1]:
        raise StaleDecision('The album decisions changed; review the current plan')
    encoded = (json.dumps(value, sort_keys=True), json.dumps(dependencies, sort_keys=True))
    previous = conn.execute('SELECT value_json,dependencies_json FROM reconcile_album_decisions WHERE review_id=? AND kind=?',
                            (row[0], kind)).fetchone()
    if previous and tuple(previous) == encoded:
        return revision
    changed = conn.execute('UPDATE reconcile_reviews SET revision=revision+1 WHERE review_id=? AND revision=?',
                           (row[0], revision)).rowcount
    if not changed:
        raise StaleDecision('The album decisions changed; review the current plan')
    conn.execute('''INSERT INTO reconcile_album_decisions
        (review_id,kind,value_json,dependencies_json,revision) VALUES(?,?,?,?,?)
        ON CONFLICT(review_id,kind) DO UPDATE SET value_json=excluded.value_json,
        dependencies_json=excluded.dependencies_json,revision=excluded.revision,
        updated_at=CURRENT_TIMESTAMP''',
        (row[0], kind, json.dumps(value, sort_keys=True), json.dumps(dependencies, sort_keys=True), revision + 1))
    return revision + 1


def touch(conn, case_id):
    row = find_review(conn, case_id)
    if row:
        conn.execute('UPDATE reconcile_reviews SET revision=revision+1 WHERE review_id=?', (row[0],))


def clear(conn, case_id, kind):
    row = find_review(conn, case_id)
    if row and conn.execute('DELETE FROM reconcile_album_decisions WHERE review_id=? AND kind=?',
                            (row[0], kind)).rowcount:
        conn.execute('UPDATE reconcile_reviews SET revision=revision+1 WHERE review_id=?', (row[0],))


def rebind_review(conn, old_case, new_case, album_id):
    """Called only after the inventory or media-server migration proves ownership."""
    old = find_review(conn, old_case)
    if not old:
        return
    target = find_review(conn, new_case)
    if target and target[0] != old[0]:
        if target[2] != old[2]:
            raise ValueError('Cannot transfer a review across profiles')
        for kind, value, dependencies, revision, updated in conn.execute(
                'SELECT kind,value_json,dependencies_json,revision,updated_at FROM reconcile_album_decisions WHERE review_id=?',
                (target[0],)).fetchall():
            previous = conn.execute('SELECT value_json,dependencies_json FROM reconcile_album_decisions WHERE review_id=? AND kind=?',
                                    (old[0], kind)).fetchone()
            if previous and tuple(previous) != (value, dependencies):
                raise ValueError('Conflicting album decisions prevent review migration')
            conn.execute('INSERT OR IGNORE INTO reconcile_album_decisions VALUES(?,?,?,?,?,?)',
                         (old[0], kind, value, dependencies, revision, updated))
        conn.execute('UPDATE reconcile_review_aliases SET review_id=? WHERE review_id=?', (old[0], target[0]))
        conn.execute('DELETE FROM reconcile_album_decisions WHERE review_id=?', (target[0],))
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='reconcile_settled_decisions'").fetchone():
            conn.execute('UPDATE reconcile_settled_decisions SET review_id=? WHERE review_id=?', (old[0], target[0]))
        conn.execute('DELETE FROM reconcile_reviews WHERE review_id=?', (target[0],))
    conn.execute('INSERT OR IGNORE INTO reconcile_review_aliases VALUES(?,?)', (new_case, old[0]))
    conn.execute('UPDATE reconcile_reviews SET album_id=?,case_id=?,revision=revision+1 WHERE review_id=?',
                 (str(album_id), new_case, old[0]))


def numbering_signature(source, release_id, slots):
    # Coverage and assignments are deliberately absent: the selected release's
    # numbering policy applies to tracks confirmed or acquired later as well.
    positions = [(str(slot.get('source_track_id') or slot.get('recording_mbid') or ''),
                  str(slot.get('disc_number') or 1), str(slot.get('track_number') or '')) for slot in slots]
    document = [1, source, str(release_id), sorted(positions)]
    return hashlib.sha256(json.dumps(document, sort_keys=True).encode()).hexdigest()


def plan_numbering_signature(plan):
    source, release = plan.get('source'), plan.get('release_id')
    selected = next((row for row in (plan.get('_planning') or {}).get('candidates') or []
                     if (row.get('source'), row.get('release_id')) == (source, release)), None)
    return numbering_signature(source, release, selected['slots']) if selected else ''


def save_numbering(conn, case_id, choice):
    if choice not in ('fix', 'dismiss'):
        raise ValueError('Choose release numbering or keep current numbering')
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='reconcile_album_plans'").fetchone():
        return False
    row = conn.execute('SELECT album_id,profile_id,plan_json FROM reconcile_album_plans WHERE case_id=?', (case_id,)).fetchone()
    if not row:
        return False
    plan = json.loads(row[2])
    signature = plan_numbering_signature(plan)
    if not signature:
        return False
    register_review(conn, case_id, row[0], row[1])
    save(conn, case_id, 'numbering', choice, {'signature': signature})
    return True


def numbering_choices(conn):
    """Return only policies whose selected-release positions are still current."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='reconcile_album_decisions'").fetchone():
        return {}
    if 'numbering_signature' not in {row[1] for row in conn.execute('PRAGMA table_info(reconcile_album_plans)')}:
        return {}
    # Start with the few saved policies, not the much larger plan documents.
    # Existing ANALYZE statistics otherwise favor scanning every plan at boot.
    return {case: json.loads(value) for case, value in conn.execute('''
        SELECT a.case_id,d.value_json FROM reconcile_album_decisions d
        CROSS JOIN reconcile_review_aliases a USING(review_id)
        CROSS JOIN reconcile_album_plans p ON p.case_id=a.case_id
        WHERE d.kind='numbering' AND p.numbering_signature!=''
          AND json_extract(d.dependencies_json,'$.signature')=p.numbering_signature''')}
