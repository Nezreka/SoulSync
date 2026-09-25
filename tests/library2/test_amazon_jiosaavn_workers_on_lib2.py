"""The Amazon and JioSaavn workers write Library v2 (docs §32.3.1 stage 2).

These two are the same shape — search, then fetch detail, then store an id plus a
couple of stand-in fields — so they share a test file, with the differences called
out where they exist.

Both collapse three near-identical ``_update_*`` methods into one ``_write``. The
rule that survives the move: everything outside the provider's own namespace is
backfill. Amazon has no artist image endpoint and substitutes an album cover;
JioSaavn's artwork and label are likewise stand-ins. Neither may overwrite what a
better source or the user chose.

JioSaavn retries ``error`` as well as ``not_found`` and Amazon does not, and that
asymmetry is deliberate: issue #964 has JioSaavn marking a failed *detail* fetch
after a successful search match, which is transient and has to come back, while a
plain Amazon error is a provider problem that must not loop.
"""

from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

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
        "INSERT INTO lib2_artists(name, sort_name) VALUES('Arijit Singh',"
        "'Arijit Singh')").lastrowid
    album = conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id,title,album_type) "
        "VALUES(?,'Aashiqui 2','album')", (artist,)).lastrowid
    conn.execute("INSERT INTO lib2_tracks(album_id,title) VALUES(?,'Tum Hi Ho')",
                 (album,))
    conn.commit()
    conn.close()


@pytest.fixture
def jiosaavn(tmp_path):
    path = str(tmp_path / "js.db")
    _seed(path)

    from core.jiosaavn_worker import JioSaavnWorker

    worker = JioSaavnWorker.__new__(JioSaavnWorker)
    worker.db = _Db(path)
    worker.retry_days = 30
    worker.name_similarity_threshold = 0.80
    worker.stats = {'matched': 0, 'not_found': 0, 'pending': 0, 'errors': 0}
    return worker


@pytest.fixture
def amazon(tmp_path):
    path = str(tmp_path / "az.db")
    _seed(path)

    from core.amazon_worker import AmazonWorker

    worker = AmazonWorker.__new__(AmazonWorker)
    worker.db = _Db(path)
    worker.retry_days = 30
    worker.name_similarity_threshold = 0.80
    worker.stats = {'matched': 0, 'not_found': 0, 'pending': 0, 'errors': 0}
    worker._outage_streak = 0
    return worker


def _stub_client(worker, **methods):
    """JioSaavnWorker.client is a read-only property that resolves the registry
    client, so a stub has to replace the property on a throwaway subclass."""
    stub = SimpleNamespace(**methods)
    worker.__class__ = type(
        f"_Stubbed{type(worker).__name__}", (type(worker),),
        {"client": property(lambda _self: stub)})


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


class TestJioSaavn:
    def test_the_queue_runs_artist_then_album_then_track(self, jiosaavn):
        assert jiosaavn._get_next_item()['type'] == 'artist'
        jiosaavn._mark_status('artist', 1, 'matched')
        assert jiosaavn._get_next_item()['type'] == 'album'
        jiosaavn._mark_status('album', 1, 'matched')
        assert jiosaavn._get_next_item()['type'] == 'track'

    def test_an_artist_match_stores_the_id_and_backfills_art(self, jiosaavn):
        jiosaavn._update_artist(1, SimpleNamespace(
            id='7001', name='Arijit Singh', image_url='http://js/img'))

        assert _ids(jiosaavn, 'lib2_artists')['jiosaavn'] == '7001'
        assert _row(jiosaavn, 'lib2_artists')['image_url'] == 'http://js/img'
        assert _status(jiosaavn, 'jiosaavn') == 'matched'

    def test_a_chosen_image_is_not_replaced(self, jiosaavn):
        conn = jiosaavn.db._get_connection()
        conn.execute("UPDATE lib2_artists SET image_url='http://chosen' WHERE id=1")
        conn.commit()
        conn.close()

        jiosaavn._update_artist(1, SimpleNamespace(
            id='7001', name='A', image_url='http://js/img'))

        assert _row(jiosaavn, 'lib2_artists')['image_url'] == 'http://chosen'

    def test_an_album_stores_its_type_in_the_payload(self, jiosaavn):
        """``lib2_albums.album_type`` always carries a classification — the importer
        and MB reconcile own it — so there is no empty state for JioSaavn to fill.
        Its word goes in the payload instead of overwriting theirs."""
        jiosaavn._update_album(1, {
            'id': '8001', 'album_type': 'soundtrack', 'label': 'T-Series',
            'image_url': 'http://js/cover', 'total_tracks': 9,
        })

        row = _row(jiosaavn, 'lib2_albums')
        assert json.loads(row['enrichment'])['jiosaavn']['album_type'] == 'soundtrack'
        assert row['album_type'] == 'album', "the importer's classification stands"
        assert row['label'] == 'T-Series'
        assert row['expected_track_count'] == 9

    def test_a_stale_error_comes_back(self, jiosaavn):
        """Issue #964: a detail fetch that failed after a search match is marked
        rather than left unattempted, so the queue stops re-picking it every tick —
        but it has to become due again."""
        for entity_type in ('album', 'track'):
            jiosaavn._mark_status(entity_type, 1, 'matched')
        jiosaavn._mark_status('artist', 1, 'error')
        conn = jiosaavn.db._get_connection()
        conn.execute("UPDATE lib2_provider_attempts "
                     "SET last_attempted_at=datetime('now','-90 days')")
        conn.commit()
        conn.close()

        assert jiosaavn._get_next_item()['type'] == 'artist'

    def test_a_claimed_id_is_refused(self, jiosaavn):
        conn = jiosaavn.db._get_connection()
        conn.execute(
            "INSERT INTO lib2_artists(name, sort_name, external_ids) "
            "VALUES('Someone Else','Someone Else','{\"jiosaavn\": \"7001\"}')")
        conn.commit()
        conn.close()
        _stub_client(jiosaavn, search_artists=lambda *a, **k: [
            SimpleNamespace(id='7001', name='Arijit Singh')])

        jiosaavn._process_artist(1, 'Arijit Singh')

        assert _status(jiosaavn, 'jiosaavn') == 'not_found'
        assert 'jiosaavn' not in _ids(jiosaavn, 'lib2_artists')

    def test_a_stored_id_is_refreshed_instead_of_searched(self, jiosaavn):
        conn = jiosaavn.db._get_connection()
        conn.execute("UPDATE lib2_albums SET external_ids='{\"jiosaavn\": \"8001\"}' "
                     "WHERE id=1")
        conn.commit()
        conn.close()

        def _no_search(*_a, **_k):
            raise AssertionError("must not search when an id is stored")

        _stub_client(jiosaavn,
                     get_album=lambda jid: {'id': jid, 'label': 'Refreshed'},
                     search_albums=_no_search)

        jiosaavn._process_album(1, 'Aashiqui 2', 'Arijit Singh')

        assert _row(jiosaavn, 'lib2_albums')['label'] == 'Refreshed'

    def test_the_worker_holds_no_legacy_sql_at_all(self):
        import pathlib

        from tests.library2.legacy_usage import count_legacy_usage

        usage = count_legacy_usage(
            pathlib.Path("core/jiosaavn_worker.py").read_text())
        assert (usage.reads, usage.writes) == (0, 0)


class TestAmazon:
    def test_an_artist_match_stores_the_asin(self, amazon):
        amazon._update_artist(1, SimpleNamespace(
            id='B00ASIN', name='Arijit Singh', image_url='http://az/img'))

        assert _ids(amazon, 'lib2_artists')['amazon'] == 'B00ASIN'
        assert _row(amazon, 'lib2_artists')['image_url'] == 'http://az/img'
        assert _status(amazon, 'amazon') == 'matched'

    def test_an_album_cover_stands_in_for_a_missing_artist_image(self, amazon):
        """Amazon has no artist image endpoint."""
        amazon.client = SimpleNamespace(
            _get_artist_image_from_albums=lambda _asin: 'http://az/cover')

        amazon._update_artist(1, SimpleNamespace(
            id='B00ASIN', name='A', image_url=None))

        assert _row(amazon, 'lib2_artists')['image_url'] == 'http://az/cover'

    def test_a_failing_image_lookup_does_not_break_the_match(self, amazon):
        def _boom(_asin):
            raise RuntimeError("proxy down")

        amazon.client = SimpleNamespace(_get_artist_image_from_albums=_boom)

        amazon._update_artist(1, SimpleNamespace(
            id='B00ASIN', name='A', image_url=None))

        assert _ids(amazon, 'lib2_artists')['amazon'] == 'B00ASIN'

    def test_an_album_takes_its_track_total_from_either_shape(self, amazon):
        amazon._update_album(1, {
            'label': 'Sony', 'images': [{'url': 'http://az/cover'}],
            'tracks': {'total': 12},
        }, 'B00ALBUM')

        row = _row(amazon, 'lib2_albums')
        assert json.loads(row['external_ids'])['amazon'] == 'B00ALBUM'
        assert row['expected_track_count'] == 12
        assert row['label'] == 'Sony'

    def test_a_stale_error_is_retried(self, amazon):
        """Per-item failures recover after the shared retry window."""
        for entity_type in ('album', 'track'):
            amazon._mark_status(entity_type, 1, 'matched')
        amazon._mark_status('artist', 1, 'error')
        conn = amazon.db._get_connection()
        conn.execute("UPDATE lib2_provider_attempts "
                     "SET last_attempted_at=datetime('now','-90 days')")
        conn.commit()
        conn.close()

        assert amazon._get_next_item()['type'] == 'artist'

    def test_pending_and_progress_cover_all_three(self, amazon):
        assert amazon._count_pending_items() == 3
        assert set(amazon._get_progress_breakdown()) == {
            'artists', 'albums', 'tracks'}

    def test_the_worker_holds_no_legacy_sql_at_all(self):
        import pathlib

        from tests.library2.legacy_usage import count_legacy_usage

        usage = count_legacy_usage(pathlib.Path("core/amazon_worker.py").read_text())
        assert (usage.reads, usage.writes) == (0, 0)
