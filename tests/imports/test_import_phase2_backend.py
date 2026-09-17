"""import page, phase 2 backend: browser upload into staging, the pre-import
preview, and the needs-attention automation event.
"""

from __future__ import annotations

import io
import os
import types

import pytest
from werkzeug.datastructures import FileStorage

import core.imports.routes as routes


def _runtime(staging_path, **over):
    base = dict(
        get_staging_path=lambda: staging_path,
        read_staging_file_metadata=lambda _f, _r: {
            'title': 'Old Title', 'album': 'Old Album', 'artist': 'Old Artist',
            'albumartist': 'Old Artist', 'track_number': 7, 'disc_number': 1,
            'duration_ms': 1000, 'bitrate': 1, 'size': 1},
        logger=types.SimpleNamespace(error=lambda *a, **k: None),
    )
    base.update(over)
    return types.SimpleNamespace(**base)


@pytest.fixture(autouse=True)
def _reset_cache():
    routes.invalidate_staging_scan_cache()
    yield
    routes.invalidate_staging_scan_cache()


# ---- upload ----

def _storage(name, data=b'x'):
    return FileStorage(stream=io.BytesIO(data), filename=name)


def test_upload_keeps_the_folder_the_browser_sent(tmp_path):
    staging = str(tmp_path / 'Staging')
    payload, status = routes.upload_to_staging(
        _runtime(staging),
        [_storage('01.flac'), _storage('02.flac'), _storage('cover.jpg')],
        ['Artist - Album/01.flac', 'Artist - Album/02.flac', 'Artist - Album/cover.jpg'],
    )
    assert status == 200 and payload['success']
    assert sorted(x['file'] for x in payload['saved']) == [
        os.path.join('Artist - Album', '01.flac'), os.path.join('Artist - Album', '02.flac')]
    assert payload['skipped'] == [{'file': os.path.join('Artist - Album', 'cover.jpg'), 'reason': 'not an audio file'}]
    assert os.path.isfile(os.path.join(staging, 'Artist - Album', '01.flac'))


def test_upload_refuses_to_leave_the_import_folder(tmp_path):
    staging = str(tmp_path / 'Staging')
    payload, _ = routes.upload_to_staging(
        _runtime(staging),
        [_storage('a.flac'), _storage('b.flac'), _storage('c.flac')],
        ['../escape.flac', '/abs/x.flac', 'C:/win/x.flac'],
    )
    # dot-dot and a drive letter are refused; a leading slash is just dropped,
    # which keeps the file inside the import folder
    assert [x['reason'] for x in payload['skipped']] == ['bad path', 'bad path']
    assert payload['saved'] == [{'file': os.path.join('abs', 'x.flac'), 'size': 1}]
    assert not os.path.exists(str(tmp_path / 'escape.flac'))
    assert os.path.isfile(os.path.join(staging, 'abs', 'x.flac'))


def test_upload_never_clobbers_and_drops_the_cache(tmp_path):
    staging = str(tmp_path / 'Staging')
    routes._staging_scan_cache.update({"path": staging, "ts": 9e12, "records": [1]})
    routes.upload_to_staging(_runtime(staging), [_storage('a.flac')], ['a.flac'])
    payload, _ = routes.upload_to_staging(_runtime(staging), [_storage('a.flac', b'yy')], ['a.flac'])
    assert payload['saved'][0]['file'] == 'a (2).flac'
    assert routes._staging_scan_cache['records'] is None


def test_upload_uses_the_filename_when_no_path_was_sent(tmp_path):
    staging = str(tmp_path / 'Staging')
    payload, _ = routes.upload_to_staging(_runtime(staging), [_storage('loose.mp3')], [])
    assert payload['saved'][0]['file'] == 'loose.mp3'


# ---- preview ----

def test_preview_shows_destination_and_tag_diff(tmp_path, monkeypatch):
    f = tmp_path / '07 - x.flac'
    f.write_bytes(b'x')
    import core.imports.paths as paths
    monkeypatch.setattr(paths, 'build_final_path_for_track',
                        lambda ctx, artist, info, ext, create_dirs=True: (
                            f"/lib/{artist['name']}/{info['album_name']}/{info['track_number']:02d} - {info['clean_track_name']}{ext}", None))
    album = {'id': 'a1', 'name': 'New Album', 'artist': 'New Artist', 'source': 'deezer',
             'release_date': '2024-05-01', 'artists': [{'name': 'New Artist', 'id': 'ar1'}]}
    matches = [{
        'track': {'id': 't1', 'name': 'New Title', 'track_number': 3, 'disc_number': 1,
                  'artists': [{'name': 'New Artist'}]},
        'staging_file': {'full_path': str(f), 'filename': f.name},
        'confidence': 0.9,
    }]
    payload, status = routes.album_preview(
        _runtime(str(tmp_path),
                 resolve_album_artist_context=lambda album, source=None: {'name': 'New Artist', 'id': 'ar1'},
                 build_album_import_context=routes.build_album_import_context),
        {'album': album, 'matches': matches},
    )
    assert status == 200, payload
    row = payload['tracks'][0]
    assert row['file'] == '07 - x.flac'
    assert row['destination'] == '/lib/New Artist/New Album/03 - New Title.flac'
    assert row['after']['title'] == 'New Title' and row['after']['album'] == 'New Album'
    assert row['after']['track_number'] == 3 and row['after']['year'] == '2024'
    assert row['before']['title'] == 'Old Title' and row['before']['track_number'] == 7
    assert set(row['changed']) >= {'title', 'album', 'artist', 'track_number', 'year'}


def test_preview_needs_an_album_and_matches():
    payload, status = routes.album_preview(_runtime('/x'), {})
    assert status == 400


# ---- needs-attention event ----

def test_worker_emits_needs_attention_with_the_reason():
    from core.auto_import_worker import AutoImportWorker, FolderCandidate

    emitted = []
    w = AutoImportWorker.__new__(AutoImportWorker)
    w._automation_engine = types.SimpleNamespace(emit=lambda t, d: emitted.append((t, d)))
    cand = FolderCandidate(path='/s/x', name='x', audio_files=['/s/x/1.flac', '/s/x/2.flac'], folder_hash='h')
    w._emit_needs_attention(cand, 'pending_review', {'album_name': 'A', 'artist_name': 'B'}, '82% match', 0.82)
    assert emitted == [('import_needs_attention', {
        'folder_name': 'x', 'status': 'pending_review', 'reason': '82% match',
        'album_name': 'A', 'artist': 'B', 'confidence': '82%', 'track_count': '2'})]

    w._automation_engine = None
    w._emit_needs_attention(cand, 'failed', None, 'boom')   # no engine: no-op, no raise


def test_needs_attention_is_a_registered_trigger():
    from core.automation.blocks import TRIGGERS

    trigger = next(t for t in TRIGGERS if t['type'] == 'import_needs_attention')
    assert trigger['available'] is True
    assert set(trigger['variables']) == {'folder_name', 'status', 'reason', 'album_name', 'artist', 'confidence', 'track_count'}


# ---- fingerprint on demand ----

class _Fp:
    def __init__(self, answers):
        self.answers = answers

    def is_available(self):
        return True, ''

    def lookup_with_status(self, path):
        return self.answers.get(os.path.basename(path), {'status': 'no_match', 'recordings': [], 'error': 'no match'})


def test_fingerprint_names_the_artist_and_ignores_what_it_cannot_read(tmp_path):
    for n in ('01.flac', '02.flac', '03.flac', '04.flac'):
        (tmp_path / n).write_bytes(b'x')
    answers = {
        '01.flac': {'status': 'ok', 'recordings': [{'mbid': 'm1', 'title': 'One', 'artist': 'Boards of Canada', 'score': 0.98}]},
        '02.flac': {'status': 'ok', 'recordings': [{'mbid': 'm2', 'title': 'Two', 'artist': 'Boards of Canada', 'score': 0.91}]},
    }
    rt = _runtime(str(tmp_path), get_allowed_import_roots=lambda: [str(tmp_path)])
    payload, status = routes.fingerprint_files(
        rt, [str(tmp_path / n) for n in ('01.flac', '02.flac', '03.flac', '04.flac')],
        client_factory=lambda: _Fp(answers))
    assert status == 200, payload
    assert [r['file'] for r in payload['results']] == ['01.flac', '02.flac', '03.flac']   # capped at 3
    assert payload['artist'] == 'Boards of Canada' and payload['recognised'] == 2
    assert payload['results'][0]['score'] == 0.98 and payload['results'][2]['status'] == 'no_match'
    assert payload['title'] is None   # an album, not a single


def test_fingerprint_single_returns_a_title(tmp_path):
    (tmp_path / 'x.flac').write_bytes(b'x')
    rt = _runtime(str(tmp_path), get_allowed_import_roots=lambda: [str(tmp_path)])
    payload, _ = routes.fingerprint_files(rt, [str(tmp_path / 'x.flac')], client_factory=lambda: _Fp({
        'x.flac': {'status': 'ok', 'recordings': [{'mbid': 'm', 'title': 'Dayvan Cowboy', 'artist': 'BoC', 'score': 0.9}]}}))
    assert payload['title'] == 'Dayvan Cowboy' and payload['artist'] == 'BoC'


def test_fingerprint_refuses_files_outside_the_roots_and_says_when_acoustid_is_off(tmp_path):
    outside = tmp_path / 'elsewhere.flac'
    outside.write_bytes(b'x')
    rt = _runtime(str(tmp_path / 'Staging'), get_allowed_import_roots=lambda: [str(tmp_path / 'Staging')])
    payload, status = routes.fingerprint_files(rt, [str(outside)], client_factory=lambda: _Fp({}))
    assert status == 200 and payload['results'][0]['status'] == 'error'

    class Off(_Fp):
        def is_available(self):
            return False, 'No AcoustID API key configured'

    payload, status = routes.fingerprint_files(rt, [str(outside)], client_factory=lambda: Off({}))
    assert status == 503 and payload['code'] == 'acoustid_unavailable'


# ---- chunked upload ----

def test_chunks_assemble_in_order_and_land_once(tmp_path):
    staging = str(tmp_path / 'Staging')
    rt = _runtime(staging)
    kw = dict(upload_id='abcdefgh1234', total=3, relative_path='Artist - Album/01.flac')
    p1, s1 = routes.upload_chunk_to_staging(rt, index=0, chunk=_storage('01.flac', b'aaa'), **kw)
    assert s1 == 200 and p1 == {'success': True, 'received': 1, 'total': 3}
    assert os.path.isfile(os.path.join(staging, '.uploads', 'abcdefgh1234.part'))
    assert not os.path.exists(os.path.join(staging, 'Artist - Album', '01.flac'))
    routes.upload_chunk_to_staging(rt, index=1, chunk=_storage('01.flac', b'bbb'), **kw)
    p3, _ = routes.upload_chunk_to_staging(rt, index=2, chunk=_storage('01.flac', b'cc'), **kw)
    assert p3['saved'] == {'file': os.path.join('Artist - Album', '01.flac'), 'size': 8}
    with open(os.path.join(staging, 'Artist - Album', '01.flac'), 'rb') as f:
        assert f.read() == b'aaabbbcc'
    assert not os.path.exists(os.path.join(staging, '.uploads', 'abcdefgh1234.part'))


def test_a_retried_first_chunk_starts_over(tmp_path):
    staging = str(tmp_path / 'Staging')
    rt = _runtime(staging)
    kw = dict(upload_id='retry-me-0001', total=2, relative_path='x.flac')
    routes.upload_chunk_to_staging(rt, index=0, chunk=_storage('x.flac', b'stale'), **kw)
    routes.upload_chunk_to_staging(rt, index=0, chunk=_storage('x.flac', b'fresh'), **kw)
    p, _ = routes.upload_chunk_to_staging(rt, index=1, chunk=_storage('x.flac', b'!'), **kw)
    with open(os.path.join(staging, 'x.flac'), 'rb') as f:
        assert f.read() == b'fresh!'


def test_chunk_refuses_bad_ids_paths_and_indexes(tmp_path):
    rt = _runtime(str(tmp_path / 'Staging'))
    bad = [
        dict(upload_id='x', index=0, total=1, relative_path='a.flac'),
        dict(upload_id='abcdefgh1234', index=1, total=1, relative_path='a.flac'),
        dict(upload_id='abcdefgh1234', index=0, total=1, relative_path='../a.flac'),
        dict(upload_id='abcdefgh1234', index=0, total=1, relative_path='a.txt'),
    ]
    for kw in bad:
        _, status = routes.upload_chunk_to_staging(rt, chunk=_storage('a.flac'), **kw)
        assert status == 400, kw
