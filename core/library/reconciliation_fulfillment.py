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
        replacement_request INTEGER NOT NULL DEFAULT 0,
        published_path TEXT NOT NULL DEFAULT '', refresh_needed INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(case_id, source, release_id, source_track_id)
    )''')
    if 'replacement_request' not in {row[1] for row in conn.execute('PRAGMA table_info(reconcile_fulfillment)')}:
        conn.execute('ALTER TABLE reconcile_fulfillment ADD COLUMN replacement_request INTEGER NOT NULL DEFAULT 0')
        if 'source_info' in {row[1] for row in conn.execute('PRAGMA table_info(wishlist_tracks)')}:
            # Preserve requests made by earlier versions of the remediation UI.
            conn.execute('''UPDATE reconcile_fulfillment SET replacement_request=1
                WHERE EXISTS (SELECT 1 FROM wishlist_tracks w
                    WHERE w.profile_id=reconcile_fulfillment.profile_id
                      AND json_valid(w.source_info) AND json_valid(w.spotify_data)
                      AND json_extract(w.source_info, '$.reason')='library_reconciliation_wrong_audio'
                      AND json_extract(w.spotify_data, '$.provider')=reconcile_fulfillment.source
                      AND json_extract(w.spotify_data, '$.album.id')=reconcile_fulfillment.release_id
                      AND json_extract(w.spotify_data, '$.id')=reconcile_fulfillment.source_track_id)''')
            conn.execute("""UPDATE reconcile_fulfillment SET state='requested'
                WHERE replacement_request=1 AND state='fulfilled' AND published_path=''""")


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
        (case_id, profile_id, album_id, album_folder, source, release_id, source_track_id, replacement_request)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO UPDATE SET
            state='requested', published_path='', refresh_needed=0, updated_at=CURRENT_TIMESTAMP,
            replacement_request=excluded.replacement_request
            WHERE reconcile_fulfillment.state IN ('fulfilled', 'untracked', 'cancelled') """, (
            review_case_key(details, None), int(details.get('profile_id') or 1),
            album, details.get('album_folder') or '', source,
            details.get('exact_release_id') or release, str(slot.get('source_track_id') or ''),
            int(bool(details.get('replacement_request')))))
    if details.get('replacement_request'):
        conn.execute('''UPDATE reconcile_fulfillment SET replacement_request=1
            WHERE case_id=? AND source=? AND release_id=? AND source_track_id=?''',
            (review_case_key(details, None), source, details.get('exact_release_id') or release,
             str(slot.get('source_track_id') or '')))


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
    repair_request_payloads(conn)
    return conn.execute('''SELECT profile_id, source_track_id, release_id, state, source
        FROM reconcile_fulfillment WHERE state IN ('requested', 'published') ''').fetchall()


def retain_request_context(conn, payload, source_info, profile_id):
    """A generic download retry must retain the exact reviewed acquisition target."""
    if not isinstance(payload, dict) or not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='reconcile_fulfillment'").fetchone():
        return payload, source_info
    album = payload.get('album') or {}
    rows = conn.execute('''SELECT source, replacement_request FROM reconcile_fulfillment
        WHERE profile_id=? AND release_id=? AND source_track_id=?
          AND state IN ('requested', 'published')''',
        (int(profile_id or 1), str(album.get('id') or ''), str(payload.get('id') or ''))).fetchall()
    sources = {row[0] for row in rows}
    if len(sources) != 1 or (payload.get('provider') and payload['provider'] not in sources):
        return payload, source_info
    source = next(iter(sources))
    payload = {**payload, 'provider': source, 'album': dict(album)}
    if source == 'musicbrainz':
        payload['album']['musicbrainz_release_id'] = album['id']
    info = dict(source_info) if isinstance(source_info, dict) else {}
    info.update(source=source, source_track_id=payload['id'], source_album_id=album['id'],
                is_album=True, reason='library_reconciliation_wrong_audio' if any(r[1] for r in rows)
                else 'library_reconciliation')
    return payload, info


def repair_request_payloads(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='wishlist_tracks'").fetchone():
        return
    if not {'id', 'source_info', 'spotify_data', 'profile_id'} <= {r[1] for r in conn.execute('PRAGMA table_info(wishlist_tracks)')}:
        return
    for row_id, raw, info, profile in conn.execute(
            'SELECT id, spotify_data, source_info, profile_id FROM wishlist_tracks').fetchall():
        try:
            before, previous = json.loads(raw or '{}'), json.loads(info or '{}')
            payload, context = retain_request_context(conn, before, previous, profile)
            if payload != before or context != previous:
                conn.execute('UPDATE wishlist_tracks SET spotify_data=?, source_info=? WHERE id=?',
                             (json.dumps(payload), json.dumps(context), row_id))
        except (ValueError, TypeError, AttributeError):
            continue
    conn.commit()


def requested_folder(db, *, profile_id, source, release_id, track_id, disc_number, root):
    """Route an exact requested slot to its reviewed album, including disc folders."""
    from core.library.path_resolver import resolve_library_file_path
    conn = db._get_connection()
    try:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='reconcile_fulfillment'").fetchone():
            return None
        requests = conn.execute('''SELECT DISTINCT album_id, album_folder FROM reconcile_fulfillment
            WHERE profile_id=? AND source=? AND release_id=? AND source_track_id=?
              AND state IN ('requested', 'published')''',
            (int(profile_id or 1), source, str(release_id), str(track_id))).fetchall()
        if len(requests) != 1:
            return None
        album_id, anchor = requests[0]
        paths = conn.execute('SELECT file_path, disc_number FROM tracks WHERE album_id=?',
                             (album_id,)).fetchall()
        folders = set()
        for path, disc in paths:
            resolved = resolve_library_file_path(path, transfer_folder=root)
            if not resolved or not os.path.isfile(resolved):
                continue
            folder = os.path.dirname(resolved)
            if os.path.commonpath([os.path.realpath(root), os.path.realpath(folder)]) != os.path.realpath(root):
                continue
            if int(disc or 1) == int(disc_number or 1):
                folders.add(folder)
        # The saved anchor wins when the old album was flat; disc directories
        # are selected only by existing files with the requested disc number.
        if anchor in folders:
            return anchor
        if len(folders) == 1:
            return next(iter(folders))
        return None
    finally:
        conn.close()


def group_delivered_files(db, folders, tracks, albums, trusted):
    """A verified delivery belongs in its requested album's audio review.

    Old imports may have created a separate folder before exact request routing
    existed. This establishes collection context, never a recording match.
    """
    with db._get_connection() as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='reconcile_fulfillment'").fetchone():
            return folders
        deliveries = conn.execute("SELECT DISTINCT album_id,profile_id,published_path FROM reconcile_fulfillment "
            "WHERE state IN ('published','fulfilled') AND published_path!=''").fetchall()
    groups = {folder: list(paths) for folder, paths in folders.items()}
    album_keys = {str(key): key for key in albums}
    anchors, locations, requests = {}, {}, {}
    for folder, paths in groups.items():
        ids = {str(tracks[os.path.normpath(path)]['album_id']) for path in paths
               if os.path.normpath(path) in tracks and tracks[os.path.normpath(path)].get('album_id') is not None}
        owner = trusted.get(folder)
        if owner is None and len(ids) == 1:
            owner = next(iter(ids))
        if owner is not None:
            anchors.setdefault(str(owner), []).append(folder)
        for path in paths:
            locations[os.path.normpath(path)] = (folder, path)
    for album, profile, path in deliveries:
        requests.setdefault(os.path.normpath(path), set()).add((str(album), int(profile or 1)))
    for path, owners in requests.items():
        if len(owners) != 1 or path not in locations:
            continue
        album, profile = next(iter(owners))
        targets = anchors.get(album, [])
        album_key = album_keys.get(album)
        metadata = albums.get(album_key) or {}
        indexed = tracks.get(path) or {}
        if (len(targets) != 1 or not metadata or int(metadata.get('owner_profile_id') or 1) != profile
                or (indexed.get('album_id') is not None and str(indexed['album_id']) != album)):
            continue
        folder, original = locations[path]
        anchor = targets[0]
        if folder == anchor:
            continue
        groups[folder].remove(original)
        groups[anchor].append(original)
        trusted[anchor] = album_key
        if not groups[folder]:
            del groups[folder]
    return groups


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


def assignment_fulfills_request(request, assignment):
    """An old kept copy cannot complete a request for another file."""
    if (assignment.get('status') not in ('confirmed', 'change', 'settled') or
            not assignment.get('file_path') or
            str(assignment.get('source_track_id') or '') != str(request.get('source_track_id') or '')):
        return False
    if not request.get('replacement_request'):
        return True
    if request.get('state') != 'published' or not request.get('published_path'):
        return False
    try:
        return os.path.samefile(request['published_path'], assignment['file_path'])
    except OSError:
        return False


def settle_assignments(db, case_id, assignments, release_id, source):
    """A published file stays pending until reconciliation assigns its exact slot."""
    if not assignments:
        return
    conn = db._get_connection()
    try:
        ensure_schema(conn)
        rows = conn.execute('''SELECT source_track_id, state, published_path, replacement_request
            FROM reconcile_fulfillment WHERE case_id=? AND release_id=? AND source=?
              AND state IN ('requested', 'published')''', (case_id, str(release_id), source)).fetchall()
        for row in rows:
            request = dict(zip(('source_track_id', 'state', 'published_path', 'replacement_request'), row))
            if not any(assignment_fulfills_request(request, assignment) for assignment in assignments):
                continue
            conn.execute('''UPDATE reconcile_fulfillment SET state='fulfilled', refresh_needed=0,
                updated_at=CURRENT_TIMESTAMP WHERE case_id=? AND release_id=? AND source_track_id=? AND source=?''',
                (case_id, str(release_id), row[0], source))
        conn.commit()
    finally:
        conn.close()


def _planned_delivery(conn, case, profile, release, track, path):
    """An older import may have retagged a delivery with another pressing ID."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='reconcile_album_plans'").fetchone():
        return False
    identities = conn.execute('SELECT DISTINCT case_id,source,release_id FROM reconcile_fulfillment '
        'WHERE profile_id=? AND source_track_id=?', (profile, track)).fetchall()
    if len(identities) != 1 or tuple(identities[0]) != (case, 'musicbrainz', release):
        return False
    row = conn.execute('SELECT plan_json FROM reconcile_album_plans WHERE case_id=?', (case,)).fetchone()
    if not row:
        return False
    plan = json.loads(row[0])
    if plan.get('source') != 'musicbrainz' or plan.get('release_id') != release:
        return False
    for assignment in plan.get('assignments') or []:
        if str(assignment.get('source_track_id') or '') != track or not assignment.get('file_path'):
            continue
        try:
            if os.path.samefile(path, assignment['file_path']):
                return True
        except OSError:
            continue
    return False


def recover_published_requests(conn):
    """Recover a missed publication callback from an exact, verified delivery."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='wishlist_removals'").fetchone():
        return
    from mutagen import File, MutagenError
    for case, profile, release, track, requested in conn.execute(
            "SELECT case_id,profile_id,release_id,source_track_id,updated_at FROM reconcile_fulfillment "
            "WHERE source='musicbrainz' AND state='requested' AND published_path='' ").fetchall():
        deliveries = conn.execute("SELECT final_path, profile_ids FROM wishlist_removals "
            "WHERE spotify_track_id=? AND reason='download_complete' AND removed_at >= ? "
            "ORDER BY removed_at DESC", (track, requested)).fetchall()
        for path, profiles in deliveries:
            try:
                if int(profile) not in json.loads(profiles or '[]') or not os.path.isfile(path):
                    continue
                tags = File(path, easy=True)
                if not tags or track not in (tags.get('musicbrainz_trackid') or []):
                    continue
                if (release not in (tags.get('musicbrainz_albumid') or [])
                        and not _planned_delivery(conn, case, profile, release, track, path)):
                    continue
                conn.execute("UPDATE reconcile_fulfillment SET state='published', published_path=?, "
                    "refresh_needed=1, updated_at=CURRENT_TIMESTAMP WHERE case_id=? AND source='musicbrainz' "
                    "AND release_id=? AND source_track_id=?", (path, case, release, track))
                break
            except (OSError, ValueError, TypeError, AttributeError, MutagenError):
                continue


def backfill_requests(conn):
    """Retain requests made before the durable acquisition state was introduced."""
    ensure_schema(conn)
    recover_published_requests(conn)
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
