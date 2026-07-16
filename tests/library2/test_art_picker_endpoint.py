"""Flask-level tests for the lib2 cover-art picker (docs §49).

``GET .../art-options`` is read-only and purely lib2-native (no legacy
record needed — works for discography-only albums too); ``POST .../art``
pins the choice as a metadata override (so a later refresh won't clobber it,
see test_artwork_manual_override.py for that guarantee at the core-module
level) and writes it straight into the artwork cache.
"""

from __future__ import annotations

import sqlite3
import time
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
    # A2: cache-busted with the cache file's own mtime, so a fresh pick gets a
    # URL the browser hasn't already cached under the old, immutable response.
    assert body["image_url"].startswith(f"/api/library/v2/artwork/album/{ids['album']}?v=")

    served = client.get(body["image_url"])
    assert served.status_code == 200
    with Image.open(BytesIO(served.data)) as image:
        assert image.getpixel((0, 0)) == pytest.approx((9, 8, 7), abs=2)


def test_apply_art_triggers_a_background_cover_embed_retag(monkeypatch, api):
    """A1: applying a pick must also (re-)embed the cover into the album's
    existing files — not just update the DB/cache — otherwise the picked
    cover never reaches files that already have every text tag correct."""
    client, db, ids = api
    conn = db._get_connection()
    conn.execute(
        "INSERT INTO lib2_tracks(album_id, title, track_number) VALUES(?, 'One Dance', 1)",
        (ids["album"],),
    )
    track_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO lib2_track_files(track_id, path) VALUES(?, '/nope/track.flac')",
        (track_id,),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(
        "core.library.artist_image.download_image_bytes",
        lambda url: _png_bytes((9, 8, 7)),
    )

    resp = client.post(
        f"/api/library/v2/albums/{ids['album']}/art", json={"url": "https://example.com/cover.jpg"},
    )
    assert resp.status_code == 200

    deadline = time.time() + 5
    state = None
    while time.time() < deadline:
        state = client.get("/api/library/v2/jobs/status").get_json()
        if not state.get("running"):
            break
        time.sleep(0.02)
    assert state is not None
    assert state["kind"] == "retag"
    assert state["result"]["failed"] == 1  # file doesn't really exist on disk


# ---------------------------------------------------------------------------
# Artist photo picker (deep-dive A9) — same pattern, no cover-embed retag.
# ---------------------------------------------------------------------------


def test_artist_art_options_returns_candidates(monkeypatch, api):
    client, _db, ids = api
    captured = {}

    def fake_gather(artist_name):
        captured["artist"] = artist_name
        return [{"url": "https://example.com/a.jpg", "source": "spotify"}]

    monkeypatch.setattr("core.metadata.art_lookup.gather_artist_image_candidates", fake_gather)

    resp = client.get(f"/api/library/v2/artists/{ids['artist']}/art-options")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["candidates"] == [{"url": "https://example.com/a.jpg", "source": "spotify"}]
    assert captured["artist"] == "Drake"


def test_artist_art_options_404_for_missing_artist(api):
    client, _db, _ids = api
    resp = client.get("/api/library/v2/artists/999999/art-options")
    assert resp.status_code == 404


def test_artist_art_options_honors_name_override(monkeypatch, api):
    client, db, ids = api
    conn = db._get_connection()
    from core.library2.metadata_overrides import set_field_override
    set_field_override(conn, entity_type="artist", entity_id=ids["artist"],
                       field_name="name", value="Drake (Corrected)")
    conn.commit()
    conn.close()

    captured = {}
    monkeypatch.setattr(
        "core.metadata.art_lookup.gather_artist_image_candidates",
        lambda artist_name: captured.update({"artist": artist_name}) or [],
    )
    resp = client.get(f"/api/library/v2/artists/{ids['artist']}/art-options")
    assert resp.status_code == 200
    assert captured["artist"] == "Drake (Corrected)"


def test_artist_art_options_caches_within_ttl_until_refresh(monkeypatch, api):
    client, _db, ids = api
    calls = []
    monkeypatch.setattr(
        "core.metadata.art_lookup.gather_artist_image_candidates",
        lambda artist_name: calls.append(1) or [{"url": "x", "source": "spotify"}],
    )
    first = client.get(f"/api/library/v2/artists/{ids['artist']}/art-options")
    second = client.get(f"/api/library/v2/artists/{ids['artist']}/art-options")
    assert first.status_code == 200 and second.status_code == 200
    assert len(calls) == 1
    assert second.get_json().get("cached") is True

    refreshed = client.get(f"/api/library/v2/artists/{ids['artist']}/art-options?refresh=1")
    assert refreshed.status_code == 200
    assert len(calls) == 2


def test_artist_and_album_art_options_caches_do_not_collide_on_a_shared_id(monkeypatch, api):
    """Both caches are keyed by a raw int id. ``lib2_artists`` and
    ``lib2_albums`` are separate tables with independent autoincrement
    counters, so the fixture's artist and album naturally share id 1 — a
    cache hit for one must never leak into the other."""
    client, _db, ids = api
    assert ids["artist"] == ids["album"], "fixture no longer produces a shared id — test needs updating"

    monkeypatch.setattr(
        "core.metadata.art_lookup.gather_album_art_candidates",
        lambda artist, album, metadata: [{"url": "album-pick", "source": "deezer"}],
    )
    monkeypatch.setattr(
        "core.metadata.art_lookup.gather_artist_image_candidates",
        lambda artist_name: [{"url": "artist-pick", "source": "spotify"}],
    )

    album_resp = client.get(f"/api/library/v2/albums/{ids['album']}/art-options")
    artist_resp = client.get(f"/api/library/v2/artists/{ids['artist']}/art-options")
    assert album_resp.get_json()["candidates"] == [{"url": "album-pick", "source": "deezer"}]
    assert artist_resp.get_json()["candidates"] == [{"url": "artist-pick", "source": "spotify"}]


def test_apply_artist_art_requires_url(api):
    client, _db, ids = api
    resp = client.post(f"/api/library/v2/artists/{ids['artist']}/art", json={})
    assert resp.status_code == 400


def test_apply_artist_art_400_for_unresolvable_url(monkeypatch, api):
    client, _db, ids = api
    monkeypatch.setattr("core.library.artist_image.download_image_bytes", lambda url: None)
    resp = client.post(
        f"/api/library/v2/artists/{ids['artist']}/art", json={"url": "https://example.com/dead.jpg"},
    )
    assert resp.status_code == 400


def test_apply_artist_art_404_for_missing_artist(monkeypatch, api):
    client, _db, _ids = api
    monkeypatch.setattr(
        "core.library.artist_image.download_image_bytes", lambda url: _png_bytes(),
    )
    resp = client.post(
        "/api/library/v2/artists/999999/art", json={"url": "https://example.com/photo.jpg"},
    )
    assert resp.status_code == 404


def test_apply_artist_art_pins_the_choice_and_serves_it_locally(monkeypatch, api):
    client, _db, ids = api
    chosen = _png_bytes((5, 6, 7))
    monkeypatch.setattr(
        "core.library.artist_image.download_image_bytes",
        lambda url: chosen if url == "https://example.com/photo.jpg" else None,
    )

    resp = client.post(
        f"/api/library/v2/artists/{ids['artist']}/art", json={"url": "https://example.com/photo.jpg"},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["image_url"].startswith(f"/api/library/v2/artwork/artist/{ids['artist']}?v=")

    served = client.get(body["image_url"])
    assert served.status_code == 200
    with Image.open(BytesIO(served.data)) as image:
        assert image.getpixel((0, 0)) == pytest.approx((5, 6, 7), abs=2)
