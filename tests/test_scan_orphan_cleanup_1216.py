"""#1216 — an incremental scan could permanently lose an album it had just written.

The scan inserts an album row, then its tracks, then sweeps orphans. Between
the first two steps a real album legitimately has zero tracks, so if the
server's track list for that one album comes back short (no exception, no
timeout - just an incomplete answer) the sweep acts on the row the same run
created.

The rule: a row this run touched is not an orphan yet. Only rows left fileless
by an EARLIER run are.

Ported to Library v2. The sweep here DETACHES the media-server stamp instead of
deleting catalogue rows - v2 keeps discography and monitored releases beside
the owned ones, so a fileless album is normal and deleting it would lose data
the server never owned. That makes upstream's permanent-loss shape impossible,
but the same race still reaches the STAMP: strip the server link off an album
written seconds ago and it stops resolving on the server until a deep scan puts
it back. So the ledger still has to be honoured, and these pin that.
"""

from __future__ import annotations

import pytest

from database.music_database import MusicDatabase


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(database_path=str(tmp_path / "music.db"))


def _artist(db, artist_id, name, *, stamped=True):
    """A catalogue artist, by default carrying a media-server stamp (only
    stamped rows are candidates for the sweep at all)."""
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO lib2_artists (id, name, name_key, server_source, server_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (artist_id, name, str(name).lower(),
             'plex' if stamped else None, f"srv-{artist_id}" if stamped else None))
        conn.commit()


def _album(db, album_id, artist_id, title, *, stamped=True):
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO lib2_albums (id, primary_artist_id, title, server_source, server_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (album_id, artist_id, title,
             'plex' if stamped else None, f"srv-{album_id}" if stamped else None))
        conn.commit()


def _track(db, track_id, album_id, artist_id, title):
    """A track WITH a live file - what makes its album and artist owned."""
    with db._get_connection() as conn:
        conn.execute("INSERT INTO lib2_tracks (id, album_id, title) VALUES (?, ?, ?)",
                     (track_id, album_id, title))
        conn.execute(
            "INSERT INTO lib2_track_files (track_id, path, is_primary, file_state) "
            "VALUES (?, ?, 1, 'active')", (track_id, f"/music/{track_id}.flac"))
        conn.commit()


def _stamped(db, table):
    """Ids that still carry a media-server stamp. The v2 sweep detaches rather
    than deletes, so this is the set the tests are actually about."""
    with db._get_connection() as conn:
        return {str(r[0]) for r in conn.execute(
            f"SELECT id FROM {table} WHERE server_id IS NOT NULL")}


def _rows(db, table):
    with db._get_connection() as conn:
        return {str(r[0]) for r in conn.execute(f"SELECT id FROM {table}")}


def test_a_fileless_album_from_an_earlier_run_is_detached(db):
    """The sweep's actual job: retire a stamp the server no longer earns."""
    _artist(db, 1, "Ghost Artist")
    _album(db, 1, 1, "Ghost Album")

    result = db.cleanup_orphaned_records()

    assert result['orphaned_albums_removed'] == 1
    assert result['orphaned_artists_removed'] == 1
    assert _stamped(db, "lib2_albums") == set()
    assert _stamped(db, "lib2_artists") == set()
    # the catalogue rows themselves survive - that is the v2 difference
    assert _rows(db, "lib2_albums") == {"1"} and _rows(db, "lib2_artists") == {"1"}


def test_an_album_this_run_wrote_survives_its_own_sweep(db):
    """#1216: the album is fileless only because its tracks have not landed
    yet. Acting on it here is what broke the reporter's library."""
    _artist(db, 1, "Various Artists")
    _album(db, 1, 1, "Compilation")

    result = db.cleanup_orphaned_records(
        protected_artist_ids={1}, protected_album_ids={1})

    assert result['orphaned_albums_removed'] == 0
    assert result['albums_protected'] == 1
    assert result['artists_protected'] == 1
    assert _stamped(db, "lib2_albums") == {"1"}, "the run's own album was detached — #1216 is back"
    assert _stamped(db, "lib2_artists") == {"1"}


def test_protection_is_per_row_not_all_or_nothing(db):
    """A run that touched one artist must not shield last week's orphans."""
    _artist(db, 1, "This Run")
    _album(db, 1, 1, "Just Inserted")
    _artist(db, 2, "Long Gone")
    _album(db, 2, 2, "Left Fileless Ages Ago")

    result = db.cleanup_orphaned_records(
        protected_artist_ids={1}, protected_album_ids={1})

    assert result['orphaned_albums_removed'] == 1
    assert result['orphaned_artists_removed'] == 1
    assert _stamped(db, "lib2_albums") == {"1"}
    assert _stamped(db, "lib2_artists") == {"1"}


def test_an_album_with_files_is_never_touched(db):
    _artist(db, 1, "Real Artist")
    _album(db, 1, 1, "Real Album")
    _track(db, 1, 1, 1, "Real Track")

    result = db.cleanup_orphaned_records()

    assert result['orphaned_albums_removed'] == 0
    assert _stamped(db, "lib2_albums") == {"1"}


def test_ids_are_compared_as_strings(db):
    """Server ids arrive as ints from Plex and strings from Jellyfin/Navidrome,
    and lib2 keys are INTEGER; the ledger must match either way."""
    _artist(db, 77, "Numeric Id")
    _album(db, 88, 77, "Numeric Album")

    result = db.cleanup_orphaned_records(
        protected_artist_ids={"77"}, protected_album_ids={"88"})

    assert result['orphaned_albums_removed'] == 0
    assert _stamped(db, "lib2_albums") == {"88"}


def test_a_sweep_larger_than_one_chunk_still_clears(db):
    """The detach is chunked around SQLite's bound-variable cap; 600 orphans
    must not silently leave 100 behind."""
    _artist(db, 1, "Prolific")
    for n in range(600):
        _album(db, n + 1, 1, f"Album {n}")

    result = db.cleanup_orphaned_records(protected_artist_ids={1})

    assert result['orphaned_albums_removed'] == 600
    assert _stamped(db, "lib2_albums") == set()


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


def test_the_owner_of_a_protected_album_is_protected_too(db):
    """Nothing cascades here (the stamp is detached, not the row deleted), but
    an artist that owns an album this run just wrote was written by the same
    run one step earlier - stripping its stamp is the same race on the same
    rows. Upstream rests this on the caller remembering both; pin it here."""
    _artist(db, 1, "Various Artists")
    _album(db, 1, 1, "Compilation")

    # Only the ALBUM is named — the artist is fileless and unprotected.
    result = db.cleanup_orphaned_records(protected_album_ids={1})

    assert _stamped(db, "lib2_albums") == {"1"}, "the protected album was detached"
    assert _stamped(db, "lib2_artists") == {"1"}
    assert result['orphaned_artists_removed'] == 0


def test_an_unrelated_orphan_artist_still_goes(db):
    """Sparing the owner must not spare everyone else."""
    _artist(db, 1, "Owns A Protected Album")
    _album(db, 1, 1, "Just Inserted")
    _artist(db, 2, "Owns Nothing")

    result = db.cleanup_orphaned_records(protected_album_ids={1})

    assert _stamped(db, "lib2_artists") == {"1"}
    assert result['orphaned_artists_removed'] == 1
