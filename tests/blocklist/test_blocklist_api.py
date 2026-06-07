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
