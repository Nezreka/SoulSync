import sqlite3
from types import SimpleNamespace

import core.metadata.cache as cache_module
from core.metadata.cache import MetadataCache


def test_maintenance_write_retries_once_after_disk_io(monkeypatch):
    cache = MetadataCache()
    attempts = []

    class _Conn:
        def close(self):
            pass

    monkeypatch.setattr(cache, "_get_db", lambda: SimpleNamespace(_get_connection=lambda: _Conn()))
    monkeypatch.setattr(cache_module.time, "sleep", lambda _seconds: None)

    def _operation(_conn):
        attempts.append(1)
        if len(attempts) == 1:
            raise sqlite3.OperationalError("disk I/O error")
        return 9

    assert cache._run_maintenance_write("Cache eviction", _operation) == 9
    assert len(attempts) == 2


def test_maintenance_write_does_not_retry_non_io_errors(monkeypatch):
    cache = MetadataCache()
    attempts = []

    class _Conn:
        def close(self):
            pass

    monkeypatch.setattr(cache, "_get_db", lambda: SimpleNamespace(_get_connection=lambda: _Conn()))

    def _operation(_conn):
        attempts.append(1)
        raise sqlite3.OperationalError("syntax error")

    assert cache._run_maintenance_write("Cache eviction", _operation) == 0
    assert len(attempts) == 1
