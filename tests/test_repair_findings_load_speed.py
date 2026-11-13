"""the Tools page opens on index reads, not on scans of the findings table.

197k findings (410 MB of json) took 15 seconds to show: counts by type, the
job list per type, the hand-set count and the tab badge each read every row,
and the album shelf parsed every open row's details.
"""

from __future__ import annotations

import json

import pytest

from core.repair_worker import RepairWorker


@pytest.fixture()
def worker(tmp_path):
    from database.music_database import MusicDatabase
    w = RepairWorker.__new__(RepairWorker)
    w.db = MusicDatabase(database_path=str(tmp_path / "m.db"))
    return w


def _add(worker, album, status='pending'):
    conn = worker.db._get_connection()
    conn.execute(
        "INSERT INTO repair_findings (job_id, finding_type, severity, status, file_path, title, details_json)"
        " VALUES ('quality_upgrade','quality_upgrade','info',?,?,?,?)",
        (status, f'/m/{album}.mp3', album,
         json.dumps({'expected_artist': 'Band', 'album_title': album, 'current_format': 'mp3',
                     'current_bitrate': 128})))
    conn.commit()
    conn.close()


def _plan(worker, query):
    conn = worker.db._get_connection()
    try:
        return ' | '.join(str(row[3]) for row in conn.execute('EXPLAIN QUERY PLAN ' + query))
    finally:
        conn.close()


@pytest.mark.parametrize('query', [
    # get_finding_groups: the counts, and which job feeds each type
    "SELECT finding_type, status, severity, COUNT(*), MAX(created_at) FROM repair_findings "
    "WHERE 1 GROUP BY finding_type, status, severity",
    "SELECT DISTINCT finding_type, job_id FROM repair_findings WHERE 1",
    # get_findings_counts: the tab badge
    "SELECT job_id, finding_type, severity, COUNT(*) FROM repair_findings "
    "WHERE status = 'pending' AND 1 GROUP BY job_id, finding_type, severity",
])
def test_the_landing_counts_never_read_a_table_row(worker, query):
    assert 'COVERING INDEX' in _plan(worker, query)


def test_the_hand_set_count_reads_only_the_rows_it_counts(worker):
    conn = worker.db._get_connection()
    for index, manual in enumerate([True, True, False]):
        conn.execute(
            "INSERT INTO repair_findings (job_id, finding_type, severity, status, file_path, title, details_json)"
            " VALUES ('library_retag','library_retag','info','pending',?,?,?)",
            (f'/m/{index}.flac', str(index), json.dumps({'has_manual_conflict': manual})))
    conn.commit()
    conn.close()
    seen = []
    real = worker.db._get_connection

    class _Spy:
        def __init__(self, inner):
            self._inner = inner

        def cursor(self):
            cursor = self._inner.cursor()
            execute = cursor.execute

            class _Cursor:
                def execute(_self, sql, *args):
                    seen.append(sql)
                    return execute(sql, *args)

                def __getattr__(_self, name):
                    return getattr(cursor, name)

            return _Cursor()

        def __getattr__(self, name):
            return getattr(self._inner, name)

    worker.db._get_connection = lambda: _Spy(real())
    groups = {g['finding_type']: g for g in worker.get_finding_groups()}
    assert groups['library_retag']['manual_conflicts'] == 2
    asked = [sql for sql in seen if 'has_manual_conflict' in sql]
    # one query, and it named the index: the plain one never had to run
    assert len(asked) == 1 and 'INDEXED BY idx_rf_manual_conflict' in asked[0]


def test_the_album_grouping_is_reused_until_a_finding_changes(worker, monkeypatch):
    _add(worker, 'One')
    calls = []
    real = worker._group_finding_albums
    monkeypatch.setattr(worker, '_group_finding_albums',
                        lambda **kw: calls.append(kw) or real(**kw))
    first = worker.get_finding_albums(group_by='album')
    again = worker.get_finding_albums(group_by='album')
    assert [g['album'] for g in first] == [g['album'] for g in again] == ['One']
    assert len(calls) == 1
    # a caller that edits its answer does not edit the next caller's
    again[0]['album'] = 'changed'
    assert worker.get_finding_albums(group_by='album')[0]['album'] == 'One'
    # another question is another answer
    worker.get_finding_albums(group_by='artist')
    assert len(calls) == 2
    # a new finding, and a status change, both show at once
    _add(worker, 'Two')
    assert {g['album'] for g in worker.get_finding_albums(group_by='album')} == {'One', 'Two'}
    conn = worker.db._get_connection()
    conn.execute("UPDATE repair_findings SET status='dismissed' WHERE title='One'")
    conn.commit()
    conn.close()
    assert [g['album'] for g in worker.get_finding_albums(group_by='album')] == ['Two']


def test_an_old_answer_is_dropped(worker, monkeypatch):
    _add(worker, 'One')
    worker.get_finding_albums(group_by='album')
    calls = []
    real = worker._group_finding_albums
    monkeypatch.setattr(worker, '_group_finding_albums',
                        lambda **kw: calls.append(kw) or real(**kw))
    monkeypatch.setattr(RepairWorker, '_ALBUMS_CACHE_SECONDS', 0.0)
    worker.get_finding_albums(group_by='album')
    assert len(calls) == 1
