"""Search provenance + candidate policy facet (area A).

One provenance per automatic worker run and one per interactive inspection:
``search_mode``/``searched_at`` (UTC)/``policy_run_id`` (12 chars) persist on
``download_decisions`` and ride the candidate payload. The policy facet names
the ladder rung the winner reached, using only the existing quality ladder.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from core.downloads import decision_log
from core.downloads.candidate_pool import build_source_rows
from core.downloads.decisions import accept
from core.downloads.provenance import (
    build_policy_facet, new_policy_run_id, new_provenance, utc_now_iso)
from core.downloads.track_detail import build_track_detail
from core.quality.model import AudioQuality
from database.music_database import MusicDatabase


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(str(tmp_path / 'm.db'))


def _profile():
    return {
        'ranked_targets': [
            {'label': 'FLAC 24-bit/96kHz', 'format': 'flac', 'bit_depth': 24,
             'min_sample_rate': 96000},
            {'label': 'FLAC 16-bit', 'format': 'flac', 'bit_depth': 16},
            {'label': 'MP3 320kbps', 'format': 'mp3', 'min_bitrate': 320},
        ],
        'fallback_enabled': True,
    }


def _candidate(**over):
    fields = dict(username='peer', filename='/m/a.flac', artist='A', title='T',
                  size=100, bitrate=900, quality='FLAC', duration=200,
                  confidence=0.9, bit_depth=16, sample_rate=44100,
                  free_upload_slots=1, upload_speed=10, queue_length=0)
    fields.update(over)
    return SimpleNamespace(**fields)


# ── provenance primitives ────────────────────────────────────────────────────

def test_run_ids_are_12_chars_and_unique():
    ids = {new_policy_run_id() for _ in range(50)}
    assert len(ids) == 50
    assert all(len(i) == 12 for i in ids)


def test_searched_at_is_utc():
    stamp = utc_now_iso()
    parsed = datetime.strptime(stamp, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
    assert abs((datetime.now(timezone.utc) - parsed).total_seconds()) < 60


def test_new_provenance_modes():
    auto = new_provenance('automatic')
    assert auto['search_mode'] == 'automatic'
    assert len(auto['policy_run_id']) == 12 and auto['searched_at']
    assert new_provenance('interactive')['search_mode'] == 'interactive'
    with pytest.raises(ValueError):
        new_provenance('manual')


# ── policy facet ─────────────────────────────────────────────────────────────

def test_policy_facet_names_the_rung_the_winner_reached():
    aq = AudioQuality.from_tier('flac', 0, 44100, 16)
    facet = build_policy_facet(_profile(), _candidate(audio_quality=aq))
    assert facet['target_index'] == 1
    assert facet['target_label'] == 'FLAC 16-bit'
    assert facet['target_count'] == 3
    assert facet['tier_score'] == aq.tier_score()
    assert facet['fallback_enabled'] is True


def test_policy_facet_without_a_winner_reaches_no_rung():
    facet = build_policy_facet(_profile())
    assert facet['target_index'] == 3 == facet['target_count']
    assert facet['target_label'] == '' and facet['tier_score'] is None


def test_policy_facet_survives_no_profile():
    facet = build_policy_facet(None)
    assert facet == {'target_index': 0, 'target_label': '', 'target_count': 0,
                     'tier_score': None, 'fallback_enabled': True}


# ── persistence ──────────────────────────────────────────────────────────────

def _summary():
    return {'chosen': None, 'alternatives': [], 'accepted_total': 0,
            'rejected_total': 0, 'rejected_counts': {}}


def test_decision_row_persists_provenance(db):
    prov = new_provenance('automatic')
    db.record_download_decision('t1', outcome='chosen', summary=_summary(),
                                provenance=prov)
    row = db.get_download_decision('t1')
    assert row['search_mode'] == 'automatic'
    assert row['searched_at'] == prov['searched_at']
    assert row['policy_run_id'] == prov['policy_run_id']


def test_retry_record_without_provenance_keeps_the_old(db):
    prov = new_provenance('interactive')
    db.record_download_decision('t1', outcome='chosen', summary=_summary(),
                                provenance=prov)
    # a retry winner record omits provenance: the old one must survive
    db.record_download_decision('t1', outcome='chosen', summary=_summary())
    row = db.get_download_decision('t1')
    assert (row['search_mode'], row['searched_at'], row['policy_run_id']) == (
        'interactive', prov['searched_at'], prov['policy_run_id'])


def test_pre_migration_rows_read_back_with_defaults(db):
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO download_decisions (task_key, outcome) VALUES ('t9', 'chosen')")
        conn.commit()
    row = db.get_download_decision('t9')
    assert row['search_mode'] == 'automatic'
    assert row['searched_at'] == '' and row['policy_run_id'] == ''


def test_migration_adds_the_columns_to_an_old_table(tmp_path):
    import sqlite3
    path = str(tmp_path / 'old.db')
    conn = sqlite3.connect(path)
    # the pre-A schema: every original column, none of the provenance ones
    conn.execute("""CREATE TABLE download_decisions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        track_download_id INTEGER,
                        task_key TEXT NOT NULL,
                        track_title TEXT,
                        track_artist TEXT,
                        quality_profile_id INTEGER,
                        outcome TEXT NOT NULL,
                        chosen_json TEXT,
                        alternatives_json TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    conn.commit()
    conn.close()
    # initializing against the old schema runs the migration
    fresh = MusicDatabase(path)
    with fresh._get_connection() as conn2:
        cols = {c[1] for c in conn2.execute("PRAGMA table_info(download_decisions)")}
    assert {'search_mode', 'searched_at', 'policy_run_id'} <= cols


# ── threading ────────────────────────────────────────────────────────────────

def test_decision_log_record_threads_provenance_and_policy(db):
    cand = _candidate(audio_quality=AudioQuality.from_tier('flac', 0, 44100, 16))
    prov = new_provenance('automatic')
    summary = decision_log.record(
        't2', [(cand, accept(0.9))], outcome='nothing_passed',
        quality_profile_id=None, database=db, provenance=prov)
    # no profile id -> the app default profile's ladder applies
    assert summary['policy']['target_count'] > 0
    assert summary['policy']['fallback_enabled'] in (True, False)
    row = db.get_download_decision('t2')
    assert row['search_mode'] == 'automatic'
    assert row['policy_run_id'] == prov['policy_run_id']
    assert row['policy']['fallback_enabled'] is True


def test_source_rows_carry_provenance_and_policy_when_given():
    prov = new_provenance('interactive')
    policy = build_policy_facet(_profile())
    rows = build_source_rows(
        [('q', [(_candidate(), accept(0.9))])], source_name='soulseek',
        is_blacklisted=lambda u, f: False,
        provenance=prov, policy=policy)
    assert rows['provenance'] == prov
    assert rows['policy']['target_label'] == ''  # no winner in this payload
    plain = build_source_rows(
        [('q', [(_candidate(), accept(0.9))])], source_name='soulseek',
        is_blacklisted=lambda u, f: False)
    assert 'provenance' not in plain and 'policy' not in plain


def test_track_detail_decision_block_names_the_search():
    prov = new_provenance('interactive')
    task = {'task_id': 't3', 'status': 'completed', 'track_info': {}}
    decision = {'outcome': 'chosen', 'policy': build_policy_facet(_profile()),
                **prov}
    detail = build_track_detail(task, decision=decision)
    assert detail['decision']['search_mode'] == 'interactive'
    assert detail['decision']['policy_run_id'] == prov['policy_run_id']
    assert detail['decision']['policy']['target_count'] == 3


def test_error_payloads_keep_the_inspection_provenance():
    from core.downloads.candidate_pool import empty_source_rows
    prov = new_provenance('interactive')
    policy = build_policy_facet(_profile())
    out = empty_source_rows('boom', provenance=prov, policy=policy)
    assert out['provenance'] == prov and out['policy'] == policy
    assert out['error'] == 'boom'
    plain = empty_source_rows()
    assert 'provenance' not in plain and 'policy' not in plain


def test_one_interactive_provenance_across_all_source_payloads():
    prov = new_provenance('interactive')
    policy = build_policy_facet(_profile())
    kw = dict(is_blacklisted=lambda u, f: False, provenance=prov, policy=policy)
    first = build_source_rows([('q', [])], source_name='soulseek', **kw)
    second = build_source_rows([('q', [])], source_name='tidal', **kw)
    assert first['provenance'] is prov and second['provenance'] is prov
    assert first['provenance']['policy_run_id'] == second['provenance']['policy_run_id']
    assert first['provenance']['search_mode'] == 'interactive'


def test_retry_winner_keeps_the_live_provenance():
    from core.runtime_state import download_tasks, tasks_lock

    class _NullDb:
        def record_download_decision(self, *a, **kw):
            return 1

    prov = new_provenance('automatic')
    task_id = 't-retry'
    summary = {'chosen': None, 'alternatives': [], 'accepted_total': 0,
               'rejected_total': 0, 'rejected_counts': {}, **prov}
    with tasks_lock:
        download_tasks[task_id] = {'decision_summary': summary,
                                   'username': 'u', 'filename': '/m/a.flac'}
    try:
        decision_log.note_retry_winner(task_id, database=_NullDb())
        with tasks_lock:
            kept = download_tasks[task_id]['decision_summary']
        assert kept['search_mode'] == 'automatic'
        assert kept['policy_run_id'] == prov['policy_run_id']
    finally:
        with tasks_lock:
            download_tasks.pop(task_id, None)


def test_record_materializes_a_generator_pool(db):
    prov = new_provenance('automatic')
    gen = ((_candidate(filename=f'/m/a{i}.flac'), accept(0.9)) for i in range(2))
    summary = decision_log.record('t-gen', gen, outcome='nothing_passed',
                                  quality_profile_id=None, database=db,
                                  provenance=prov)
    # summarize_pool would exhaust the generator; without materializing, the
    # policy facet walk below would see nothing and the row would be wrong
    assert summary is not None
    assert summary['accepted_total'] == 2
    row = db.get_download_decision('t-gen')
    assert row['search_mode'] == 'automatic'
    assert row['policy']['target_count'] > 0  # default profile ladder recorded
    assert row['policy']['fallback_enabled'] in (True, False)
