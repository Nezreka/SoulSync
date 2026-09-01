"""#1216 — an incremental scan could permanently lose an album it had just written.

The scan inserts an album row, then its tracks, then sweeps orphans. Between
the first two steps a real album legitimately has zero tracks, so if the
server's track list for that one album comes back short (no exception, no
timeout - just an incomplete answer) the sweep deletes the row the same run
created.

That loss is permanent, not transient: with the row gone the media server no
longer reports the album as recently added, so the next incremental scan never
rediscovers it. The reporter watched the same "Added artist ... for processing"
line repeat every scan forever with nothing landing.

The rule: a row this run touched is not an orphan yet. Only rows left trackless
by an EARLIER run are.
"""

from __future__ import annotations

import pytest

from database.music_database import MusicDatabase


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(database_path=str(tmp_path / "music.db"))


def _artist(db, artist_id, name):
    with db._get_connection() as conn:
        conn.execute("INSERT INTO artists (id, name) VALUES (?, ?)", (artist_id, name))
        conn.commit()


def _album(db, album_id, artist_id, title):
    with db._get_connection() as conn:
        conn.execute("INSERT INTO albums (id, artist_id, title) VALUES (?, ?, ?)",
                     (album_id, artist_id, title))
        conn.commit()


def _track(db, track_id, album_id, artist_id, title):
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO tracks (id, album_id, artist_id, title) VALUES (?, ?, ?, ?)",
            (track_id, album_id, artist_id, title))
        conn.commit()


def _ids(db, table):
    with db._get_connection() as conn:
        return {str(r[0]) for r in conn.execute(f"SELECT id FROM {table}")}


def test_a_trackless_album_from_an_earlier_run_is_still_removed(db):
    """The sweep's actual job, unchanged."""
    _artist(db, "a1", "Ghost Artist")
    _album(db, "al1", "a1", "Ghost Album")

    result = db.cleanup_orphaned_records()

    assert result['orphaned_albums_removed'] == 1
    assert result['orphaned_artists_removed'] == 1
    assert _ids(db, "albums") == set()
    assert _ids(db, "artists") == set()


def test_an_album_this_run_wrote_survives_its_own_sweep(db):
    """#1216: the album is trackless only because its tracks have not landed
    yet. Deleting it here is what made the loss permanent."""
    _artist(db, "a1", "Various Artists")
    _album(db, "al1", "a1", "Compilation")

    result = db.cleanup_orphaned_records(
        protected_artist_ids={"a1"}, protected_album_ids={"al1"})

    assert result['orphaned_albums_removed'] == 0
    assert result['albums_protected'] == 1
    assert result['artists_protected'] == 1
    assert _ids(db, "albums") == {"al1"}, "the run's own album was deleted — #1216 is back"
    assert _ids(db, "artists") == {"a1"}


def test_protection_is_per_row_not_all_or_nothing(db):
    """A run that touched one artist must not shield last week's orphans."""
    _artist(db, "fresh", "This Run")
    _album(db, "fresh-al", "fresh", "Just Inserted")
    _artist(db, "stale", "Long Gone")
    _album(db, "stale-al", "stale", "Left Trackless Ages Ago")

    result = db.cleanup_orphaned_records(
        protected_artist_ids={"fresh"}, protected_album_ids={"fresh-al"})

    assert result['orphaned_albums_removed'] == 1
    assert result['orphaned_artists_removed'] == 1
    assert _ids(db, "albums") == {"fresh-al"}
    assert _ids(db, "artists") == {"fresh"}


def test_an_album_with_tracks_is_never_touched(db):
    _artist(db, "a1", "Real Artist")
    _album(db, "al1", "a1", "Real Album")
    _track(db, "t1", "al1", "a1", "Real Track")

    result = db.cleanup_orphaned_records()

    assert result['orphaned_albums_removed'] == 0
    assert _ids(db, "albums") == {"al1"}


def test_ids_are_compared_as_strings(db):
    """Server ids arrive as ints from Plex and strings from Jellyfin/Navidrome;
    the ledger must match either way."""
    _artist(db, "77", "Numeric Id")
    _album(db, "88", "77", "Numeric Album")

    result = db.cleanup_orphaned_records(
        protected_artist_ids={77}, protected_album_ids={88})

    assert result['orphaned_albums_removed'] == 0
    assert _ids(db, "albums") == {"88"}


def test_a_sweep_larger_than_one_chunk_still_clears(db):
    """The delete is chunked around SQLite's bound-variable cap; 600 orphans
    must not silently leave 100 behind."""
    _artist(db, "a1", "Prolific")
    for n in range(600):
        _album(db, f"al{n}", "a1", f"Album {n}")

    result = db.cleanup_orphaned_records(protected_artist_ids={"a1"})

    assert result['orphaned_albums_removed'] == 600
    assert _ids(db, "albums") == set()


def test_nothing_to_do_reports_zeroes(db):
    assert db.cleanup_orphaned_records() == {
        'orphaned_artists_removed': 0, 'orphaned_albums_removed': 0,
        'artists_protected': 0, 'albums_protected': 0}


# ── the worker's side of it: the ledger the sweep is handed ──────────────────

class _FakeDb:
    def __init__(self):
        self.tracks = []

    def insert_or_update_media_artist(self, artist, server_source=None):
        return True

    def insert_or_update_media_album(self, album, artist_id, server_source=None):
        return True

    def insert_or_update_media_track(self, track, album_id, artist_id, server_source=None):
        self.tracks.append(str(track.ratingKey))
        return 'inserted'

    def track_exists_by_server(self, track_id, server_source):
        return False


class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _worker(tmp_path):
    from core.database_update_worker import DatabaseUpdateWorker
    w = DatabaseUpdateWorker(None, database_path=str(tmp_path / "m.db"))
    w.database = _FakeDb()
    return w


def test_the_worker_records_what_it_wrote(tmp_path):
    """Everything the sweep is asked to spare has to actually get into the
    ledger, or the protection is empty and #1216 is unchanged."""
    track = _Obj(ratingKey="t1", title="Song")
    album = _Obj(ratingKey="al1", title="Album", tracks=lambda: [track])
    artist = _Obj(ratingKey="a1", title="Artist", albums=lambda: [album])

    w = _worker(tmp_path)
    ok, _details, albums, tracks = w._process_artist_with_content(artist)

    assert ok and albums == 1 and tracks == 1
    assert w._touched_artist_ids == {"a1"}
    assert w._touched_album_ids == {"al1"}


def test_an_album_whose_tracks_come_back_empty_is_still_recorded(tmp_path):
    """THE case from the report: the track list comes back short. The album was
    written, so the sweep must be told about it even though it has no tracks."""
    album = _Obj(ratingKey="al1", title="Album", tracks=lambda: [])
    artist = _Obj(ratingKey="a1", title="Artist", albums=lambda: [album])

    w = _worker(tmp_path)
    w._process_artist_with_content(artist)

    assert w._touched_album_ids == {"al1"}, (
        "the album with the incomplete track list is exactly the one that gets "
        "deleted without protection")


def test_both_cleanup_call_sites_pass_the_ledger(tmp_path):
    """A supplement to the behaviour tests above, not a substitute: the sweep is
    called from the incremental run AND the deep scan, and protecting only one
    leaves the other able to erase the same rows."""
    import inspect

    from core import database_update_worker

    source = inspect.getsource(database_update_worker)
    calls = source.count("cleanup_orphaned_records(")
    protected = source.count("protected_artist_ids=self._touched_artist_ids")
    assert calls == protected == 2, (
        f"{calls} cleanup call site(s), {protected} passing the ledger")
