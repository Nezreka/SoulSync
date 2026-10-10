"""get_database() cache contract: keyed by (thread id, resolved path).

Regression coverage for the CI flake where a test module's DATABASE_PATH was
silently ignored: get_database() cached one instance per thread, so the first
path requested won and every later path in that thread got the WRONG database
file (e.g. the reorganize ownership-guard tests 404'd with "Album not found"
because the endpoint queried another module's database file).
"""
from __future__ import annotations

import database.music_database as mdb


def _fresh_cache(monkeypatch):
    monkeypatch.setattr(mdb, "_database_instances", {})


def test_same_path_returns_same_instance(tmp_path, monkeypatch):
    _fresh_cache(monkeypatch)
    a = mdb.get_database(str(tmp_path / "a.db"))
    b = mdb.get_database(str(tmp_path / "a.db"))
    assert a is b


def test_different_paths_return_different_instances(tmp_path, monkeypatch):
    _fresh_cache(monkeypatch)
    a = mdb.get_database(str(tmp_path / "a.db"))
    b = mdb.get_database(str(tmp_path / "b.db"))
    assert a is not b


def test_env_path_change_is_honored_after_cache_prime(tmp_path, monkeypatch):
    # The exact CI shape: module A primes the cache, then module B sets
    # DATABASE_PATH and must get its own database, not A's.
    _fresh_cache(monkeypatch)
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "a.db"))
    a = mdb.get_database()
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "b.db"))
    b = mdb.get_database()
    assert a is not b


def test_writes_land_in_the_requested_file(tmp_path, monkeypatch):
    # End-to-end proof of the fixed failure mode: rows written through one
    # instance must not leak into (or vanish from) the other file.
    _fresh_cache(monkeypatch)
    a = mdb.get_database(str(tmp_path / "a.db"))
    b = mdb.get_database(str(tmp_path / "b.db"))
    a.create_profile("alice")
    names_a = {p["name"] for p in a.get_all_profiles()}
    names_b = {p["name"] for p in b.get_all_profiles()}
    assert "alice" in names_a
    assert "alice" not in names_b
