import sqlite3

from database.music_database import MusicDatabase


def test_clear_server_data_does_not_fail_when_vacuum_hits_disk_io():
    db = object.__new__(MusicDatabase)

    class _Cursor:
        rowcount = 0

        def __init__(self):
            self.calls = []

        def execute(self, query, params=None):
            self.calls.append((query, params))
            if query == "VACUUM":
                raise sqlite3.OperationalError("disk I/O error")
            if "tracks" in query:
                self.rowcount = 1500
            elif "albums" in query:
                self.rowcount = 200
            elif "artists" in query:
                self.rowcount = 20

    class _Conn:
        def __init__(self):
            self.cursor_obj = _Cursor()
            self.commits = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def cursor(self):
            return self.cursor_obj

        def commit(self):
            self.commits += 1

    conn = _Conn()
    db._get_connection = lambda: conn

    db.clear_server_data("jellyfin")

    assert conn.commits == 1
    assert any(call[0] == "VACUUM" for call in conn.cursor_obj.calls)


def test_clear_server_data_retries_transient_disk_io_before_commit(monkeypatch):
    db = object.__new__(MusicDatabase)
    connections = []

    class _Cursor:
        rowcount = 0

        def __init__(self, fail_first_delete=False):
            self.fail_first_delete = fail_first_delete
            self.calls = []

        def execute(self, query, params=None):
            self.calls.append((query, params))
            if self.fail_first_delete and "DELETE FROM tracks" in query:
                self.fail_first_delete = False
                raise sqlite3.OperationalError("disk I/O error")
            self.rowcount = 1

    class _Conn:
        def __init__(self, fail_first_delete=False):
            self.cursor_obj = _Cursor(fail_first_delete=fail_first_delete)
            self.commits = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def cursor(self):
            return self.cursor_obj

        def commit(self):
            self.commits += 1

    def _connect():
        conn = _Conn(fail_first_delete=not connections)
        connections.append(conn)
        return conn

    db._get_connection = _connect
    monkeypatch.setattr("database.music_database.time.sleep", lambda _seconds: None)

    db.clear_server_data("jellyfin")

    assert len(connections) == 2
    assert connections[1].commits == 1
