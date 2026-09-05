"""The Qobuz, Tidal and Deezer workers write Library v2 (docs §32.3.1 stage 2).

The last three flat-shaped provider workers, and the three that carried the most
awkward per-provider response handling: Qobuz returns artwork as a size-keyed dict
or a bare string, Tidal exposes it four different ways depending on which endpoint
answered, and all three wrap label and copyright as either a string or a named
object. Those shape-normalizing bits are extracted to named helpers rather than
inlined, because that is where the response variance actually lives.

Two shared decisions worth pinning:

* ``duration`` and ``copyright`` have no album-level column in lib2 — tracks carry
  both, albums never did — so Qobuz's and Tidal's album values go to the enrichment
  payload rather than inventing a column;
* the old code committed the provider id in one transaction and then backfilled in a
  second, so a bad value could not lose the match. write_provider_enrichment skips an
  empty value outright, which gets the same guarantee without the second commit.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from core.library2.provider_attempts import (
    attempt_state, ensure_provider_attempt_schema,
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
    conn.execute("INSERT INTO lib2_tracks(album_id,title) VALUES(?,'Bora')", (album,))
    conn.commit()
    conn.close()


def _make(cls, path):
    worker = cls.__new__(cls)
    worker.db = _Db(path)
    worker.retry_days = 30
    worker.name_similarity_threshold = 0.80
    worker.stats = {'matched': 0, 'not_found': 0, 'pending': 0, 'errors': 0}
    worker.client = None
    return worker


@pytest.fixture
def qobuz(tmp_path):
    from core.qobuz_worker import QobuzWorker

    path = str(tmp_path / "qb.db")
    _seed(path)
    return _make(QobuzWorker, path)


@pytest.fixture
def tidal(tmp_path):
    from core.tidal_worker import TidalWorker

    path = str(tmp_path / "td.db")
    _seed(path)
    return _make(TidalWorker, path)


@pytest.fixture
def deezer(tmp_path):
    from core.deezer_worker import DeezerWorker

    path = str(tmp_path / "dz.db")
    _seed(path)
    return _make(DeezerWorker, path)


def _row(worker, table, entity_id=1):
    conn = worker.db._get_connection()
    try:
        return conn.execute(
            f"SELECT * FROM {table} WHERE id=?", (entity_id,)).fetchone()
    finally:
        conn.close()


def _payload(worker, table, service, entity_id=1):
    return json.loads(_row(worker, table, entity_id)['enrichment']).get(service, {})


def _status(worker, service, entity_type='artist', entity_id=1):
    conn = worker.db._get_connection()
    try:
        return attempt_state(conn, entity_type=entity_type, entity_id=entity_id
                             ).get(service, {}).get('status')
    finally:
        conn.close()


class TestTheSharedQueue:
    @pytest.mark.parametrize("name", ["qobuz", "tidal", "deezer"])
    def test_the_queue_runs_artist_then_album_then_track(self, request, name):
        worker = request.getfixturevalue(name)

        assert worker._get_next_item()['type'] == 'artist'
        worker._mark_status('artist', 1, 'matched')
        assert worker._get_next_item()['type'] == 'album'
        worker._mark_status('album', 1, 'matched')
        assert worker._get_next_item()['type'] == 'track'

    @pytest.mark.parametrize("name", ["qobuz", "tidal", "deezer"])
    def test_a_child_item_carries_its_artists_provider_id(self, request, name):
        """_verify_artist_id compares a child's result against it, so a
        collaboration cannot stamp the wrong id onto our artist."""
        worker = request.getfixturevalue(name)
        worker._update_artist(1, {'id': 'p-artist', 'name': 'Rone'})

        item = worker._get_next_item()

        assert item['type'] == 'album'
        assert item[f'artist_{name}_id'] == 'p-artist'

    @pytest.mark.parametrize("name", ["qobuz", "tidal", "deezer"])
    def test_pending_and_progress_cover_all_three(self, request, name):
        worker = request.getfixturevalue(name)

        assert worker._count_pending_items() == 3
        assert set(worker._get_progress_breakdown()) == {
            'artists', 'albums', 'tracks'}


class TestQobuzResponseShapes:
    def test_a_size_keyed_image_dict_takes_the_largest(self, qobuz):
        qobuz._update_artist(1, {'id': 1, 'image': {
            'small': 'http://s', 'medium': 'http://m', 'large': 'http://l'}})

        assert _row(qobuz, 'lib2_artists')['image_url'] == 'http://l'

    def test_a_bare_image_string_works_too(self, qobuz):
        qobuz._update_artist(1, {'id': 1, 'image': 'http://plain'})

        assert _row(qobuz, 'lib2_artists')['image_url'] == 'http://plain'

    def test_the_picture_field_is_the_last_resort(self, qobuz):
        qobuz._update_artist(1, {'id': 1, 'image': {}, 'picture': 'http://pic'})

        assert _row(qobuz, 'lib2_artists')['image_url'] == 'http://pic'

    def test_album_detail_lands_where_lib2_has_a_column(self, qobuz):
        qobuz._update_album(1, {'id': 'qb-al'}, {
            'id': 'qb-al', 'label': {'name': 'InFiné'}, 'upc': 123456,
            'parental_warning': True, 'tracks_count': 12,
            'genre': {'name': 'Electronic'},
            'image': {'large': 'http://cover'},
            'duration': 3600, 'copyright': {'text': '(C) InFiné'},
        })

        row = _row(qobuz, 'lib2_albums')
        assert row['label'] == 'InFiné'
        assert row['upc'] == '123456'
        assert row['explicit'] == 1
        assert row['track_count'] == 12
        assert json.loads(row['genres']) == ['Electronic']
        assert row['image_url'] == 'http://cover'
        # No album-level duration or copyright column exists in lib2.
        payload = json.loads(row['enrichment'])['qobuz']
        assert payload['duration_ms'] == 3600000
        assert payload['copyright'] == '(C) InFiné'

    def test_track_detail_uses_the_columns_tracks_do_have(self, qobuz):
        qobuz._update_track(1, {'id': 'qb-tr'}, {
            'id': 'qb-tr', 'parental_warning': False, 'isrc': 'FR1234500001',
            'duration': 210, 'copyright': '(C) InFiné',
        })

        row = _row(qobuz, 'lib2_tracks')
        assert row['explicit'] == 0
        assert row['isrc'] == 'FR1234500001'
        assert row['duration'] == 210000
        assert row['copyright'] == '(C) InFiné'

    def test_the_match_survives_an_unusable_backfill_value(self, qobuz):
        """The old code committed the id first and backfilled second for exactly
        this; an empty value is now simply skipped."""
        qobuz._update_album(1, {'id': 'qb-al'}, {'id': 'qb-al', 'label': None,
                                                 'upc': '', 'genre': {}})

        assert json.loads(
            _row(qobuz, 'lib2_albums')['external_ids'])['qobuz'] == 'qb-al'
        assert _status(qobuz, 'qobuz', 'album', 1) == 'matched'


class TestTidalResponseShapes:
    def test_a_sized_picture_array_prefers_the_biggest(self, tidal):
        tidal._update_artist(1, {'id': 1}, {'picture': [
            {'url': 'http://cdn/320x320.jpg'},
            {'url': 'http://cdn/1080x1080.jpg'}]})

        assert _row(tidal, 'lib2_artists')['image_url'] == 'http://cdn/1080x1080.jpg'

    def test_an_unsized_array_falls_back_to_the_first(self, tidal):
        tidal._update_artist(1, {'id': 1}, {'picture': [{'url': 'http://only'}]})

        assert _row(tidal, 'lib2_artists')['image_url'] == 'http://only'

    def test_json_api_image_links_are_read(self, tidal):
        tidal._update_artist(1, {'id': 1}, {
            'picture': [], 'imageLinks': [{'href': 'http://jsonapi'}]})

        assert _row(tidal, 'lib2_artists')['image_url'] == 'http://jsonapi'

    def test_the_search_result_is_the_last_resort(self, tidal):
        tidal._update_artist(1, {'id': 1, 'image': 'http://search'}, None)

        assert _row(tidal, 'lib2_artists')['image_url'] == 'http://search'

    def test_an_iso_duration_becomes_milliseconds(self, tidal):
        tidal._update_track(1, {'id': 'td-tr'}, {
            'id': 'td-tr', 'duration': 'PT3M36S', 'isrc': 'FR1234500001'})

        assert _row(tidal, 'lib2_tracks')['duration'] == 216000

    def test_the_barcode_id_stands_in_for_upc(self, tidal):
        tidal._update_album(1, {'id': 'td-al'}, {
            'id': 'td-al', 'barcodeId': '00602', 'numberOfItems': 9,
            'label': 'InFiné', 'explicit': False, 'cover': 'http://cover'})

        row = _row(tidal, 'lib2_albums')
        assert row['upc'] == '00602'
        assert row['track_count'] == 9
        assert row['label'] == 'InFiné'
        assert row['explicit'] == 0


class TestDeezer:
    def test_the_artist_picture_is_backfilled(self, deezer):
        deezer._update_artist(1, {'id': 42, 'picture_xl': 'http://dz/xl'})

        row = _row(deezer, 'lib2_artists')
        assert row['image_url'] == 'http://dz/xl'
        assert json.loads(row['external_ids'])['deezer'] == '42'

    def test_album_genres_come_from_the_detail_fetch(self, deezer):
        deezer._update_album(1, {'id': 7, 'cover_xl': 'http://dz/cover',
                                 'nb_tracks': 12}, {
            'id': 7, 'label': 'InFiné', 'explicit_lyrics': True,
            'record_type': 'album',
            'genres': {'data': [{'name': 'Electronic'}]}, 'nb_tracks': 12})

        row = _row(deezer, 'lib2_albums')
        assert row['label'] == 'InFiné'
        assert row['explicit'] == 1
        assert json.loads(row['genres']) == ['Electronic']
        assert row['image_url'] == 'http://dz/cover'
        assert row['expected_track_count'] == 12

    def test_the_record_type_goes_to_the_payload(self, deezer):
        """lib2_albums.album_type always carries a classification the importer and
        MB reconcile own, so there is no empty state for a provider to fill."""
        deezer._update_album(1, {'id': 7}, {'id': 7, 'record_type': 'ep'})

        assert _payload(deezer, 'lib2_albums', 'deezer')['record_type'] == 'ep'
        assert _row(deezer, 'lib2_albums')['album_type'] == 'album'

    def test_bpm_only_arrives_from_the_detail_fetch(self, deezer):
        deezer._update_track(1, {'id': 3}, {'id': 3, 'bpm': 128.5,
                                            'explicit_lyrics': False})

        row = _row(deezer, 'lib2_tracks')
        assert row['bpm'] == 128.5
        assert row['explicit'] == 0

    def test_a_search_only_result_stores_no_bpm(self, deezer):
        deezer._update_track(1, {'id': 3, 'bpm': 999}, None)

        assert _row(deezer, 'lib2_tracks')['bpm'] is None


class TestNoBackfillOverwrites:
    @pytest.mark.parametrize("name", ["qobuz", "tidal", "deezer"])
    def test_a_chosen_image_is_never_replaced(self, request, name):
        worker = request.getfixturevalue(name)
        conn = worker.db._get_connection()
        conn.execute("UPDATE lib2_artists SET image_url='http://chosen' WHERE id=1")
        conn.commit()
        conn.close()

        worker._update_artist(1, {'id': 1, 'image': 'http://provider',
                                  'picture_xl': 'http://provider'})

        assert _row(worker, 'lib2_artists')['image_url'] == 'http://chosen'


def test_none_of_the_three_holds_any_legacy_sql():
    import pathlib

    from tests.library2.legacy_usage import count_legacy_usage

    for path in ("core/qobuz_worker.py", "core/tidal_worker.py",
                 "core/deezer_worker.py"):
        usage = count_legacy_usage(pathlib.Path(path).read_text())
        assert (usage.reads, usage.writes) == (0, 0), path
