"""Live per-task detail on the download status payloads (#1156, wishx).

    "It's a mystery going from Pending to Searching to Downloading to
    Processing. No clue where it search, where it found and pulled the
    download from."

The engine always knew: the task dict carries the query ladder position, the
winning peer/file, candidate counts and the raw slskd state — the builders
just never serialized any of it. These tests pin the new ``live_detail``
decoration on both payloads and the ``download_source`` label that live rows
were missing (the "YouTube"/"Tidal" text only existed on history rows).
"""

from __future__ import annotations

import pytest

from core.downloads import status as st
from core.downloads.live_detail import build_live_detail, resolve_source_label
from core.runtime_state import download_batches, download_tasks


@pytest.fixture(autouse=True)
def reset_state():
    download_tasks.clear()
    download_batches.clear()
    yield
    download_tasks.clear()
    download_batches.clear()


class _FakeConfig:
    def __init__(self, values=None):
        self._v = values or {'soulseek.download_timeout': 600}

    def get(self, key, default=None):
        return self._v.get(key, default)


def _deps(cached_transfers=None):
    return st.StatusDeps(
        config_manager=_FakeConfig(),
        docker_resolve_path=lambda p: p,
        find_completed_file=lambda *a, **kw: (None, None),
        make_context_key=lambda u, f: f"{u}::{f}",
        submit_post_processing=lambda *a: None,
        get_cached_transfer_data=cached_transfers or (lambda: {}),
        download_orchestrator=None,
        run_async=None,
        get_persistent_download_history=None,
    )


# ── the pure builder ─────────────────────────────────────────────────────────

def test_terminal_states_carry_no_detail():
    task = {'current_query': 'xo john mayer', 'username': 'peer'}
    for status in ('completed', 'failed', 'not_found', 'cancelled', 'pending'):
        assert build_live_detail(task, None, status) is None


def test_searching_detail_carries_the_ladder_position():
    task = {
        'current_query': 'xo john mayer',
        'current_query_index': 1,
        'query_count': 4,
        'current_source': 'soulseek → youtube',
        'search_live': {'responses': 7, 'results': 12,
                        'by_source': {'soulseek': 9, 'youtube': 3}},
    }
    d = build_live_detail(task, None, 'searching')
    assert d == {
        'query': 'xo john mayer',
        'query_index': 1,
        'query_count': 4,
        'source': 'soulseek → youtube',
        'responses': 7,
        'results': 12,
        'by_source': {'soulseek': 9, 'youtube': 3},
    }


def test_downloading_detail_names_the_peer_file_and_raw_state():
    task = {
        'username': 'some_peer',
        'filename': 'Music\\Flac\\John Mayer\\03 - XO.flac',
        'current_candidate_index': 2,
        'candidate_count': 14,
        'picked_candidate': {'quality': 'flac', 'bitrate': 1024,
                             'size': 31457280, 'confidence': 0.93},
    }
    live = {'state': 'Queued, Remotely', 'averageSpeed': 0,
            'size': 31457280, 'bytesTransferred': 0}
    d = build_live_detail(task, live, 'downloading')
    assert d['source'] == 'Soulseek'
    assert d['username'] == 'some_peer'
    assert d['filename'] == '03 - XO.flac', 'windows path reduced to basename'
    assert d['slskd_state'] == 'Queued, Remotely', 'raw state, not the collapsed word'
    assert d['candidate_index'] == 2 and d['candidate_count'] == 14
    assert d['picked']['confidence'] == 0.93
    assert d['size'] == 31457280


def test_queued_remotely_carries_the_wait_clock_and_journey():
    import time as _time
    task = {
        'username': 'peer', 'filename': 'a.flac',
        'queued_start_time': _time.time() - 45,
        'used_sources': {'p1_f1', 'p2_f2', 'p3_f3'},
        'exhausted_download_sources': {'soulseek'},
    }
    live = {'state': 'Queued, Remotely'}
    d = build_live_detail(task, live, 'downloading')
    assert 40 <= d['queued_seconds'] <= 50
    assert d['tried_sources'] == 3
    assert d['exhausted_sources'] == ['soulseek']


def test_no_wait_clock_while_actually_transferring():
    task = {'username': 'peer', 'filename': 'a.flac',
            'queued_start_time': 1.0}
    d = build_live_detail(task, {'state': 'InProgress'}, 'downloading')
    assert 'queued_seconds' not in d


def test_streaming_username_resolves_to_service_label():
    d = build_live_detail({'username': 'youtube', 'filename': 'x.opus'}, None, 'post_processing')
    assert d['source'] == 'YouTube'


def test_malformed_task_degrades_to_partial_detail_never_raises():
    # query_count is garbage — the fields before it survive, nothing raises
    task = {'current_query': 'q', 'query_count': object()}
    d = build_live_detail(task, None, 'searching')
    assert d['query'] == 'q'
    assert 'query_count' not in d


def test_source_label_resolution():
    assert resolve_source_label('tidal') == 'Tidal'
    assert resolve_source_label('random_slsk_peer') == 'Soulseek'
    assert resolve_source_label('') == ''
    assert resolve_source_label(None) == ''


# ── the batch payload (the missing-tracks modal) ────────────────────────────

def _seed_task(task_id='t1', **extra):
    download_tasks[task_id] = {
        'status': 'searching',
        'track_index': 0,
        'track_info': {'name': 'XO'},
        'status_change_time': __import__('time').time(),
        **extra,
    }
    return download_tasks[task_id]


def test_batch_payload_carries_live_detail_while_searching():
    _seed_task(current_query='xo john mayer', query_count=3,
               current_query_index=0, current_source='soulseek')
    batch = {'phase': 'downloading', 'queue': ['t1']}
    out = st.build_batch_status_data('b1', batch, {}, _deps())
    task_row = out['tasks'][0]
    assert task_row['status'] == 'searching'
    assert task_row['live_detail']['query'] == 'xo john mayer'
    assert task_row['live_detail']['source'] == 'soulseek'


def test_batch_payload_carries_raw_slskd_state_while_queued():
    _seed_task(status='downloading', username='peer', filename='a/b.flac',
               download_id='d1')
    batch = {'phase': 'downloading', 'queue': ['t1']}
    live = {'peer::a/b.flac': {'state': 'Queued, Remotely', 'percentComplete': 0}}
    out = st.build_batch_status_data('b1', batch, live, _deps())
    task_row = out['tasks'][0]
    # the payload status stays the collapsed 'queued' the UI already knows...
    assert task_row['status'] == 'queued'
    # ...but the detail now says WHICH queued this is
    assert task_row['live_detail']['slskd_state'] == 'Queued, Remotely'


def test_batch_payload_has_no_detail_for_terminal_rows():
    _seed_task(status='failed', error_message='nope',
               current_query='leftover from the search')
    batch = {'phase': 'downloading', 'queue': ['t1']}
    out = st.build_batch_status_data('b1', batch, {}, _deps())
    assert 'live_detail' not in out['tasks'][0]


# ── the unified payload (the downloads page) ────────────────────────────────

def test_unified_rows_carry_live_detail_and_source_label():
    download_batches['b1'] = {'playlist_name': 'My PL', 'queue': ['t1']}
    _seed_task(status='downloading', batch_id='b1', username='tidal',
               filename='track.flac',
               picked_candidate={'quality': 'flac', 'bitrate': 1411,
                                 'size': 1, 'confidence': 0.99})
    out = st.build_unified_downloads_response(100, _deps())
    row = out['downloads'][0]
    assert row['download_source'] == 'Tidal'
    assert row['live_detail']['source'] == 'Tidal'
    assert row['live_detail']['filename'] == 'track.flac'


def test_unified_live_completed_row_finally_names_its_source():
    """The reported gap behind #1156's second screenshot: 'YouTube'/'Tidal'
    only appeared once the row aged into persistent history."""
    download_batches['b1'] = {'playlist_name': 'My PL', 'queue': ['t1']}
    _seed_task(status='completed', batch_id='b1', username='youtube',
               quality='MP3-320')
    out = st.build_unified_downloads_response(100, _deps())
    row = out['downloads'][0]
    assert row['download_source'] == 'YouTube'
    assert 'live_detail' not in row, 'completed rows carry the label, not live detail'
