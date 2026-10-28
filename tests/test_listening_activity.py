"""Listening activity endpoint — /api/v1/library/listening-activity.

Feeds the MetaSync node's opt-in listen_album SIN signal (MetaSync
DESIGN-LISTEN-SIGNALS.md). The contract pinned here:

* per-album aggregates keyed by MusicBrainz RELEASE id — plays, distinct
  tracks and distinct days only, never titles, play times or profiles;
* only the SHARED listening pile is read: a profile with its own pile
  (its own listenbrainz / last.fm / media-server account) is never
  included, so the admin opting in never shares a housemate's history;
* plays outside ``[since, until)``, plays that matched no library track,
  and albums without a MusicBrainz release id never appear;
* both bounds are required, and the window is capped — the endpoint
  answers "this week", never "everything ever played".
"""

from __future__ import annotations

import hashlib
from urllib.parse import urlencode

import pytest
from flask import Flask

from api import create_api_blueprint, limiter
from database.music_database import MusicDatabase

RAW_KEY = "sk_listening_activity_test_key"
REL_A = "11111111-1111-4111-8111-111111111111"
REL_B = "22222222-2222-4222-8222-222222222222"
SHARED = 1
HOUSEMATE = 7

WEEK_START = "2026-10-19T00:00:00"
WEEK_END = "2026-10-26T00:00:00"


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
    return MusicDatabase(str(tmp_path / "listening_activity.db"))


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
        "VALUES (12, 1, 'Album B', ?)", (REL_B,))
    for track_id, album_id in ((100, 10), (101, 10), (102, 10), (103, 11), (104, 12)):
        cur.execute(
            "INSERT INTO tracks (id, album_id, artist_id, title, file_path) "
            "VALUES (?, ?, 1, ?, ?)",
            (track_id, album_id, f"T{track_id}", f"/music/{track_id}.flac"))

    def play(db_track_id, played_at, profile_id=SHARED):
        cur.execute(
            "INSERT INTO listening_history (track_id, title, artist, album, "
            "played_at, server_source, db_track_id, profile_id) "
            "VALUES (?, 'T', 'The Testers', 'X', ?, 'plex', ?, ?)",
            (f"src-{db_track_id}-{played_at}", played_at, db_track_id, profile_id))

    # Album A, shared pile: 4 plays of 3 tracks over 2 days inside the week.
    play(100, "2026-10-19 08:00:00")
    play(100, "2026-10-20 21:00:00")
    play(101, "2026-10-20 21:04:00")
    play(102, "2026-10-20 21:09:00")
    # Outside the window on both sides: never counted.
    play(101, "2026-10-18 23:59:59")
    play(102, "2026-10-26 00:00:00")
    # Album B is played only by a housemate with their own pile.
    play(104, "2026-10-21 10:00:00", profile_id=HOUSEMATE)
    play(104, "2026-10-22 10:00:00", profile_id=HOUSEMATE)
    # No release id, and a play that matched no library track.
    play(103, "2026-10-21 10:00:00")
    play(None, "2026-10-21 11:00:00")
    conn.commit()


def _get(client, **params):
    qs = urlencode({k: v for k, v in params.items() if v not in (None, "")})
    return client.get(f"/api/v1/library/listening-activity?{qs}",
                      headers={"Authorization": f"Bearer {RAW_KEY}"})


def test_aggregates_shared_plays_per_release_inside_the_window(db, client):
    _seed(db)
    resp = _get(client, since=WEEK_START, until=WEEK_END)
    assert resp.status_code == 200
    albums = resp.get_json()["data"]["albums"]
    assert albums == [{
        "musicbrainz_release_id": REL_A,
        "plays": 4,
        "distinct_tracks": 3,
        "distinct_days": 2,
    }]


def test_a_housemates_own_pile_is_never_included(db, client):
    _seed(db)
    albums = _get(client, since=WEEK_START, until=WEEK_END).get_json()["data"]["albums"]
    assert REL_B not in {a["musicbrainz_release_id"] for a in albums}


def test_response_carries_only_the_contract_fields(db, client):
    _seed(db)
    row = _get(client, since=WEEK_START, until=WEEK_END).get_json()["data"]["albums"][0]
    assert set(row.keys()) == {
        "musicbrainz_release_id", "plays", "distinct_tracks", "distinct_days",
    }


def test_window_bounds_are_instants_with_an_offset(db, client):
    _seed(db)
    # The same week named in UTC+02:00: still exactly the four plays.
    albums = _get(
        client, since="2026-10-19T02:00:00+02:00", until="2026-10-26T02:00:00+02:00",
    ).get_json()["data"]["albums"]
    assert albums[0]["plays"] == 4


def test_both_bounds_are_required_and_the_window_is_capped(db, client):
    _seed(db)
    assert _get(client, since=WEEK_START).status_code == 400
    assert _get(client, until=WEEK_END).status_code == 400
    assert _get(client, since="last-tuesday", until=WEEK_END).status_code == 400
    assert _get(client, since=WEEK_END, until=WEEK_START).status_code == 400
    assert _get(client, since="2026-01-01T00:00:00", until=WEEK_END).status_code == 400


def test_missing_api_key_is_a_401(client):
    assert client.get(
        f"/api/v1/library/listening-activity?since={WEEK_START}&until={WEEK_END}"
    ).status_code == 401


def test_no_plays_is_an_empty_list(db, client):
    resp = _get(client, since=WEEK_START, until=WEEK_END)
    assert resp.status_code == 200
    assert resp.get_json()["data"]["albums"] == []
