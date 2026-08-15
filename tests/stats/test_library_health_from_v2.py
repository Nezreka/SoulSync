"""The System-Statistics aggregates, read from the v2 catalogue.

``get_library_health`` and ``get_statistics`` describe the library the user
owns. v2 stores more than that — a tracked artist's discography lives in the
same tables with ``origin='discography'`` — so every count here has to say
which rows it means, and the bytes/paths live on the file rows (ADR-03).
"""

from __future__ import annotations

import json

import pytest

from database.music_database import MusicDatabase


@pytest.fixture()
def db(tmp_path) -> MusicDatabase:
    return MusicDatabase(database_path=str(tmp_path / "health.db"))


def _artist(db, name, *, spotify_id=None, musicbrainz_id=None,
            external_ids=None, enrichment=None) -> int:
    conn = db._get_connection()
    try:
        artist_id = conn.execute(
            "INSERT INTO lib2_artists(name, name_key, spotify_id, musicbrainz_id,"
            "                         external_ids, enrichment)"
            " VALUES(?,?,?,?,?,?)",
            (name, name.lower(), spotify_id, musicbrainz_id,
             json.dumps(external_ids or {}), json.dumps(enrichment or {})),
        ).lastrowid
        conn.commit()
        return int(artist_id)
    finally:
        conn.close()


def _track(db, artist_id, *, title='Song', origin='library', path=None,
           size=None, duration=None, play_count=0, album_title='Album',
           file_state='active') -> int:
    conn = db._get_connection()
    try:
        album_id = conn.execute(
            "INSERT INTO lib2_albums(primary_artist_id, title, origin) VALUES(?,?,?)",
            (artist_id, album_title, origin)).lastrowid
        track_id = conn.execute(
            "INSERT INTO lib2_tracks(album_id, title, duration, play_count)"
            " VALUES(?,?,?,?)", (album_id, title, duration, play_count)).lastrowid
        if path is not None:
            conn.execute(
                "INSERT INTO lib2_track_files(track_id, path, size, is_primary,"
                "                             file_state) VALUES(?,?,?,1,?)",
                (track_id, path, size, file_state))
        conn.commit()
        return int(track_id)
    finally:
        conn.close()


# ── get_library_health ─────────────────────────────────────────────────────

def test_health_counts_only_the_library(db):
    """A release the user does not own contributes no tracks and no bytes."""
    artist = _artist(db, 'Muse')
    _track(db, artist, title='Owned', path='/m/owned.flac')
    _track(db, artist, title='Merely Listed', origin='discography')

    health = db.get_library_health()

    assert health['total_tracks'] == 1


def test_health_splits_formats_by_file_extension(db):
    artist = _artist(db, 'Muse')
    _track(db, artist, title='A', path='/m/a.flac')
    _track(db, artist, title='B', path='/m/b.FLAC')
    _track(db, artist, title='C', path='/m/c.mp3')

    assert db.get_library_health()['format_breakdown'] == {'FLAC': 2, 'MP3': 1}


def test_health_ignores_a_deleted_file(db):
    """A deleted file row is history, not a format the library holds."""
    artist = _artist(db, 'Muse')
    _track(db, artist, title='Gone', path='/m/gone.flac', file_state='deleted')

    assert db.get_library_health()['format_breakdown'] == {}


def test_health_reports_unplayed_share_and_duration(db):
    artist = _artist(db, 'Muse')
    _track(db, artist, title='Played', path='/m/a.flac', play_count=3, duration=1000)
    _track(db, artist, title='Never', path='/m/b.flac', play_count=0, duration=500)

    health = db.get_library_health()

    assert health['unplayed_count'] == 1
    assert health['unplayed_percentage'] == 50.0
    assert health['total_duration_ms'] == 1500


def test_enrichment_coverage_reads_columns_and_external_ids(db):
    """Only Spotify and MusicBrainz are promoted to columns in v2; the rest of
    the services this page reports on live in ``external_ids``, and Last.fm in
    the enrichment payload."""
    matched = _artist(db, 'Fully Matched', spotify_id='SP1', musicbrainz_id='MB1',
                      external_ids={'deezer': 'DZ1', 'itunes': 'IT1'},
                      enrichment={'lastfm': {'url': 'https://last.fm/x'}})
    unmatched = _artist(db, 'Unmatched')
    _track(db, matched, title='Owned A', path='/m/a.flac')
    _track(db, unmatched, title='Owned B', path='/m/b.flac')

    coverage = db.get_library_health()['enrichment_coverage']

    assert coverage['spotify'] == 50.0
    assert coverage['musicbrainz'] == 50.0
    assert coverage['deezer'] == 50.0
    assert coverage['itunes'] == 50.0
    assert coverage['lastfm'] == 50.0
    assert coverage['qobuz'] == 0


def test_health_on_an_empty_library(db):
    health = db.get_library_health()

    assert health['total_tracks'] == 0
    assert health['unplayed_percentage'] == 0
    assert health['format_breakdown'] == {}


# ── get_statistics ─────────────────────────────────────────────────────────

def test_statistics_count_the_owned_catalogue(db):
    artist = _artist(db, 'Muse')
    _track(db, artist, title='Owned', path='/m/a.flac', album_title='Owned Album')
    _track(db, artist, title='Listed', origin='discography', album_title='Listed Album')

    assert db.get_statistics() == {'artists': 1, 'albums': 1, 'tracks': 1}


def test_statistics_do_not_treat_library_origin_without_a_file_as_owned(db):
    artist = _artist(db, 'Muse')
    _track(db, artist, title='Missing', path=None, album_title='Known Album')

    assert db.get_statistics() == {'artists': 0, 'albums': 0, 'tracks': 0}


def test_statistics_count_an_artist_name_once(db):
    """Two rows for the same name are one artist to the user — the legacy count
    said ``COUNT(DISTINCT name)`` for exactly that reason."""
    first = _artist(db, 'Muse')
    second = _artist(db, 'Muse')
    _track(db, first, path='/m/a.flac')
    _track(db, second, path='/m/b.flac')

    assert db.get_statistics()['artists'] == 1


def test_enrichment_coverage_denominator_is_the_owned_library(db):
    """A tracked artist's discography must not dilute the percentages.

    v2 keeps provider-only, discography and watchlist artists in the same table
    legacy's `artists` never held. Counting them made the same library report
    worse coverage after the migration than before it.
    """
    owned = _artist(db, 'Owned', spotify_id='SP1')
    _track(db, owned, title='Owned', path='/m/owned.flac')
    for index in range(3):
        listed = _artist(db, f'Merely Listed {index}')
        _track(db, listed, title='Listed', origin='discography')

    health = db.get_library_health()

    assert health['enrichment_coverage']['spotify'] == 100.0
