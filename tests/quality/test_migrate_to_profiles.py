"""One-time migration: pre-existing global quality settings must become the
new `quality_profiles` default row (winning over the factory seed), and every
existing wishlist row must be backfilled with the resolved flag columns.

Uses a real MusicDatabase (sqlite-only, no Flask app) so the full startup
schema-init sequence — rename, seed, additive columns, migration — runs
exactly as it does in the app, following the `tests/blocklist/test_blocklist_db.py`
pattern of a plain `MusicDatabase(tmp_path/...)` fixture.
"""

from __future__ import annotations

import json

import pytest

from database.music_database import MusicDatabase
from core.quality.migrate_to_profiles import (
    materialize_default_profile_and_backfill,
    _MIGRATION_FLAG_KEY,
)


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "m.db"))


def _insert_wishlist_row(db, spotify_track_id="sp1"):
    conn = db._get_connection()
    try:
        conn.execute(
            "INSERT INTO wishlist_tracks (spotify_track_id, spotify_data, source_type) "
            "VALUES (?, ?, 'manual')",
            (spotify_track_id, json.dumps({"id": spotify_track_id, "name": "Song"})),
        )
        conn.commit()
    finally:
        conn.close()


def test_migration_runs_once_on_fresh_db_and_sets_flag(db):
    conn = db._get_connection()
    try:
        flag = conn.execute(
            "SELECT value FROM metadata WHERE key=?", (_MIGRATION_FLAG_KEY,)
        ).fetchone()
        assert flag is not None
        row = conn.execute(
            "SELECT description FROM quality_profiles WHERE id=1"
        ).fetchone()
        assert row["description"] == "Migrated from your previous global Quality settings"
    finally:
        conn.close()


def test_migration_is_a_noop_on_second_call(db):
    conn = db._get_connection()
    try:
        # Already ran during MusicDatabase.__init__; a second explicit call
        # must be a no-op (flag already set) and must not raise.
        ran = materialize_default_profile_and_backfill(db, conn)
        assert ran is False
    finally:
        conn.close()


def test_migration_overwrites_factory_seed_with_real_prior_settings(db):
    """The critical correctness point: a pre-existing, non-default global
    profile must win over the hardcoded factory "Balanced" seed, not the other
    way around."""
    custom_profile = {
        "version": 3,
        "preset": "custom",
        "fallback_enabled": False,
        "search_mode": "best_quality",
        "rank_candidates_by_quality": True,
        "upgrade_policy": "until_cutoff",
        "upgrade_cutoff_index": 1,
        "ranked_targets": [{"label": "Only WAV", "format": "wav"}],
    }
    conn = db._get_connection()
    try:
        # Simulate: this WAS the user's active profile before the migration
        # code existed, and the one-time migration hasn't run yet on this row.
        db.set_preference("quality_profile", json.dumps(custom_profile))
        conn.execute("DELETE FROM metadata WHERE key=?", (_MIGRATION_FLAG_KEY,))
        conn.commit()

        ran = materialize_default_profile_and_backfill(db, conn)
        assert ran is True

        row = conn.execute(
            "SELECT ranked_targets, fallback_enabled, search_mode, "
            "rank_candidates_by_quality, upgrade_policy, upgrade_cutoff_index "
            "FROM quality_profiles WHERE id=1"
        ).fetchone()
        assert json.loads(row["ranked_targets"]) == custom_profile["ranked_targets"]
        assert row["fallback_enabled"] == 0
        assert row["search_mode"] == "best_quality"
        assert row["rank_candidates_by_quality"] == 1
        assert row["upgrade_policy"] == "until_cutoff"
        assert row["upgrade_cutoff_index"] == 1
    finally:
        conn.close()


def test_migration_gives_auto_import_its_own_relaxed_profile_when_filter_was_off(db, monkeypatch):
    """If an old install had the import-only quality gate disabled entirely,
    that most plausibly reflects Auto-Import specifically (it scans an
    already-acquired Staging folder with nothing to search for as an
    alternative) — not "I want every download/Wishlist item to accept
    anything too". The migration must NOT loosen the default profile itself;
    it creates a second, relaxed profile and assigns it to Auto-Import."""
    custom_profile = {
        "version": 3,
        "preset": "custom",
        "fallback_enabled": False,
        "ranked_targets": [{"label": "Only FLAC", "format": "flac"}],
    }
    set_calls = {}

    class _FakeCfg:
        def get(self, key, default=None):
            return {"import.quality_filter_enabled": False}.get(key, default)

        def set(self, key, value):
            set_calls[key] = value

    monkeypatch.setattr("config.settings.config_manager", _FakeCfg(), raising=False)

    conn = db._get_connection()
    try:
        db.set_preference("quality_profile", json.dumps(custom_profile))
        conn.execute("DELETE FROM metadata WHERE key=?", (_MIGRATION_FLAG_KEY,))
        conn.commit()

        assert materialize_default_profile_and_backfill(db, conn) is True

        # Default profile keeps the user's real fallback setting — untouched.
        default_row = conn.execute(
            "SELECT fallback_enabled FROM quality_profiles WHERE id=1"
        ).fetchone()
        assert default_row["fallback_enabled"] == 0

        # A second, relaxed profile exists and is assigned to Auto-Import.
        relaxed = conn.execute(
            "SELECT id, fallback_enabled, is_default FROM quality_profiles WHERE name=?",
            ("Auto-Import (accept anything)",),
        ).fetchone()
        assert relaxed is not None
        assert relaxed["fallback_enabled"] == 1
        assert relaxed["is_default"] == 0
        assert set_calls["auto_import.quality_profile_id"] == relaxed["id"]
    finally:
        conn.close()


def test_migration_does_not_overwrite_existing_auto_import_override(db, monkeypatch):
    """The relaxed profile must never clobber an Auto-Import assignment the
    user already made some other way."""
    set_calls = {}

    class _FakeCfg:
        def get(self, key, default=None):
            return {
                "import.quality_filter_enabled": False,
                "auto_import.quality_profile_id": 2,
            }.get(key, default)

        def set(self, key, value):
            set_calls[key] = value

    monkeypatch.setattr("config.settings.config_manager", _FakeCfg(), raising=False)

    conn = db._get_connection()
    try:
        conn.execute("DELETE FROM metadata WHERE key=?", (_MIGRATION_FLAG_KEY,))
        conn.commit()
        assert materialize_default_profile_and_backfill(db, conn) is True
        assert "auto_import.quality_profile_id" not in set_calls
    finally:
        conn.close()


def test_migration_renames_seeded_default_row_to_default(db):
    """The seeded name ("Balanced") describes a factory preset the user never
    actually chose; once the row holds their real carried-over settings it
    should read 'Default', not a preset name that no longer means anything."""
    conn = db._get_connection()
    try:
        row = conn.execute("SELECT name FROM quality_profiles WHERE id=1").fetchone()
        assert row["name"] == "Default"
    finally:
        conn.close()


def test_migration_preserves_a_name_the_user_already_chose(db):
    """An intermediate build may have let the user rename the default row
    before this migration flag existed — that choice must survive, not get
    silently overwritten to 'Default'."""
    conn = db._get_connection()
    try:
        conn.execute("UPDATE quality_profiles SET name='My Custom Profile' WHERE id=1")
        conn.execute("DELETE FROM metadata WHERE key=?", (_MIGRATION_FLAG_KEY,))
        conn.commit()

        ran = materialize_default_profile_and_backfill(db, conn)
        assert ran is True

        row = conn.execute("SELECT name FROM quality_profiles WHERE id=1").fetchone()
        assert row["name"] == "My Custom Profile"
    finally:
        conn.close()


def test_migration_backfills_existing_library_tracks(db):
    conn = db._get_connection()
    try:
        conn.execute("DELETE FROM metadata WHERE key=?", (_MIGRATION_FLAG_KEY,))
        conn.execute(
            "INSERT INTO artists (id, name) VALUES (1, 'Artist')"
        )
        conn.execute(
            "INSERT INTO albums (id, artist_id, title) VALUES (1, 1, 'Album')"
        )
        conn.execute(
            "INSERT INTO tracks (id, album_id, artist_id, title, quality_profile_id) "
            "VALUES (1, 1, 1, 'Track', NULL)"
        )
        conn.commit()
    finally:
        conn.close()

    conn = db._get_connection()
    try:
        ran = materialize_default_profile_and_backfill(db, conn)
        assert ran is True
        row = conn.execute("SELECT quality_profile_id FROM tracks WHERE id=1").fetchone()
        assert row["quality_profile_id"] == 1
    finally:
        conn.close()


def test_migration_backfills_existing_wishlist_rows(db):
    conn = db._get_connection()
    try:
        conn.execute("DELETE FROM metadata WHERE key=?", (_MIGRATION_FLAG_KEY,))
        conn.execute("UPDATE wishlist_tracks SET quality_profile_id=NULL")
        conn.commit()
    finally:
        conn.close()

    _insert_wishlist_row(db, "sp_backfill_1")

    conn = db._get_connection()
    try:
        ran = materialize_default_profile_and_backfill(db, conn)
        assert ran is True
        row = conn.execute(
            "SELECT quality_profile_id FROM wishlist_tracks WHERE spotify_track_id='sp_backfill_1'"
        ).fetchone()
        assert row["quality_profile_id"] == 1
    finally:
        conn.close()


def test_migration_uses_existing_default_when_id_one_is_gone(db):
    """Intermediate builds may have let users delete the seeded id=1 row
    before this migration flag existed. Upgrade must materialize into the
    surviving default row and backfill wishlist rows to that id, not recreate
    a dangling id=1 pointer."""
    conn = db._get_connection()
    try:
        conn.execute("DELETE FROM quality_profiles WHERE id=1")
        conn.execute("UPDATE quality_profiles SET is_default=1 WHERE id=2")
        conn.execute("DELETE FROM metadata WHERE key=?", (_MIGRATION_FLAG_KEY,))
        conn.execute("UPDATE wishlist_tracks SET quality_profile_id=NULL")
        conn.commit()
    finally:
        conn.close()

    _insert_wishlist_row(db, "sp_backfill_default_2")

    conn = db._get_connection()
    try:
        ran = materialize_default_profile_and_backfill(db, conn)
        assert ran is True
        row = conn.execute(
            "SELECT description, is_default FROM quality_profiles WHERE id=2"
        ).fetchone()
        assert row["description"] == "Migrated from your previous global Quality settings"
        assert row["is_default"] == 1
        wishlist_row = conn.execute(
            "SELECT quality_profile_id FROM wishlist_tracks WHERE spotify_track_id='sp_backfill_default_2'"
        ).fetchone()
        assert wishlist_row["quality_profile_id"] == 2
        assert conn.execute("SELECT id FROM quality_profiles WHERE id=1").fetchone() is None
    finally:
        conn.close()


def test_migration_resolves_acoustid_required_from_config(db, monkeypatch):
    conn = db._get_connection()
    try:
        conn.execute("DELETE FROM metadata WHERE key=?", (_MIGRATION_FLAG_KEY,))
        conn.commit()
    finally:
        conn.close()

    class _FakeCfg:
        def get(self, key, default=None):
            return {"acoustid.enabled": True, "acoustid.require_verified": True}.get(key, default)

    monkeypatch.setattr("config.settings.config_manager", _FakeCfg(), raising=False)

    conn = db._get_connection()
    try:
        materialize_default_profile_and_backfill(db, conn)
        row = conn.execute("SELECT acoustid_required FROM quality_profiles WHERE id=1").fetchone()
        assert row["acoustid_required"] == 1
    finally:
        conn.close()


def test_migration_captures_full_settings_bundle(db, monkeypatch):
    """Every Settings -> Quality toggle the profile now owns (deep verify,
    import quality-filter/replace-lower-quality, lossy-copy) must carry
    forward from the user's pre-existing global config, not just AcoustID
    and downsample."""
    conn = db._get_connection()
    try:
        conn.execute("DELETE FROM metadata WHERE key=?", (_MIGRATION_FLAG_KEY,))
        conn.commit()
    finally:
        conn.close()

    class _FakeCfg:
        def get(self, key, default=None):
            return {
                "acoustid.require_verified": True,
                "lossy_copy.downsample_hires": True,
                "post_processing.audio_completeness_check": True,
                "import.replace_lower_quality": True,
                "lossy_copy.enabled": True,
                "lossy_copy.codec": "aac",
                "lossy_copy.bitrate": "192",
                "lossy_copy.delete_original": True,
            }.get(key, default)

    monkeypatch.setattr("config.settings.config_manager", _FakeCfg(), raising=False)

    conn = db._get_connection()
    try:
        materialize_default_profile_and_backfill(db, conn)
        row = conn.execute("SELECT * FROM quality_profiles WHERE id=1").fetchone()
        assert row["deep_audio_verify"] == 1
        assert row["replace_lower_quality"] == 1
        assert row["lossy_copy_enabled"] == 1
        assert row["lossy_copy_codec"] == "aac"
        assert row["lossy_copy_bitrate"] == "192"
        assert row["lossy_copy_delete_original"] == 1
    finally:
        conn.close()


def test_migration_output_is_actually_consumed_end_to_end(db, monkeypatch, tmp_path):
    """Closes the loop between "the migration writes X" and "every consumer
    reads X" with a single test, instead of trusting isolated unit tests to
    compose correctly. Simulates a real upgrade: a user with real pre-existing
    global settings (never touched quality_profiles before) and one wishlist
    row queued from before the migration existed. After migration:

    - The REAL (unmocked) `load_profile_by_id(None)` — what every pipeline
      stage actually calls — must return the migrated settings, not the
      factory seed.
    - The wishlist row must be backfilled to point at that same profile.
    - Auto-Import (which has no `auto_import.quality_profile_id` configured,
      same as every existing install on upgrade) must resolve to the exact
      same migrated profile via the exact same code path — proving it
      inherits the user's real settings automatically, with zero extra
      configuration required on upgrade.
    """
    conn = db._get_connection()
    try:
        conn.execute("DELETE FROM metadata WHERE key=?", (_MIGRATION_FLAG_KEY,))
        conn.execute("UPDATE wishlist_tracks SET quality_profile_id=NULL")
        conn.commit()
    finally:
        conn.close()

    _insert_wishlist_row(db, "sp_e2e_1")

    # A real user's pre-existing global settings: strict AcoustID, downsample
    # on, deep-verify on, a non-default ranked-target list via preferences.
    custom_profile = {
        "version": 3,
        "preset": "custom",
        "fallback_enabled": False,
        "search_mode": "best_quality",
        "rank_candidates_by_quality": True,
        "ranked_targets": [{"label": "Only FLAC", "format": "flac"}],
    }
    db.set_preference("quality_profile", json.dumps(custom_profile))

    class _FakeCfg:
        def get(self, key, default=None):
            return {
                "acoustid.require_verified": True,
                "lossy_copy.downsample_hires": True,
                "post_processing.audio_completeness_check": True,
            }.get(key, default)

    monkeypatch.setattr("config.settings.config_manager", _FakeCfg(), raising=False)

    conn = db._get_connection()
    try:
        ran = materialize_default_profile_and_backfill(db, conn)
        assert ran is True
        # `materialize_default_profile_and_backfill` deliberately doesn't
        # commit (the real call site commits once for the whole init
        # transaction, see `MusicDatabase._initialize_database`) — this test
        # opens a genuinely separate connection below, so it must commit
        # here or the writes are invisible to it (and rolled back on close).
        conn.commit()
    finally:
        conn.close()

    # Point the default-path MusicDatabase() singleton `load_profile_by_id`
    # opens internally at this exact test DB, so we're calling the REAL
    # consumer code path, not a mock of it.
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "m.db"))

    from core.quality.selection import load_profile_by_id

    # What every pipeline stage with no per-item profile assigned resolves to
    # (Quality Upgrade's global default, and Auto-Import with no
    # auto_import.quality_profile_id configured — the exact state of every
    # existing install right after upgrading).
    resolved_default = load_profile_by_id(None)
    assert resolved_default["ranked_targets"] == custom_profile["ranked_targets"]
    assert resolved_default["fallback_enabled"] is False
    assert resolved_default["search_mode"] == "best_quality"
    assert resolved_default["acoustid_required"] is True
    assert resolved_default["downsample_enabled"] is True
    assert resolved_default["deep_audio_verify"] is True

    # The pre-existing wishlist row now points at that same profile.
    conn = db._get_connection()
    try:
        row = conn.execute(
            "SELECT quality_profile_id FROM wishlist_tracks WHERE spotify_track_id='sp_e2e_1'"
        ).fetchone()
        wishlist_profile_id = row["quality_profile_id"]
    finally:
        conn.close()
    assert wishlist_profile_id == 1

    resolved_for_wishlist_item = load_profile_by_id(wishlist_profile_id)
    assert resolved_for_wishlist_item["ranked_targets"] == custom_profile["ranked_targets"]

    # Auto-Import with nothing configured (auto_import.quality_profile_id is
    # unset on every existing install right after upgrading) resolves to the
    # IDENTICAL migrated profile as the wishlist item above — no separate
    # migration step needed for it to inherit the user's real settings.
    resolved_for_auto_import = load_profile_by_id(None)
    assert resolved_for_auto_import == resolved_default == resolved_for_wishlist_item
