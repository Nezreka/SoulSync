"""Tests for core/library/embedded_id_reconcile.py.

The reconcile job reads provider IDs already embedded in a file's tags
(by SoulSync or MusicBrainz Picard) and gap-fills them into the library
DB so enrichment workers skip the API call. These pin the guarantees that
make it safe to run across a whole library while workers run concurrently:

  1. gap-fill only — an existing id is NEVER overwritten,
  2. disagreements are reported as conflicts, not applied,
  3. the write is ATOMICALLY guarded — if a worker fills the column
     between plan and apply, the apply no-ops (no clobber).
"""

from __future__ import annotations

import sqlite3

from core.library.embedded_id_reconcile import ReconcileTotals, reconcile_library
from core.library2.schema import ensure_library_v2_schema


# ---------------------------------------------------------------------------
# reconcile_library — the shared orchestration (paging, lazy maps, scope)
# ---------------------------------------------------------------------------

def _make_library_db():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    ensure_library_v2_schema(conn)
    cur = conn.cursor()
    # Two tracks on the same album/artist, one orphan track with no file.
    cur.execute("INSERT INTO lib2_artists(id,name) VALUES (1,'Artist')")
    cur.execute("INSERT INTO lib2_albums(id,primary_artist_id,title) VALUES (1,1,'Album')")
    cur.execute("INSERT INTO lib2_tracks(id,album_id,title) VALUES (1,1,'A'),(2,1,'B'),(3,1,'NoFile')")
    cur.execute("INSERT INTO lib2_track_files(track_id,path,is_primary) VALUES (1,'/a.flac',1),(2,'/b.flac',1)")
    conn.commit()
    return conn, cur


def _reader(mapping):
    """read_tags stub: file_path -> tags dict (or None)."""
    return lambda fp: mapping.get(fp)


def test_reconcile_library_whole_library_fills_all():
    conn, cur = _make_library_db()
    tags = {
        '/a.flac': {'spotify_track_id': 'TA', 'spotify_album_id': 'ALB', 'spotify_artist_id': 'ART'},
        '/b.flac': {'spotify_track_id': 'TB', 'spotify_album_id': 'ALB', 'spotify_artist_id': 'ART'},
    }
    totals = reconcile_library(conn, _reader(tags))
    assert isinstance(totals, ReconcileTotals)
    # t3 has empty file_path -> excluded from the whole-library SELECT entirely.
    assert totals.total == 2 and totals.processed == 2
    # t1: track+album+artist (3); t2: only its own track id (parents already filled) (1)
    assert totals.ids_filled == 4
    cur.execute("SELECT spotify_id FROM lib2_tracks WHERE id=2")
    assert cur.fetchone()[0] == 'TB'
    cur.execute("SELECT spotify_id FROM lib2_albums WHERE id=1")
    assert cur.fetchone()[0] == 'ALB'


def test_reconcile_library_scoped_to_given_ids():
    conn, cur = _make_library_db()
    tags = {'/b.flac': {'spotify_track_id': 'TB'}, '/a.flac': {'spotify_track_id': 'TA'}}
    totals = reconcile_library(conn, _reader(tags), track_ids=[2])
    assert totals.total == 1 and totals.ids_filled == 1
    cur.execute("SELECT spotify_id FROM lib2_tracks WHERE id=2")
    assert cur.fetchone()[0] == 'TB'
    cur.execute("SELECT spotify_id FROM lib2_tracks WHERE id=1")
    assert cur.fetchone()[0] is None  # not in scope


def test_reconcile_library_unreadable_counted():
    conn, cur = _make_library_db()
    totals = reconcile_library(conn, _reader({}), track_ids=[1, 2])  # reader returns None
    assert totals.unreadable == 2 and totals.ids_filled == 0


def test_reconcile_library_is_idempotent():
    conn, cur = _make_library_db()
    tags = {'/a.flac': {'spotify_track_id': 'TA', 'spotify_album_id': 'ALB', 'spotify_artist_id': 'ART'}}
    first = reconcile_library(conn, _reader(tags), track_ids=[1])
    second = reconcile_library(conn, _reader(tags), track_ids=[1])
    assert first.ids_filled == 3
    assert second.ids_filled == 0  # nothing left to fill


def test_reconcile_library_progress_and_stop():
    conn, cur = _make_library_db()
    seen = []
    reconcile_library(conn, _reader({'/a.flac': {'spotify_track_id': 'TA'}}),
                      track_ids=[1, 2],
                      on_progress=lambda totals, title: seen.append(title))
    assert seen == ['A', 'B']

    # should_stop halts before processing anything
    totals = reconcile_library(conn, _reader({}), track_ids=[1, 2],
                               should_stop=lambda: True)
    assert totals.processed == 0


def test_reconcile_library_native_scope_uses_server_ids():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    ensure_library_v2_schema(conn)
    artist = conn.execute(
        "INSERT INTO lib2_artists(name,server_source,server_id) VALUES('A','plex','a')"
    ).lastrowid
    album = conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id,title,server_source,server_id) "
        "VALUES(?,'Album','plex','al')", (artist,)).lastrowid
    track = conn.execute(
        "INSERT INTO lib2_tracks(album_id,title,server_source,server_id) "
        "VALUES(?,'Song','plex','server-track')", (album,)).lastrowid
    conn.execute("INSERT INTO lib2_track_files(track_id,path,is_primary) "
                 "VALUES(?, '/song.flac', 1)", (track,))

    totals = reconcile_library(
        conn, _reader({'/song.flac': {'spotify_track_id': 't',
                                      'spotify_album_id': 'al',
                                      'spotify_artist_id': 'a'}}),
        track_ids=['server-track'], server_source='plex')

    assert totals.ids_filled == 3
    assert conn.execute(
        "SELECT COUNT(*) FROM lib2_provider_attempts WHERE service='spotify' "
        "AND status='matched'").fetchone()[0] == 3


def test_reconcile_library_native_scope_uses_mapping_after_server_switch():
    conn, _cur = _make_library_db()
    conn.execute(
        "UPDATE lib2_tracks SET server_source='jellyfin',server_id='j-track' WHERE id=1"
    )
    conn.execute(
        "INSERT INTO lib2_media_server_mappings "
        "(entity_type,entity_id,server_source,server_id) "
        "VALUES('track',1,'plex','p-track')"
    )

    totals = reconcile_library(
        conn, _reader({'/a.flac': {'spotify_track_id': 'mapped'}}),
        track_ids=['p-track'], server_source='plex',
    )

    assert totals.processed == 1
    assert conn.execute(
        "SELECT spotify_id FROM lib2_tracks WHERE id=1"
    ).fetchone()[0] == 'mapped'


# ---------------------------------------------------------------------------
# Whose id is it? — an `*_artist_id` tag names the TRACK's performer
# ---------------------------------------------------------------------------

def _make_compilation_db():
    """A Various-Artists compilation: the album artist is not the performer."""
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    ensure_library_v2_schema(conn)
    cur = conn.cursor()
    cur.execute("INSERT INTO lib2_artists(id,name) VALUES (1,'Various Artists'),(2,'Real Performer')")
    cur.execute("INSERT INTO lib2_albums(id,primary_artist_id,title) VALUES (1,1,'A Compilation')")
    cur.execute("INSERT INTO lib2_tracks(id,album_id,title) VALUES (1,1,'Their Song')")
    cur.execute("INSERT INTO lib2_track_artists(track_id,artist_id,role,position) "
                "VALUES (1,2,'primary',0)")
    cur.execute("INSERT INTO lib2_track_files(track_id,path,is_primary) VALUES (1,'/a.flac',1)")
    conn.commit()
    return conn, cur


def test_artist_tag_lands_on_the_credited_performer_not_the_album_artist():
    """The bug this pins: every artist tag was written to the album's primary
    artist, so the first track of a compilation stamped its performer's MBID
    onto Various Artists — and marked it matched, so enrichment never
    corrected it."""
    conn, cur = _make_compilation_db()
    totals = reconcile_library(
        conn, _reader({'/a.flac': {'musicbrainz_artistid': 'PERFORMER-MBID'}}))

    assert totals.ids_filled == 1
    assert cur.execute(
        "SELECT musicbrainz_id FROM lib2_artists WHERE id=2").fetchone()[0] == 'PERFORMER-MBID'
    assert cur.execute(
        "SELECT musicbrainz_id FROM lib2_artists WHERE id=1").fetchone()[0] is None


def test_artist_tag_falls_back_to_the_album_artist_without_credits():
    conn, cur = _make_compilation_db()
    cur.execute("DELETE FROM lib2_track_artists")
    conn.commit()

    reconcile_library(conn, _reader({'/a.flac': {'musicbrainz_artistid': 'ONLY-GUESS'}}))

    assert cur.execute(
        "SELECT musicbrainz_id FROM lib2_artists WHERE id=1").fetchone()[0] == 'ONLY-GUESS'


def test_a_joined_multi_value_tag_is_not_written_as_one_id():
    """"A feat. B" carries both performers' ids in one frame, and the tag
    reader joins them with ', '. That string is not an id."""
    conn, cur = _make_compilation_db()
    totals = reconcile_library(
        conn, _reader({'/a.flac': {'musicbrainz_artistid': 'MBID-A, MBID-B'}}))

    assert totals.ids_filled == 0
    assert cur.execute(
        "SELECT musicbrainz_id FROM lib2_artists WHERE id=2").fetchone()[0] is None


def test_a_worker_that_wins_the_race_keeps_its_id(monkeypatch):
    """The window the guard closes: an enrichment worker settles the entity
    between this job's read and its write. The guard is in the statement, so
    the fill affects no rows instead of replacing a fresher match."""
    from core.library2 import worker_support

    real = worker_support.stored_provider_id
    conn, cur = _make_compilation_db()
    cur.execute("UPDATE lib2_artists SET musicbrainz_id='WORKER-WON' WHERE id=2")
    conn.commit()
    # ...but this job read the row a moment earlier, when it was still empty.
    # Only that FIRST read is stale; everything after it sees the real row.
    reads = []

    def _stale_once(*args, **kwargs):
        reads.append(1)
        return None if len(reads) == 1 else real(*args, **kwargs)

    monkeypatch.setattr(worker_support, 'stored_provider_id', _stale_once)

    totals = reconcile_library(
        conn, _reader({'/a.flac': {'musicbrainz_artistid': 'STALE-TAG'}}))

    assert totals.ids_filled == 0
    assert totals.conflicts == 1
    assert cur.execute(
        "SELECT musicbrainz_id FROM lib2_artists WHERE id=2").fetchone()[0] == 'WORKER-WON'


# ---------------------------------------------------------------------------
# Upstream a61506e58: the MusicBrainz recording id is reconciled as well
# ---------------------------------------------------------------------------

REC = '1f9df192-a621-4f54-8850-2c5373b7eac9'


def test_picard_mp3_recording_id_is_read_and_reconciled(tmp_path):
    # picard keeps the recording id in a UFID frame and the album /
    # release-track ids in TXXX frames. The reader only walked TXXX, so the
    # mp3 never got a recording id while its flac twin did.
    from mutagen.id3 import ID3, TXXX, UFID

    from core.library.file_tags import read_embedded_tags

    path = tmp_path / 'friend.mp3'
    path.write_bytes((bytes([0xFF, 0xFB, 0x90, 0x64]) + bytes(413)) * 10)
    tags = ID3()
    tags.add(UFID(owner='http://musicbrainz.org', data=REC.encode('ascii')))
    tags.add(TXXX(encoding=3, desc='MusicBrainz Release Track Id',
                  text=['aaaaaaaa-0000-0000-0000-000000000001']))
    tags.save(str(path))

    embedded = read_embedded_tags(str(path))['tags']
    assert embedded['musicbrainz_trackid'] == REC
    assert embedded['musicbrainz_releasetrackid'] == 'aaaaaaaa-0000-0000-0000-000000000001'

    conn, cur = _make_library_db()
    totals = reconcile_library(conn, _reader({'/a.flac': embedded}))

    assert totals.ids_filled == 1
    assert cur.execute(
        "SELECT musicbrainz_id FROM lib2_tracks WHERE id=1").fetchone()[0] == REC


def test_recording_id_is_lowercased_and_junk_is_not_filled():
    conn, cur = _make_library_db()
    reconcile_library(conn, _reader({
        '/a.flac': {'musicbrainz_trackid': REC.upper()},
        '/b.flac': {'musicbrainz_trackid': 'not-a-recording-id'},
    }))

    assert cur.execute(
        "SELECT musicbrainz_id FROM lib2_tracks WHERE id=1").fetchone()[0] == REC
    assert cur.execute(
        "SELECT musicbrainz_id FROM lib2_tracks WHERE id=2").fetchone()[0] is None
