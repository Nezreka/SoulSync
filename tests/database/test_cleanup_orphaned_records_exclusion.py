"""cleanup_orphaned_records() must not delete a row the CURRENT scan run
just wrote, even though it has no tracks yet (see
soulsync_navidrome_compilation_id_instability_TODO memory / redhome's
"Rare Funk & Disco 24" — an album inserted this run, then swept by this
same run's cleanup before its tracks landed, never came back because the
media server stopped reporting it as "recently added" once the row had
briefly existed)."""

from __future__ import annotations

import sqlite3

from database.music_database import MusicDatabase


class _InMemoryDB(MusicDatabase):
    def __init__(self):
        self._conn = sqlite3.connect(":memory:")
        self._conn.row_factory = sqlite3.Row

    def _get_connection(self):
        return _NonClosingConn(self._conn)


class _NonClosingConn:
    def __init__(self, real):
        self._real = real

    def cursor(self):
        return self._real.cursor()

    def commit(self):
        return self._real.commit()

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False


def _seed(db):
    cur = db._conn.cursor()
    cur.execute("CREATE TABLE artists (id TEXT PRIMARY KEY, name TEXT)")
    cur.execute("CREATE TABLE albums (id TEXT PRIMARY KEY, artist_id TEXT, title TEXT)")
    cur.execute("""
        CREATE TABLE tracks (id TEXT PRIMARY KEY, album_id TEXT, artist_id TEXT, title TEXT)
    """)
    # A normal artist/album with a track — never at risk, sanity baseline.
    cur.execute("INSERT INTO artists VALUES ('artist-ok', 'Has Tracks')")
    cur.execute("INSERT INTO albums VALUES ('album-ok', 'artist-ok', 'Real Album')")
    cur.execute("INSERT INTO tracks VALUES ('track-1', 'album-ok', 'artist-ok', 'Song')")
    # A genuinely stale artist/album from a PRIOR run — no tracks, not touched
    # this run. Cleanup must still remove these (regression guard).
    cur.execute("INSERT INTO artists VALUES ('artist-stale', 'Old Orphan')")
    cur.execute("INSERT INTO albums VALUES ('album-stale', 'artist-stale', 'Old Orphan Album')")
    # This run's freshly-inserted, still-trackless artist/album — the race.
    cur.execute("INSERT INTO artists VALUES ('artist-fresh', 'Various Artists')")
    cur.execute("INSERT INTO albums VALUES ('album-fresh', 'artist-fresh', 'Rare Funk & Disco 24')")
    db._conn.commit()


def test_excluded_trackless_album_and_artist_survive_cleanup():
    db = _InMemoryDB()
    _seed(db)

    result = db.cleanup_orphaned_records(
        exclude_artist_ids={'artist-fresh'},
        exclude_album_ids={'album-fresh'},
    )

    remaining_albums = {r['id'] for r in db._conn.execute("SELECT id FROM albums")}
    remaining_artists = {r['id'] for r in db._conn.execute("SELECT id FROM artists")}

    assert 'album-fresh' in remaining_albums, "excluded (this-run) album must not be deleted"
    assert 'artist-fresh' in remaining_artists, "excluded (this-run) artist must not be deleted"
    # The genuinely stale, non-excluded rows are still cleaned up.
    assert 'album-stale' not in remaining_albums
    assert 'artist-stale' not in remaining_artists
    assert result['orphaned_albums_removed'] == 1
    assert result['orphaned_artists_removed'] == 1


def test_no_exclusions_behaves_like_before():
    db = _InMemoryDB()
    _seed(db)

    result = db.cleanup_orphaned_records()

    remaining_albums = {r['id'] for r in db._conn.execute("SELECT id FROM albums")}
    remaining_artists = {r['id'] for r in db._conn.execute("SELECT id FROM artists")}

    # Without an exclude list every trackless row is swept, including the
    # this-run one — this is the OLD (buggy) behavior, pinned so a future
    # change can't silently widen the default to "always exclude everything".
    assert remaining_albums == {'album-ok'}
    assert remaining_artists == {'artist-ok'}
    assert result['orphaned_albums_removed'] == 2
    assert result['orphaned_artists_removed'] == 2


def test_album_with_tracks_never_removed_regardless_of_exclusion():
    db = _InMemoryDB()
    _seed(db)

    db.cleanup_orphaned_records(exclude_artist_ids=set(), exclude_album_ids=set())

    row = db._conn.execute("SELECT id FROM albums WHERE id = 'album-ok'").fetchone()
    assert row is not None
