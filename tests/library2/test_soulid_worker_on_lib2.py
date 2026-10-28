"""The SoulID worker mints kinded_v2 ids on Library v2 (upstream 9b21ba217).

The id is a deterministic hash every SoulSync node computes alike, so the
inputs must be the ones every other node feeds it:

- a track hashes its ALBUM's artist. Legacy joined ``tracks.artist_id``, the
  album artist the media-server scan filled; lib2 keeps a featured credit in
  ``lib2_track_artists``, and preferring it would re-key the track away from
  every other node;
- only owned releases are in the id space: legacy's catalogue was the owned
  library, so a provider-only discography row gets no id.

The cutover is upstream's: park every pre-v2 id in ``*_legacy``, clear the live
column, re-mint. These are upstream's tests (tests/test_soulid_v2.py) on the
native tables; a track always has an album here, so its "no album" case has
no counterpart.
"""

from __future__ import annotations

from contextlib import closing

import pytest

from core.library2.schema import ensure_library_v2_schema
from core.soulid_worker import SOULID_VERSION, SoulIDWorker, generate_soul_id
from tests.library2.legacy_usage import count_legacy_usage
from tests.lib2_seed import RowDb as _Db, row_conn


@pytest.fixture
def worker(tmp_path):
    path = str(tmp_path / "lib2.db")
    conn = row_conn(path)
    ensure_library_v2_schema(conn)
    conn.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS track_downloads (id INTEGER PRIMARY KEY, soul_id TEXT)")
    conn.commit()
    conn.close()
    instance = SoulIDWorker(_Db(path))
    instance.batch_size = 100
    return instance


def _exec(worker, sql, params=()):
    with closing(worker.db._get_connection()) as conn:
        cursor = conn.execute(sql, params)
        conn.commit()
        return cursor.lastrowid


def _rows(worker, sql, params=()):
    with closing(worker.db._get_connection()) as conn:
        return [tuple(r) for r in conn.execute(sql, params).fetchall()]


def _artist(worker, name, **cols):
    keys = ", ".join(["name", *cols])
    marks = ", ".join("?" * (1 + len(cols)))
    return _exec(worker, f"INSERT INTO lib2_artists({keys}) VALUES({marks})", (name, *cols.values()))


def _album(worker, artist_id, title, *, origin="library", **cols):
    keys = ", ".join(["primary_artist_id", "title", "album_type", "origin", *cols])
    marks = ", ".join("?" * (4 + len(cols)))
    return _exec(worker, f"INSERT INTO lib2_albums({keys}) VALUES({marks})",
                 (artist_id, title, "album", origin, *cols.values()))


def _track(worker, album_id, title, *, owned=True, **cols):
    keys = ", ".join(["album_id", "title", *cols])
    marks = ", ".join("?" * (2 + len(cols)))
    track_id = _exec(worker, f"INSERT INTO lib2_tracks({keys}) VALUES({marks})",
                     (album_id, title, *cols.values()))
    if owned:
        _exec(worker, "INSERT INTO lib2_track_files(track_id, path) VALUES(?, ?)",
              (track_id, f"/music/{track_id}.flac"))
    return track_id


def _owned_artist(worker, name, **cols):
    artist_id = _artist(worker, name, **cols)
    _track(worker, _album(worker, artist_id, f"{name} album"), f"{name} song")
    return artist_id


# ── minting ───────────────────────────────────────────────────────────────

def test_artists_mint_from_the_name_alone_in_a_batch(worker):
    ids = {name: _owned_artist(worker, name) for name in ("Rone", "Кино", "!!!", "   ")}
    assert worker._process_artists() == 4
    rows = {r[0]: (r[1], r[2]) for r in _rows(worker, "SELECT id, soul_id, soul_id_path FROM lib2_artists")}
    assert rows[ids["Rone"]] == (generate_soul_id("artist", "Rone"), "name")
    assert rows[ids["Кино"]] == (generate_soul_id("artist", "Кино"), "name")
    assert rows[ids["!!!"]] == (generate_soul_id("artist", "!!!"), "name")
    # only a name with no content at all is a placeholder, with no derivation
    assert rows[ids["   "]] == (f"soul_unnamed_{ids['   ']}", None)


def test_albums_and_tracks_mint_kinded_ids_from_the_album_artist(worker):
    artist = _artist(worker, "Michael Jackson")
    album = _album(worker, artist, "Thriller")
    track = _track(worker, album, "Thriller")
    guest = _artist(worker, "Vincent Price")
    _exec(worker, "INSERT INTO lib2_track_artists(track_id, artist_id, role, position) "
                  "VALUES(?, ?, 'featured', 1)", (track, guest))
    assert worker._process_albums() == 1
    assert worker._process_tracks() == 1
    (album_id,), = _rows(worker, "SELECT soul_id FROM lib2_albums WHERE id=?", (album,))
    (song_id, album_track_id), = _rows(worker, "SELECT soul_id, album_soul_id FROM lib2_tracks")
    assert album_id == generate_soul_id("album", "Michael Jackson", "Thriller")
    assert song_id == generate_soul_id("track", "Michael Jackson", "Thriller")
    assert album_track_id == generate_soul_id("album_track", "Michael Jackson", "Thriller", "Thriller")
    assert len({album_id, song_id, album_track_id}) == 3


def test_a_release_without_a_file_is_not_in_the_id_space(worker):
    artist = _artist(worker, "Drake")
    _track(worker, _album(worker, artist, "Scorpion", origin="discography"), "Nonstop", owned=False)
    assert (worker._process_artists(), worker._process_albums(), worker._process_tracks()) == (0, 0, 0)
    assert worker._count_pending() == 0


def test_an_existing_id_is_never_regenerated(worker):
    _owned_artist(worker, "Rone", soul_id="soul_kept")
    assert worker._process_artists() == 0
    assert _rows(worker, "SELECT soul_id FROM lib2_artists") == [("soul_kept",)]


def test_the_pending_count_matches_what_the_batches_would_take(worker):
    _owned_artist(worker, "Rone")
    assert worker._count_pending() == 3
    worker._cut_over = {"artists": True, "albums": True, "tracks": True}
    assert worker._mint_round() == 3
    assert worker._count_pending() == 0


# ── the cutover ───────────────────────────────────────────────────────────

def test_artist_cutover_parks_the_old_id_and_clears_for_remint(worker):
    _artist(worker, "Rone", soul_id="soul_old", soul_id_path="canonical")
    assert worker._migrate_artist_soul_ids() is True
    assert _rows(worker, "SELECT soul_id, soul_id_path, soul_id_legacy FROM lib2_artists") == [
        (None, None, "soul_old")]
    assert _rows(worker, "SELECT value FROM metadata WHERE key='soulid_artist_version'") == [
        (SOULID_VERSION,)]


def test_cutover_runs_once_and_never_overwrites_the_parked_id(worker):
    artist = _artist(worker, "Rone", soul_id="soul_old", soul_id_path="album")
    worker._migrate_artist_soul_ids()
    _exec(worker, "UPDATE lib2_artists SET soul_id='soul_new' WHERE id=?", (artist,))
    worker._migrate_artist_soul_ids()
    assert _rows(worker, "SELECT soul_id, soul_id_legacy FROM lib2_artists") == [("soul_new", "soul_old")]


def test_an_unflagged_rerun_still_never_overwrites_the_parked_id(worker):
    artist = _artist(worker, "Rone", soul_id="soul_old", soul_id_path="album")
    worker._migrate_artist_soul_ids()
    _exec(worker, "DELETE FROM metadata WHERE key='soulid_artist_version'")
    _exec(worker, "UPDATE lib2_artists SET soul_id='soul_other' WHERE id=?", (artist,))
    assert worker._migrate_artist_soul_ids() is True
    assert _rows(worker, "SELECT soul_id, soul_id_legacy FROM lib2_artists") == [(None, "soul_old")]


def test_placeholder_ids_are_reset_but_never_parked(worker):
    artist = _artist(worker, "!!!")
    _exec(worker, "UPDATE lib2_artists SET soul_id=? WHERE id=?", (f"soul_unnamed_{artist}", artist))
    assert worker._migrate_artist_soul_ids() is True
    assert _rows(worker, "SELECT soul_id, soul_id_legacy FROM lib2_artists") == [(None, None)]


def test_album_cutover_parks_and_clears(worker):
    _album(worker, _artist(worker, "Michael Jackson"), "Thriller", soul_id="soul_old")
    worker._migrate_album_soul_ids()
    assert _rows(worker, "SELECT soul_id, soul_id_legacy FROM lib2_albums") == [(None, "soul_old")]


def test_track_cutover_parks_both_ids_and_leaves_provenance_history(worker):
    album = _album(worker, _artist(worker, "Michael Jackson"), "Thriller")
    _track(worker, album, "Thriller", soul_id="soul_old", album_soul_id="soul_old_album")
    _exec(worker, "INSERT INTO track_downloads(soul_id) VALUES('soul_old')")
    assert worker._migrate_track_soul_ids() is True
    assert _rows(worker, "SELECT soul_id, soul_id_legacy, album_soul_id, album_soul_id_legacy "
                         "FROM lib2_tracks") == [(None, "soul_old", None, "soul_old_album")]
    assert _rows(worker, "SELECT soul_id FROM track_downloads") == [("soul_old",)]


def test_track_cutover_with_only_the_album_id_set(worker):
    album = _album(worker, _artist(worker, "Michael Jackson"), "Thriller")
    _track(worker, album, "Thriller", album_soul_id="soul_old_album")
    assert worker._migrate_track_soul_ids() is True
    assert _rows(worker, "SELECT soul_id, soul_id_legacy, album_soul_id, album_soul_id_legacy "
                         "FROM lib2_tracks") == [(None, None, None, "soul_old_album")]


def test_cutover_refuses_to_reset_when_the_legacy_column_is_missing(worker):
    _artist(worker, "Rone", soul_id="soul_old", soul_id_path="canonical")
    _exec(worker, "ALTER TABLE lib2_artists DROP COLUMN soul_id_legacy")
    assert worker._migrate_artist_soul_ids() is False
    assert _rows(worker, "SELECT soul_id, soul_id_path FROM lib2_artists") == [("soul_old", "canonical")]
    assert _rows(worker, "SELECT key FROM metadata") == []


def _old_tracks(worker, n):
    album = _album(worker, _artist(worker, "Rone"), "Mirapolis")
    return [_track(worker, album, f"T{i}", soul_id=f"soul_old{i}", album_soul_id=f"soul_alb{i}")
            for i in range(1, n + 1)]


def test_track_cutover_commits_in_chunks_and_lets_writers_in(worker, monkeypatch):
    import sqlite3
    import core.soulid_worker as sw

    _old_tracks(worker, 5)
    worker.cutover_chunk = 2
    writes = []

    def other_writer(_seconds):
        # no busy wait: it only gets in when the cutover released the lock
        with closing(sqlite3.connect(worker.db.path, timeout=0)) as conn:
            conn.execute("INSERT INTO metadata(key, value) VALUES(?, 'x')", (f"w{len(writes)}",))
            conn.commit()
            writes.append(1)

    monkeypatch.setattr(sw.time, "sleep", other_writer)
    assert worker._migrate_track_soul_ids() is True
    assert len(writes) == 3
    rows = _rows(worker, "SELECT soul_id, soul_id_legacy, album_soul_id, album_soul_id_legacy FROM lib2_tracks")
    assert all(r[0] is None and r[2] is None for r in rows)
    assert sorted(r[1] for r in rows) == sorted(f"soul_old{i}" for i in range(1, 6))
    assert sorted(r[3] for r in rows) == sorted(f"soul_alb{i}" for i in range(1, 6))


def test_an_interrupted_cutover_resumes_without_losing_an_id(worker, monkeypatch):
    import core.soulid_worker as sw

    tracks = _old_tracks(worker, 5)
    worker.cutover_chunk = 2
    monkeypatch.setattr(sw.time, "sleep", lambda _s: setattr(worker, "should_stop", True))
    assert worker._migrate_track_soul_ids() is False
    assert _rows(worker, "SELECT key FROM metadata WHERE key='soulid_track_version'") == []
    done = _rows(worker, "SELECT soul_id_legacy FROM lib2_tracks WHERE soul_id IS NULL")
    assert done and all(legacy for (legacy,) in done)
    worker.should_stop = False
    monkeypatch.setattr(sw.time, "sleep", lambda _s: None)
    assert worker._migrate_track_soul_ids() is True
    assert dict(_rows(worker, "SELECT id, soul_id_legacy FROM lib2_tracks")) == {
        tid: f"soul_old{i}" for i, tid in enumerate(tracks, 1)}


def test_a_table_whose_cutover_failed_mints_nothing(worker):
    _owned_artist(worker, "Rone")
    worker._cut_over = {"artists": False, "albums": False, "tracks": False}
    assert worker._mint_round() == 0
    worker._cut_over["artists"] = True
    assert worker._mint_round() == 1


def test_the_worker_holds_no_legacy_sql():
    import inspect

    import core.soulid_worker as sw

    usage = count_legacy_usage(inspect.getsource(sw))
    assert (usage.reads, usage.writes) == (0, 0)
