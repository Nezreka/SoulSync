"""TheHomeGuy: the Navidrome hourly update kept missing things; a full refresh
added "a TON", and the last one was over three months old.

The incremental walk reads Navidrome's "newest" albums and stops after 25 known
ones in a row. "newest" is the album's created date, taken from file times, so
an import whose files kept old dates sorts deep, and tracks added to an album
Navidrome already had never move it. The census reads every album's song count
(500 a request) and re-reads the artist of any album we don't fully hold.
"""

from __future__ import annotations

import json

import pytest

from core.database_update_worker import DatabaseUpdateWorker
from core.library.navidrome_census import albums_to_sync
from core.navidrome_client import NavidromeClient
from database.music_database import MusicDatabase


def _a(aid, n, artist="r1"):
    return {"id": aid, "songCount": n, "artistId": artist, "name": aid}


# ── the rule ────────────────────────────────────────────────────────────────


def test_unknown_and_short_albums_are_picked_complete_ones_are_not():
    picked, record = albums_to_sync([_a("new", 10), _a("short", 12), _a("full", 9)],
                                    {"short": 10, "full": 9}, {})
    assert [a["id"] for a in picked] == ["new", "short"]
    assert record == {"new": 10, "short": 12}


def test_an_album_still_short_at_the_same_count_waits_for_the_count_to_move():
    """a song SoulSync skips on purpose would otherwise re-read the artist
    every hour, which is more Navidrome traffic, not less."""
    picked, record = albums_to_sync([_a("x", 12)], {"x": 11}, {"x": 12})
    assert picked == [] and record == {"x": 12}
    picked, _ = albums_to_sync([_a("x", 13)], {"x": 11}, {"x": 12})
    assert [a["id"] for a in picked] == ["x"]


def test_a_caught_up_album_leaves_the_record():
    _picked, record = albums_to_sync([_a("x", 12)], {"x": 12}, {"x": 12})
    assert record == {}


def test_a_drifted_library_is_read_a_slice_at_a_time():
    picked, record = albums_to_sync([_a(f"a{i}", 5) for i in range(500)], {}, {}, cap=200)
    assert len(picked) == 200 and len(record) == 200


# ── the client ──────────────────────────────────────────────────────────────


def _nd(pages):
    c = NavidromeClient.__new__(NavidromeClient)
    NavidromeClient.__init__(c)
    c.base_url, c.username, c.password = "http://nd", "app", "pw"
    c.ensure_connection = lambda: True
    calls = []

    def req(endpoint, params=None, as_user=None, timeout=None):
        calls.append(params)
        page = pages.pop(0)
        return page if page is None else {"albumList2": {"album": page}}

    c._make_request = req
    return c, calls


def test_the_census_pages_through_every_album():
    c, calls = _nd([[_a(f"a{i}", 1) for i in range(500)], [_a("last", 1)]])
    assert len(c.get_album_census()) == 501
    assert [p["offset"] for p in calls] == [0, 500]
    assert calls[0]["type"] == "alphabeticalByName"


def test_a_failed_page_is_no_census_not_a_smaller_library():
    c, _calls = _nd([[_a(f"a{i}", 1) for i in range(500)], None])
    assert c.get_album_census() is None


# ── the worker ──────────────────────────────────────────────────────────────


class _Client:
    def __init__(self, census):
        self.census = census
        self.artist_reads = []

    def get_album_census(self):
        return self.census

    def get_artist_by_id(self, aid):
        self.artist_reads.append(aid)
        return type("Artist", (), {"ratingKey": aid, "title": aid})()


@pytest.fixture()
def db(tmp_path, monkeypatch):
    database = MusicDatabase(str(tmp_path / "music.db"))
    monkeypatch.setattr("core.database_update_worker.get_database", lambda path=None: database)
    with database._get_connection() as conn:
        conn.execute("INSERT INTO artists (id, name, server_source) VALUES ('r1', 'Known', 'navidrome')")
        conn.execute("INSERT INTO albums (id, title, artist_id, server_source) VALUES ('held', 'Held', 'r1', 'navidrome')")
        for i in range(3):
            conn.execute("INSERT INTO tracks (id, album_id, artist_id, title, server_source) "
                         "VALUES (?, 'held', 'r1', 'T', 'navidrome')", (f"t{i}",))
        conn.commit()
    return database


def _worker(client, path):
    w = DatabaseUpdateWorker(media_client=client, database_path=path, server_type="navidrome",
                             force_sequential=True)
    w.database = MusicDatabase(path)    # run() binds it before the incremental pass
    return w


def test_the_hourly_update_reads_the_artists_the_newest_list_hides(db, tmp_path):
    client = _Client([_a("held", 3, "r1"), _a("added_to", 5, "r2"), _a("old_dates", 8, "r3")])
    held = db.album_track_counts("navidrome")
    assert held == {"held": 3}
    w = _worker(client, str(tmp_path / "music.db"))
    artists = w._navidrome_census_artists(set())
    assert sorted(a.ratingKey for a in artists) == ["r2", "r3"]
    # an hour later, if r3's album still came up short at the same count, it isn't read again
    assert json.loads(db.get_metadata("navidrome_census_tried")) == {"added_to": 5, "old_dates": 8}
    client.artist_reads.clear()
    assert w._navidrome_census_artists(set()) == []
    assert client.artist_reads == []


def test_an_artist_the_newest_pass_already_queued_is_not_read_twice(db, tmp_path):
    client = _Client([_a("added_to", 5, "r2")])
    w = _worker(client, str(tmp_path / "music.db"))
    assert w._navidrome_census_artists({"r2"}) == []


def test_an_unreadable_census_changes_nothing(db, tmp_path):
    db.set_metadata("navidrome_census_tried", json.dumps({"x": 4}))
    w = _worker(_Client(None), str(tmp_path / "music.db"))
    assert w._navidrome_census_artists(set()) == []
    assert json.loads(db.get_metadata("navidrome_census_tried")) == {"x": 4}


def test_the_incremental_update_runs_the_census_even_with_nothing_newest(db, tmp_path):
    client = _Client([_a("old_dates", 8, "r3")])
    w = _worker(client, str(tmp_path / "music.db"))
    w._get_recent_albums_for_server = lambda: []
    assert [a.ratingKey for a in w._get_artists_for_navidrome_incremental_update()] == ["r3"]
