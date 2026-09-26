"""A Watchlist add monitors the artist in Library v2 right away.

Upstream's artist page shows the artists the catalogue does not hold, and its
"Add to Watchlist" button is how such an artist gets monitored. Before this the
add only wrote the Watchlist row, so the artist had no Library v2 row until a
download created one and the artist page kept treating it as unknown.
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
    from api import artist_watchlist
    monkeypatch.setattr(artist_watchlist, "get_database", lambda *a, **k: db)
    monkeypatch.setattr(artist_watchlist, "get_current_profile_id", lambda: 1)
    monkeypatch.setattr(artist_watchlist, "_get_metadata_fallback_source", lambda: "deezer")

    def _no_network(*_a, **_k):
        raise OSError("no network in tests")

    # The add also fetches an artist image; that must not leave the machine.
    monkeypatch.setattr(artist_watchlist.requests, "get", _no_network)
    monkeypatch.setattr(web_server, "spotify_client", None)
    web_server.app.config["TESTING"] = True
    with web_server.app.test_client() as test_client:
        yield test_client, db


def _lib2_artist(db, name):
    conn = db._get_connection()
    try:
        row = conn.execute(
            "SELECT id, monitored, spotify_id, external_ids FROM lib2_artists WHERE name = ?",
            (name,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def test_a_spotify_artist_is_monitored_under_its_id(client):
    test_client, db = client

    response = test_client.post("/api/watchlist/add", json={
        "artist_id": "4tZwfgrHOc3mvqYlEYSvVi", "artist_name": "Daft Punk"})

    assert response.status_code == 200, response.get_json()
    artist = _lib2_artist(db, "Daft Punk")
    assert artist is not None
    assert artist["monitored"] == 1
    assert artist["spotify_id"] == "4tZwfgrHOc3mvqYlEYSvVi"


def test_a_guessed_numeric_source_is_not_written_as_an_id(client):
    """2481 could be Deezer, iTunes or Discogs; the endpoint only guesses."""
    test_client, db = client

    response = test_client.post("/api/watchlist/add", json={
        "artist_id": "2481", "artist_name": "Aphex Twin"})

    assert response.status_code == 200, response.get_json()
    artist = _lib2_artist(db, "Aphex Twin")
    assert artist is not None
    assert artist["monitored"] == 1
    assert artist["spotify_id"] is None
    assert artist["external_ids"] in (None, "{}")


def test_a_named_source_is_trusted(client):
    test_client, db = client

    response = test_client.post("/api/watchlist/add", json={
        "artist_id": "2481", "artist_name": "Aphex Twin", "source": "deezer"})

    assert response.status_code == 200, response.get_json()
    artist = _lib2_artist(db, "Aphex Twin")
    assert artist is not None
    assert '"deezer": "2481"' in (artist["external_ids"] or "")


def test_another_profile_only_gets_its_watchlist_row(client, monkeypatch):
    test_client, db = client
    from api import artist_watchlist
    other = db.create_profile("Guest")
    assert other and other != 1
    monkeypatch.setattr(artist_watchlist, "get_current_profile_id", lambda: other)

    test_client.post("/api/watchlist/add", json={
        "artist_id": "4tZwfgrHOc3mvqYlEYSvVi", "artist_name": "Daft Punk"})

    assert _lib2_artist(db, "Daft Punk") is None
