"""#1761: the add's photo lookup goes to the provider that owns the id.

the artist page now sends ``source`` for a source artist, so an itunes id
lands in the itunes column. the image fetch still went to deezer whenever
deezer was the fallback source, looking the itunes id up as a deezer id and
caching some other artist's photo on the row.
"""

from __future__ import annotations

import pytest

pytest.importorskip("flask")

import web_server  # noqa: E402
from database.music_database import MusicDatabase  # noqa: E402


class _Resp:
    ok = True
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


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
    monkeypatch.setattr(web_server, "spotify_client", None)

    import core.deezer_throttle as deezer_throttle
    monkeypatch.setattr(deezer_throttle, "wait_for_slot", lambda *a, **k: None)

    urls = []

    def fake_get(url, *a, **k):
        urls.append(url)
        if "api.deezer.com" in url:
            return _Resp({"picture_xl": "https://deezer.example/photo.jpg"})
        return _Resp({"results": [{"artworkUrl100": "https://itunes.example/100x100.jpg"}]})

    monkeypatch.setattr(artist_watchlist.requests, "get", fake_get)
    web_server.app.config["TESTING"] = True
    with web_server.app.test_client() as test_client:
        yield test_client, db, urls


def _row(db, column, value):
    conn = db._get_connection()
    try:
        return conn.execute(
            f"SELECT itunes_artist_id, deezer_artist_id, image_url FROM watchlist_artists WHERE {column} = ?",
            (value,),
        ).fetchone()
    finally:
        conn.close()


def test_itunes_id_with_its_source_gets_an_itunes_photo(client):
    test_client, db, urls = client

    response = test_client.post("/api/watchlist/add", json={
        "artist_id": "262836961", "artist_name": "Adele", "source": "itunes",
    })

    assert response.get_json()["success"] is True
    assert not any("api.deezer.com" in u for u in urls), urls
    row = _row(db, "itunes_artist_id", "262836961")
    assert row["deezer_artist_id"] in (None, "")
    assert row["image_url"] == "https://itunes.example/600x600.jpg"


def test_bare_numeric_id_still_uses_the_deezer_fallback(client):
    test_client, db, urls = client

    response = test_client.post("/api/watchlist/add", json={
        "artist_id": "75798", "artist_name": "Adele",
    })

    assert response.get_json()["success"] is True
    assert any(u.endswith("/artist/75798") for u in urls), urls
    assert _row(db, "deezer_artist_id", "75798")["image_url"] == "https://deezer.example/photo.jpg"
