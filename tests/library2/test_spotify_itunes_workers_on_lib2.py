"""The Spotify and iTunes workers write Library v2 (docs §32.3.1 stage 2).

These two are the batch-first pair: both have "every album by this artist" and
"every track on this album" endpoints, and their queues are built around spending
one API call per parent instead of one per child. That whole order now lives in
``worker_queue.next_batch_pending`` and is shared, because the two queues were
character-for-character the same apart from the service prefix.

What is worth pinning past the mechanical port:

* the item dicts keep their service-prefixed key (``spotify_artist_id`` /
  ``itunes_artist_id``), so the process methods did not have to change;
* a failed bulk call records one outcome for every child still unattempted, and
  leaves children the provider already settled alone;
* Spotify's id is promoted to a real ``spotify_id`` column as well as
  ``external_ids`` — the read paths join on the column, so writing only the JSON
  would leave them behind. iTunes has no promoted column and lives in the JSON only.
"""

from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest

from core.library2.provider_attempts import (
    attempt_state, ensure_provider_attempt_schema, record_attempt,
)
from core.library2.schema import ensure_library_v2_schema

from .conftest import own_every_track


class _Db:
    def __init__(self, path):
        self.path = path

    def _get_connection(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn


def _seed(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    ensure_library_v2_schema(conn)
    own_every_track(conn)
    ensure_provider_attempt_schema(conn.cursor())
    artist = conn.execute(
        "INSERT INTO lib2_artists(name, sort_name) VALUES('Rone','Rone')").lastrowid
    album = conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id,title,album_type) "
        "VALUES(?,'Tohu Bohu','album')", (artist,)).lastrowid
    conn.execute(
        "INSERT INTO lib2_tracks(album_id,title,track_number) VALUES(?,'Bora',1)",
        (album,))
    conn.commit()
    conn.close()


def _worker(cls, path):
    worker = cls.__new__(cls)
    worker.db = _Db(path)
    worker.retry_days = 30
    worker.name_similarity_threshold = 0.80
    worker.stats = {'matched': 0, 'not_found': 0, 'pending': 0, 'errors': 0}
    worker.client = None
    return worker


@pytest.fixture
def spotify(tmp_path):
    from core.spotify_worker import SpotifyWorker

    path = str(tmp_path / "sp.db")
    _seed(path)
    return _worker(SpotifyWorker, path)


@pytest.fixture
def itunes(tmp_path):
    from core.itunes_worker import iTunesWorker

    path = str(tmp_path / "it.db")
    _seed(path)
    return _worker(iTunesWorker, path)


def _row(worker, table, entity_id=1):
    conn = worker.db._get_connection()
    try:
        return conn.execute(
            f"SELECT * FROM {table} WHERE id=?", (entity_id,)).fetchone()
    finally:
        conn.close()


def _ids(worker, table, entity_id=1):
    return json.loads(_row(worker, table, entity_id)['external_ids'])


def _status(worker, service, entity_type='artist', entity_id=1):
    conn = worker.db._get_connection()
    try:
        return attempt_state(conn, entity_type=entity_type, entity_id=entity_id
                             ).get(service, {}).get('status')
    finally:
        conn.close()


class TestTheBatchFirstQueue:
    def test_the_artist_comes_before_any_batch(self, spotify):
        item = spotify._get_next_item()

        assert item['type'] == 'artist'
        assert item['id'] == 1

    def test_a_matched_artist_yields_an_album_batch_with_its_id(self, spotify):
        spotify._update_artist(1, SimpleNamespace(
            id='sp-artist', name='Rone', image_url=None, genres=[]))

        item = spotify._get_next_item()

        assert item['type'] == 'album_batch'
        assert item['artist_id'] == 1
        assert item['spotify_artist_id'] == 'sp-artist'

    def test_itunes_gets_its_own_prefixed_key(self, itunes):
        """Both workers' process methods read item['<service>_artist_id'], so the
        shared queue has to key it by service rather than settling on one name."""
        itunes._update_artist(1, SimpleNamespace(
            id='it-artist', name='Rone', image_url=None, genres=[]))

        item = itunes._get_next_item()

        assert item['type'] == 'album_batch'
        assert item['itunes_artist_id'] == 'it-artist'

    def test_a_matched_album_yields_a_track_batch(self, spotify):
        spotify._update_artist(1, SimpleNamespace(
            id='sp-artist', name='Rone', image_url=None, genres=[]))
        spotify._update_album(1, SimpleNamespace(
            id='sp-album', name='Tohu Bohu', image_url=None, album_type='album',
            release_date=None, total_tracks=0))

        item = spotify._get_next_item()

        assert item['type'] == 'track_batch'
        assert item['album_id'] == 1
        assert item['spotify_album_id'] == 'sp-album'
        assert item['artist_name'] == 'Rone'

    def test_an_unmatchable_artist_pushes_its_albums_to_the_individual_path(self, spotify):
        """Without a provider artist id there is no bulk endpoint to call, so the
        per-child lookup is the only option left."""
        spotify._mark_status('artist', 1, 'not_found')

        item = spotify._get_next_item()

        assert item['type'] == 'album_individual'
        assert item['id'] == 1
        assert item['artist'] == 'Rone'


class TestTheBatchChildren:
    def test_only_unattempted_children_are_offered(self, spotify):
        conn = spotify.db._get_connection()
        album = conn.execute(
            "INSERT INTO lib2_albums(primary_artist_id,title,album_type) "
            "VALUES(1,'Creatures','album')").lastrowid
        # The album needs a track (and, via own_every_track, a file): the batch
        # queue offers only the OWNED library, so a bare album row is a
        # discography entry the worker must not spend API budget on.
        conn.execute("INSERT INTO lib2_tracks(album_id,title,track_number) "
                     "VALUES(?,'Ghosts',1)", (album,))
        conn.commit()
        conn.close()
        spotify._mark_status('album', 1, 'matched')

        albums = spotify._get_unmatched_albums_for_artist(1)

        assert [a['title'] for a in albums] == ['Creatures']

    def test_track_children_carry_their_number(self, spotify):
        tracks = spotify._get_unmatched_tracks_for_album(1)

        assert tracks == [{'id': 1, 'title': 'Bora', 'track_number': 1}]

    def test_a_failed_album_batch_records_every_pending_child(self, spotify):
        conn = spotify.db._get_connection()
        album = conn.execute(
            "INSERT INTO lib2_albums(primary_artist_id,title,album_type) "
            "VALUES(1,'Creatures','album')").lastrowid
        conn.execute("INSERT INTO lib2_tracks(album_id,title,track_number) "
                     "VALUES(?,'Ghosts',1)", (album,))
        conn.commit()
        conn.close()

        spotify._mark_artist_albums_error(1)

        assert _status(spotify, 'spotify', 'album', 1) == 'error'
        assert _status(spotify, 'spotify', 'album', 2) == 'error'

    def test_a_failed_batch_leaves_a_settled_child_alone(self, spotify):
        """The bulk call was never about children the provider already matched."""
        spotify._update_album(1, SimpleNamespace(
            id='sp-album', name='Tohu Bohu', image_url=None, album_type='album',
            release_date=None, total_tracks=0))

        spotify._mark_artist_albums_not_found(1)

        assert _status(spotify, 'spotify', 'album', 1) == 'matched'

    def test_a_failed_track_batch_records_the_albums_tracks(self, spotify):
        spotify._mark_album_tracks_error(1)

        assert _status(spotify, 'spotify', 'track', 1) == 'error'


class TestWriting:
    def test_the_spotify_id_reaches_both_the_column_and_the_json(self, spotify):
        """The read paths join on the promoted column, so the JSON alone would
        leave them behind."""
        spotify._update_artist(1, SimpleNamespace(
            id='sp-artist', name='Rone', image_url='http://img',
            genres=['Electronic']))

        row = _row(spotify, 'lib2_artists')
        assert row['spotify_id'] == 'sp-artist'
        assert json.loads(row['external_ids'])['spotify'] == 'sp-artist'
        assert row['image_url'] == 'http://img'
        assert json.loads(row['genres']) == ['Electronic']

    def test_itunes_lives_in_the_json_only(self, itunes):
        itunes._update_artist(1, SimpleNamespace(
            id='it-artist', name='Rone', image_url=None, genres=[]))

        row = _row(itunes, 'lib2_artists')
        assert row['spotify_id'] is None
        assert json.loads(row['external_ids'])['itunes'] == 'it-artist'

    def test_a_full_release_date_is_stored_and_a_bare_year_is_not_faked(self, spotify):
        """#824: a YYYY-MM or YYYY-MM-DD date is worth keeping; a bare year is only
        good for the year column."""
        spotify._update_album(1, SimpleNamespace(
            id='sp-album', name='Tohu Bohu', image_url=None, album_type='album',
            release_date='2009-10-19', total_tracks=12))

        row = _row(spotify, 'lib2_albums')
        assert row['release_date'] == '2009-10-19'
        assert row['year'] == 2009
        assert row['expected_track_count'] == 12

    def test_a_bare_year_fills_only_the_year(self, spotify):
        spotify._update_album(1, SimpleNamespace(
            id='sp-album', name='T', image_url=None, album_type='album',
            release_date='2009', total_tracks=0))

        row = _row(spotify, 'lib2_albums')
        assert row['year'] == 2009
        assert row['release_date'] is None

    def test_the_album_type_goes_to_the_payload_not_the_column(self, spotify):
        """``lib2_albums.album_type`` always carries a classification the importer
        and MB reconcile own, so there is no empty state for a provider to fill."""
        spotify._update_album(1, SimpleNamespace(
            id='sp-album', name='T', image_url=None, album_type='single',
            release_date=None, total_tracks=0))

        row = _row(spotify, 'lib2_albums')
        assert json.loads(row['enrichment'])['spotify']['album_type'] == 'single'
        assert row['album_type'] == 'album'

    def test_a_chosen_image_is_never_replaced(self, spotify):
        conn = spotify.db._get_connection()
        conn.execute("UPDATE lib2_artists SET image_url='http://chosen' WHERE id=1")
        conn.commit()
        conn.close()

        spotify._update_artist(1, SimpleNamespace(
            id='sp-a', name='Rone', image_url='http://spotify', genres=[]))

        assert _row(spotify, 'lib2_artists')['image_url'] == 'http://chosen'

    def test_the_explicit_flag_is_backfilled_from_a_batch_track(self, spotify):
        spotify._update_track(1, {'id': 'sp-track', 'explicit': True})

        row = _row(spotify, 'lib2_tracks')
        assert row['explicit'] == 1
        assert json.loads(row['external_ids'])['spotify'] == 'sp-track'
        assert row['spotify_id'] == 'sp-track'

    def test_an_individual_search_result_stores_just_the_id(self, spotify):
        spotify._update_track_from_search(1, SimpleNamespace(id='sp-track'))

        assert _ids(spotify, 'lib2_tracks')['spotify'] == 'sp-track'
        assert _status(spotify, 'spotify', 'track', 1) == 'matched'


class TestTheGates:
    def test_a_claimed_spotify_id_is_refused(self, spotify):
        conn = spotify.db._get_connection()
        conn.execute("INSERT INTO lib2_artists(name, sort_name, spotify_id) "
                     "VALUES('Someone Else','Someone Else','sp-claimed')")
        conn.commit()
        conn.close()
        spotify.client = SimpleNamespace(
            search_artists=lambda *a, **k: [
                SimpleNamespace(id='sp-claimed', name='Rone', image_url=None,
                                genres=[])],
            get_artist_albums=lambda _id: [])

        spotify._process_artist({'type': 'artist', 'id': 1, 'name': 'Rone'})

        assert _status(spotify, 'spotify') == 'not_found'
        assert _row(spotify, 'lib2_artists')['spotify_id'] is None

    def test_a_stored_id_is_refreshed_instead_of_searched(self, spotify):
        conn = spotify.db._get_connection()
        conn.execute("UPDATE lib2_albums SET external_ids='{\"spotify\": \"sp-al\"}' "
                     "WHERE id=1")
        conn.commit()
        conn.close()

        def _no_search(*_a, **_k):
            raise AssertionError("must not search when an id is stored")

        # client.get_album returns a raw dict; _refresh_album_via_stored_id is the
        # adapter that shapes it for _update_album.
        spotify.client = SimpleNamespace(
            get_album=lambda sid: {
                'id': sid, 'name': 'Tohu Bohu', 'album_type': 'album',
                'images': [{'url': 'http://refreshed'}], 'total_tracks': 11},
            search_albums=_no_search)

        spotify._process_album_individual(
            {'type': 'album_individual', 'id': 1, 'name': 'Tohu Bohu',
             'artist': 'Rone'})

        assert _row(spotify, 'lib2_albums')['expected_track_count'] == 11


class TestCounting:
    def test_pending_and_progress_cover_all_three(self, spotify):
        assert spotify._count_pending_items() == 3
        assert set(spotify._get_progress_breakdown()) == {
            'artists', 'albums', 'tracks'}

    def test_stale_errors_are_retried(self, spotify):
        """Per-item failures recover after the shared retry window."""
        record_attempt_all = [('artist', 1), ('album', 1), ('track', 1)]
        conn = spotify.db._get_connection()
        for entity_type, entity_id in record_attempt_all:
            record_attempt(conn, entity_type=entity_type, entity_id=entity_id,
                           service='spotify', status='error')
        conn.execute("UPDATE lib2_provider_attempts "
                     "SET last_attempted_at=datetime('now','-90 days')")
        conn.commit()
        conn.close()

        assert spotify._get_next_item()['type'] == 'artist'


def test_neither_worker_holds_any_legacy_sql():
    import pathlib

    from tests.library2.legacy_usage import count_legacy_usage

    for path in ("core/spotify_worker.py", "core/itunes_worker.py"):
        usage = count_legacy_usage(pathlib.Path(path).read_text())
        assert (usage.reads, usage.writes) == (0, 0), path
