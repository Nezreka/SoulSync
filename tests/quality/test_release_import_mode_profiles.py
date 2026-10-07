"""Profile-specific album import policy survives migration and API edits."""

from __future__ import annotations

import json
import sqlite3

import pytest
from flask import Flask

from api import quality_profiles
from core.quality.schema import ensure_quality_profiles_schema
from core.quality.selection import load_profile_by_id
from database.music_database import MusicDatabase


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "profiles.db"
    monkeypatch.setenv("DATABASE_PATH", str(path))
    return MusicDatabase(str(path))


@pytest.fixture
def client(db, monkeypatch):
    app = Flask(__name__)
    app.register_blueprint(quality_profiles.create_blueprint())
    monkeypatch.setattr(quality_profiles, "add_activity_item", lambda *args: None)
    # Applying/updating the default may mirror existing settings into config;
    # keep that unrelated file-write boundary isolated from the real config.
    from core.settings import config_manager
    monkeypatch.setattr(config_manager, "set", lambda *args: None)
    return app.test_client()


def test_old_sqlite_profiles_gain_safe_policy_without_losing_settings(tmp_path):
    conn = sqlite3.connect(tmp_path / "legacy.db")
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE quality_profiles (
        id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, description TEXT,
        ranked_targets TEXT NOT NULL DEFAULT '[]',
        fallback_enabled INTEGER NOT NULL DEFAULT 1,
        search_mode TEXT NOT NULL DEFAULT 'priority',
        rank_candidates_by_quality INTEGER NOT NULL DEFAULT 0,
        is_default INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute("""INSERT INTO quality_profiles
        (id, name, ranked_targets, fallback_enabled, is_default)
        VALUES (17, 'Existing', '[{"format":"flac"}]', 0, 1)""")
    ensure_quality_profiles_schema(conn)
    row = conn.execute("SELECT * FROM quality_profiles WHERE id=17").fetchone()
    assert row["release_import_mode"] == "requested_tracks"
    assert json.loads(row["ranked_targets"]) == [{"format": "flac"}]
    assert row["fallback_enabled"] == 0
    assert row["is_default"] == 1
    conn.execute("UPDATE quality_profiles SET release_import_mode='complete_album' WHERE id=17")
    ensure_quality_profiles_schema(conn)
    assert conn.execute("SELECT release_import_mode FROM quality_profiles WHERE id=17").fetchone()[0] == "complete_album"
    assert conn.execute("SELECT COUNT(*) FROM quality_profiles").fetchone()[0] == 1
    conn.close()


def test_every_fresh_profile_load_defaults_to_requested_tracks(db):
    assert db.get_quality_profile()["release_import_mode"] == "requested_tracks"
    assert all(p["release_import_mode"] == "requested_tracks" for p in db.list_quality_profiles())
    assert load_profile_by_id(None)["release_import_mode"] == "requested_tracks"
    assert load_profile_by_id(999999)["release_import_mode"] == "requested_tracks"
    for name in ("balanced", "audiophile", "space_saver"):
        assert db.get_quality_preset(name)["release_import_mode"] == "requested_tracks"


def test_custom_opt_in_round_trips_and_applies_as_default(db):
    pid = db.create_quality_profile("Album collector", {"release_import_mode": "complete_album"})
    assert pid is not None
    assert load_profile_by_id(pid)["release_import_mode"] == "complete_album"
    listed = next(p for p in db.list_quality_profiles() if p["id"] == pid)
    assert listed["release_import_mode"] == "complete_album"
    assert db.set_default_quality_profile(pid)
    assert db.get_quality_profile()["release_import_mode"] == "complete_album"
    assert db.set_quality_profile(db.get_quality_profile())
    assert db.get_quality_profile()["release_import_mode"] == "complete_album"
    assert db.update_quality_profile(pid, {"release_import_mode": "requested_tracks"})
    assert load_profile_by_id(pid)["release_import_mode"] == "requested_tracks"


def test_partial_custom_edit_preserves_policy_and_other_settings(db):
    pid = db.create_quality_profile("Strict album", {
        "release_import_mode": "complete_album", "acoustid_required": True,
        "fallback_enabled": False, "ranked_targets": [{"format": "flac"}],
    })
    assert db.update_quality_profile(pid, {"deep_audio_verify": True})
    profile = load_profile_by_id(pid)
    assert profile["release_import_mode"] == "complete_album"
    assert profile["acoustid_required"] is True
    assert profile["deep_audio_verify"] is True
    assert profile["fallback_enabled"] is False
    assert profile["ranked_targets"] == [{"format": "flac"}]


def test_default_partial_and_legacy_saves_preserve_album_policy(db):
    before = db.get_quality_profile()
    assert db.set_quality_profile({"release_import_mode": "complete_album"})
    opted_in = db.get_quality_profile()
    assert opted_in["release_import_mode"] == "complete_album"
    assert opted_in["ranked_targets"] == before["ranked_targets"]
    assert db.set_quality_profile({"ranked_targets": [{"format": "mp3"}], "fallback_enabled": False})
    after = db.get_quality_profile()
    assert after["release_import_mode"] == "complete_album"
    assert after["ranked_targets"] == [{"format": "mp3"}]
    assert db._legacy_quality_profile_from_preferences()["release_import_mode"] == "complete_album"


@pytest.mark.parametrize("value", ["all_files", "", None, True, [], {}])
def test_direct_profile_writes_normalize_malformed_policy(db, value):
    pid = db.create_quality_profile("Malformed", {"release_import_mode": value})
    assert load_profile_by_id(pid)["release_import_mode"] == "requested_tracks"
    assert db.update_quality_profile(pid, {"release_import_mode": value})
    assert load_profile_by_id(pid)["release_import_mode"] == "requested_tracks"
    assert db.set_quality_profile({"release_import_mode": value})
    assert db.get_quality_profile()["release_import_mode"] == "requested_tracks"


@pytest.mark.parametrize("value", ["all_files", None, [], {}])
def test_malformed_stored_policy_loads_as_requested_tracks(db, value):
    # Corrupt/pre-release rows must never accidentally enable expansion.
    conn = db._get_connection()
    conn.execute("UPDATE quality_profiles SET release_import_mode=?", (json.dumps(value),))
    conn.commit()
    conn.close()
    assert db.get_quality_profile()["release_import_mode"] == "requested_tracks"
    assert all(p["release_import_mode"] == "requested_tracks" for p in db.list_quality_profiles())


@pytest.mark.parametrize("version", [2, 3])
@pytest.mark.parametrize("value", ["complete_album", "invalid", None])
def test_legacy_fallback_policy_is_present_and_safe(db, version, value):
    db.set_preference("quality_profile", json.dumps({"version": version, "release_import_mode": value}))
    conn = db._get_connection()
    conn.execute("DROP TABLE quality_profiles")
    conn.commit()
    conn.close()
    expected = "complete_album" if value == "complete_album" else "requested_tracks"
    assert db.get_quality_profile()["release_import_mode"] == expected


def test_api_create_edit_list_load_and_default_round_trip(client, db):
    response = client.post("/api/quality-profile/custom", json={
        "name": "API album", "release_import_mode": "complete_album", "acoustid_required": True,
    })
    assert response.status_code == 200
    pid = response.get_json()["id"]
    response = client.post(f"/api/quality-profile/custom/{pid}/update", json={"deep_audio_verify": True})
    assert response.status_code == 200
    profile = client.get(f"/api/quality-profile/custom/{pid}").get_json()["profile"]
    assert profile["release_import_mode"] == "complete_album"
    assert profile["acoustid_required"] is True
    assert profile["deep_audio_verify"] is True
    assert db.set_default_quality_profile(pid)
    assert client.get("/api/quality-profile").get_json()["profile"]["release_import_mode"] == "complete_album"
    assert client.post("/api/quality-profile", json={"fallback_enabled": False}).status_code == 200
    assert client.get("/api/quality-profile").get_json()["profile"]["release_import_mode"] == "complete_album"
    assert all(isinstance(p["release_import_mode"], str)
               for p in client.get("/api/quality-profile/custom").get_json()["profiles"])


@pytest.mark.parametrize("value", ["everything", "", None, True, 1, [], {}])
@pytest.mark.parametrize("route", ["default", "create", "update"])
def test_api_rejects_invalid_policy_without_mutation(client, db, route, value):
    pid = db.get_quality_profile()["id"]
    path = {
        "default": "/api/quality-profile",
        "create": "/api/quality-profile/custom",
        "update": f"/api/quality-profile/custom/{pid}/update",
    }[route]
    before = db.list_quality_profiles()
    response = client.post(path, json={"name": "Must not exist", "release_import_mode": value})
    assert response.status_code == 400
    assert response.get_json()["success"] is False
    assert db.list_quality_profiles() == before


@pytest.mark.parametrize("suffix", ["", "/reset"])
def test_api_quick_sets_preserve_current_album_import_policy(client, db, suffix):
    assert db.set_quality_profile({"release_import_mode": "complete_album"})
    response = client.post(f"/api/quality-profile/preset/audiophile{suffix}")
    assert response.status_code == 200
    assert response.get_json()["profile"]["release_import_mode"] == "complete_album"
    assert db.get_quality_profile()["release_import_mode"] == "complete_album"
