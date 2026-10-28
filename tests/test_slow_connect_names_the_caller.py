"""a slow sqlite connect says who was waiting.

the warning fired ~2,000 times a day with only the db path in it, so there
was no way to tell which job or page was stuck behind the lock.
"""

import sqlite3
import threading

import database.music_database as mdb
from database.music_database import MusicDatabase


def test_slow_connect_warning_names_the_caller_and_thread(tmp_path, monkeypatch):
    db = MusicDatabase(database_path=str(tmp_path / "m.db"))
    warned = []
    monkeypatch.setattr(mdb.logger, "warning", lambda msg, *a, **k: warned.append(msg % a))

    real_connect = sqlite3.connect
    clock = iter([0.0, 0.001] + [0.5] * 50)
    monkeypatch.setattr(mdb.time, "perf_counter", lambda: next(clock))
    monkeypatch.setattr(mdb.sqlite3, "connect", lambda *a, **k: real_connect(*a, **k))

    mdb.connection_pool.discard(db.database_path)   # a reused connection opens nothing, so nothing is slow

    def the_job_that_waited():
        db._get_connection().close()

    t = threading.Thread(target=the_job_that_waited, name="watchlist-scan")
    t.start()
    t.join()

    slow = [w for w in warned if w.startswith("slow sqlite connect")]
    assert slow, warned
    assert "from the_job_that_waited <- run" in slow[0]
    assert slow[0].endswith("[watchlist-scan]")
