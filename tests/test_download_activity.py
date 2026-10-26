"""Download activity endpoint — /api/v1/library/download-activity.

Feeds the MetaSync node's download-based SIN signals (download_grab /
download_churn, MetaSync DESIGN-DOWNLOAD-SIGNALS.md). The contract pinned
here:

* per-album aggregates keyed by MusicBrainz RELEASE id — counts and
  timestamps only, never titles or file paths;
* only completed downloads count, joined to the library through the
  tracks' file paths;
* albums without a MusicBrainz release id, downloads whose files left
  the library, and albums with no downloads never appear;
* ``since`` is an instant comparison (ISO-8601 in, UTC-normalized),
  not a text comparison.
"""

from __future__ import annotations

import hashlib
from urllib.parse import urlencode

import pytest
from flask import Flask

from api import create_api_blueprint, limiter
from database.music_database import MusicDatabase

RAW_KEY = "sk_download_activity_test_key"
REL_A = "11111111-1111-4111-8111-111111111111"
REL_B = "22222222-2222-4222-8222-222222222222"


class _Config:
    def __init__(self):
        self._keys = [{
            "id": "k1",
            "label": "test",
            "key_hash": hashlib.sha256(RAW_KEY.encode()).hexdigest(),
            "key_prefix": RAW_KEY[:11],
        }]

    def get(self, key, default=None):
        return self._keys if key == "api_keys" else default

    def set(self, *a, **kw):
        return None

    def save_config(self, *a, **kw):
        return None


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "download_activity.db"))


@pytest.fixture
def client(db, monkeypatch):
    import database.music_database as mdb
    monkeypatch.setattr(mdb, "get_database", lambda: db)
    import api.library as lib
    monkeypatch.setattr(lib, "get_database", lambda: db)

    app = Flask(__name__)
    app.config["RATELIMIT_ENABLED"] = False
    limiter.init_app(app)
    app.register_blueprint(create_api_blueprint(), url_prefix="/api/v1")
    app.soulsync = {"config_manager": _Config()}
    return app.test_client()


def _seed(db):
    conn = db._get_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO artists (id, name) VALUES (1, 'The Testers')")
    cur.execute(
        "INSERT INTO albums (id, artist_id, title, musicbrainz_release_id) "
        "VALUES (10, 1, 'Album A', ?)", (REL_A,))
    cur.execute(
        "INSERT INTO albums (id, artist_id, title, musicbrainz_release_id) "
        "VALUES (11, 1, 'Album NoMBID', NULL)")
    cur.execute(
        "INSERT INTO albums (id, artist_id, title, musicbrainz_release_id) "
        "VALUES (12, 1, 'Album Quiet', ?)", (REL_B,))
    cur.execute(
        "INSERT INTO tracks (id, album_id, artist_id, title, file_path) "
        "VALUES (100, 10, 1, 'A1', '/music/a1.flac')")
    cur.execute(
        "INSERT INTO tracks (id, album_id, artist_id, title, file_path) "
        "VALUES (101, 10, 1, 'A2', '/music/a2.flac')")
    cur.execute(
        "INSERT INTO tracks (id, album_id, artist_id, title, file_path) "
        "VALUES (102, 11, 1, 'B1', '/music/b1.flac')")

    def td(path, status, when):
        cur.execute(
            "INSERT INTO track_downloads (track_title, track_artist, "
            "track_album, file_path, status, created_at, source_service) "
            "VALUES ('T', 'The Testers', 'X', ?, ?, ?, 'slskd')",
            (path, status, when))

    td("/music/a1.flac", "completed", "2026-09-01 10:00:00")
    td("/music/a2.flac", "completed", "2026-10-21 09:30:00")
    td("/music/a1.flac", "failed", "2026-10-21 11:00:00")
    td("/music/b1.flac", "completed", "2026-10-20 08:00:00")
    td("/music/gone.flac", "completed", "2026-10-20 08:00:00")
    conn.commit()


def _get(client, **params):
    qs = urlencode({k: v for k, v in params.items() if v not in (None, "")})
    return client.get(f"/api/v1/library/download-activity?{qs}",
                      headers={"Authorization": f"Bearer {RAW_KEY}"})


def test_aggregates_completed_downloads_per_release(db, client):
    _seed(db)
    resp = _get(client)
    assert resp.status_code == 200
    albums = resp.get_json()["data"]["albums"]
    # Album NoMBID (no release id), Album Quiet (no downloads) and the
    # dangling /music/gone.flac download are all absent; the failed row
    # for a1 does not count.
    assert len(albums) == 1
    row = albums[0]
    assert row["musicbrainz_release_id"] == REL_A
    assert row["completed_downloads"] == 2
    assert row["completed_since"] == 2  # no since -> everything counts
    assert row["first_downloaded_at"] == "2026-09-01 10:00:00"
    assert row["last_downloaded_at"] == "2026-10-21 09:30:00"


def test_response_carries_only_the_contract_fields(db, client):
    _seed(db)
    row = _get(client).get_json()["data"]["albums"][0]
    assert set(row.keys()) == {
        "musicbrainz_release_id",
        "completed_downloads",
        "completed_since",
        "first_downloaded_at",
        "last_downloaded_at",
    }


def test_since_counts_only_downloads_at_or_after_it(db, client):
    _seed(db)
    albums = _get(client, since="2026-10-19T00:00:00").get_json()["data"]["albums"]
    assert albums[0]["completed_downloads"] == 2
    assert albums[0]["completed_since"] == 1
    # The boundary instant itself is included (same lesson as L2-010:
    # compared as instants after UTC normalization, never as raw text).
    albums = _get(client, since="2026-10-21T09:30:00").get_json()["data"]["albums"]
    assert albums[0]["completed_since"] == 1
    albums = _get(client, since="2026-10-21T09:30:01").get_json()["data"]["albums"]
    assert albums[0]["completed_since"] == 0


def test_malformed_since_is_a_400(db, client):
    _seed(db)
    assert _get(client, since="last-tuesday").status_code == 400


def test_missing_api_key_is_a_401(client):
    assert client.get("/api/v1/library/download-activity").status_code == 401


def test_empty_library_is_an_empty_list(db, client):
    resp = _get(client)
    assert resp.status_code == 200
    assert resp.get_json()["data"]["albums"] == []


def test_until_closes_the_window(db, client):
    _seed(db)
    # The node reports a closed week: [since, until). a2 completed at
    # 2026-10-21 09:30:00, so a window ending exactly then excludes it.
    albums = _get(client, since="2026-10-19T00:00:00",
                  until="2026-10-21T09:30:00").get_json()["data"]["albums"]
    assert albums[0]["completed_since"] == 0
    albums = _get(client, since="2026-10-19T00:00:00",
                  until="2026-10-21T09:30:01").get_json()["data"]["albums"]
    assert albums[0]["completed_since"] == 1
    # completed_downloads stays the all-time total either way.
    assert albums[0]["completed_downloads"] == 2


def test_malformed_until_is_a_400(db, client):
    _seed(db)
    assert _get(client, until="next-friday").status_code == 400
