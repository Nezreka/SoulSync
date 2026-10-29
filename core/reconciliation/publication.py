"""Publish an album snapshot and its findings as one revision-checked unit."""
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar

_current = ContextVar('review_publication', default=None)


def connection_for(db):
    current = _current.get()
    return current[1] if current and current[0] is db else None


def revisions(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='reconcile_review_aliases'").fetchone():
        return {}
    return dict(conn.execute('''SELECT a.case_id,r.revision FROM reconcile_review_aliases a
        JOIN reconcile_reviews r USING(review_id)'''))


class StalePublication(ValueError):
    pass


@contextmanager
def publish(db, expected, cases, *, exclusive=True):
    from core.library.maintenance_lock import mutation_lock
    # Review commands hold a planning lease through their final revision bump.
    # The short publication lock excludes those commands, never network reads.
    with mutation_lock if exclusive else nullcontext():
        conn = db._get_connection()
        token = None
        try:
            conn.execute('BEGIN IMMEDIATE')
            current = revisions(conn)
            if any(current.get(case, 0) != expected.get(case, 0) for case in cases):
                raise StalePublication('Album decisions changed while analysis was running')
            token = _current.set((db, conn))
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            if token is not None:
                _current.reset(token)
            conn.close()
