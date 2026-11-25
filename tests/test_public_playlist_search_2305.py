"""#2305: public playlists can be found by name, across the services that
need nobody's account, and each result carries the link the import takes."""

from unittest.mock import MagicMock

import pytest
from flask import Flask


def _row(source, n):
    return {"id": f"{source}{n}", "title": f"{source} mix {n}", "creator": "x", "track_count": 10,
            "image_url": "", "link": f"https://{source}.example/playlist/{n}", "source": source}


@pytest.fixture
def client(monkeypatch):
    import api.source_playlists as sp
    deezer = MagicMock()
    deezer.search_playlists.return_value = [_row("deezer", 1), _row("deezer", 2), _row("deezer", 3)]
    monkeypatch.setattr(sp, "_get_deezer_client", lambda: deezer)
    monkeypatch.setattr(sp, "_spotify_public_playlists", lambda q, limit: [_row("spotify", 1)])
    app = Flask(__name__)
    app.register_blueprint(sp.bp)
    c = app.test_client()
    c._deezer, c._sp = deezer, sp
    return c


def test_results_from_each_service_take_turns(client):
    body = client.get("/api/playlists/public-search?q=road+trip").get_json()
    assert [p["id"] for p in body["playlists"]] == ["deezer1", "spotify1", "deezer2", "deezer3"]
    assert body["sources"] == ["deezer", "spotify"]
    assert all(p["link"] for p in body["playlists"])
    client._deezer.search_playlists.assert_called_once_with("road trip", limit=15)


def test_one_service_can_be_asked_alone(client):
    body = client.get("/api/playlists/public-search?q=road+trip&source=spotify").get_json()
    assert [p["source"] for p in body["playlists"]] == ["spotify"]
    client._deezer.search_playlists.assert_not_called()


@pytest.mark.parametrize("q", ["", "a", "  "])
def test_too_little_to_search_asks_nobody(client, q):
    body = client.get(f"/api/playlists/public-search?q={q}").get_json()
    assert body == {"success": True, "playlists": [], "sources": []}
    client._deezer.search_playlists.assert_not_called()


def test_a_service_that_fails_is_left_out_not_an_error(client, monkeypatch):
    client._deezer.search_playlists.side_effect = RuntimeError("deezer is down")
    res = client.get("/api/playlists/public-search?q=road+trip")
    assert res.status_code == 200
    assert [p["source"] for p in res.get_json()["playlists"]] == ["spotify"]
    assert res.get_json()["sources"] == ["spotify"]


def test_a_result_with_no_link_cannot_be_added_so_is_not_offered(client):
    client._deezer.search_playlists.return_value = [dict(_row("deezer", 1), link=""), _row("deezer", 2)]
    body = client.get("/api/playlists/public-search?q=road+trip&source=deezer").get_json()
    assert [p["id"] for p in body["playlists"]] == ["deezer2"]


def test_the_limit_is_bounded(client):
    client.get("/api/playlists/public-search?q=road+trip&limit=9999")
    client._deezer.search_playlists.assert_called_once_with("road trip", limit=30)


def test_a_preview_shows_the_first_songs(client):
    client._deezer.get_playlist.return_value = {
        "track_count": 3, "tracks": [{"name": "Drive", "artists": ["Incubus"]}, {"name": "Go", "artists": []}]}
    body = client.get("/api/playlists/public-preview?source=deezer&id=908622995").get_json()
    assert body == {"success": True, "total": 3,
                    "songs": [{"title": "Drive", "artist": "Incubus"}, {"title": "Go", "artist": ""}]}


@pytest.mark.parametrize("query", ["source=deezer&id=", "source=deezer&id=../x", "source=tidal&id=123"])
def test_a_preview_refuses_junk(client, query):
    assert client.get(f"/api/playlists/public-preview?{query}").status_code == 400


def test_a_preview_that_fails_shows_nothing(client):
    client._deezer.get_playlist.side_effect = RuntimeError("down")
    body = client.get("/api/playlists/public-preview?source=deezer&id=1").get_json()
    assert body == {"success": True, "songs": [], "total": 0}
