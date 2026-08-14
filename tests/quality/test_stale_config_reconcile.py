"""Deleting a Quality Profile leaves no dangling config reference.

Audit finding P3-02: the delete commits the database transaction and only then
clears the matching Auto-Import config override. A DB commit and a config-file
write cannot be atomic, so the second step can fail and leave a stale id behind.

The contract: the database is authoritative, and the config cleanup is idempotent
and retried on every startup until it succeeds.
"""

from __future__ import annotations

import pytest

from core.quality.migrate_to_profiles import (
    QUALITY_PROFILE_CONFIG_KEYS,
    reconcile_stale_quality_profile_config,
)
from database.music_database import MusicDatabase


class _StubConfig:
    """Minimal config_manager stand-in; can be told to fail on write."""

    def __init__(self, values=None, fail_on_set=False):
        self.values = dict(values or {})
        self.fail_on_set = fail_on_set
        self.sets = []

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        if self.fail_on_set:
            raise OSError("config volume is read-only")
        self.sets.append((key, value))
        self.values[key] = value


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "m.db"))


@pytest.fixture()
def config(monkeypatch):
    stub = _StubConfig()
    import core.settings as settings
    monkeypatch.setattr(settings, "config_manager", stub)
    return stub


def test_key_list_is_not_empty():
    assert QUALITY_PROFILE_CONFIG_KEYS


def test_reconcile_clears_a_reference_to_a_deleted_profile(db, config):
    config.values["auto_import.quality_profile_id"] = 999999

    assert reconcile_stale_quality_profile_config(db) == 1
    assert config.values["auto_import.quality_profile_id"] is None


def test_reconcile_keeps_a_valid_reference(db, config):
    valid = db.create_quality_profile("Hi-Res", {})
    config.values["auto_import.quality_profile_id"] = valid

    assert reconcile_stale_quality_profile_config(db) == 0
    assert config.values["auto_import.quality_profile_id"] == valid


def test_reconcile_ignores_an_unset_reference(db, config):
    assert reconcile_stale_quality_profile_config(db) == 0
    assert config.sets == []


def test_reconcile_clears_a_non_integer_reference(db, config):
    """A junk value can never name a profile, so it is stale by definition."""
    config.values["auto_import.quality_profile_id"] = "not-an-id"

    assert reconcile_stale_quality_profile_config(db) == 1
    assert config.values["auto_import.quality_profile_id"] is None


def test_reconcile_is_idempotent(db, config):
    config.values["auto_import.quality_profile_id"] = 999999

    reconcile_stale_quality_profile_config(db)
    assert reconcile_stale_quality_profile_config(db) == 0


def test_delete_reconciles_the_config_override(db, config):
    keep = db.create_quality_profile("Keep", {})
    doomed = db.create_quality_profile("Doomed", {})
    assert keep and doomed
    config.values["auto_import.quality_profile_id"] = doomed

    ok, reason = db.delete_quality_profile(doomed)

    assert (ok, reason) == (True, "")
    assert config.values["auto_import.quality_profile_id"] is None


def test_a_failing_config_write_does_not_fail_the_delete(db, config, caplog):
    """The DB is the source of truth: the row must go even if config can't be written."""
    keep = db.create_quality_profile("Keep", {})
    doomed = db.create_quality_profile("Doomed", {})
    config.values["auto_import.quality_profile_id"] = doomed
    config.fail_on_set = True

    ok, reason = db.delete_quality_profile(doomed)

    assert (ok, reason) == (True, "")
    assert db.quality_profile_exists(doomed) is False
    # …and the stale reference is still there, waiting for the startup retry.
    assert config.values["auto_import.quality_profile_id"] == doomed

    # Which then fixes it, once the config store is writable again.
    config.fail_on_set = False
    assert reconcile_stale_quality_profile_config(db) == 1
    assert config.values["auto_import.quality_profile_id"] is None
