"""Automatic grabs keep why they took what they took (download_decisions)."""

from __future__ import annotations

import pytest

from core.download_plugins.types import TrackResult
from core.downloads import decision_log
from core.downloads import task_worker as tw
from core.downloads.candidate_pool import summarize_pool
from core.downloads.decisions import accept, reject
from core.downloads.track_detail import build_track_detail
from core.runtime_state import download_batches, download_tasks
from database.music_database import MusicDatabase


@pytest.fixture(autouse=True)
def reset_state():
    download_tasks.clear()
    download_batches.clear()
    yield
    download_tasks.clear()
    download_batches.clear()


@pytest.fixture
def db(tmp_path, monkeypatch):
    database = MusicDatabase(str(tmp_path / "m.db"))
    monkeypatch.setattr(decision_log, '_database', lambda: database)
    return database


def _hit(username='peer', filename='A/Song.flac', **over):
    base = dict(username=username, filename=filename, size=30_000_000, bitrate=1411,
                duration=200_000, quality='flac', free_upload_slots=1, upload_speed=1,
                queue_length=0, artist='Artist', title='Song')
    base.update(over)
    return TrackResult(**base)


# ---------------------------------------------------------------------------
# summarize_pool
# ---------------------------------------------------------------------------

def test_summary_names_the_winner_and_ranks_the_rest():
    win, second, live, weak = _hit('a'), _hit('b'), _hit('c'), _hit('d')
    s = summarize_pool(
        [(win, accept(0.95)), (second, accept(0.9)),
         (live, reject('version_conflict', 'live', 0.3)), (weak, reject('match_weak', '', 0.5))],
        chosen_key=('a', 'A/Song.flac'),
    )
    assert s['chosen']['username'] == 'a'
    assert s['chosen']['display_name'] == 'Song.flac'
    assert [a['username'] for a in s['alternatives']] == ['b', 'd', 'c']
    assert s['accepted_total'] == 2
    assert s['rejected_total'] == 2
    assert s['rejected_counts'] == {'version_conflict': 1, 'match_weak': 1}


def test_summary_keeps_the_real_source_name():
    s = summarize_pool([(_hit('tidal', 'tid||1'), accept(0.9)),
                        (_hit('torrent', 't||1'), accept(0.8)),
                        (_hit('some_peer'), accept(0.7))])
    assert [a['source_service'] for a in s['alternatives']] == ['tidal', 'torrent', 'soulseek']


def test_summary_caps_alternatives_but_counts_everything():
    pairs = [(_hit(str(i)), reject('match_weak', score=i / 100)) for i in range(40)]
    s = summarize_pool(pairs, limit=10)
    assert len(s['alternatives']) == 10
    assert s['rejected_total'] == 40
    assert s['chosen'] is None


def test_summary_dedupes_the_same_file_from_two_queries():
    s = summarize_pool([(_hit('a'), reject('match_weak', score=0.2)), (_hit('a'), accept(0.9))])
    assert s['accepted_total'] == 1 and s['rejected_total'] == 0


# ---------------------------------------------------------------------------
# the table
# ---------------------------------------------------------------------------

def _summary(**over):
    base = {'chosen': {'username': 'a', 'display_name': 'x.flac'}, 'alternatives': [],
            'accepted_total': 1, 'rejected_total': 3, 'rejected_counts': {'preview': 3}}
    base.update(over)
    return base


def test_record_and_read_back(db):
    row_id = db.record_download_decision('t1', outcome='chosen', summary=_summary(),
                                         track_title='Song', track_artist='Artist',
                                         quality_profile_id=2)
    got = db.get_download_decision('t1')
    assert got['id'] == row_id
    assert got['outcome'] == 'chosen'
    assert got['chosen'] == {'username': 'a', 'display_name': 'x.flac'}
    assert got['rejected_counts'] == {'preview': 3}
    assert (got['track_title'], got['track_artist'], got['quality_profile_id']) == ('Song', 'Artist', 2)


def test_a_task_has_one_answer(db):
    db.record_download_decision('t1', outcome='nothing_passed', summary=_summary(chosen=None))
    db.record_download_decision('t1', outcome='chosen', summary=_summary())
    conn = db._get_connection()
    try:
        n = conn.execute("SELECT COUNT(*) FROM download_decisions WHERE task_key='t1'").fetchone()[0]
    finally:
        conn.close()
    assert n == 1
    assert db.get_download_decision('t1')['outcome'] == 'chosen'


def test_link_to_the_downloaded_file(db):
    db.record_download_decision('t1', outcome='chosen', summary=_summary())
    assert db.link_download_decision('t1', 77) is True
    assert db.get_download_decision_for_track_download(77)['task_key'] == 't1'
    assert db.link_download_decision('missing', 78) is False


def test_table_keeps_only_the_newest_rows(db, monkeypatch):
    monkeypatch.setattr(MusicDatabase, 'DOWNLOAD_DECISIONS_KEPT', 3)
    for i in range(6):
        db.record_download_decision(f't{i}', outcome='chosen', summary=_summary())
    assert db.get_download_decision('t0') is None
    assert db.get_download_decision('t2') is None
    assert db.get_download_decision('t5') is not None


def test_clearing_download_history_clears_decisions(db):
    db.record_download_decision('t1', outcome='chosen', summary=_summary())
    db.clear_completed_download_history()
    assert db.get_download_decision('t1') is None


def test_missing_key_and_bad_json_are_harmless(db):
    assert db.record_download_decision('', outcome='chosen', summary=_summary()) is None
    assert db.get_download_decision('') is None
    conn = db._get_connection()
    try:
        conn.execute("INSERT INTO download_decisions (task_key, outcome, chosen_json, alternatives_json) "
                     "VALUES ('bad', 'chosen', '{nope', 'also nope')")
        conn.commit()
    finally:
        conn.close()
    got = db.get_download_decision('bad')
    assert got['chosen'] is None and got['alternatives'] == []


# ---------------------------------------------------------------------------
# decision_log
# ---------------------------------------------------------------------------

class _Track:
    name = 'Song'
    artists = ['Artist']


def test_record_chosen_uses_the_file_the_task_took(db):
    download_tasks['t1'] = {'username': 'b', 'filename': 'A/Song.flac'}
    decision_log.record('t1', [(_hit('a'), accept(0.95)), (_hit('b'), accept(0.9))],
                        outcome='chosen', track=_Track())
    assert download_tasks['t1']['decision_summary']['chosen']['username'] == 'b'
    stored = db.get_download_decision('t1')
    assert stored['chosen']['username'] == 'b'
    assert stored['alternatives'][0]['username'] == 'a'
    assert (stored['track_title'], stored['track_artist']) == ('Song', 'Artist')


def test_winner_outside_the_pool_still_gets_named(db):
    download_tasks['t1'] = {'username': 'youtube', 'filename': 'yt||abc||Song'}
    decision_log.record('t1', [], outcome='chosen', track=_Track())
    assert db.get_download_decision('t1')['chosen']['username'] == 'youtube'


def test_record_rejects_unknown_outcomes():
    with pytest.raises(ValueError):
        decision_log.record('t1', [], outcome='vibes')


def test_a_broken_database_never_raises(monkeypatch):
    class _Broken:
        def record_download_decision(self, *a, **kw):
            raise RuntimeError('disk full')

    download_tasks['t1'] = {}
    monkeypatch.setattr(decision_log, '_database', lambda: _Broken())
    assert decision_log.record('t1', [(_hit(), reject('preview'))], outcome='nothing_passed') is None


def test_partial_retry_pool_merges_instead_of_replacing(db):
    download_tasks['t1'] = {'username': 'a', 'filename': 'A/Song.flac'}
    decision_log.record('t1', [(_hit('a'), accept(0.95)), (_hit('z'), reject('preview'))],
                        outcome='chosen')
    # 'a' got quarantined; the retry's unsearched query found nothing usable.
    decision_log.record('t1', [(_hit('q'), reject('match_weak', score=0.3))],
                        outcome='nothing_passed', merge=True)
    stored = db.get_download_decision('t1')
    assert stored['outcome'] == 'nothing_passed'
    assert stored['chosen'] is None
    assert stored['rejected_counts'] == {'preview': 1, 'match_weak': 1}
    assert stored['rejected_total'] == 2
    # the file it tried first is still on record, at the top
    assert stored['alternatives'][0]['username'] == 'a'


def test_a_full_research_replaces(db):
    download_tasks['t1'] = {}
    decision_log.record('t1', [(_hit('z'), reject('preview'))], outcome='nothing_passed')
    decision_log.record('t1', [(_hit('q'), reject('match_weak'))], outcome='nothing_passed')
    assert db.get_download_decision('t1')['rejected_counts'] == {'match_weak': 1}


def test_retry_winner_moves_chosen_to_the_new_file(db):
    download_tasks['t1'] = {'username': 'a', 'filename': 'A/Song.flac',
                            'track_info': {'name': 'Song', 'artists': [{'name': 'Artist'}]}}
    decision_log.record('t1', [(_hit('a'), accept(0.95)), (_hit('b'), accept(0.9))],
                        outcome='chosen')
    download_tasks['t1'].update(username='b', filename='A/Song.flac')
    decision_log.note_retry_winner('t1')
    stored = db.get_download_decision('t1')
    assert stored['chosen']['username'] == 'b'
    assert stored['alternatives'][0]['username'] == 'a'
    assert stored['track_artist'] == 'Artist'


# ---------------------------------------------------------------------------
# the worker
# ---------------------------------------------------------------------------

class _Client:
    mode = 'soulseek'

    def __init__(self, results):
        self._results = results

    def client(self, _name):
        return None

    async def search(self, query, **_kw):
        return list(self._results), None


class _Engine:
    def generate_download_queries(self, _track):
        return ['Artist Song']

    @staticmethod
    def _title_is_distinctive_enough_to_broadcast(_title):
        return True


def _sync(coro):
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _deps(results, evaluate, attempt, **over):
    return tw.TaskWorkerDeps(
        download_orchestrator=_Client(results), matching_engine=_Engine(), run_async=_sync,
        try_source_reuse=lambda *a, **k: False, store_batch_source=lambda *a, **k: None,
        try_staging_match=lambda *a, **k: False,
        get_valid_candidates=lambda *a, **k: pytest.fail('evaluate should be used'),
        attempt_download_with_candidates=attempt,
        on_download_completed=lambda *a, **k: None, recover_worker_slot=lambda *a, **k: None,
        evaluate_candidates=evaluate, **over,
    )


def _seed():
    download_tasks['t1'] = {'status': 'pending', 'track_info': {
        'id': 'x', 'name': 'Song', 'artists': ['Artist'], 'album': 'LP', 'duration_ms': 200_000}}


def test_worker_records_the_winner(db):
    _seed()
    good, live = _hit('a'), _hit('b')

    def evaluate(results, *_a):
        return [(good, accept(0.95)), (live, reject('version_conflict', 'live', 0.2))]

    def attempt(task_id, candidates, *_a, **_k):
        assert candidates == [good]  # only accepted rows reach the download walk
        download_tasks[task_id].update(username='a', filename='A/Song.flac')
        return True

    tw.download_track_worker('t1', 'b1', _deps([good, live], evaluate, attempt))
    stored = db.get_download_decision('t1')
    assert stored['outcome'] == 'chosen'
    assert stored['chosen']['username'] == 'a'
    assert stored['rejected_counts'] == {'version_conflict': 1}


def test_worker_records_nothing_passed(db):
    _seed()

    def evaluate(results, *_a):
        return [(r, reject('duration_mismatch', 'x')) for r in results]

    tw.download_track_worker('t1', 'b1', _deps([_hit('a'), _hit('b')], evaluate,
                                               lambda *a, **k: pytest.fail('nothing to try')))
    assert download_tasks['t1']['status'] == 'not_found'
    stored = db.get_download_decision('t1')
    assert stored['outcome'] == 'nothing_passed'
    assert stored['rejected_counts'] == {'duration_mismatch': 2}
    assert download_tasks['t1']['decision_summary']['outcome'] == 'nothing_passed'


def test_worker_records_download_failed_when_hits_passed_but_nothing_started(db):
    _seed()

    def evaluate(results, *_a):
        return [(results[0], accept(0.9))]

    tw.download_track_worker('t1', 'b1', _deps([_hit('a')], evaluate, lambda *a, **k: False))
    assert db.get_download_decision('t1')['outcome'] == 'download_failed'


def test_worker_without_evaluate_records_nothing(db):
    _seed()
    deps = _deps([_hit('a')], None, lambda *a, **k: False)
    deps.get_valid_candidates = lambda *a, **k: []
    tw.download_track_worker('t1', 'b1', deps)
    assert db.get_download_decision('t1') is None
    assert 'decision_summary' not in download_tasks['t1']


# ---------------------------------------------------------------------------
# track detail payload
# ---------------------------------------------------------------------------

def test_track_detail_carries_the_decision():
    task = {'status': 'not_found', 'track_info': {'name': 'Song'}}
    d = build_track_detail(task, None, {'outcome': 'nothing_passed', 'chosen': None,
                                        'alternatives': [], 'rejected_total': 9,
                                        'rejected_counts': {'duration_mismatch': 9}})
    assert d['decision']['outcome'] == 'nothing_passed'
    assert d['decision']['rejected_counts'] == {'duration_mismatch': 9}
    assert build_track_detail(task)['decision'] is None
