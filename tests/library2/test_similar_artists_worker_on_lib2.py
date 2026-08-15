"""The Similar Artists worker reads Library v2 (docs §32.3.1 stage 2).

Two things set this worker apart from the enrichment workers, and both survive the
move rather than being smoothed away:

* it retries ``error`` as well as ``not_found`` — its errors are MusicMap timeouts
  and 5xx, and ``process_artist`` already sorts a definitive 400/404 into
  ``not_found``, so retrying is right here where it would be wrong elsewhere;
* its universe is only artists already matched to a metadata source, because the
  similars it stores are keyed by that source id. Offering an unmatched artist
  would mark it failed forever.

``pick_source_artist_id`` keeps its priority order (spotify → itunes → deezer →
musicbrainz), which is not cosmetic: it is the order the watchlist scanner uses to
key its own rows, so a library artist that is also watchlisted resolves to the
same source_artist_id instead of duplicating the work.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from core.library2.provider_attempts import (
    attempt_state, ensure_provider_attempt_schema, record_attempt,
)
from core.library2.schema import ensure_library_v2_schema

from .conftest import own_every_track
from core.similar_artists_worker import SimilarArtistsWorker, pick_source_artist_id


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
    own_every_track(conn)
    ensure_provider_attempt_schema(conn.cursor())
    conn.commit()
    conn.close()

    instance = SimilarArtistsWorker.__new__(SimilarArtistsWorker)
    instance.db = _Db(path)
    instance.retry_days = 30
    instance.limit = 25
    instance.profile_id = 1
    instance.stats = {'matched': 0, 'not_found': 0, 'pending': 0, 'errors': 0}
    return instance


def _artist(worker, name, *, spotify_id=None, musicbrainz_id=None,
            external_ids=None):
    conn = worker.db._get_connection()
    try:
        artist_id = conn.execute(
            "INSERT INTO lib2_artists(name, sort_name, spotify_id, musicbrainz_id, "
            "external_ids) VALUES(?,?,?,?,?)",
            (name, name, spotify_id, musicbrainz_id,
             json.dumps(external_ids or {}))).lastrowid
        # The queue offers owned artists only; `own_every_track` files the track.
        album = conn.execute(
            "INSERT INTO lib2_albums(primary_artist_id,title,album_type) "
            "VALUES(?,'Tohu Bohu','album')", (artist_id,)).lastrowid
        conn.execute(
            "INSERT INTO lib2_tracks(album_id,title) VALUES(?,'Bora')", (album,))
        conn.commit()
        return artist_id
    finally:
        conn.close()


class TestPickingTheSourceId:
    def test_spotify_wins_over_the_others(self):
        """The watchlist scanner's own priority order — a library artist that is
        also watchlisted has to resolve to the same key or the work is done twice."""
        row = {'spotify_id': 'sp-1', 'musicbrainz_id': 'mb-1',
               'external_ids': {'deezer': 'dz-1', 'itunes': 'it-1'}}

        assert pick_source_artist_id(row) == 'sp-1'

    def test_itunes_then_deezer_then_musicbrainz(self):
        assert pick_source_artist_id(
            {'external_ids': {'itunes': 'it-1', 'deezer': 'dz-1'}}) == 'it-1'
        assert pick_source_artist_id(
            {'external_ids': {'deezer': 'dz-1'}, 'musicbrainz_id': 'mb-1'}) == 'dz-1'
        assert pick_source_artist_id({'musicbrainz_id': 'mb-1'}) == 'mb-1'

    def test_an_unmatched_artist_yields_nothing(self):
        assert pick_source_artist_id({'external_ids': {}}) is None

    def test_a_source_with_no_column_of_its_own_is_not_a_key(self):
        """Only the four the similar_artists table can store are usable keys."""
        assert pick_source_artist_id({'external_ids': {'discogs': '12345'}}) is None

    def test_a_raw_json_string_is_accepted(self):
        """lib2 rows carry external_ids as text; the caller should not have to
        remember to parse it."""
        assert pick_source_artist_id(
            {'external_ids': '{"deezer": "dz-1"}'}) == 'dz-1'


class TestTheQueue:
    def test_a_matched_artist_is_offered_with_its_ids(self, worker):
        artist = _artist(worker, "Rone", spotify_id="sp-1")

        row = worker._get_next_artist()

        assert row['id'] == artist
        assert row['name'] == "Rone"
        assert pick_source_artist_id(row) == "sp-1"

    def test_an_unmatched_artist_is_never_offered(self, worker):
        _artist(worker, "Unmatched")

        assert worker._get_next_artist() is None

    def test_an_external_id_is_enough_to_be_offered(self, worker):
        artist = _artist(worker, "Rone", external_ids={"deezer": "dz-1"})

        row = worker._get_next_artist()

        assert row['id'] == artist
        assert pick_source_artist_id(row) == "dz-1"

    def test_a_matched_artist_is_not_offered_again(self, worker):
        artist = _artist(worker, "Rone", spotify_id="sp-1")

        worker._mark(artist, 'matched')

        assert worker._get_next_artist() is None

    def test_a_stale_error_comes_back(self, worker):
        """MusicMap timeouts and 5xx are transient; a definitive miss is already
        sorted into not_found by process_artist."""
        artist = _artist(worker, "Rone", spotify_id="sp-1")
        worker._mark(artist, 'error')
        conn = worker.db._get_connection()
        conn.execute("UPDATE lib2_provider_attempts "
                     "SET last_attempted_at=datetime('now','-90 days')")
        conn.commit()
        conn.close()

        assert worker._get_next_artist()['id'] == artist

    def test_a_fresh_error_does_not(self, worker):
        artist = _artist(worker, "Rone", spotify_id="sp-1")

        worker._mark(artist, 'error')

        assert worker._get_next_artist() is None


class TestTheTallies:
    def test_each_outcome_is_counted(self, worker):
        a = _artist(worker, "A", spotify_id="sp-1")
        b = _artist(worker, "B", spotify_id="sp-2")
        c = _artist(worker, "C", spotify_id="sp-3")
        _artist(worker, "D", spotify_id="sp-4")
        worker._mark(a, 'matched')
        worker._mark(b, 'not_found')
        worker._mark(c, 'error')

        counts = worker._db_counts()

        assert counts == {'matched': 1, 'not_found': 1, 'error': 1,
                          'pending': 1, 'total': 4}
        assert worker._count_pending() == 1

    def test_unmatched_artists_are_outside_the_universe(self, worker):
        """A total counted over a wider population than the queue picks from
        would leave the progress bar short of 100% forever."""
        _artist(worker, "Matched", spotify_id="sp-1")
        _artist(worker, "Unmatched")

        assert worker._db_counts()['total'] == 1


def test_the_mark_reaches_the_ledger(worker):
    artist = _artist(worker, "Rone", spotify_id="sp-1")

    worker._mark(artist, 'matched')

    conn = worker.db._get_connection()
    try:
        state = attempt_state(conn, entity_type='artist', entity_id=artist)
    finally:
        conn.close()
    assert state['similar_artists']['status'] == 'matched'


def test_the_worker_holds_no_legacy_sql_at_all():
    import pathlib

    from tests.library2.legacy_usage import count_legacy_usage

    usage = count_legacy_usage(
        pathlib.Path("core/similar_artists_worker.py").read_text())

    assert (usage.reads, usage.writes) == (0, 0)
