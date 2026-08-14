"""The SoulID worker generates its ids on Library v2 (docs §32.3.1 stage 2).

Unlike the fourteen provider workers this one does not *look up* an id, it
*computes* one — a deterministic hash of normalized names that every SoulSync
node arrives at independently (that is what makes it Hydrabase's key). Two
consequences shape the port:

- there is no provider attempt to record and no ``worker_queue`` batch to take;
  the pending set is simply "rows whose ``soul_id`` is still empty";
- the hash inputs must not change, or the same recording gets a different id
  here than on every other node. The trap is the track's artist: legacy hashed
  ``tracks.artist_id``, which is the *album* artist — the featured credit lives
  in the separate ``track_artist`` column and never reached the hash. lib2 keeps
  that credit in ``lib2_track_artists``, so preferring it (as the file-subject
  query rightly does elsewhere) would silently re-key the whole library.
"""

from __future__ import annotations

import sqlite3

import pytest

from core.library2.schema import ensure_library_v2_schema
from core.soulid_worker import SoulIDWorker, generate_soul_id
from tests.library2.legacy_usage import count_legacy_usage


class _Db:
    def __init__(self, path):
        self.path = path

    def _get_connection(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn


@pytest.fixture
def worker(tmp_path):
    path = str(tmp_path / "lib2.db")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    ensure_library_v2_schema(conn)
    conn.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)")
    artist = conn.execute(
        "INSERT INTO lib2_artists(name, sort_name) VALUES('Drake','Drake')").lastrowid
    album = conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id,title,album_type,origin) "
        "VALUES(?,'Views','album','library')", (artist,)).lastrowid
    track = conn.execute(
        "INSERT INTO lib2_tracks(album_id,title) VALUES(?,'One Dance')",
        (album,)).lastrowid
    conn.execute("INSERT INTO lib2_track_files(track_id,path) VALUES(?, '/music/one.flac')",
                 (track,))
    # The featured credit legacy kept out of the hash.
    guest = conn.execute(
        "INSERT INTO lib2_artists(name) VALUES('Wizkid')").lastrowid
    conn.execute(
        "INSERT INTO lib2_track_artists(track_id,artist_id,role,position) "
        "VALUES(?,?,'featured',1)", (track, guest))
    conn.commit()
    conn.close()

    instance = SoulIDWorker(_Db(path))
    instance.batch_size = 100
    instance.artist_sleep = 0
    return instance


def _row(worker, table, entity_id):
    conn = worker.db._get_connection()
    try:
        return conn.execute(f"SELECT * FROM {table} WHERE id=?", (entity_id,)).fetchone()
    finally:
        conn.close()


def _exec(worker, sql, params=()):
    conn = worker.db._get_connection()
    try:
        cursor = conn.execute(sql, params)
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


class TestAlbums:
    def test_the_id_is_the_hash_of_album_artist_and_title(self, worker):
        assert worker._process_albums() == 1
        assert _row(worker, "lib2_albums", 1)["soul_id"] == generate_soul_id("Drake", "Views")

    def test_an_existing_id_is_never_regenerated(self, worker):
        _exec(worker, "UPDATE lib2_albums SET soul_id='soul_kept' WHERE id=1")
        assert worker._process_albums() == 0
        assert _row(worker, "lib2_albums", 1)["soul_id"] == "soul_kept"

    def test_a_provider_only_release_is_not_part_of_the_id_space(self, worker):
        """``origin='discography'`` rows have no legacy counterpart and no owned
        file; legacy's soul-id space was the owned library and stays that."""
        _exec(worker,
              "INSERT INTO lib2_albums(primary_artist_id,title,album_type,origin) "
              "VALUES(1,'Scorpion','album','discography')")
        worker._process_albums()
        assert _row(worker, "lib2_albums", 2)["soul_id"] is None


class TestTracks:
    def test_the_song_id_hashes_the_album_artist_not_the_featured_credit(self, worker):
        assert worker._process_tracks() == 1
        row = _row(worker, "lib2_tracks", 1)
        assert row["soul_id"] == generate_soul_id("Drake", "One Dance")
        assert row["soul_id"] != generate_soul_id("Drake feat. Wizkid", "One Dance")

    def test_the_release_specific_id_adds_the_album_title(self, worker):
        worker._process_tracks()
        assert _row(worker, "lib2_tracks", 1)["album_soul_id"] == generate_soul_id(
            "Drake", "Views", "One Dance")

    def test_an_existing_id_is_never_regenerated(self, worker):
        _exec(worker, "UPDATE lib2_tracks SET soul_id='soul_kept' WHERE id=1")
        assert worker._process_tracks() == 0
        assert _row(worker, "lib2_tracks", 1)["soul_id"] == "soul_kept"


class TestArtists:
    def test_a_verified_canonical_id_is_what_gets_hashed(self, worker, monkeypatch):
        seen = {}

        def _lookup(name, verify_track):
            seen["args"] = (name, verify_track)
            return 4242

        monkeypatch.setattr(worker, "_lookup_canonical_artist_id", _lookup)
        assert worker._process_next_artist() == 1
        assert seen["args"] == ("Drake", "One Dance")
        assert _row(worker, "lib2_artists", 1)["soul_id"] == generate_soul_id("Drake", "4242")

    def test_without_a_canonical_id_the_first_album_title_disambiguates(self, worker, monkeypatch):
        monkeypatch.setattr(worker, "_lookup_canonical_artist_id", lambda *a: None)
        worker._process_next_artist()
        assert _row(worker, "lib2_artists", 1)["soul_id"] == generate_soul_id("Drake", "Views")

    def test_an_artist_with_nothing_owned_is_hashed_from_the_name_alone(self, worker, monkeypatch):
        monkeypatch.setattr(worker, "_lookup_canonical_artist_id", lambda *a: None)
        # Artist 2 ("Wizkid") is credited on a track but owns no album.
        _exec(worker, "UPDATE lib2_artists SET soul_id='soul_done' WHERE id=1")
        worker._process_next_artist()
        assert _row(worker, "lib2_artists", 2)["soul_id"] == generate_soul_id("Wizkid")

    def test_an_existing_id_is_never_regenerated(self, worker, monkeypatch):
        monkeypatch.setattr(worker, "_lookup_canonical_artist_id", lambda *a: None)
        _exec(worker, "UPDATE lib2_artists SET soul_id='soul_kept'")
        assert worker._process_next_artist() == 0
        assert _row(worker, "lib2_artists", 1)["soul_id"] == "soul_kept"


class TestPending:
    def test_the_count_matches_what_the_batches_would_take(self, worker):
        assert worker._count_pending() == 4  # two artists, one album, one track
        worker._process_albums()
        worker._process_tracks()
        assert worker._count_pending() == 2


class TestTheAlgorithmVersionReset:
    def test_a_new_version_clears_the_native_ids_and_records_itself(self, worker):
        _exec(worker, "UPDATE lib2_artists SET soul_id='soul_old' WHERE id=1")
        worker._migrate_artist_soul_ids()
        assert _row(worker, "lib2_artists", 1)["soul_id"] is None
        conn = worker.db._get_connection()
        try:
            value = conn.execute(
                "SELECT value FROM metadata WHERE key='soulid_artist_version'").fetchone()[0]
        finally:
            conn.close()
        assert value == "debut_year_api_v2"

    def test_it_does_not_run_twice(self, worker):
        worker._migrate_artist_soul_ids()
        _exec(worker, "UPDATE lib2_artists SET soul_id='soul_fresh' WHERE id=1")
        worker._migrate_artist_soul_ids()
        assert _row(worker, "lib2_artists", 1)["soul_id"] == "soul_fresh"


def test_the_worker_holds_no_legacy_sql():
    source = (__import__("pathlib").Path(__file__).resolve().parents[2]
              / "core" / "soulid_worker.py").read_text(encoding="utf-8")
    assert count_legacy_usage(source) == count_legacy_usage("")
