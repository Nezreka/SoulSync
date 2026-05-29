"""Tests for the additive schema_migrations ledger + PRAGMA user_version backstop.

The ledger unifies the previously-scattered migration state (marker tables +
metadata flags) into one readable place so a half-migrated DB is detectable.
It is NON-GATING: nothing decides whether a migration runs based on it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from database.music_database import MusicDatabase


def _fresh_db(tmp_path: Path) -> MusicDatabase:
    return MusicDatabase(str(tmp_path / "library.db"))


def test_schema_migrations_table_exists(tmp_path: Path) -> None:
    db = _fresh_db(tmp_path)
    with db._get_connection() as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        ).fetchone()
    assert row is not None


def test_user_version_stamped(tmp_path: Path) -> None:
    db = _fresh_db(tmp_path)
    with db._get_connection() as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == MusicDatabase.SCHEMA_VERSION == 1


def test_record_migration_is_idempotent(tmp_path: Path) -> None:
    db = _fresh_db(tmp_path)
    with db._get_connection() as conn:
        cur = conn.cursor()
        db._record_migration(cur, "unit_test_mig")
        db._record_migration(cur, "unit_test_mig")
        conn.commit()
        n = cur.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE name = 'unit_test_mig'"
        ).fetchone()[0]
    assert n == 1


def test_genres_migration_recorded_on_fresh_init(tmp_path: Path) -> None:
    """The forward pattern: the genres migration records itself in the ledger."""
    db = _fresh_db(tmp_path)
    with db._get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM schema_migrations WHERE name = 'genres_json'"
        ).fetchone()
    assert row is not None


def test_ledger_backfills_from_existing_signals(tmp_path: Path) -> None:
    """Back-fill records both metadata-flag and marker-table migrations that are
    already present, under their canonical ledger names."""
    db = _fresh_db(tmp_path)
    with db._get_connection() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM schema_migrations")
        # A metadata-flag-style signal and a marker-table-style signal.
        cur.execute(
            "INSERT OR REPLACE INTO metadata (key, value, updated_at) "
            "VALUES ('metadata_cache_v1', '1', CURRENT_TIMESTAMP)"
        )
        cur.execute(
            "CREATE TABLE IF NOT EXISTS _genius_search_fix_applied "
            "(applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
        conn.commit()
        db._sync_migration_ledger(cur)
        conn.commit()
        names = {r[0] for r in cur.execute("SELECT name FROM schema_migrations")}
    assert "metadata_cache_v1" in names  # from the metadata flag
    assert "genius_search_fix" in names  # from the marker table


def test_ledger_does_not_record_absent_signals(tmp_path: Path) -> None:
    """A migration whose signal is absent must NOT be recorded as applied."""
    db = _fresh_db(tmp_path)
    with db._get_connection() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM schema_migrations")
        # Ensure the deezer-cache marker table does not exist.
        cur.execute("DROP TABLE IF EXISTS _deezer_cache_v2_migrated")
        conn.commit()
        db._sync_migration_ledger(cur)
        conn.commit()
        names = {r[0] for r in cur.execute("SELECT name FROM schema_migrations")}
    assert "deezer_cache_v2" not in names
