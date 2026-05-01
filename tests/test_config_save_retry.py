"""Regression tests for ConfigManager._save_config retry behaviour.

The DB-locking spam reported in #434 was caused by an aggressive retry
loop that gave up after one second and logged each transient lock as
ERROR. These tests pin the new behaviour:

- Lock errors during retries log at DEBUG, not ERROR (no spam).
- Six attempts with exponential backoff before giving up.
- Successful retry after a few transient locks emits zero ERROR logs.
- Genuine exhaustion logs a single ERROR and falls back to config.json.
- Non-lock OperationalErrors don't trigger the lock-specific quiet path.
"""

import json
import logging
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from config.settings import ConfigManager


@pytest.fixture
def manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ConfigManager:
    """Build a ConfigManager rooted at a tmp dir so every test starts clean.

    CRITICAL: ConfigManager reads ``DATABASE_PATH`` (not ``SOULSYNC_DB_PATH``)
    when picking the DB location. Setting the wrong env var here would let
    tests reach the real ``database/music_library.db`` and clobber the
    user's encrypted credentials. The ``database_path`` is also pinned
    directly on the instance after construction as a defense-in-depth check
    in case ConfigManager's resolution logic ever changes.
    """
    config_path = tmp_path / "config.json"
    db_path = tmp_path / "database" / "music_library.db"
    monkeypatch.setenv("SOULSYNC_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    mgr = ConfigManager(str(config_path))
    # Defense-in-depth: pin the path on the instance so even if ConfigManager
    # ignored the env var, the DB writes still land in the tmp directory.
    mgr.database_path = db_path
    mgr.config_path = config_path
    # Replace whatever was loaded with a known payload so we can assert on it
    mgr.config_data = {"plex": {"base_url": "http://example.test"}}
    assert str(mgr.database_path).startswith(str(tmp_path)), (
        "Test fixture would write to a non-tmp DB — refusing to run"
    )
    return mgr


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fail_n_times_then_succeed(n: int, manager: ConfigManager):
    """Patch ``_save_to_database`` so the first ``n`` calls fail (lock),
    then subsequent calls succeed."""
    state = {"calls": 0}
    real_save = manager._save_to_database

    def stub(config_data):
        state["calls"] += 1
        if state["calls"] <= n:
            return False
        return real_save(config_data)

    return stub, state


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_save_succeeds_on_first_attempt_emits_no_error_logs(
    manager: ConfigManager, caplog: pytest.LogCaptureFixture
) -> None:
    """Happy path: a successful save should not log at ERROR."""
    caplog.set_level(logging.DEBUG, logger="soulsync.config")
    with patch("config.settings.time.sleep") as sleep_mock:
        with patch.object(manager, "_save_to_database", return_value=True) as save_mock:
            manager._save_config()
    assert save_mock.call_count == 1
    sleep_mock.assert_not_called()
    error_logs = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert error_logs == []


def test_lock_errors_during_retries_log_at_debug_not_error(
    manager: ConfigManager, caplog: pytest.LogCaptureFixture
) -> None:
    """Three transient locks then success should produce DEBUG noise only."""
    caplog.set_level(logging.DEBUG, logger="soulsync.config")
    stub, state = _fail_n_times_then_succeed(3, manager)
    with patch("config.settings.time.sleep") as sleep_mock:
        with patch.object(manager, "_save_to_database", side_effect=stub):
            with patch.object(manager, "_ensure_database_exists"):
                manager._save_config()
    assert state["calls"] == 4
    assert sleep_mock.call_count == 3
    error_logs = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert error_logs == [], "transient locks should not log ERROR"


def test_save_uses_six_attempts_with_exponential_backoff(
    manager: ConfigManager,
) -> None:
    """All six attempts must run, with the documented backoff schedule."""
    with patch("config.settings.time.sleep") as sleep_mock:
        with patch.object(manager, "_save_to_database", return_value=False) as save_mock:
            with patch("builtins.open"):  # silence the json fallback's filesystem write
                with patch.object(Path, "mkdir"):
                    manager._save_config()
    assert save_mock.call_count == 6
    expected_delays = [0.2, 0.5, 1.0, 2.0, 4.0]
    actual_delays = [c.args[0] for c in sleep_mock.call_args_list]
    assert actual_delays == expected_delays


def test_all_retries_exhausted_logs_single_error_and_falls_back_to_json(
    manager: ConfigManager, caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    """Exhausting retries should produce one ERROR log + one fallback file."""
    caplog.set_level(logging.DEBUG, logger="soulsync.config")
    manager.config_path = tmp_path / "config.json"
    with patch("config.settings.time.sleep"):
        with patch.object(manager, "_save_to_database", return_value=False):
            manager._save_config()
    error_logs = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(error_logs) == 1
    assert "falling back to config.json" in error_logs[0].getMessage()
    assert manager.config_path.exists()
    payload = json.loads(manager.config_path.read_text())
    assert payload["plex"]["base_url"] == "http://example.test"


def test_save_to_database_lock_error_logs_at_debug(
    manager: ConfigManager, caplog: pytest.LogCaptureFixture
) -> None:
    """sqlite3.OperationalError("...locked...") must surface as DEBUG only."""
    caplog.set_level(logging.DEBUG, logger="soulsync.config")
    with patch.object(manager, "_ensure_database_exists"):
        with patch("config.settings.sqlite3.connect") as connect_mock:
            connect_mock.return_value.execute.side_effect = sqlite3.OperationalError(
                "database is locked"
            )
            ok = manager._save_to_database({"x": 1})
    assert ok is False
    error_logs = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert error_logs == []
    debug_logs = [
        r for r in caplog.records
        if r.levelno == logging.DEBUG and "locked" in r.getMessage().lower()
    ]
    assert len(debug_logs) == 1


def test_save_to_database_non_lock_operational_error_logs_at_error(
    manager: ConfigManager, caplog: pytest.LogCaptureFixture
) -> None:
    """A non-lock OperationalError is a real failure and must log ERROR."""
    caplog.set_level(logging.DEBUG, logger="soulsync.config")
    with patch.object(manager, "_ensure_database_exists"):
        with patch("config.settings.sqlite3.connect") as connect_mock:
            connect_mock.return_value.execute.side_effect = sqlite3.OperationalError(
                "no such table: metadata"
            )
            ok = manager._save_to_database({"x": 1})
    assert ok is False
    error_logs = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(error_logs) == 1


def test_connect_db_sets_required_pragmas(manager: ConfigManager) -> None:
    """All four pragmas must be applied on every config-DB connection."""
    conn = manager._connect_db()
    try:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        synchronous = conn.execute("PRAGMA synchronous").fetchone()[0]
    finally:
        conn.close()
    assert journal_mode.lower() == "wal"
    assert busy_timeout == 30000
    # synchronous returns 0=OFF, 1=NORMAL, 2=FULL, 3=EXTRA
    assert synchronous == 1
