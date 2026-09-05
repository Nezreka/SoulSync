"""The Discogs worker writes Library v2 (docs §32.3.1 stage 2, third worker).

Discogs is the first conversion to lean on the shared helpers in
``core.library2.worker_support``: the artist-match acceptance gate (an id already
held by a differently-named artist is refused) and the expected-track-count cache
the Album Completeness repair job reads. Artist+album only — Discogs has no track
endpoint.
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


class _Result:
    def __init__(self, rid, name):
        self.id = rid
        self.name = name


@pytest.fixture
def worker(tmp_path, monkeypatch):
    path = str(tmp_path / "lib2.db")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    ensure_library_v2_schema(conn)
    own_every_track(conn)
    ensure_provider_attempt_schema(conn.cursor())
    artist = conn.execute(
        "INSERT INTO lib2_artists(name, sort_name) VALUES('Rone','Rone')"
    ).lastrowid
    album = conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id,title,album_type) "
        "VALUES(?,'Tohu Bohu','album')", (artist,)).lastrowid
    # Owned, so the queue offers it — `own_every_track` gives the track its file.
    conn.execute("INSERT INTO lib2_tracks(album_id,title) VALUES(?,'Bora')", (album,))
    conn.commit()
    conn.close()

    from core.discogs_worker import DiscogsWorker

    instance = DiscogsWorker.__new__(DiscogsWorker)
    instance.db = _Db(path)
    instance.retry_days = 30
    instance.name_similarity_threshold = 0.80
    instance.stats = {'matched': 0, 'not_found': 0, 'pending': 0, 'errors': 0}
    instance.client = None
    return instance


def _row(worker, table, entity_id=1):
    conn = worker.db._get_connection()
    try:
        return conn.execute(f"SELECT * FROM {table} WHERE id=?", (entity_id,)).fetchone()
    finally:
        conn.close()


def test_tracks_are_never_offered(worker):
    """Discogs has no track endpoint; offering tracks would mark each one
    not_found and count it as progress."""
    worker._mark_status('artist', 1, 'matched')

    item = worker._get_next_item()

    assert item['type'] == 'album'
    assert item['artist'] == 'Rone'

    worker._mark_status('album', 1, 'matched')
    assert worker._get_next_item() is None


def test_artist_detail_lands_in_enrichment_and_backfills(worker):
    worker._update_artist(1, {
        'id': 12345,
        'profile': 'A French electronic musician.',
        'members': [{'name': 'Erwan Castex'}],
        'urls': ['https://rone.fr'],
        'images': [{'type': 'primary', 'uri': 'http://img/discogs'}],
    })

    row = _row(worker, 'lib2_artists')
    payload = json.loads(row['enrichment'])['discogs']
    assert payload['bio'] == 'A French electronic musician.'
    assert payload['members'] == ['Erwan Castex']
    assert payload['urls'] == ['https://rone.fr']
    assert json.loads(row['external_ids'])['discogs'] == '12345'
    assert row['summary'] == 'A French electronic musician.', 'empty column backfilled'
    assert row['image_url'] == 'http://img/discogs'


def test_a_chosen_image_is_not_overwritten(worker):
    conn = worker.db._get_connection()
    conn.execute("UPDATE lib2_artists SET image_url='http://chosen' WHERE id=1")
    conn.commit()
    conn.close()

    worker._update_artist(1, {
        'id': 1, 'profile': '', 'images': [{'uri': 'http://discogs'}]})

    assert _row(worker, 'lib2_artists')['image_url'] == 'http://chosen'


def test_album_release_detail_lands_in_enrichment(worker):
    worker._update_album(1, {
        'id': 999,
        'genres': ['Electronic'],
        'styles': ['Downtempo'],
        'labels': [{'name': 'InFiné', 'catno': 'IF1018'}],
        'country': 'France',
        'community': {'rating': {'average': 4.3, 'count': 57}},
        'tracklist': [{'title': 'A', 'type_': 'track'},
                      {'title': 'B', 'type_': ''},
                      {'title': 'Side One', 'type_': 'heading'}],
    })

    row = _row(worker, 'lib2_albums')
    payload = json.loads(row['enrichment'])['discogs']
    assert payload['genres'] == ['Electronic']
    assert payload['styles'] == ['Downtempo']
    assert payload['label'] == 'InFiné'
    assert payload['catno'] == 'IF1018'
    assert payload['country'] == 'France'
    assert payload['rating'] == 4.3
    assert payload['rating_count'] == 57
    assert row['expected_track_count'] == 2, 'headings are not tracks'


def test_a_claimed_provider_id_is_refused(worker):
    """The acceptance gate: one Discogs id smeared across unrelated artists is
    the bug this exists to stop."""
    conn = worker.db._get_connection()
    conn.execute(
        "INSERT INTO lib2_artists(name, sort_name, external_ids) "
        "VALUES('Rone Jazz Trio','Rone Jazz Trio','{\"discogs\": \"12345\"}')")
    conn.commit()
    conn.close()

    class _Client:
        @staticmethod
        def search_artists(_name, limit=5):
            return [_Result(12345, 'Rone')]

        @staticmethod
        def _fetch_and_cache_artist(_rid):
            raise AssertionError("must not fetch a claimed id")

    worker.client = _Client()
    worker._search_and_match_artist(1, 'Rone')

    conn = worker.db._get_connection()
    try:
        state = attempt_state(conn, entity_type='artist', entity_id=1)
    finally:
        conn.close()
    assert state['discogs']['status'] == 'not_found'
    assert json.loads(_row(worker, 'lib2_artists')['external_ids']) == {}


def test_a_stored_id_is_refreshed_instead_of_searched(worker):
    """Issue #501: a manual match must not be searched over."""
    conn = worker.db._get_connection()
    conn.execute(
        "UPDATE lib2_artists SET external_ids='{\"discogs\": \"777\"}' WHERE id=1")
    conn.commit()
    conn.close()

    class _Client:
        @staticmethod
        def _fetch_and_cache_artist(rid):
            assert rid == '777'
            return {'id': 777, 'profile': 'refreshed'}

        @staticmethod
        def search_artists(*_a, **_k):
            raise AssertionError("must not search when an id is stored")

    worker.client = _Client()
    worker._process_item({'type': 'artist', 'id': 1, 'name': 'Rone'})

    payload = json.loads(_row(worker, 'lib2_artists')['enrichment'])['discogs']
    assert payload['bio'] == 'refreshed'


def test_pending_counts_artists_and_albums_only(worker):
    assert worker._count_pending_items() == 2

    worker._mark_status('artist', 1, 'matched')

    assert worker._count_pending_items() == 1


def test_the_worker_holds_no_legacy_sql_at_all():
    import pathlib

    from tests.library2.legacy_usage import count_legacy_usage

    usage = count_legacy_usage(pathlib.Path("core/discogs_worker.py").read_text())

    assert (usage.reads, usage.writes) == (0, 0)
