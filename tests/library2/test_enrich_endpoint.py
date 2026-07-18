"""Flask-level tests for native Library-v2 provider enrichment (P3)."""

from __future__ import annotations

import sqlite3

import pytest

flask = pytest.importorskip("flask")


class FakeDB:
    def __init__(self, path: str):
        self.database_path = path

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn


@pytest.fixture
def api(tmp_path, monkeypatch):
    db_path = str(tmp_path / "lib2.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    from core.library2.schema import ensure_library_v2_schema

    ensure_library_v2_schema(conn)
    cur = conn.cursor()
    # Keep one rollback-window back-reference deliberately: P3 must ignore it.
    cur.execute(
        "INSERT INTO lib2_artists(name, legacy_artist_id) VALUES('Drake', 501)"
    )
    artist_id = cur.lastrowid
    cur.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title) VALUES(?, 'Views')",
        (artist_id,),
    )
    album_id = cur.lastrowid
    cur.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title) "
        "VALUES(?, 'Discography Only')",
        (artist_id,),
    )
    native_album_id = cur.lastrowid
    cur.execute(
        "INSERT INTO lib2_tracks(album_id, title, legacy_track_id) "
        "VALUES(?, 'One Dance', 701)",
        (album_id,),
    )
    track_id = cur.lastrowid
    conn.commit()
    conn.close()

    db = FakeDB(db_path)
    calls = []

    def fake_native_enrich(native_conn, entity_type, entity_id, service):
        table = {
            "artist": "lib2_artists",
            "album": "lib2_albums",
            "track": "lib2_tracks",
        }[entity_type]
        if native_conn.execute(
            f"SELECT 1 FROM {table} WHERE id=?", (entity_id,)
        ).fetchone() is None:
            raise LookupError(f"Library v2 {entity_type} {entity_id} not found")
        calls.append({
            "service": service,
            "entity_type": entity_type,
            "entity_id": entity_id,
        })
        return {
            "success": True,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "requested_source": service,
            "source": service,
            "provider_id": f"{service}-{entity_id}",
        }

    import core.library2.native_enrich as native_enrich

    monkeypatch.setattr(
        native_enrich,
        "enrich_native_entity_for_service",
        fake_native_enrich,
    )

    app = flask.Flask(__name__)
    from api.library_v2 import register_library_v2_routes

    register_library_v2_routes(
        app,
        get_database=lambda: db,
        config_get=lambda key, default=None: {
            "features.library_v2": True,
        }.get(key, default),
        config_manager=None,
        profile_id_getter=lambda: 1,
    )
    ids = {
        "artist": artist_id,
        "album": album_id,
        "native_album": native_album_id,
        "track": track_id,
    }
    yield app.test_client(), db, ids, calls


@pytest.mark.parametrize(
    ("entity", "id_key", "service", "singular"),
    [
        ("artists", "artist", "lastfm", "artist"),
        ("albums", "album", "deezer", "album"),
        ("tracks", "track", "musicbrainz", "track"),
    ],
)
def test_enrich_delegates_to_native_entity_id(
    api, entity, id_key, service, singular
):
    client, _db, ids, calls = api
    response = client.post(
        f"/api/library/v2/{entity}/{ids[id_key]}/enrich",
        json={"service": service},
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["success"] is True
    assert body["source"] == service
    assert body["resynced"] is True
    assert calls == [{
        "service": service,
        "entity_type": singular,
        "entity_id": ids[id_key],
    }]


def test_enrich_native_discography_entity_needs_no_legacy_record(api):
    client, _db, ids, calls = api
    response = client.post(
        f"/api/library/v2/albums/{ids['native_album']}/enrich",
        json={"service": "itunes"},
    )

    assert response.status_code == 200
    assert response.get_json()["success"] is True
    assert calls[0]["entity_id"] == ids["native_album"]


def test_enrich_rejects_service_unsupported_for_entity_type(api):
    client, _db, ids, calls = api
    response = client.post(
        f"/api/library/v2/albums/{ids['album']}/enrich",
        json={"service": "genius"},
    )

    assert response.status_code == 400
    assert "does not support album" in response.get_json()["error"]
    assert calls == []


def test_enrich_rejects_unknown_service(api):
    client, _db, ids, calls = api
    response = client.post(
        f"/api/library/v2/artists/{ids['artist']}/enrich",
        json={"service": "not-a-real-service"},
    )

    assert response.status_code == 400
    assert calls == []


def test_enrich_requires_service(api):
    client, _db, ids, calls = api
    response = client.post(
        f"/api/library/v2/artists/{ids['artist']}/enrich", json={}
    )

    assert response.status_code == 400
    assert "service is required" in response.get_json()["error"]
    assert calls == []


def test_enrich_rejects_unsupported_entity(api):
    client, _db, ids, calls = api
    response = client.post(
        f"/api/library/v2/playlists/{ids['artist']}/enrich",
        json={"service": "lastfm"},
    )

    assert response.status_code == 400
    assert calls == []


def test_enrich_returns_404_for_missing_native_entity(api):
    client, _db, _ids, calls = api
    response = client.post(
        "/api/library/v2/artists/999999/enrich",
        json={"service": "lastfm"},
    )

    assert response.status_code == 404
    assert "not found" in response.get_json()["error"]
    assert calls == []


def test_reconcile_unmapped_artists_endpoint_starts_a_job(api):
    client, _db, _ids, _calls = api
    response = client.post(
        "/api/library/v2/maintenance/reconcile-unmapped-artists"
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["success"] is True
    assert body["started"] is True
    assert body["job_id"]
