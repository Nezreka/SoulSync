"""Durable follow-up work saved in the decision transaction.

Generation checks prevent a finishing replan from acknowledging a newer
decision. Failures retain work with bounded retry delay across process restarts.
"""
import time

RANK = {'plan': 0, 'album': 1, 'discovery': 2, 'library': 3}


def ensure_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS reconcile_review_work (
        case_id TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'replan', folder TEXT NOT NULL,
        scope TEXT NOT NULL, refresh_catalogue INTEGER NOT NULL DEFAULT 0,
        generation INTEGER NOT NULL DEFAULT 1, next_attempt REAL NOT NULL DEFAULT 0,
        error TEXT NOT NULL DEFAULT '', PRIMARY KEY(case_id,kind))''')


def enqueue(conn, case, folder, *, scope='plan', refresh_catalogue=False, kind='replan'):
    if not folder:
        return None
    ensure_schema(conn)
    previous = conn.execute('SELECT scope,refresh_catalogue,generation FROM reconcile_review_work WHERE case_id=? AND kind=?',
                            (case, kind)).fetchone()
    generation = previous[2] + 1 if previous else 1
    if previous:
        scope = max((scope, previous[0]), key=RANK.get)
        refresh_catalogue = refresh_catalogue or previous[1]
    conn.execute('''INSERT INTO reconcile_review_work VALUES(?,?,?,?,?,?,0,'')
        ON CONFLICT(case_id,kind) DO UPDATE SET folder=excluded.folder,scope=excluded.scope,
          refresh_catalogue=excluded.refresh_catalogue,generation=excluded.generation,next_attempt=0,error='' ''',
        (case, kind, folder, scope, int(refresh_catalogue), generation))
    return generation


def pending(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='reconcile_review_work'").fetchone():
        return []
    return conn.execute('''SELECT case_id,kind,folder,scope,refresh_catalogue,generation
        FROM reconcile_review_work WHERE next_attempt<=? ORDER BY next_attempt,case_id LIMIT 10''', (time.time(),)).fetchall()


def complete(conn, case, generation, *, kind='replan', error=''):
    if error:
        conn.execute('''UPDATE reconcile_review_work SET error=?,next_attempt=?
            WHERE case_id=? AND kind=? AND generation=?''', (error[:300], time.time() + 30, case, kind, generation))
    else:
        conn.execute('DELETE FROM reconcile_review_work WHERE case_id=? AND kind=? AND generation=?',
                     (case, kind, generation))
