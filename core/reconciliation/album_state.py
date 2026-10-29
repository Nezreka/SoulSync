"""Typed album/assignment stores shared by commands and pure scan readers."""
import json


def ensure_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS reconcile_album_intent (
        album_id TEXT PRIMARY KEY, scope TEXT NOT NULL, source TEXT NOT NULL,
        release_id TEXT NOT NULL, updated_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS reconcile_wanted_slots (
        album_id TEXT PRIMARY KEY, source TEXT NOT NULL, release_id TEXT NOT NULL, slots_json TEXT NOT NULL)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS reconcile_release_decisions (
        album_id TEXT PRIMARY KEY, source TEXT NOT NULL, release_id TEXT NOT NULL)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS reconcile_audio_copy_releases (
        album_id TEXT PRIMARY KEY, source TEXT NOT NULL, release_id TEXT NOT NULL)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS reconcile_track_assignment (
        album_id TEXT NOT NULL, source TEXT NOT NULL, release_id TEXT NOT NULL,
        file_path TEXT NOT NULL, slot_key TEXT NOT NULL, size_bytes INTEGER NOT NULL,
        mtime_ns INTEGER NOT NULL, fingerprint_key TEXT NOT NULL,
        PRIMARY KEY(album_id,source,release_id,file_path))''')


def intents(conn):
    return {str(row[0]): (row[1], row[2], row[3], json.loads(row[4] or '[]'))
            for row in conn.execute('''SELECT i.album_id,i.scope,i.source,i.release_id,
                CASE WHEN w.source=i.source AND w.release_id=i.release_id THEN w.slots_json ELSE '[]' END
                FROM reconcile_album_intent i LEFT JOIN reconcile_wanted_slots w ON w.album_id=i.album_id
                UNION ALL SELECT w.album_id,'',w.source,w.release_id,w.slots_json FROM reconcile_wanted_slots w
                WHERE NOT EXISTS (SELECT 1 FROM reconcile_album_intent i WHERE i.album_id=w.album_id
                    AND i.source=w.source AND i.release_id=w.release_id)''')}


def assignments(conn):
    return {(str(row[0]), row[1], row[2], row[3]): tuple(row[4:]) for row in conn.execute('''
        SELECT album_id,source,release_id,file_path,slot_key,size_bytes,mtime_ns,fingerprint_key
        FROM reconcile_track_assignment''')}


def choose_release(conn, db, case_id, album_id, source, release_id, score, *, audio_copy=False, folder=''):
    from core.reconciliation import decisions, work
    ensure_schema(conn)
    if not db.set_album_canonical(album_id, source, release_id, score=score, locked=True, connection=conn):
        raise ValueError('Could not pin that album release')
    conn.execute('INSERT OR REPLACE INTO reconcile_release_decisions VALUES(?,?,?)',
                 (str(album_id), source, release_id))
    conn.execute('DELETE FROM reconcile_audio_copy_releases WHERE album_id=?', (str(album_id),))
    if audio_copy:
        conn.execute('INSERT INTO reconcile_audio_copy_releases VALUES(?,?,?)', (str(album_id), source, release_id))
    decisions.clear(conn, case_id, 'disposition')
    decisions.touch(conn, case_id)
    work.enqueue(conn, case_id, folder, scope='discovery')


def choose_scope(conn, album_id, source, release_id, scope, wanted):
    conn.execute('''INSERT INTO reconcile_album_intent(album_id,scope,source,release_id) VALUES(?,?,?,?)
        ON CONFLICT(album_id) DO UPDATE SET scope=excluded.scope,source=excluded.source,
        release_id=excluded.release_id,updated_at=CURRENT_TIMESTAMP''', (str(album_id), scope, source, release_id))
    conn.execute('''INSERT INTO reconcile_wanted_slots VALUES(?,?,?,?)
        ON CONFLICT(album_id) DO UPDATE SET source=excluded.source,release_id=excluded.release_id,
        slots_json=excluded.slots_json''', (str(album_id), source, release_id, json.dumps(sorted(wanted))))
