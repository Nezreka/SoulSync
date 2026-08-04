"""Tests for the library-wide Wanted Views (docs §64 I2 / §74):
``core.library2.wanted_views.list_missing``/``list_cutoff_unmet`` and the
``GET /api/library/v2/wanted`` endpoint.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from core.library2.wanted_views import list_cutoff_unmet, list_missing

flask = pytest.importorskip("flask")


def _make_conn(tmp_path, name="lib2.db") -> sqlite3.Connection:
    db_path = str(tmp_path / name)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    from core.library2.schema import ensure_library_v2_schema
    ensure_library_v2_schema(conn)
    return conn


def _artist(conn, name, sort_name=None):
    cur = conn.execute(
        "INSERT INTO lib2_artists(name, sort_name, monitored) VALUES(?,?,1)",
        (name, sort_name or name),
    )
    return cur.lastrowid


def _album(conn, artist_id, title):
    cur = conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, monitored) VALUES(?,?,1)",
        (artist_id, title),
    )
    album_id = cur.lastrowid
    conn.execute(
        "INSERT INTO lib2_album_artists(album_id, artist_id) VALUES(?,?)",
        (album_id, artist_id),
    )
    return album_id


def _track(conn, album_id, title, *, track_number=1, canonical_track_id=None):
    cur = conn.execute(
        "INSERT INTO lib2_tracks(album_id, title, track_number, monitored, canonical_track_id) "
        "VALUES(?,?,?,1,?)",
        (album_id, title, track_number, canonical_track_id),
    )
    return cur.lastrowid


def _want(conn, track_id, *, wanted=True, effective_profile_id=None, profile_id=1):
    conn.execute(
        "INSERT INTO lib2_wanted_tracks(profile_id, track_id, wanted, reason, "
        "effective_profile_id, projection_version) VALUES(?,?,?,?,?,2)",
        (profile_id, track_id, 1 if wanted else 0, "test", effective_profile_id),
    )


def _file(conn, track_id, *, format="mp3", bitrate=128, sample_rate=44100,
         bit_depth=None, file_state="active"):
    conn.execute(
        "INSERT INTO lib2_track_files(track_id, path, format, bitrate, sample_rate, "
        "bit_depth, is_primary, file_state) VALUES(?,?,?,?,?,?,1,?)",
        (track_id, f"/music/{track_id}.{format}", format, bitrate, sample_rate,
         bit_depth, file_state),
    )


def _profile(conn, *, name="Lossless", targets=None, upgrade_policy="acceptable",
            upgrade_cutoff_index=0):
    ranked = targets if targets is not None else [{"label": "FLAC", "format": "flac"}]
    cur = conn.execute(
        "INSERT INTO quality_profiles(name, ranked_targets, upgrade_policy, "
        "upgrade_cutoff_index) VALUES(?,?,?,?)",
        (name, json.dumps(ranked), upgrade_policy, upgrade_cutoff_index),
    )
    return cur.lastrowid


class TestListMissing:
    def test_wanted_track_with_no_file_is_missing(self, tmp_path):
        conn = _make_conn(tmp_path)
        artist = _artist(conn, "Drake")
        album = _album(conn, artist, "Views")
        track = _track(conn, album, "Hotline Bling")
        _want(conn, track)
        conn.commit()

        rows, total = list_missing(conn)

        assert total == 1
        assert rows[0]["track_id"] == track
        assert rows[0]["title"] == "Hotline Bling"
        assert rows[0]["artist"]["name"] == "Drake"
        assert rows[0]["album"]["title"] == "Views"

    def test_wanted_track_with_file_is_not_missing(self, tmp_path):
        conn = _make_conn(tmp_path)
        artist = _artist(conn, "Drake")
        album = _album(conn, artist, "Views")
        track = _track(conn, album, "One Dance")
        _want(conn, track)
        _file(conn, track)
        conn.commit()

        rows, total = list_missing(conn)

        assert total == 0
        assert rows == []

    def test_unwanted_track_with_no_file_is_not_missing(self, tmp_path):
        conn = _make_conn(tmp_path)
        artist = _artist(conn, "Drake")
        album = _album(conn, artist, "Views")
        track = _track(conn, album, "Not Wanted")
        _want(conn, track, wanted=False)
        conn.commit()

        rows, total = list_missing(conn)

        assert total == 0
        assert rows == []

    def test_consolidated_duplicate_is_excluded(self, tmp_path):
        """A track deliberately left fileless because its canonical duplicate
        partner owns the file must not nag the user to redownload it."""
        conn = _make_conn(tmp_path)
        artist = _artist(conn, "Drake")
        album = _album(conn, artist, "Views")
        canonical = _track(conn, album, "One Dance (Album)")
        _file(conn, canonical)
        single = _track(conn, album, "One Dance (Single)", canonical_track_id=canonical)
        _want(conn, canonical)
        _want(conn, single)
        conn.commit()

        rows, total = list_missing(conn)

        assert total == 0

    def test_missing_confirmed_file_state_still_counts_as_missing(self, tmp_path):
        conn = _make_conn(tmp_path)
        artist = _artist(conn, "Drake")
        album = _album(conn, artist, "Views")
        track = _track(conn, album, "Ghost File")
        _want(conn, track)
        _file(conn, track, file_state="missing_confirmed")
        conn.commit()

        rows, total = list_missing(conn)

        assert total == 1

    def test_search_filters_by_track_album_or_artist(self, tmp_path):
        conn = _make_conn(tmp_path)
        artist = _artist(conn, "Drake")
        album = _album(conn, artist, "Views")
        t1 = _track(conn, album, "Hotline Bling")
        t2 = _track(conn, album, "Controlla", track_number=2)
        _want(conn, t1)
        _want(conn, t2)
        conn.commit()

        rows, total = list_missing(conn, search="hotline")

        assert total == 1
        assert rows[0]["track_id"] == t1

    def test_pagination(self, tmp_path):
        conn = _make_conn(tmp_path)
        artist = _artist(conn, "Drake")
        album = _album(conn, artist, "Views")
        for i in range(5):
            track = _track(conn, album, f"Track {i}", track_number=i + 1)
            _want(conn, track)
        conn.commit()

        rows, total = list_missing(conn, page=1, limit=2)
        assert total == 5
        assert len(rows) == 2

        rows2, total2 = list_missing(conn, page=3, limit=2)
        assert total2 == 5
        assert len(rows2) == 1


class TestListCutoffUnmet:
    def test_file_below_profile_is_cutoff_unmet(self, tmp_path):
        conn = _make_conn(tmp_path)
        profile = _profile(conn, targets=[{"label": "FLAC", "format": "flac"}])
        artist = _artist(conn, "Drake")
        album = _album(conn, artist, "Views")
        track = _track(conn, album, "One Dance")
        _file(conn, track, format="mp3", bitrate=128)
        _want(conn, track, effective_profile_id=profile)
        conn.commit()

        rows, total = list_cutoff_unmet(conn)

        assert total == 1
        assert rows[0]["track_id"] == track
        assert rows[0]["meets_profile"] is False
        assert rows[0]["file"]["format"] == "mp3"

    def test_file_meeting_profile_is_not_cutoff_unmet(self, tmp_path):
        conn = _make_conn(tmp_path)
        profile = _profile(conn, targets=[{"label": "FLAC", "format": "flac"}])
        artist = _artist(conn, "Drake")
        album = _album(conn, artist, "Views")
        track = _track(conn, album, "One Dance")
        _file(conn, track, format="flac", bit_depth=16, sample_rate=44100)
        _want(conn, track, effective_profile_id=profile)
        conn.commit()

        rows, total = list_cutoff_unmet(conn)

        assert total == 0

    def test_missing_track_never_appears_in_cutoff_unmet(self, tmp_path):
        conn = _make_conn(tmp_path)
        profile = _profile(conn, targets=[{"label": "FLAC", "format": "flac"}])
        artist = _artist(conn, "Drake")
        album = _album(conn, artist, "Views")
        track = _track(conn, album, "Hotline Bling")
        _want(conn, track, effective_profile_id=profile)
        conn.commit()

        rows, total = list_cutoff_unmet(conn)

        assert total == 0

    def test_no_profile_never_flags_a_false_positive(self, tmp_path):
        """quality_eval's tri-state contract: unresolvable profile/quality
        must never masquerade as a confident 'below cutoff' finding."""
        conn = _make_conn(tmp_path)
        artist = _artist(conn, "Drake")
        album = _album(conn, artist, "Views")
        track = _track(conn, album, "One Dance")
        _file(conn, track, format="mp3", bitrate=128)
        _want(conn, track, effective_profile_id=None)
        conn.commit()

        rows, total = list_cutoff_unmet(conn)

        assert total == 0

    def test_until_cutoff_policy_uses_cutoff_index(self, tmp_path):
        targets = [
            {"label": "FLAC 24bit", "format": "flac", "bit_depth": 24},
            {"label": "FLAC 16bit", "format": "flac", "bit_depth": 16},
            {"label": "MP3", "format": "mp3"},
        ]
        conn = _make_conn(tmp_path)
        profile = _profile(conn, targets=targets, upgrade_policy="until_cutoff",
                          upgrade_cutoff_index=1)
        artist = _artist(conn, "Drake")
        album = _album(conn, artist, "Views")
        # Matches target index 2 (MP3) — below cutoff index 1, so still wanted.
        track = _track(conn, album, "One Dance")
        _file(conn, track, format="mp3", bitrate=320)
        _want(conn, track, effective_profile_id=profile)
        conn.commit()

        rows, total = list_cutoff_unmet(conn)

        assert total == 1

    def test_pagination_after_filtering(self, tmp_path):
        conn = _make_conn(tmp_path)
        profile = _profile(conn, targets=[{"label": "FLAC", "format": "flac"}])
        artist = _artist(conn, "Drake")
        album = _album(conn, artist, "Views")
        for i in range(3):
            track = _track(conn, album, f"Track {i}", track_number=i + 1)
            _file(conn, track, format="mp3")
            _want(conn, track, effective_profile_id=profile)
        conn.commit()

        rows, total = list_cutoff_unmet(conn, page=1, limit=2)
        assert total == 3
        assert len(rows) == 2


class FakeDB:
    def __init__(self, path: str):
        self.database_path = path

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn


def _build_api(tmp_path):
    conn = _make_conn(tmp_path)
    artist = _artist(conn, "Drake")
    album = _album(conn, artist, "Views")
    track = _track(conn, album, "Hotline Bling")
    _want(conn, track)
    conn.commit()
    conn.close()

    db = FakeDB(str(tmp_path / "lib2.db"))
    db.config = {"features.library_v2": True}
    app = flask.Flask(__name__)
    from api.library_v2 import register_library_v2_routes
    register_library_v2_routes(
        app,
        get_database=lambda: db,
        config_get=lambda key, default=None: db.config.get(key, default),
        config_manager=None,
        profile_id_getter=lambda: 1,
    )
    return app.test_client(), {"artist": artist, "album": album, "track": track}


@pytest.fixture
def api(tmp_path):
    yield _build_api(tmp_path)


class TestWantedEndpoint:
    def test_unknown_kind_is_400(self, api):
        client, _ids = api
        response = client.get("/api/library/v2/wanted?kind=bogus")
        assert response.status_code == 400

    def test_default_kind_is_missing(self, api):
        client, ids = api
        response = client.get("/api/library/v2/wanted")
        assert response.status_code == 200
        body = response.get_json()
        assert body["kind"] == "missing"
        assert body["pagination"]["total_count"] == 1
        assert body["tracks"][0]["track_id"] == ids["track"]

    def test_cutoff_unmet_kind(self, api):
        client, _ids = api
        response = client.get("/api/library/v2/wanted?kind=cutoff_unmet")
        assert response.status_code == 200
        body = response.get_json()
        assert body["kind"] == "cutoff_unmet"
        assert body["pagination"]["total_count"] == 0

    def test_invalid_page_is_400(self, api):
        client, _ids = api
        response = client.get("/api/library/v2/wanted?page=0")
        assert response.status_code == 400
