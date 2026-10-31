"""the convergence pass that runs on every start checkpoints the WAL without
waiting on readers.

a TRUNCATE checkpoint takes the write lock and then waits for every reader to
finish. after each start that held every writer, and through the connection
pool every request, for half a minute: a search sat on "Searching Deezer..."
"""

import sqlite3

from core.library2 import bootstrap, wal


class _Db:
    def __init__(self, path):
        self.path = path

    def _get_connection(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn


def test_the_startup_pass_checkpoints_passively(tmp_path, monkeypatch):
    from core.library2.schema import ensure_library_v2_schema
    path = str(tmp_path / "m.db")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    ensure_library_v2_schema(conn, run_backfills=False)
    conn.commit()
    conn.close()

    modes = []
    real = wal.checkpoint_wal
    monkeypatch.setattr(wal, "checkpoint_wal",
                        lambda c, mode=wal.CHECKPOINT_TRUNCATE: modes.append(mode) or real(c, mode))

    bootstrap.run_deferred_backfills(_Db(path))

    assert modes and modes[-1] == wal.CHECKPOINT_PASSIVE
    assert wal.CHECKPOINT_TRUNCATE not in modes
