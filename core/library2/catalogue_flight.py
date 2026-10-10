"""Serialize catalogue loading for one album across its UI/job consumers."""

from contextlib import contextmanager
import os
import threading

_guard = threading.Lock()
_flights = {}


@contextmanager
def album_catalogue_flight(conn, album_id):
    """Scope to the actual SQLite file and album; unrelated releases run freely.

    Callers must release their write transaction before waiting. Entries count
    holders and waiters and disappear after the last consumer, avoiding a
    permanent lock registry proportional to the whole catalogue.
    """
    database_file = next((row[2] for row in conn.execute('PRAGMA database_list')
                          if row[1] == 'main'), '')
    database_key = os.path.realpath(database_file) if database_file else id(conn)
    key = (database_key, int(album_id))
    with _guard:
        entry = _flights.setdefault(key, [threading.RLock(), 0])
        entry[1] += 1
    try:
        with entry[0]:
            yield
    finally:
        with _guard:
            entry[1] -= 1
            if entry[1] == 0:
                del _flights[key]
