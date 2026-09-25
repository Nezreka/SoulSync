"""`get_quality_profile()` compatibility shim + the new app-wide quality-profile
CRUD (`list/create/rename/delete/set_default_quality_profile`).

The shim must keep returning the same v3 dict shape every existing caller
(`core/imports/guards.py`, `core/repair_jobs/quality_upgrade.py`, the legacy
`/api/quality-profile*` endpoints) already relies on, even though the data now
lives in `quality_profiles` instead of the `preferences.quality_profile`
singleton.
"""

from __future__ import annotations

import pytest

from database.music_database import MusicDatabase


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "m.db"))


def test_get_quality_profile_dict_shape_unchanged(db):
    profile = db.get_quality_profile()
    assert isinstance(profile["id"], int)
    assert profile["name"] == "Default"
    assert profile["is_default"] is True
    assert profile["version"] == 3
    # `preset` is name-derived (see `_quality_profile_row_to_dict`) and the
    # migration renamed the default row away from the seeded "Balanced" name
    # (see test_migrate_to_profiles.py) — it no longer matches a built-in
    # preset name, so it's "custom" even though its targets are the factory
    # balanced defaults.
    assert profile["preset"] == "custom"
    assert isinstance(profile["ranked_targets"], list) and profile["ranked_targets"]
    assert isinstance(profile["fallback_enabled"], bool)
    assert profile["search_mode"] in ("priority", "best_quality")
    assert isinstance(profile["rank_candidates_by_quality"], bool)
    assert profile["upgrade_policy"] in ("none", "acceptable", "until_cutoff", "until_top")
    assert isinstance(profile["upgrade_cutoff_index"], int)


def test_set_quality_profile_writes_through_to_default_row(db):
    custom = {
        "version": 3,
        "preset": "custom",
        "fallback_enabled": False,
        "search_mode": "best_quality",
        "rank_candidates_by_quality": True,
        "upgrade_policy": "until_cutoff",
        "upgrade_cutoff_index": 1,
        "ranked_targets": [{"label": "Only FLAC", "format": "flac"}],
    }
    assert db.set_quality_profile(custom) is True

    reloaded = db.get_quality_profile()
    assert reloaded["ranked_targets"] == custom["ranked_targets"]
    assert reloaded["fallback_enabled"] is False
    assert reloaded["search_mode"] == "best_quality"
    assert reloaded["rank_candidates_by_quality"] is True
    assert reloaded["upgrade_policy"] == "until_cutoff"
    assert reloaded["upgrade_cutoff_index"] == 1
    # `preset` is derived from the default row's `name` column (still
    # "Default" — set_quality_profile only updates targets/flags, not the
    # row's name), not from whatever the caller's dict happened to carry.
    assert reloaded["preset"] == "custom"


def test_list_quality_profiles_includes_builtins(db):
    profiles = db.list_quality_profiles()
    names = [p["name"] for p in profiles]
    assert names[0] == "Default"  # is_default DESC, id — default sorts first
    assert "Upgrade until top quality" in names


def test_create_rename_delete_custom_profile(db):
    pid = db.create_quality_profile("My Profile", {
        "ranked_targets": [{"label": "MP3", "format": "mp3", "min_bitrate": 320}],
        "fallback_enabled": True,
    })
    assert pid is not None
    names = [p["name"] for p in db.list_quality_profiles()]
    assert "My Profile" in names

    ok, reason = db.rename_quality_profile(pid, "Renamed Profile")
    assert ok is True and reason == ""
    names = [p["name"] for p in db.list_quality_profiles()]
    assert "Renamed Profile" in names and "My Profile" not in names

    # Renaming onto an existing name is refused with a useful reason.
    ok, reason = db.rename_quality_profile(pid, "Default")
    assert ok is False and "already exists" in reason

    ok, reason = db.delete_quality_profile(pid)
    assert ok is True and reason == ""
    names = [p["name"] for p in db.list_quality_profiles()]
    assert "Renamed Profile" not in names


def test_delete_allows_builtins_when_not_default(db):
    # id=1 ("Default", migrated from the seeded "Balanced") is the default;
    # id=2 ("Upgrade until top quality") isn't — nothing about being a
    # built-in blocks deleting it.
    ok, reason = db.delete_quality_profile(2)
    assert ok is True and reason == ""
    names = [p["name"] for p in db.list_quality_profiles()]
    assert "Upgrade until top quality" not in names


def test_delete_current_default_auto_promotes_another(db):
    pid = db.create_quality_profile("Extra", {"ranked_targets": []})
    ok, reason = db.delete_quality_profile(1)  # id=1 is the default
    assert ok is True and reason == ""

    profiles = db.list_quality_profiles()
    assert not any(p["id"] == 1 for p in profiles)
    defaults = [p for p in profiles if p["is_default"]]
    assert len(defaults) == 1  # exactly one profile is now default
    assert defaults[0]["id"] in (2, pid)  # promoted to another remaining row


def test_delete_refuses_the_last_remaining_profile(db):
    db.delete_quality_profile(2)
    ok, reason = db.delete_quality_profile(1)
    assert ok is False
    assert "at least one" in reason.lower()
    assert len(db.list_quality_profiles()) == 1


def test_delete_unknown_profile_id_fails(db):
    ok, reason = db.delete_quality_profile(999999)
    assert ok is False
    assert reason == "Profile not found"


def test_delete_repoints_wishlist_references_to_null(db):
    """Wishlist rows assigned the deleted profile are re-pointed to NULL
    (= "use the default at read time") in the same transaction, so no row is
    left holding a dangling id."""
    pid = db.create_quality_profile("Doomed", {"ranked_targets": []})
    track = {"id": "sp-del-1", "name": "Song",
             "artists": [{"id": "ar1", "name": "Artist"}],
             "album": {"id": "al1", "name": "Album", "artists": [{"id": "ar1", "name": "Artist"}]}}
    assert db.add_to_wishlist(track, source_type="manual", user_initiated=True,
                              quality_profile_id=pid) is True

    ok, reason = db.delete_quality_profile(pid)
    assert ok is True and reason == ""

    conn = db._get_connection()
    try:
        # Keyed `<track>::<album>` since composite ids became canonical.
        row = conn.execute(
            "SELECT quality_profile_id FROM wishlist_tracks "
            "WHERE spotify_track_id = 'sp-del-1' OR spotify_track_id LIKE 'sp-del-1::%'"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["quality_profile_id"] is None


def _seed_lib2_quality_references(db, profile_id):
    conn = db._get_connection()
    try:
        artist_id = conn.execute(
            "INSERT INTO lib2_artists(name, quality_profile_id) VALUES(?, ?)",
            (f"Artist {profile_id}", profile_id),
        ).lastrowid
        album_id = conn.execute(
            "INSERT INTO lib2_albums(primary_artist_id, title, quality_profile_id) "
            "VALUES(?, ?, ?)",
            (artist_id, f"Album {profile_id}", profile_id),
        ).lastrowid
        track_id = conn.execute(
            "INSERT INTO lib2_tracks(album_id, title, quality_profile_id) "
            "VALUES(?, ?, ?)",
            (album_id, f"Track {profile_id}", profile_id),
        ).lastrowid
        conn.commit()
        return artist_id, album_id, track_id
    finally:
        conn.close()


def _lib2_quality_references(db, ids):
    artist_id, album_id, track_id = ids
    conn = db._get_connection()
    try:
        return (
            conn.execute(
                "SELECT quality_profile_id FROM lib2_artists WHERE id=?",
                (artist_id,),
            ).fetchone()[0],
            conn.execute(
                "SELECT quality_profile_id FROM lib2_albums WHERE id=?",
                (album_id,),
            ).fetchone()[0],
            conn.execute(
                "SELECT quality_profile_id FROM lib2_tracks WHERE id=?",
                (track_id,),
            ).fetchone()[0],
        )
    finally:
        conn.close()


def test_delete_repoints_lib2_references_to_current_default(db):
    """The library's own assignments — the counterpart of the wishlist case.
    A library reference is easy to forget to wire into the same cleanup
    (caught in review once: it was). Note the difference from the wishlist:
    there "no profile" is NULL, in the catalogue it is the current default
    plus `quality_profile_explicit = 0`, because the column is NOT NULL and a
    trigger rejects a pointer to a profile that no longer exists."""
    pid = db.create_quality_profile("Doomed Lib2", {"ranked_targets": []})
    ids = _seed_lib2_quality_references(db, pid)
    default_id = next(p["id"] for p in db.list_quality_profiles() if p["is_default"])

    ok, reason = db.delete_quality_profile(pid)

    assert ok is True and reason == ""
    assert _lib2_quality_references(db, ids) == (default_id, default_id, default_id)


def test_delete_default_repoints_lib2_references_to_promoted_profile(db):
    ids = _seed_lib2_quality_references(db, 1)

    ok, reason = db.delete_quality_profile(1)

    assert ok is True and reason == ""
    promoted_id = next(p["id"] for p in db.list_quality_profiles() if p["is_default"])
    assert promoted_id != 1
    assert _lib2_quality_references(db, ids) == (promoted_id, promoted_id, promoted_id)


def test_delete_clears_matching_auto_import_override(db, monkeypatch):
    calls = {}

    class _FakeCfg:
        def get(self, key, default=None):
            return {"auto_import.quality_profile_id": "2"}.get(key, default)

        def set(self, key, value):
            calls[key] = value

    monkeypatch.setattr("core.settings.config_manager", _FakeCfg(), raising=False)

    ok, reason = db.delete_quality_profile(2)
    assert ok is True and reason == ""
    assert calls == {"auto_import.quality_profile_id": None}


def test_sync_default_quality_profile_from_config(db, monkeypatch):
    """Settings-save write-through: the config values of every profile-owned
    setting land on the is_default row, so the pipeline (which reads the
    profile, not the config) picks up Settings-page edits immediately."""
    class _FakeCfg:
        def get(self, key, default=None):
            return {
                "acoustid.require_verified": True,
                "lossy_copy.downsample_hires": True,
                "post_processing.audio_completeness_check": True,
                "import.replace_lower_quality": True,
                "lossy_copy.enabled": True,
                "lossy_copy.codec": "opus",
                "lossy_copy.bitrate": "192",
                "lossy_copy.delete_original": True,
            }.get(key, default)

    monkeypatch.setattr("core.settings.config_manager", _FakeCfg(), raising=False)

    assert db.sync_default_quality_profile_from_config() is True

    profile = db.get_quality_profile()
    assert profile["acoustid_required"] is True
    assert profile["downsample_enabled"] is True
    assert profile["deep_audio_verify"] is True
    assert profile["replace_lower_quality"] is True
    assert profile["lossy_copy_enabled"] is True
    assert profile["lossy_copy_codec"] == "opus"
    assert profile["lossy_copy_bitrate"] == "192"
    assert profile["lossy_copy_delete_original"] is True

    # Only the default row is touched — a non-default profile keeps its values.
    conn = db._get_connection()
    try:
        other = conn.execute(
            "SELECT acoustid_required FROM quality_profiles WHERE is_default=0 LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    assert other["acoustid_required"] == 0


def test_set_default_quality_profile_switches_get_quality_profile(db):
    pid = db.create_quality_profile("Space Saver Custom", {
        "ranked_targets": [{"label": "MP3 128kbps", "format": "mp3", "min_bitrate": 128}],
        "fallback_enabled": True,
    })
    assert db.set_default_quality_profile(pid) is True

    profile = db.get_quality_profile()
    assert profile["ranked_targets"] == [{"label": "MP3 128kbps", "format": "mp3", "min_bitrate": 128}]

    # Only one row should carry is_default=1.
    conn = db._get_connection()
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM quality_profiles WHERE is_default=1"
        ).fetchone()[0]
        assert count == 1
    finally:
        conn.close()


def test_create_quality_profile_rejects_blank_name(db):
    assert db.create_quality_profile("", {"ranked_targets": []}) is None
    assert db.create_quality_profile("   ", {"ranked_targets": []}) is None


# ── Full settings bundle (acoustid/downsample/deep-verify/import/lossy-copy) ──

_FULL_BUNDLE = {
    "ranked_targets": [{"label": "FLAC", "format": "flac"}],
    "fallback_enabled": False,
    "search_mode": "best_quality",
    "rank_candidates_by_quality": True,
    "upgrade_policy": "until_cutoff",
    "upgrade_cutoff_index": 0,
    "acoustid_required": True,
    "downsample_enabled": True,
    "deep_audio_verify": True,
    "replace_lower_quality": True,
    "lossy_copy_enabled": True,
    "lossy_copy_codec": "opus",
    "lossy_copy_bitrate": "256",
    "lossy_copy_delete_original": True,
}


def _assert_bundle_matches(profile, bundle=_FULL_BUNDLE):
    for key in bundle:
        assert profile[key] == bundle[key], key


def test_create_quality_profile_captures_full_settings_bundle(db):
    pid = db.create_quality_profile("Full Bundle", _FULL_BUNDLE)
    assert pid is not None

    conn = db._get_connection()
    row = conn.execute("SELECT * FROM quality_profiles WHERE id=?", (pid,)).fetchone()
    conn.close()
    profile = db._quality_profile_row_to_dict(row)
    _assert_bundle_matches(profile)


def test_update_quality_profile_overwrites_settings_in_place(db):
    pid = db.create_quality_profile("Editable", {"ranked_targets": [], "acoustid_required": False})
    assert db.update_quality_profile(pid, _FULL_BUNDLE) is True

    conn = db._get_connection()
    row = conn.execute("SELECT * FROM quality_profiles WHERE id=?", (pid,)).fetchone()
    conn.close()
    profile = db._quality_profile_row_to_dict(row)
    _assert_bundle_matches(profile)
    # Name is untouched by update.
    assert row["name"] == "Editable"


def test_apply_quality_profile_to_settings_pushes_into_config_manager(db, monkeypatch):
    calls = {}

    class _FakeCfg:
        def set(self, key, value):
            calls[key] = value

    monkeypatch.setattr("core.settings.config_manager", _FakeCfg(), raising=False)

    pid = db.create_quality_profile("Strict Everything", _FULL_BUNDLE)
    applied = db.apply_quality_profile_to_settings(pid)

    assert applied is not None
    _assert_bundle_matches(applied)
    assert calls == {
        "acoustid.require_verified": True,
        "lossy_copy.downsample_hires": True,
        "post_processing.audio_completeness_check": True,
        "import.replace_lower_quality": True,
        "lossy_copy.enabled": True,
        "lossy_copy.codec": "opus",
        "lossy_copy.bitrate": "256",
        "lossy_copy.delete_original": True,
    }

    # Also becomes the default row.
    conn = db._get_connection()
    is_default = conn.execute("SELECT is_default FROM quality_profiles WHERE id=?", (pid,)).fetchone()[0]
    conn.close()
    assert is_default == 1


def test_apply_quality_profile_to_settings_returns_none_for_unknown_id(db):
    assert db.apply_quality_profile_to_settings(999999) is None


# ---------------------------------------------------------------------------
# The write-through only touches what the caller actually sent (#1103)
# ---------------------------------------------------------------------------

_BUNDLE_ON = {
    "acoustid_required": 1,
    "downsample_enabled": 1,
    "deep_audio_verify": 1,
    "replace_lower_quality": 1,
    "lossy_copy_enabled": 1,
    "lossy_copy_codec": "opus",
    "lossy_copy_bitrate": "256",
    "lossy_copy_delete_original": 1,
}

# Exactly what settings.js::collectQualityProfileFromUI() posts to
# POST /api/quality-profile — the ladder and nothing else.
_LADDER_ONLY = {
    "version": 3,
    "preset": "custom",
    "fallback_enabled": True,
    "search_mode": "priority",
    "rank_candidates_by_quality": False,
    "upgrade_policy": "acceptable",
    "upgrade_cutoff_index": 0,
    "ranked_targets": [{"format": "flac"}],
}


def _arm_bundle(db):
    """Turn every non-ladder setting on, the way the profile CRUD would."""
    conn = db._get_connection()
    try:
        conn.execute(
            "UPDATE quality_profiles SET "
            + ", ".join(f"{c}=?" for c in _BUNDLE_ON)
            + " WHERE is_default=1",
            tuple(_BUNDLE_ON.values()),
        )
        conn.commit()
    finally:
        conn.close()


def _bundle_row(db):
    conn = db._get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM quality_profiles WHERE is_default=1").fetchone()
        return {c: row[c] for c in _BUNDLE_ON}
    finally:
        conn.close()


def test_a_ladder_only_save_does_not_reset_the_other_settings(db):
    """The Settings page posts six fields; the row holds fourteen.

    Coercing the eight it never mentions would turn lossy-copy, AcoustID
    strictness and replace-lower-quality off every time somebody reordered the
    target ladder — and those eight are owned by the config, which reaches this
    row through sync_default_quality_profile_from_config().
    """
    _arm_bundle(db)

    assert db.set_quality_profile(dict(_LADDER_ONLY)) is True

    assert _bundle_row(db) == _BUNDLE_ON


def test_a_preset_apply_does_not_reset_them_either(db):
    """A Quick Set preset carries fewer keys still (ranked_targets, preset,
    version, fallback_enabled, qualities) — same rule applies."""
    _arm_bundle(db)

    assert db.set_quality_profile(db.get_quality_preset("lossless")) is True

    assert _bundle_row(db) == _BUNDLE_ON


def test_naming_a_field_writes_it_including_switching_it_off(db):
    """Presence decides, not truthiness — otherwise a caller could turn these
    on but never off, which is the half-working state #1103 reported."""
    _arm_bundle(db)

    assert db.set_quality_profile({
        **_LADDER_ONLY,
        "replace_lower_quality": False,
        "lossy_copy_enabled": False,
        "lossy_copy_codec": "aac",
    }) is True

    row = _bundle_row(db)
    assert row["replace_lower_quality"] == 0
    assert row["lossy_copy_enabled"] == 0
    assert row["lossy_copy_codec"] == "aac"
    # Untouched keys keep their value.
    assert row["acoustid_required"] == 1
    assert row["lossy_copy_bitrate"] == "256"
    assert row["lossy_copy_delete_original"] == 1


def test_a_full_get_payload_round_trips(db):
    """GET returns all fourteen; posting that back must be a no-op rather than
    dropping the eight the endpoint used to ignore."""
    _arm_bundle(db)
    before = db.get_quality_profile()

    assert db.set_quality_profile(before) is True

    after = db.get_quality_profile()
    for field in _BUNDLE_ON:
        assert after[field] == before[field], field


def test_the_ladder_is_still_written_unconditionally(db):
    """Guard for the sparse UPDATE: the six ladder columns must never fall out
    of the statement just because the bundle ones did."""
    assert db.set_quality_profile({
        **_LADDER_ONLY,
        "fallback_enabled": False,
        "search_mode": "best_quality",
        "rank_candidates_by_quality": True,
        "upgrade_policy": "until_cutoff",
        "upgrade_cutoff_index": 2,
        "ranked_targets": [{"label": "Only FLAC", "format": "flac"}],
    }) is True

    reloaded = db.get_quality_profile()
    assert reloaded["fallback_enabled"] is False
    assert reloaded["search_mode"] == "best_quality"
    assert reloaded["rank_candidates_by_quality"] is True
    assert reloaded["upgrade_policy"] == "until_cutoff"
    assert reloaded["upgrade_cutoff_index"] == 2
    assert reloaded["ranked_targets"] == [{"label": "Only FLAC", "format": "flac"}]
