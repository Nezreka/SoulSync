"""Batch watchlist add must report what it rejected.

Review-round-2 finding R2-15: an artist whose ``quality_profile_id`` failed
validation was skipped with a bare ``continue`` — no counter, no message. The
endpoint answered success with a silently smaller ``added`` count and the client
never learned which artists were dropped. The validation also pulled the whole
``quality_profiles`` table on every loop iteration.
"""

from __future__ import annotations

import pytest

pytest.importorskip("flask")

import web_server  # noqa: E402
from database.music_database import MusicDatabase  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db = MusicDatabase(str(tmp_path / "m.db"))

    import database.music_database as music_database
    monkeypatch.setattr(music_database, "get_database", lambda *a, **k: db)
    monkeypatch.setattr(web_server, "get_database", lambda *a, **k: db)
    # the route lives in api.artist_watchlist and holds its own injected
    # seams - patching web_server's aliases never reached it, which made
    # this test order-dependent (green only when an earlier test primed
    # the shared singleton)
    from api import artist_watchlist
    monkeypatch.setattr(artist_watchlist, "get_database", lambda *a, **k: db)
    monkeypatch.setattr(artist_watchlist, "get_current_profile_id", lambda: 1)
    monkeypatch.setattr(artist_watchlist, "_get_metadata_fallback_source", lambda: "deezer")
    monkeypatch.setattr(web_server, "spotify_client", None)
    web_server.app.config["TESTING"] = True
    with web_server.app.test_client() as test_client:
        yield test_client, db


def test_rejected_artists_are_reported_not_silently_dropped(client):
    test_client, db = client
    good = db.create_quality_profile("Hi-Res", {})

    response = test_client.post("/api/watchlist/add-batch", json={"artists": [
        {"artist_id": "sp-ok", "artist_name": "Kept", "quality_profile_id": good},
        {"artist_id": "sp-bad", "artist_name": "Dropped", "quality_profile_id": 999999},
    ]})

    body = response.get_json()
    assert response.status_code == 200, body
    assert body["added"] == 1
    assert [r["artist_name"] for r in body["rejected"]] == ["Dropped"]
    assert "rejected" in body["message"]


def test_profile_table_is_not_read_once_per_artist(client, monkeypatch):
    test_client, db = client
    good = db.create_quality_profile("Hi-Res", {})

    calls = {"n": 0}
    original = db.list_quality_profiles

    def counting():
        calls["n"] += 1
        return original()

    monkeypatch.setattr(db, "list_quality_profiles", counting)

    test_client.post("/api/watchlist/add-batch", json={"artists": [
        {"artist_id": f"sp-{i}", "artist_name": f"Artist {i}", "quality_profile_id": good}
        for i in range(25)
    ]})

    assert calls["n"] == 0, "validation must use quality_profile_exists, not a full table read"


def test_a_metadata_only_fallback_source_does_not_fail_the_batch(client, monkeypatch):
    """R2-01 for the batch path: hydrabase has no watchlist id column."""
    test_client, db = client
    monkeypatch.setattr(web_server, "_get_metadata_fallback_source", lambda: "hydrabase")

    response = test_client.post("/api/watchlist/add-batch", json={"artists": [
        {"artist_id": "123456", "artist_name": "Numeric Artist"},
    ]})

    body = response.get_json()
    assert response.status_code == 200, body
    assert body["added"] == 1
    assert body["rejected"] == []
