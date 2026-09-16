"""the inbox join: one row per staging candidate, with the worker's history
and live state folded in, plus history rows for things no longer in staging.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from core.imports.inbox import build_inbox, derive_status, summarize


def _cand(path, files, h, single=False):
    return SimpleNamespace(path=path, name=path.rsplit('/', 1)[-1], audio_files=files,
                           folder_hash=h, is_single=single)


def _rec(path, **over):
    base = {'full_path': path, 'rel_path': path.split('/Staging/')[-1], 'title': 't',
            'artist': 'Art', 'albumartist': '', 'album': 'Alb', 'track_number': 1,
            'disc_number': 1, 'duration_ms': 200000, 'bitrate': 900000, 'size': 30000000}
    base.update(over)
    return base


def test_status_precedence():
    assert derive_status(None, None, True) == 'waiting'
    assert derive_status('pending_review', None, True) == 'needs_review'
    assert derive_status('pending_review', 'processing', True) == 'importing'
    assert derive_status('needs_identification', 'identifying', True) == 'identifying'
    assert derive_status('completed', None, False) == 'imported'
    assert derive_status('rejected', None, True) == 'dismissed'
    assert derive_status('approved', None, True) == 'queued'


def test_candidate_with_no_history_is_waiting_and_carries_file_facts():
    c = _cand('/Staging/Artist - Album', ['/Staging/Artist - Album/01.flac',
                                          '/Staging/Artist - Album/02.mp3'], 'h1')
    rows = build_inbox([c], [_rec(c.audio_files[0]), _rec(c.audio_files[1], album='Alb')],
                       [], [], staging_root='/Staging')
    assert len(rows) == 1
    r = rows[0]
    assert r['status'] == 'waiting' and r['key'] == 'h1' and r['kind'] == 'album'
    assert r['name'] == 'Alb' and r['artist'] == 'Art'
    assert r['rel_path'] == 'Artist - Album'
    assert r['formats'] == ['FLAC', 'MP3']
    assert r['total_duration_ms'] == 400000 and r['total_size'] == 60000000
    assert r['files'][0]['format'] == 'FLAC' and r['files'][0]['bitrate'] == 900000


def test_history_joins_by_hash_and_falls_back_to_path():
    c = _cand('/Staging/X', ['/Staging/X/01.flac'], 'h-new')
    hist = [{'id': 7, 'folder_hash': 'h-old', 'folder_path': '/Staging/X', 'status': 'pending_review',
             'confidence': 0.82, 'album_name': 'Real Name', 'artist_name': 'Real Artist',
             'image_url': 'a.jpg', 'match_data': json.dumps({
                 'matched_count': 1, 'total_tracks': 2,
                 'matches': [{'track_name': 'One', 'file': '/Staging/X/01.flac', 'confidence': 0.9}]})}]
    rows = build_inbox([c], [_rec(c.audio_files[0])], hist, [], '/Staging')
    r = rows[0]
    assert r['status'] == 'needs_review' and r['history_id'] == 7
    assert r['name'] == 'Real Name' and r['artist'] == 'Real Artist' and r['image_url'] == 'a.jpg'
    assert r['confidence'] == 0.82
    assert r['match']['source'] is None
    assert r['match']['matches'][0] == {'track_name': 'One', 'track_number': None,
                                        'file': '01.flac', 'file_path': '/Staging/X/01.flac',
                                        'confidence': 0.9}
    # the claimed history row is not repeated as a history-only row
    assert len(rows) == 1


def test_live_import_overrides_history_and_carries_progress():
    c = _cand('/Staging/X', ['/Staging/X/01.flac'], 'h1')
    hist = [{'id': 1, 'folder_hash': 'h1', 'status': 'processing'}]
    active = [{'folder_hash': 'h1', 'status': 'processing', 'track_index': 3,
               'track_total': 10, 'track_name': 'Three'}]
    r = build_inbox([c], [], hist, active, '/Staging')[0]
    assert r['status'] == 'importing'
    assert r['live'] == {'track_index': 3, 'track_total': 10, 'track_name': 'Three'}


def test_history_without_files_keeps_only_records_worth_keeping():
    hist = [
        {'id': 1, 'folder_hash': 'a', 'folder_path': '/Staging/A', 'status': 'completed',
         'album_name': 'Done', 'total_files': 12},
        {'id': 2, 'folder_hash': 'b', 'folder_path': '/Staging/B', 'status': 'needs_identification',
         'album_name': 'Gone'},
        {'id': 3, 'folder_hash': 'c', 'folder_path': '/Staging/C', 'status': 'failed',
         'error_message': 'boom', 'total_files': 1},
    ]
    rows = build_inbox([], [], hist, [], '/Staging')
    assert [(r['status'], r['in_staging']) for r in rows] == [('imported', False), ('failed', False)]
    assert rows[0]['kind'] == 'album' and rows[1]['kind'] == 'single'
    assert rows[1]['error_message'] == 'boom'


def test_single_uses_its_title_as_the_name():
    c = _cand('/Staging/loose.flac', ['/Staging/loose.flac'], 's1', single=True)
    r = build_inbox([c], [_rec('/Staging/loose.flac', title='Song', album='')], [], [], '/Staging')[0]
    assert r['kind'] == 'single' and r['name'] == 'Song'


def test_summary_counts_only_what_is_in_staging():
    c1 = _cand('/Staging/A', ['/Staging/A/1.flac'], 'a')
    c2 = _cand('/Staging/B', ['/Staging/B/1.flac', '/Staging/B/2.flac'], 'b')
    hist = [{'id': 1, 'folder_hash': 'b', 'status': 'pending_review'},
            {'id': 2, 'folder_hash': 'z', 'folder_path': '/Staging/Z', 'status': 'completed'}]
    rows = build_inbox([c1, c2], [_rec('/Staging/A/1.flac', size=5), _rec('/Staging/B/1.flac', size=5),
                                  _rec('/Staging/B/2.flac', size=5)], hist, [], '/Staging')
    s = summarize(rows)
    assert s['items'] == 2 and s['files'] == 3 and s['size'] == 15
    assert s['attention'] == 2
    assert s['by_status'] == {'waiting': 1, 'needs_review': 1, 'imported': 1}


# ---- the endpoint over a real staging folder ----

import types

import pytest

import core.imports.routes as routes


def _runtime(staging_path):
    return types.SimpleNamespace(
        get_staging_path=lambda: staging_path,
        read_staging_file_metadata=lambda _f, _r: {
            'title': 'Song', 'album': 'Alb', 'artist': 'Art', 'albumartist': 'Art',
            'track_number': 1, 'disc_number': 1, 'duration_ms': 1000, 'bitrate': 320000,
            'size': 9},
        logger=types.SimpleNamespace(error=lambda *a, **k: None),
    )


@pytest.fixture(autouse=True)
def _reset_cache():
    routes.invalidate_staging_scan_cache()
    yield
    routes.invalidate_staging_scan_cache()


def _staging(tmp_path):
    root = tmp_path / 'Staging'
    (root / 'Artist - Album').mkdir(parents=True)
    (root / 'Artist - Album' / '01.flac').write_bytes(b'x')
    (root / 'Artist - Album' / '02.flac').write_bytes(b'x')
    return str(root)


def test_endpoint_without_a_worker_still_lists_staging(tmp_path):
    payload, status = routes.inbox(_runtime(_staging(tmp_path)), None)
    assert status == 200 and payload['success']
    assert payload['worker'] == {'available': False, 'running': False, 'paused': False,
                                 'current_status': 'idle', 'last_scan_time': None, 'stats': {}}
    assert len(payload['items']) == 1
    item = payload['items'][0]
    assert item['status'] == 'waiting' and item['file_count'] == 2 and item['name'] == 'Alb'
    assert item['files'][0]['bitrate'] == 320000
    assert payload['summary']['items'] == 1


def test_endpoint_joins_the_worker(tmp_path):
    from core.auto_import_worker import AutoImportWorker
    root = _staging(tmp_path)
    bare = AutoImportWorker.__new__(AutoImportWorker)
    cands, _ = bare.enumerate_candidates(root)
    h = cands[0].folder_hash

    class Worker:
        def enumerate_candidates(self, path):
            return bare.enumerate_candidates(path)

        def get_results(self, limit=50):
            return [{'id': 3, 'folder_hash': h, 'folder_path': cands[0].path, 'status': 'pending_review',
                     'confidence': 0.8, 'album_name': 'Named', 'artist_name': 'Who'}]

        def get_status(self):
            return {'running': True, 'paused': False, 'current_status': 'idle',
                    'active_imports': [], 'stats': {'scanned': 1}, 'last_scan_time': 't'}

    payload, status = routes.inbox(_runtime(root), Worker())
    assert status == 200
    assert payload['worker']['running'] is True and payload['worker']['stats'] == {'scanned': 1}
    item = payload['items'][0]
    assert item['status'] == 'needs_review' and item['name'] == 'Named' and item['history_id'] == 3
    assert payload['summary']['attention'] == 1
