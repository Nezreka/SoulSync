from __future__ import annotations

import threading

from flask import Flask, g
import pytest

from core.playlists.curation import decorate_tracks
from database.music_database import MusicDatabase


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "music.db"))


def _mirror(db, *, profile_id=1, source="spotify", source_playlist_id="mix", tracks=None):
    return db.mirror_playlist(
        source=source,
        source_playlist_id=source_playlist_id,
        name="Mix",
        tracks=tracks or [
            {"track_name": "One", "artist_name": "Artist", "source_track_id": "one"},
            {"track_name": "Two", "artist_name": "Artist", "source_track_id": "two"},
        ],
        profile_id=profile_id,
    )


def _client(db, monkeypatch, *, admin=True, profile_id=1):
    import api.mirrored_playlists as mod

    monkeypatch.setattr(mod, "get_database", lambda: db)
    monkeypatch.setattr(mod, "get_current_profile_id", lambda: profile_id)
    invalidated = []
    monkeypatch.setattr(mod, "_invalidate_mirror_sync_fingerprint", invalidated.append)
    app = Flask(__name__)

    @app.before_request
    def _profile():
        g.is_admin = admin

    app.register_blueprint(mod.create_blueprint())
    return app.test_client(), invalidated


def test_schema_is_idempotent_and_existing_behavior_defaults_disabled(tmp_path):
    path = str(tmp_path / "music.db")
    first = MusicDatabase(path)
    playlist_id = _mirror(first)

    second = MusicDatabase(path)
    row = second.get_mirrored_playlist(playlist_id)
    policy = second.get_mirrored_playlist_curation(playlist_id)

    assert row["curation_enabled"] is False
    assert row["curation_revision"] == 0
    assert policy["enabled"] is False
    assert policy["decisions"] == {}
    assert policy["global_rejections"] == set()


def test_pending_is_stored_while_unreviewed_is_absence(db):
    playlist_id = _mirror(db)

    outcome = db.update_mirrored_playlist_decisions(
        playlist_id,
        [
            {"source_track_id": "one", "decision": "pending"},
            {"source_track_id": "two", "decision": "keep"},
        ],
        profile_id=1,
    )
    assert outcome["changed"] == 2
    assert outcome["revision"] == 1
    assert db.get_mirrored_playlist_curation(playlist_id)["decisions"] == {
        "one": "pending",
        "two": "keep",
    }

    cleared = db.update_mirrored_playlist_decisions(
        playlist_id,
        [{"source_track_id": "one", "decision": None}],
        profile_id=1,
    )
    assert cleared["changed"] == 1
    assert "one" not in db.get_mirrored_playlist_curation(playlist_id)["decisions"]


def test_keep_overrides_profile_source_global_rejection(db):
    playlist_id = _mirror(db)
    db.update_mirrored_playlist_decisions(
        playlist_id,
        [{"source_track_id": "one", "decision": "keep"}],
        profile_id=1,
    )
    result = db.update_mirrored_global_rejections(
        playlist_id, ["one", "two"], True, profile_id=1
    )
    assert result["changed"] == 2

    policy = db.get_mirrored_playlist_curation(playlist_id)
    tracks, counts = decorate_tracks(
        db.get_mirrored_playlist_tracks(playlist_id),
        enabled=True,
        decisions=policy["decisions"],
        global_rejections=policy["global_rejections"],
    )
    assert [(track["source_track_id"], track["effective_decision"]) for track in tracks] == [
        ("one", "keep"),
        ("two", "reject"),
    ]
    assert counts == {"keep": 1, "pending": 0, "reject": 1, "unreviewed": 0}


def test_decisions_survive_refresh_reorder_and_duplicate_membership(db):
    playlist_id = _mirror(db)
    db.update_mirrored_playlist_decisions(
        playlist_id,
        [{"source_track_id": "one", "decision": "keep"}],
        profile_id=1,
    )

    refreshed = db.mirror_playlist(
        source="spotify",
        source_playlist_id="mix",
        name="Mix",
        tracks=[
            {"track_name": "Two", "artist_name": "Artist", "source_track_id": "two"},
            {"track_name": "One", "artist_name": "Artist", "source_track_id": "one"},
            {"track_name": "One again", "artist_name": "Artist", "source_track_id": "one"},
        ],
        profile_id=1,
    )

    assert refreshed == playlist_id
    assert [row["source_track_id"] for row in db.get_mirrored_playlist_tracks(playlist_id)] == [
        "two", "one", "one"
    ]
    assert db.get_mirrored_playlist_curation(playlist_id)["decisions"] == {"one": "keep"}


def test_bulk_validation_is_atomic(db):
    playlist_id = _mirror(db)
    with pytest.raises(ValueError):
        db.update_mirrored_playlist_decisions(
            playlist_id,
            [
                {"source_track_id": "one", "decision": "keep"},
                {"source_track_id": "two", "decision": "maybe"},
            ],
            profile_id=1,
        )
    assert db.get_mirrored_playlist_curation(playlist_id)["decisions"] == {}

    unknown = db.update_mirrored_playlist_decisions(
        playlist_id,
        [
            {"source_track_id": "one", "decision": "keep"},
            {"source_track_id": "missing", "decision": "reject"},
        ],
        profile_id=1,
    )
    assert unknown["status"] == "unknown_track"
    assert db.get_mirrored_playlist_curation(playlist_id)["decisions"] == {}


def test_global_rejections_are_profile_and_source_scoped(db):
    spotify_one = _mirror(db, profile_id=1, source="spotify", source_playlist_id="one")
    spotify_two = _mirror(db, profile_id=2, source="spotify", source_playlist_id="two")
    deezer_one = _mirror(db, profile_id=1, source="deezer", source_playlist_id="three")

    db.update_mirrored_global_rejections(spotify_one, ["one"], True, profile_id=1)

    assert db.get_mirrored_playlist_curation(spotify_one)["global_rejections"] == {"one"}
    assert db.get_mirrored_playlist_curation(spotify_two)["global_rejections"] == set()
    assert db.get_mirrored_playlist_curation(deezer_one)["global_rejections"] == set()


def test_owner_scope_blocks_foreign_decision_mutations(db):
    playlist_id = _mirror(db, profile_id=2)
    outcome = db.update_mirrored_playlist_decisions(
        playlist_id,
        [{"source_track_id": "one", "decision": "keep"}],
        profile_id=1,
    )
    assert outcome["status"] == "not_found"
    assert db.get_mirrored_playlist_curation(playlist_id)["decisions"] == {}


def test_api_stages_decisions_enables_curation_and_projects_effective_state(db, monkeypatch):
    playlist_id = _mirror(db)
    client, invalidated = _client(db, monkeypatch)

    staged = client.patch(
        f"/api/mirrored-playlists/{playlist_id}/curation/decisions",
        json={"updates": [{"source_track_id": "one", "decision": "keep"}]},
    )
    assert staged.status_code == 200
    assert db.get_mirrored_playlist(playlist_id)["curation_enabled"] is False

    rejected = client.patch(
        f"/api/mirrored-playlists/{playlist_id}/curation/global-rejections",
        json={"source_track_ids": ["one", "two"], "rejected": True},
    )
    assert rejected.status_code == 200

    enabled = client.patch(
        f"/api/mirrored-playlists/{playlist_id}/preferences",
        json={"curation_enabled": True},
    )
    assert enabled.status_code == 200

    detail = client.get(f"/api/mirrored-playlists/{playlist_id}")
    assert detail.status_code == 200
    payload = detail.get_json()
    assert payload["curation_enabled"] is True
    assert payload["curation_counts"] == {
        "keep": 1, "pending": 0, "reject": 1, "unreviewed": 0,
    }
    assert [track["effective_decision"] for track in payload["tracks"]] == ["keep", "reject"]
    assert invalidated == [playlist_id, playlist_id, playlist_id]


def test_concurrent_bulk_mutations_serialize_without_lost_revision(db):
    playlist_id = _mirror(db)
    barrier = threading.Barrier(3)
    outcomes = []

    def _write(source_track_id, decision):
        barrier.wait()
        outcomes.append(db.update_mirrored_playlist_decisions(
            playlist_id,
            [{"source_track_id": source_track_id, "decision": decision}],
            profile_id=1,
        ))

    threads = [
        threading.Thread(target=_write, args=("one", "keep")),
        threading.Thread(target=_write, args=("two", "pending")),
    ]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=5)

    assert len(outcomes) == 2
    assert all(outcome["status"] == "ok" for outcome in outcomes)
    assert db.get_mirrored_playlist_curation(playlist_id)["decisions"] == {
        "one": "keep", "two": "pending",
    }
    assert db.get_mirrored_playlist(playlist_id)["curation_revision"] == 2
