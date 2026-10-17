"""Status checks must not compete with an album publisher's write lock."""
import sqlite3
from types import SimpleNamespace

from core.library2.bootstrap import get_state
from core.library2.migration_gate import migration_required


def test_status_and_gate_do_not_write_while_publisher_holds_lock(tmp_path):
    path = tmp_path / 'state.db'
    writer = sqlite3.connect(path)
    writer.execute('PRAGMA journal_mode=WAL')
    writer.execute('CREATE TABLE publisher (id INTEGER)')
    writer.commit()
    writer.execute('INSERT INTO publisher VALUES (1)')

    def connect():
        conn = sqlite3.connect(path, timeout=0.05)
        conn.row_factory = sqlite3.Row
        return conn

    db = SimpleNamespace(_get_connection=connect)
    try:
        assert get_state(db)['status'] == 'pending'
        assert migration_required(db) is False
        with connect() as reader:
            assert reader.execute("SELECT 1 FROM sqlite_master WHERE name='lib2_bootstrap_state'").fetchone() is None
    finally:
        writer.rollback()
        writer.close()
