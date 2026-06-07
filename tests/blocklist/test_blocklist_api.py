"""Blocklist HTTP API: search / add (+ synchronous cross-source backfill) /
list / delete, end to end through the Flask test client."""

from __future__ import annotations

from unittest.mock import patch

import pytest

web_server = pytest.importorskip("web_server")


@pytest.fixture()
def client(monkeypatch):
    web_server.app.config["TESTING"] = True
    monkeypatch.setattr(web_server, "get_current_profile_id", lambda: 1)
    return web_server.app.test_client()


def test_search_proxies_active_source(client):
    with patch.object(web_server, "_search_service", return_value=[
            {"id": "drake-sp", "name": "Drake", "image": None, "extra": "", "provider": "spotify"}]):
        r = client.get("/api/blocklist/search?type=artist&q=drake")
    assert r.status_code == 200
    body = r.get_json()
    assert body["success"] and body["results"][0]["name"] == "Drake"


def test_add_resolves_other_sources_then_list_and_delete(client):
    resolvers = {
        "itunes": lambda et, n, p: "drake-it",
        "deezer": lambda et, n, p: None,
        "spotify": lambda et, n, p: None,
        "musicbrainz": lambda et, n, p: None,
    }
    with patch("core.blocklist.runtime.build_resolvers", return_value=resolvers):
        r = client.post("/api/blocklist", json={
            "entity_type": "artist", "name": "Drake Test One",
            "source": "spotify", "source_id": "drake-sp1"})
    assert r.status_code == 200 and r.get_json()["success"]
    eid = r.get_json()["id"]

    rows = client.get("/api/blocklist?entity_type=artist").get_json()["entries"]
    row = next(x for x in rows if x["id"] == eid)
    assert row["spotify_id"] == "drake-sp1"
    assert row["itunes_id"] == "drake-it"        # resolved at add time
    assert row["match_status"] == "matched"

    assert client.delete(f"/api/blocklist/{eid}").get_json()["success"] is True
    rows = client.get("/api/blocklist?entity_type=artist").get_json()["entries"]
    assert all(x["id"] != eid for x in rows)


def test_add_requires_type_and_name(client):
    r = client.post("/api/blocklist", json={"entity_type": "artist"})
    assert r.status_code == 400


def test_invalid_entity_type_rejected(client):
    assert client.get("/api/blocklist?entity_type=bogus").status_code == 400
    assert client.get("/api/blocklist/search?type=bogus&q=x").status_code == 400


# ── Phase 2b: download-time guards (manual + modal up-front) ─────────────────

@pytest.fixture()
def banned_artist(client):
    """Block 'Banned Guy' for the test profile; clean up after."""
    db = web_server.get_database()
    eid = db.add_blocklist_entry(1, "artist", "Banned Guy", spotify_id="bg-sp")
    yield
    db.remove_blocklist_entry(1, eid)


def test_manual_download_blocked_by_artist_name(client, banned_artist):
    r = client.post("/api/download", json={
        "result_type": "track", "username": "peer", "filename": "x.flac",
        "artist": "Banned Guy", "title": "Song"})
    assert r.status_code == 409
    body = r.get_json()
    assert body["blocked"] is True and body["blocked_entity_type"] == "artist"


def test_manual_download_unrelated_artist_not_blocked(client, banned_artist):
    # Allowed artist → guard passes (the download itself may fail offline; we
    # only assert it wasn't blocked by the blocklist).
    r = client.post("/api/download", json={
        "result_type": "track", "username": "peer", "filename": "y.flac",
        "artist": "Allowed Artist", "title": "Song"})
    assert not (r.get_json() or {}).get("blocked")


def test_manual_download_override_passes_guard(client, banned_artist):
    r = client.post("/api/download", json={
        "result_type": "track", "username": "peer", "filename": "x.flac",
        "artist": "Banned Guy", "title": "Song", "ignore_blocklist": True})
    assert not (r.get_json() or {}).get("blocked")   # override skips the guard


def test_modal_blocked_album_returns_409_before_starting(client):
    db = web_server.get_database()
    eid = db.add_blocklist_entry(1, "album", "Banned Album", spotify_id="ba-sp")
    try:
        r = client.post("/api/playlists/artist_album_test/start-missing-process", json={
            "tracks": [{"id": "t1", "name": "Track"}],
            "is_album_download": True,
            "album_context": {"id": "ba-sp", "name": "Banned Album"},
            "artist_context": {"id": "ar1", "name": "Someone"},
        })
        assert r.status_code == 409
        body = r.get_json()
        assert body["blocked"] is True and body["blocked_entity_type"] == "album"
    finally:
        db.remove_blocklist_entry(1, eid)
