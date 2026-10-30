"""contracts / metadata api regression contracts."""
from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest
from flask import Flask
from mutagen.id3 import TIT2
from mutagen.mp4 import AtomDataType, MP4Cover, MP4FreeForm
from mutagen.wave import WAVE

from core.library.file_metadata import read_file_metadata
from core.repair_worker import RepairWorker
from tests.reconciliation.support.audio import audio_file
from tests.reconciliation.support.queue import _Database


def test_review_http_api_returns_exact_case_and_rejects_stale_apply(monkeypatch):
    from api import repair

    db = _Database()
    db.add(1, '/music/A/01.flac', {'album_id': 5, 'album_title': 'OK Computer'})
    db.add(2, '/music/A/02.flac', {'album_id': 5, 'album_title': 'OK Computer'},
           job_id='library_reconciliation', kind='reconciliation_metadata')
    worker = RepairWorker.__new__(RepairWorker)
    worker.db = db
    worker._review_apply_lock = threading.RLock()
    worker.fix_finding = lambda *_args, **_kwargs: {'success': True}
    monkeypatch.setattr(repair, '_worker_getter', lambda: worker)
    app = Flask(__name__)
    app.register_blueprint(repair.create_blueprint())
    client = app.test_client()
    queue = client.get('/api/repair/review-queue').json
    case_id = queue['items'][0]['case_id']
    detail = client.get(f'/api/repair/review-queue/{case_id}').json
    assert detail['count'] == 2
    assert detail['album'] == 'OK Computer'
    filtered = client.get('/api/repair/review-queue?job_id=library_reconciliation').json
    assert filtered['items'][0]['pending'] == 1
    filtered_detail = client.get(
        f'/api/repair/review-queue/{case_id}?job_id=library_reconciliation').json
    assert [item['id'] for item in filtered_detail['items']] == [2]
    response = client.post(f'/api/repair/review-queue/{case_id}/findings/1/apply',
                           json={'action': 'fix', 'evidence_token': 'old'})
    assert response.status_code == 409
    assert response.json['stale'] is True
    calls = []
    worker.start_review_safe = lambda **kwargs: calls.append(kwargs) or {
        'started': True, 'total': 4,
    }
    response = client.post('/api/repair/review-queue/apply-ready', json={
        'case_id': case_id, 'organize': True,
        'signature': client.get('/api/repair/review-queue/apply-ready', query_string={'case_id': case_id}).json['signature'],
    })
    assert response.status_code == 200
    assert response.json == {'started': True, 'total': 4}
    assert calls == [{'case_id': case_id, 'organize': True, 'case_ids': None, 'organize_case_ids': None,
                      'expected_signature': client.get('/api/repair/review-queue/apply-ready', query_string={'case_id': case_id}).json['signature']}]


def test_native_tags_keep_custom_values_multiple_artists_and_picture_metadata(tmp_path):
    path = tmp_path / 'song.wav'
    audio_file(path)
    record = read_file_metadata(str(path))
    assert record['Tag: TIT2']['text'] == ['Local title']
    assert record['Tag: TPE1']['text'] == ['Noah Kahan', 'Hozier']
    assert record['Tag: TXXX:custom user tag']['text'] == ['one', 'two']
    assert record['Tag: UFID:http://musicbrainz.org']['data'] == '93ef0bf8-5478-43a7-ad00-1650069bd721'
    assert record['Tag: APIC:Cover']['data'] == {'binary_bytes': 13}
    assert record['Sample rate (Hz)'] == 8000
    assert record['Channels'] == 1
    assert record['Duration (seconds)'] == 1
    audio = WAVE(path)
    audio.tags['TIT2'] = TIT2(encoding=3, text='Changed since review')
    audio.save()
    assert read_file_metadata(str(path))['Tag: TIT2']['text'] == ['Changed since review']


def test_native_binary_containers_preserve_text_without_expanding_images(monkeypatch):
    import mutagen
    from mutagen.asf import ASFByteArrayAttribute, ASFUnicodeAttribute
    monkeypatch.setattr(mutagen, 'File', lambda *_args, **_kwargs: SimpleNamespace(
        info=SimpleNamespace(), tags={
            '----:com.apple.iTunes:MusicBrainz Track Id': [MP4FreeForm(b'recording-id')],
            'custom UTF16': [MP4FreeForm('Bj\u00f6rk'.encode('utf-16-be'), dataformat=AtomDataType.UTF16)],
            'invalid UTF8': [MP4FreeForm(b'\xff')],
            'covr': [MP4Cover(b'picture-bytes')],
            'WM/Artist': ASFUnicodeAttribute('Noah Kahan & Hozier'),
            'WM/Picture': ASFByteArrayAttribute(b'picture-bytes'),
        }))
    record = read_file_metadata('/music/test.m4a')
    assert record['Tag: ----:com.apple.iTunes:MusicBrainz Track Id'] == ['recording-id']
    assert record['Tag: custom UTF16'] == ['Bj\u00f6rk']
    assert record['Tag: invalid UTF8'] == [{'binary_bytes': 1}]
    assert record['Tag: covr'] == [{'binary_bytes': 13}]
    assert record['Tag: WM/Artist'] == 'Noah Kahan & Hozier'
    assert record['Tag: WM/Picture'] == {'binary_bytes': 13}


@pytest.mark.parametrize('membership', ['finding', 'assignment', 'unrelated', 'outside', 'missing'])
def test_metadata_endpoint_requires_case_membership_and_library_path(tmp_path, monkeypatch, membership):
    from api import repair
    library = tmp_path / 'library'
    library.mkdir()
    path = library / 'song.wav'
    audio_file(path)
    if membership == 'outside':
        outside = tmp_path / 'private.wav'
        audio_file(outside)
        path.unlink()
        path.symlink_to(outside)
    if membership == 'missing': path.unlink()
    member = {'file_path': str(path if membership != 'unrelated' else library / 'other.wav')}
    case = {'items': [member] if membership != 'assignment' else [],
            'album_plan': {'assignments': [member] if membership == 'assignment' else []}}
    worker = SimpleNamespace(get_review_case=lambda *_args, **_kwargs: case,
        _job_context=lambda: SimpleNamespace(transfer_folder=str(library)))
    monkeypatch.setattr(repair, '_worker_getter', lambda: worker)
    app = Flask(__name__)
    app.register_blueprint(repair.create_blueprint())
    result = app.test_client().get('/api/repair/review-queue/case/file-metadata', query_string={'path': str(path)})
    assert result.status_code == {'unrelated': 404, 'outside': 403, 'missing': 422}.get(membership, 200)
    if result.status_code == 200:
        assert result.json['record']['Tag: TPE1']['text'] == ['Noah Kahan', 'Hozier']
        assert result.headers['Cache-Control'] == 'no-store'
