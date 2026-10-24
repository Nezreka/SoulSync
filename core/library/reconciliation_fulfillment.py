"""Durable acquisition state: a removed wishlist row is not proof of an assigned file."""
from __future__ import annotations

import json
import os

from core.repair_review_identity import review_case_key


def ensure_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS reconcile_fulfillment (
        case_id TEXT NOT NULL, profile_id INTEGER NOT NULL, album_id TEXT NOT NULL,
        album_folder TEXT NOT NULL, source TEXT NOT NULL, release_id TEXT NOT NULL,
        source_track_id TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'requested',
        published_path TEXT NOT NULL DEFAULT '', refresh_needed INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(case_id, source, release_id, source_track_id)
    )''')


def record_request_on_connection(conn, details, slot):
    """Persist exact collection intent in the wishlist transaction."""
    from core.repair_jobs.library_reconciliation import _wanted_slot_key
    ensure_schema(conn)
    conn.execute("""CREATE TABLE IF NOT EXISTS reconcile_wanted_slots (
        album_id TEXT PRIMARY KEY, source TEXT NOT NULL,
        release_id TEXT NOT NULL, slots_json TEXT NOT NULL)""")
    source = details.get('source') or ''
    release = details.get('release_id') or ''
    album = str(details['album_id'])
    row = conn.execute('SELECT source, release_id, slots_json FROM reconcile_wanted_slots WHERE album_id=?',
                       (album,)).fetchone()
    wanted = set(json.loads(row[2])) if row and tuple(row[:2]) == (source, release) else set()
    wanted.add(_wanted_slot_key(slot))
    conn.execute('INSERT OR REPLACE INTO reconcile_wanted_slots VALUES (?, ?, ?, ?)',
                 (album, source, release, json.dumps(sorted(wanted))))
    conn.execute("""INSERT INTO reconcile_fulfillment
        (case_id, profile_id, album_id, album_folder, source, release_id, source_track_id)
        VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO UPDATE SET
            state='requested', published_path='', refresh_needed=0, updated_at=CURRENT_TIMESTAMP
            WHERE reconcile_fulfillment.state IN ('fulfilled', 'untracked', 'cancelled') """, (
            review_case_key(details, None), int(details.get('profile_id') or 1),
            album, details.get('album_folder') or '', source,
            details.get('exact_release_id') or release, str(slot.get('source_track_id') or '')))


def record_request(db, details, slot):
    with db._get_connection() as conn:
        record_request_on_connection(conn, details, slot)
        conn.commit()


def published_wishlist_track(db, stored_id, profiles, path):
    """Capture exact wishlist identity before removal, after publication succeeded."""
    if not path or not os.path.isfile(path):
        return
    conn = db._get_connection()
    try:
        ensure_schema(conn)
        rows = conn.execute('SELECT profile_id, spotify_data FROM wishlist_tracks WHERE spotify_track_id=?',
                            (stored_id,)).fetchall()
        for profile, raw in rows:
            if profiles is not None and profile not in profiles:
                continue
            payload = json.loads(raw or '{}')
            track_id = str(payload.get('id') or str(stored_id).split('::')[0])
            release_id = str((payload.get('album') or {}).get('id') or '')
            source = str(payload.get('provider') or '')
            if not track_id or not release_id or not source:
                continue
            conn.execute('''UPDATE reconcile_fulfillment SET state='published',
                published_path=?, refresh_needed=1, updated_at=CURRENT_TIMESTAMP
                WHERE profile_id=? AND release_id=? AND source_track_id=? AND source=?
                  AND state != 'fulfilled' ''', (str(path), profile, release_id, track_id, source))
        conn.commit()
    finally:
        conn.close()


def outstanding(conn):
    ensure_schema(conn)
    return conn.execute('''SELECT profile_id, source_track_id, release_id, state, source
        FROM reconcile_fulfillment WHERE state IN ('requested', 'published') ''').fetchall()


def cancel_request(conn, profile_id, payload):
    """Cancel acquisition only; collection intent is a separate user decision."""
    if not isinstance(payload, dict):
        return
    source = payload.get('provider') or 'spotify'
    track_id = str(payload.get('id') or '')
    release_id = str((payload.get('album') or {}).get('id') or '')
    if not track_id or not release_id:
        return
    ensure_schema(conn)
    conn.execute("""UPDATE reconcile_fulfillment SET state='cancelled', refresh_needed=1,
        updated_at=CURRENT_TIMESTAMP WHERE profile_id=? AND source=? AND release_id=?
        AND source_track_id=? AND state='requested'""",
        (profile_id, source, release_id, track_id))


def settle_assignments(db, case_id, assignments, release_id, source):
    """A published file stays pending until reconciliation assigns its exact slot."""
    ids = {str(row.get('source_track_id')) for row in assignments
           if row.get('status') in ('confirmed', 'change', 'settled') and row.get('file_path')
           and row.get('source_track_id')}
    if not ids:
        return
    conn = db._get_connection()
    try:
        ensure_schema(conn)
        for track_id in ids:
            conn.execute('''UPDATE reconcile_fulfillment SET state='fulfilled', refresh_needed=0,
                updated_at=CURRENT_TIMESTAMP WHERE case_id=? AND release_id=? AND source_track_id=? AND source=?''',
                (case_id, str(release_id), track_id, source))
        conn.commit()
    finally:
        conn.close()


def backfill_requests(conn):
    """Retain requests made before the durable acquisition state was introduced."""
    ensure_schema(conn)
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='repair_findings'").fetchone():
        return
    active_requests = set()
    for profile, raw in conn.execute('SELECT profile_id, spotify_data FROM wishlist_tracks'):
        try:
            payload = json.loads(raw or '{}')
            active_requests.add((int(profile or 1), str(payload.get('provider') or 'spotify'),
                str((payload.get('album') or {}).get('id') or ''), str(payload.get('id') or '')))
        except (ValueError, TypeError, AttributeError):
            continue
    rows = conn.execute("""SELECT review_case_key, details_json FROM repair_findings
        WHERE finding_type='reconciliation_missing' AND
          (user_action='wishlisted' OR (status='pending' AND
           json_extract(details_json, '$.fulfillment_state')='wishlisted'))""").fetchall()
    for key, raw in rows:
        details = json.loads(raw or '{}')
        if details.get('album_id') is None:
            continue
        for slot in details.get('missing_tracks') or []:
            if not slot.get('source_track_id'):
                continue
            identity = (int(details.get('profile_id') or 1), details.get('source') or '',
                        details.get('exact_release_id') or details.get('release_id') or '',
                        str(slot['source_track_id']))
            if identity not in active_requests:
                # Legacy 'wishlisted' history cannot prove a request is still
                # active after the old wishlist row has disappeared. Preserve
                # publication records and any currently active album finding.
                conn.execute("""UPDATE reconcile_fulfillment SET state='untracked'
                    WHERE case_id=? AND source=? AND release_id=? AND source_track_id=?
                      AND state='requested' AND published_path=''
                      AND NOT EXISTS (SELECT 1 FROM repair_findings f
                        WHERE f.review_case_key=? AND f.status='pending')""",
                    (key, identity[1], identity[2], identity[3], key))
                continue
            conn.execute('''INSERT INTO reconcile_fulfillment
                (case_id, profile_id, album_id, album_folder, source, release_id, source_track_id)
                VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO UPDATE SET
                  state='requested', updated_at=CURRENT_TIMESTAMP
                  WHERE reconcile_fulfillment.state='untracked' ''', (
                    key, int(details.get('profile_id') or 1), str(details['album_id']),
                    details.get('album_folder') or '', details.get('source') or '',
                    details.get('exact_release_id') or details.get('release_id') or '',
                    str(slot['source_track_id'])))
    conn.commit()
