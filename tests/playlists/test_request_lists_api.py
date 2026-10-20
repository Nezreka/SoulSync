"""Tests for Request Lists API endpoints.
Tests copy-to-request-list, request-list-settings, and get-mirrored-playlists with is_request_list filtering.
"""

import pytest
from flask import Flask, g

from database.music_database import MusicDatabase


@pytest.fixture()
def mdb(tmp_path):
    return MusicDatabase(database_path=str(tmp_path / "music.db"))


def _mirror(db, name="Roadtrip", source="spotify"):
    return db.mirror_playlist(
        source=source,
        source_playlist_id=f"sp_{name}",
        name=name,
        tracks=[
            {"track_name": "Song 1", "artist_name": "Artist A", "source_track_id": "tr1"},
            {"track_name": "Song 2", "artist_name": "Artist B", "source_track_id": "tr2"},
        ],
        profile_id=1,
    )


def _client(db, monkeypatch):
    import api.mirrored_playlists as mod
    monkeypatch.setattr(mod, "get_database", lambda: db)
    app = Flask(__name__)

    @app.before_request
    def _stamp():
        g.is_admin = True

    app.register_blueprint(mod.create_blueprint())
    return app.test_client()


def test_copy_to_request_list(mdb, monkeypatch):
    pk = _mirror(mdb, "Original Mirror")
    c = _client(mdb, monkeypatch)

    resp = c.post(
        f"/api/mirrored-playlists/{pk}/copy-to-request-list",
        json={"custom_name": "My Copied Request List"},
    )
    assert resp.status_code == 200, resp.data
    data = resp.get_json()
    assert data["success"] is True
    new_pk = data["new_playlist_id"]
    assert new_pk != pk

    orig = mdb.get_mirrored_playlist(pk)
    assert orig["is_request_list"] is False

    copied = mdb.get_mirrored_playlist(new_pk)
    assert copied["is_request_list"] is True
    assert copied["name"] == "My Copied Request List"
    assert copied["upstream_playlist_id"] == "sp_Original Mirror"
    tracks = mdb.get_mirrored_playlist_tracks(new_pk)
    assert len(tracks) == 2


def test_request_list_settings(mdb, monkeypatch):
    pk = _mirror(mdb, "Spotify Requests")
    copy_pk = mdb.copy_to_request_list(pk, name="Spotify Requests (Copy)")
    c = _client(mdb, monkeypatch)

    # Update auto_remove_fulfilled
    resp = c.patch(
        f"/api/mirrored-playlists/{copy_pk}/request-list-settings",
        json={"auto_remove_fulfilled": True},
    )
    assert resp.status_code == 200, resp.data
    assert resp.get_json()["playlist"]["auto_remove_fulfilled"] is True

    # Turn it off
    resp = c.patch(
        f"/api/mirrored-playlists/{copy_pk}/request-list-settings",
        json={"auto_remove_fulfilled": False},
    )
    assert resp.status_code == 200, resp.data
    assert resp.get_json()["playlist"]["auto_remove_fulfilled"] is False

    # Validation: missing field
    resp = c.patch(f"/api/mirrored-playlists/{copy_pk}/request-list-settings", json={})
    assert resp.status_code == 400


def test_get_mirrored_playlists_with_filter(mdb, monkeypatch):
    pk1 = _mirror(mdb, "Normal Mirror 1")
    pk2 = _mirror(mdb, "Normal Mirror 2")
    req_pk = mdb.copy_to_request_list(pk2, name="Req Mirror")

    c = _client(mdb, monkeypatch)

    # All
    resp = c.get("/api/mirrored-playlists")
    assert resp.status_code == 200
    all_pls = resp.get_json()
    assert len(all_pls) == 3

    # Filter is_request_list=true
    resp = c.get("/api/mirrored-playlists?is_request_list=true")
    assert resp.status_code == 200
    req_pls = resp.get_json()
    assert len(req_pls) == 1
    assert req_pls[0]["id"] == req_pk

    # Filter is_request_list=false
    resp = c.get("/api/mirrored-playlists?is_request_list=false")
    assert resp.status_code == 200
    norm_pls = resp.get_json()
    assert len(norm_pls) == 2
    assert {p["id"] for p in norm_pls} == {pk1, pk2}
