"""the diagnostics block reads the database where the app opens it.

docker sets DATABASE_PATH=/app/data/music_library.db; the block looked only
at database/music_library.db and said "Database  not found" on every
container install (#2216)."""

from core.debug_info import database_size_label


def test_reads_the_database_path_the_app_uses(tmp_path, monkeypatch):
    db = tmp_path / "music_library.db"
    db.write_bytes(b"x" * (3 * 1024 * 1024))
    monkeypatch.setenv("DATABASE_PATH", str(db))
    assert database_size_label() == "3.0 MB"


def test_a_missing_database_still_says_not_found(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "nope.db"))
    assert database_size_label() == "not found"
