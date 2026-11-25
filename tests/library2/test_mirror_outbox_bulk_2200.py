"""#2200: a run of release withdrawals drains as one write.

Removing 57 watchlist artists queued one wishlist withdrawal per track, and
the drain gave each its own connection, delete commit and outbox commit:
most of the 70 s the request took. Withdrawals that follow each other in the
queue now go in one write, same deletes in the same order; anything else
still replays one row at a time.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing

from core.library2 import mirror_outbox as MO
from core.library2.schema import ensure_library_v2_schema
from tests.lib2_seed import row_conn


class _Recorder:
    """Records every legacy write the drain makes, in order."""

    def __init__(self, path, *, bulk_fails=False):
        self.path = path
        self.log = []
        self.bulk_calls = 0
        self.bulk_fails = bulk_fails

    def _get_connection(self):
        return row_conn(self.path)

    def add_to_wishlist(self, payload, **kw):
        self.log.append(("add", payload.get("id"), kw.get("profile_id")))
        return True

    def remove_from_wishlist(self, track_id, profile_id=1, raise_on_error=False):
        self.log.append(("remove_all", track_id, profile_id))
        return True

    def remove_release_from_wishlist(self, track_id, album_id=None, profile_id=1,
                                     raise_on_error=False):
        self.log.append(("remove", track_id, album_id, profile_id))
        return True

    def remove_releases_from_wishlist(self, releases, profile_id=1, raise_on_error=False):
        self.bulk_calls += 1
        if self.bulk_fails:
            raise sqlite3.OperationalError("database is locked")
        for track_id, album_id in releases:
            self.log.append(("remove", track_id, album_id, profile_id))
        return [True for _ in releases]


def _outbox(tmp_path):
    path = str(tmp_path / "lib2.db")
    with closing(row_conn(path)) as conn:
        ensure_library_v2_schema(conn)
        conn.commit()
    return path


def _queue(path, rows):
    ids = []
    with closing(row_conn(path)) as conn:
        for op, track, album, profile in rows:
            if op == "wishlist_add":
                data = {"payload": {"id": track}, "source_type": "album",
                        "key": f"{track}::{album}"}
            else:
                data = {"id": track, "album_id": album,
                        "key": f"{track}::{album}" if album else track}
            ids.append(conn.execute(
                "INSERT INTO lib2_mirror_outbox(op, payload, profile_id) VALUES(?,?,?)",
                (op, json.dumps(data), profile)).lastrowid)
        conn.commit()
    return ids


QUEUE = [
    ("wishlist_remove", "t1", "a1", 1),
    ("wishlist_remove", "t2", "a1", 1),
    ("wishlist_remove", "t3", "a2", 1),
    ("wishlist_add", "t9", "a9", 1),
    ("wishlist_remove", "t4", "a3", 1),        # alone between two others
    ("wishlist_remove", "t5", None, 1),        # no album: the recording-wide remove
    ("wishlist_remove", "t6", "a4", 2),        # another profile starts a new run
    ("wishlist_remove", "t7", "a4", 2),
]

IN_ORDER = [
    ("remove", "t1", "a1", 1),
    ("remove", "t2", "a1", 1),
    ("remove", "t3", "a2", 1),
    ("add", "t9", 1),
    ("remove", "t4", "a3", 1),
    ("remove_all", "t5", 1),
    ("remove", "t6", "a4", 2),
    ("remove", "t7", "a4", 2),
]


def _statuses(path, ids):
    with closing(row_conn(path)) as conn:
        return [conn.execute("SELECT status FROM lib2_mirror_outbox WHERE id=?", (i,)).fetchone()[0]
                for i in ids]


def test_a_run_of_withdrawals_is_one_write_in_queue_order(tmp_path):
    path = _outbox(tmp_path)
    ids = _queue(path, QUEUE)
    db = _Recorder(path)

    result = MO.drain(db, ids=ids)

    assert db.log == IN_ORDER
    assert db.bulk_calls == 2  # t1-t3 for profile 1, t6-t7 for profile 2
    assert result["done"] == len(QUEUE) and result["failed"] == 0
    assert set(_statuses(path, ids)) == {"done"}


def test_a_failed_bulk_write_replays_its_rows_one_by_one(tmp_path):
    path = _outbox(tmp_path)
    ids = _queue(path, QUEUE)
    db = _Recorder(path, bulk_fails=True)

    result = MO.drain(db, ids=ids)

    assert db.log == IN_ORDER
    assert result["done"] == len(QUEUE)
    assert set(_statuses(path, ids)) == {"done"}


def _wish(conn, key, album=None, profile=1):
    conn.execute(
        "INSERT INTO wishlist_tracks(spotify_track_id, spotify_data, profile_id) VALUES(?,?,?)",
        (key, json.dumps({"id": key.split("::")[0], "album": {"id": album} if album else {}}),
         profile))


def _seed_wishlist(db):
    with closing(db._get_connection()) as conn:
        _wish(conn, "t1::a1", "a1")
        _wish(conn, "t2", "a2")          # bare row naming this release
        _wish(conn, "t3", "other")       # bare row naming another album: kept
        _wish(conn, "t4")                # bare row naming no album
        _wish(conn, "t5::a5", "a5", profile=2)
        _wish(conn, "t6::a6", "a6")
        conn.commit()


def _rows(db):
    with closing(db._get_connection()) as conn:
        return sorted(tuple(r) for r in conn.execute(
            "SELECT profile_id, spotify_track_id FROM wishlist_tracks"))


def test_the_bulk_removal_matches_one_by_one(tmp_path):
    from database.music_database import MusicDatabase

    releases = [("t1", "a1"), ("t2", "a2"), ("t3", "a3"), ("t4", "a4"),
                ("t5", "a5"), ("missing", "a0"), ("", "a1")]

    one_by_one = MusicDatabase(str(tmp_path / "one.db"))
    _seed_wishlist(one_by_one)
    singles = [one_by_one.remove_release_from_wishlist(t, a, 1) for t, a in releases]

    bulk_db = MusicDatabase(str(tmp_path / "bulk.db"))
    _seed_wishlist(bulk_db)
    bulk = bulk_db.remove_releases_from_wishlist(releases, 1)

    assert bulk == singles
    assert singles.count(True) >= 3, singles
    assert _rows(bulk_db) == _rows(one_by_one)
    # the other album's bare row, the other profile's row and the release
    # nobody named are all that is left
    assert _rows(bulk_db) == [(1, "t3"), (1, "t6::a6"), (2, "t5::a5")]


def _release_tracks(conn):
    """a provider track on a provider album, a provider-less track on a
    provider-less album (the stable-id path), a MusicBrainz-only pair, and
    a track whose album holds only another provider's id."""
    def artist(name, **ids):
        return conn.execute(
            "INSERT INTO lib2_artists(name, spotify_id, external_ids) VALUES(?,?,?)",
            (name, ids.get("spotify"), json.dumps(ids.get("ext", {})))).lastrowid

    def album(artist_id, title, spotify=None, mb=None, ext=None):
        album_id = conn.execute(
            "INSERT INTO lib2_albums(primary_artist_id, title, spotify_id, musicbrainz_id, "
            "external_ids) VALUES(?,?,?,?,?)",
            (artist_id, title, spotify, mb, json.dumps(ext or {}))).lastrowid
        conn.execute("INSERT INTO lib2_album_artists(album_id, artist_id) VALUES(?,?)",
                     (album_id, artist_id))
        return album_id

    def track(album_id, artist_id, title, spotify=None, mb=None, ext=None, isrc=None):
        track_id = conn.execute(
            "INSERT INTO lib2_tracks(album_id, title, spotify_id, musicbrainz_id, external_ids, isrc) "
            "VALUES(?,?,?,?,?,?)",
            (album_id, title, spotify, mb, json.dumps(ext or {}), isrc)).lastrowid
        conn.execute("INSERT INTO lib2_track_artists(track_id, artist_id) VALUES(?,?)",
                     (track_id, artist_id))
        return track_id

    a = artist("Release Ids", spotify="sp-ar")
    return [
        track(album(a, "Provider", spotify="sp-al"), a, "One", spotify="sp-tr"),
        track(album(a, "Bare"), a, "Two"),
        track(album(a, "MB", mb="mb-al"), a, "Three", mb="mb-tr", isrc="USX"),
        track(album(a, "Deezer", ext={"deezer": "dz-al"}), a, "Four", spotify="sp-tr-4"),
    ]


def test_a_withdrawal_carries_the_same_ids_as_the_full_payload(imported_conn):
    from core.library2.wishlist_mirror import track_wishlist_payload, track_wishlist_release

    conn = imported_conn
    track_ids = _release_tracks(conn)
    conn.commit()

    for track_id in track_ids:
        payload = track_wishlist_payload(conn, track_id)
        assert track_wishlist_release(conn, track_id) == {
            "id": payload["id"], "album": {"id": payload["album"]["id"]}}
    assert track_wishlist_release(conn, 999_999) is None

    # and the queued withdrawal is the one the full payload made
    full = []
    for track_id in track_ids:
        payload = track_wishlist_payload(conn, track_id)
        from core.wishlist.identity import wishlist_key_from_payload
        full.append(MO._remove_payload(payload, wishlist_key_from_payload(payload) or payload["id"]))
    ids = MO.enqueue_tracks(conn, track_ids, False, profile_id=1)
    queued = [json.loads(conn.execute("SELECT payload FROM lib2_mirror_outbox WHERE id=?",
                                      (i,)).fetchone()[0]) for i in ids]
    assert queued == full
