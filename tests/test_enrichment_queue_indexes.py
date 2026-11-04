"""Native port of the queue index regressions: no sorting an entire pending
catalogue to choose one item. Library v2 stores attempts in one shared ledger,
so the old 35 per-provider status indexes and their conversion do not exist.
"""
import pytest

from core.library2.provider_attempts import record_attempt
from core.library2.worker_queue import _pending_sql, _params
from database.music_database import MusicDatabase
from tests import lib2_seed


@pytest.fixture
def db(tmp_path):
    db = MusicDatabase(str(tmp_path / 'queue.db'))
    with db._get_connection() as conn:
        for i in range(50):
            lib2_seed.track(conn, 'Artist', 'Album', f'Song {i}')
        conn.commit()
    return db


@pytest.mark.parametrize('entity', ['artist', 'album', 'track'])
def test_pending_lookup_needs_no_sort(db, entity):
    with db._get_connection() as conn:
        sql = _pending_sql(entity) + ' LIMIT 1'
        plan = [r[-1] for r in conn.execute('EXPLAIN QUERY PLAN ' + sql, _params(entity, 'audiodb', 30))]
    assert not any('TEMP B-TREE' in step for step in plan), plan


def test_due_attempts_have_an_index_on_a_fresh_and_reopened_database(db):
    for database in (db, MusicDatabase(str(db.database_path))):
        with database._get_connection() as conn:
            columns = [r[2] for r in conn.execute('PRAGMA index_info(idx_lib2_provider_attempts_due)')]
        assert columns == ['entity_type', 'service', 'status', 'last_attempted_at']


def test_audiodb_worker_reads_native_queue_without_sorting(db, monkeypatch):
    from core.audiodb_worker import AudioDBWorker
    import core.library2.worker_queue as queue

    with db._get_connection() as conn:
        for entity in ('artist', 'album'):
            record_attempt(conn, entity_type=entity, entity_id=1, service='audiodb', status='matched')
        conn.commit()
    plans = []
    real_fetch = queue._fetch

    def fetch(conn, entity, *args, **kwargs):
        if entity == 'track':
            plans.extend(r[-1] for r in conn.execute(
                'EXPLAIN QUERY PLAN ' + _pending_sql(entity) + ' LIMIT 1', _params(entity, 'audiodb', 30)))
        return real_fetch(conn, entity, *args, **kwargs)

    monkeypatch.setattr(queue, '_fetch', fetch)
    monkeypatch.setattr('core.worker_utils.read_enrichment_priority', lambda _: None)
    worker = AudioDBWorker.__new__(AudioDBWorker)
    worker.db, worker.retry_days = db, 30
    item = worker._get_next_item()
    assert item and item['type'] == 'track' and item['id'] == 1
    assert plans and not any('TEMP B-TREE' in p for p in plans)
