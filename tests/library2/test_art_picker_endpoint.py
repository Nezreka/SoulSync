"""Flask-level tests for the lib2 cover-art picker (docs §49).

``GET .../art-options`` is read-only and purely lib2-native (no legacy
record needed — works for discography-only albums too); ``POST .../art``
pins the choice as a metadata override (so a later refresh won't clobber it,
see test_artwork_manual_override.py for that guarantee at the core-module
level) and writes it straight into the artwork cache.
"""

from __future__ import annotations

import sqlite3
from io import BytesIO

import pytest
from PIL import Image

flask = pytest.importorskip("flask")


class FakeDB:
    def __init__(self, path: str):
        self.database_path = path

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn


def _png_bytes(color=(1, 2, 3)) -> bytes:
    image = Image.new("RGB", (4, 3), color)
    output = BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


@pytest.fixture
def api(tmp_path):
    db_path = str(tmp_path / "lib2.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    from core.library2.schema import ensure_library_v2_schema
    ensure_library_v2_schema(conn)

    cur = conn.cursor()
    cur.execute("INSERT INTO lib2_artists(name) VALUES('Drake')")
    artist_id = cur.lastrowid
    cur.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, musicbrainz_id) "
        "VALUES(?, 'Views', 'rg-mbid-1')", (artist_id,),
    )
    album_id = cur.lastrowid
    conn.commit()
    conn.close()

    db = FakeDB(db_path)
    app = flask.Flask(__name__)
    from api.library_v2 import register_library_v2_routes
    register_library_v2_routes(
        app,
        get_database=lambda: db,
        config_get=lambda key, default=None: {"features.library_v2": True}.get(key, default),
        config_manager=None,
        profile_id_getter=lambda: 1,
    )
    yield app.test_client(), db, {"artist": artist_id, "album": album_id}


def test_art_options_returns_candidates(monkeypatch, api):
    client, _db, ids = api
    captured = {}

    def fake_gather(artist, album, metadata):
        captured.update({"artist": artist, "album": album, "metadata": metadata})
        return [{"url": "https://example.com/a.jpg", "source": "deezer"}]

    monkeypatch.setattr("core.metadata.art_lookup.gather_album_art_candidates", fake_gather)

    resp = client.get(f"/api/library/v2/albums/{ids['album']}/art-options")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["candidates"] == [{"url": "https://example.com/a.jpg", "source": "deezer"}]
    assert captured["artist"] == "Drake"
    assert captured["album"] == "Views"
    assert captured["metadata"] == {"musicbrainz_release_id": "rg-mbid-1"}


def test_art_options_404_for_missing_album(api):
    client, _db, _ids = api
    resp = client.get("/api/library/v2/albums/999999/art-options")
    assert resp.status_code == 404


def test_art_options_honors_title_override(monkeypatch, api):
    client, db, ids = api
    conn = db._get_connection()
    from core.library2.metadata_overrides import set_field_override
    set_field_override(conn, entity_type="release_group", entity_id=ids["album"],
                       field_name="title", value="Views (Corrected)")
    conn.commit()
    conn.close()

    captured = {}
    monkeypatch.setattr(
        "core.metadata.art_lookup.gather_album_art_candidates",
        lambda artist, album, metadata: captured.update({"album": album}) or [],
    )
    resp = client.get(f"/api/library/v2/albums/{ids['album']}/art-options")
    assert resp.status_code == 200
    assert captured["album"] == "Views (Corrected)"


def test_art_options_caches_within_ttl_until_refresh(monkeypatch, api):
    client, _db, ids = api
    calls = []
    monkeypatch.setattr(
        "core.metadata.art_lookup.gather_album_art_candidates",
        lambda artist, album, metadata: calls.append(1) or [{"url": "x", "source": "deezer"}],
    )
    first = client.get(f"/api/library/v2/albums/{ids['album']}/art-options")
    second = client.get(f"/api/library/v2/albums/{ids['album']}/art-options")
    assert first.status_code == 200 and second.status_code == 200
    assert len(calls) == 1
    assert second.get_json().get("cached") is True

    refreshed = client.get(f"/api/library/v2/albums/{ids['album']}/art-options?refresh=1")
    assert refreshed.status_code == 200
    assert len(calls) == 2


def test_apply_art_requires_url(api):
    client, _db, ids = api
    resp = client.post(f"/api/library/v2/albums/{ids['album']}/art", json={})
    assert resp.status_code == 400


def test_apply_art_400_for_unresolvable_url(monkeypatch, api):
    client, _db, ids = api
    monkeypatch.setattr("core.library.artist_image.download_image_bytes", lambda url: None)
    resp = client.post(
        f"/api/library/v2/albums/{ids['album']}/art", json={"url": "https://example.com/dead.jpg"},
    )
    assert resp.status_code == 400


def test_apply_art_404_for_missing_album(monkeypatch, api):
    client, _db, _ids = api
    monkeypatch.setattr(
        "core.library.artist_image.download_image_bytes", lambda url: _png_bytes(),
    )
    resp = client.post(
        "/api/library/v2/albums/999999/art", json={"url": "https://example.com/cover.jpg"},
    )
    assert resp.status_code == 404


def test_apply_art_pins_the_choice_and_serves_it_locally(monkeypatch, api):
    client, _db, ids = api
    chosen = _png_bytes((9, 8, 7))
    monkeypatch.setattr(
        "core.library.artist_image.download_image_bytes",
        lambda url: chosen if url == "https://example.com/cover.jpg" else None,
    )

    resp = client.post(
        f"/api/library/v2/albums/{ids['album']}/art", json={"url": "https://example.com/cover.jpg"},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["image_url"] == f"/api/library/v2/artwork/album/{ids['album']}"

    served = client.get(f"/api/library/v2/artwork/album/{ids['album']}")
    assert served.status_code == 200
    with Image.open(BytesIO(served.data)) as image:
        assert image.getpixel((0, 0)) == pytest.approx((9, 8, 7), abs=2)
