"""a v1 wishlist delete reads that track's rows, not the whole wishlist.

library v2 added a reverse sync to the delete route, and it found the rows to
demonitor by reading and parsing every wishlist row of the profile on each
call. a script clearing a few hundred tracks parsed the wishlist a few
hundred times. it now asks the database for just that key.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from flask import Blueprint, Flask

from database.music_database import MusicDatabase


@pytest.fixture
def db(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    with db._get_connection() as conn:
        for key, profile in [("abc", 1), ("abc::alb1", 1), ("abc::alb2", 1), ("abcd", 1),
                             ("abc_x::alb", 1), ("ab", 1), ("abc", 2)]:
            conn.execute(
                "INSERT INTO wishlist_tracks (spotify_track_id, spotify_data, profile_id) "
                "VALUES (?, ?, ?)", (key, json.dumps({"id": key, "name": key}), profile))
        conn.commit()
    return db


def _keys(rows):
    return sorted(r["spotify_track_id"] for r in rows)


def test_a_bare_id_reads_itself_and_its_album_rows(db):
    assert _keys(db.get_wishlist_tracks(profile_id=1, track_key="abc")) == [
        "abc", "abc::alb1", "abc::alb2"]


def test_a_composite_key_reads_only_itself(db):
    assert _keys(db.get_wishlist_tracks(profile_id=1, track_key="abc::alb2")) == ["abc::alb2"]


def test_another_profiles_row_never_comes_back(db):
    assert _keys(db.get_wishlist_tracks(profile_id=2, track_key="abc")) == ["abc"]


def test_no_key_still_reads_everything(db):
    assert len(db.get_wishlist_tracks(profile_id=1)) == 6


def test_the_delete_route_asks_for_one_track(monkeypatch):
    from api import wishlist as wishlist_mod

    calls = []

    class FakeDb:
        def get_wishlist_tracks(self, **kwargs):
            calls.append(kwargs)
            return [{"spotify_track_id": "abc"}]

        def remove_from_wishlist(self, track_id, profile_id=1):
            return True

    monkeypatch.setattr("database.music_database.get_database", lambda: FakeDb())
    monkeypatch.setattr("core.library2.monitor_sync.sync_wishlist_removal",
                        lambda *a, **k: None)
    app = Flask(__name__)
    bp = Blueprint("v1", __name__, url_prefix="/api/v1")
    with patch.object(wishlist_mod, "require_api_key", lambda f: f):
        wishlist_mod.register_routes(bp)
    app.register_blueprint(bp)

    resp = app.test_client().delete("/api/v1/wishlist/abc")
    assert resp.status_code == 200
    assert calls and all(c.get("track_key") == "abc" for c in calls)


# ---- composite keys match exactly (LIKE ignored case and read _ as a wildcard) ----

def _insert(db, *keys):
    with db._get_connection() as conn:
        for key in keys:
            conn.execute(
                "INSERT INTO wishlist_tracks (spotify_track_id, spotify_data, profile_id) "
                "VALUES (?, ?, 1)", (key, json.dumps({"id": key, "name": key})))
        conn.commit()


def _all_keys(db):
    with db._get_connection() as conn:
        return sorted(r[0] for r in conn.execute("SELECT spotify_track_id FROM wishlist_tracks"))


def test_removing_a_track_leaves_another_case_of_its_id(tmp_path):
    db = MusicDatabase(str(tmp_path / "c.db"))
    _insert(db, "4uLU6h::alb", "4ULU6H::alb")
    assert db.remove_from_wishlist("4uLU6h") is True
    assert _all_keys(db) == ["4ULU6H::alb"]


def test_an_underscore_in_an_id_is_not_a_wildcard(tmp_path):
    db = MusicDatabase(str(tmp_path / "u.db"))
    _insert(db, "yt_abc::alb", "ytXabc::alb")
    assert db.remove_from_wishlist("yt_abc") is True
    assert _all_keys(db) == ["ytXabc::alb"]


def test_a_spotify_data_read_takes_the_right_case(tmp_path):
    db = MusicDatabase(str(tmp_path / "s.db"))
    _insert(db, "AbC::alb")
    assert db.get_wishlist_spotify_data("abc") in ({}, None)
    assert db.get_wishlist_spotify_data("AbC").get("id") == "AbC::alb"
