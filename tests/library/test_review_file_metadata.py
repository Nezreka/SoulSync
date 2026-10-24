"""The review inspector reads full native file metadata, scoped to its case."""
from types import SimpleNamespace
import wave

from flask import Flask
from mutagen.id3 import TIT2, TPE1, TXXX, APIC
from mutagen.wave import WAVE
import pytest

from core.library.file_metadata import read_file_metadata


def audio_file(path, title='Local title'):
    with wave.open(str(path), 'wb') as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(8000)
        out.writeframes(b'\0\0' * 8000)
    audio = WAVE(path)
    audio.add_tags()
    audio.tags.add(TIT2(encoding=3, text=title))
    audio.tags.add(TPE1(encoding=3, text=['Noah Kahan', 'Hozier']))
    audio.tags.add(TXXX(encoding=3, desc='custom user tag', text=['one', 'two']))
    audio.tags.add(APIC(encoding=3, mime='image/png', type=3, desc='Cover', data=b'picture-bytes'))
    audio.save()


def test_native_tags_keep_custom_values_multiple_artists_and_picture_metadata(tmp_path):
    path = tmp_path / 'song.wav'
    audio_file(path)
    record = read_file_metadata(str(path))
    assert record['Tag: TIT2']['text'] == ['Local title']
    assert record['Tag: TPE1']['text'] == ['Noah Kahan', 'Hozier']
    assert record['Tag: TXXX:custom user tag']['text'] == ['one', 'two']
    assert record['Tag: APIC:Cover']['data'] == {'binary_bytes': 13}
    assert record['Sample rate (Hz)'] == 8000
    assert record['Channels'] == 1
    assert record['Duration (seconds)'] == 1
    audio = WAVE(path)
    audio.tags['TIT2'] = TIT2(encoding=3, text='Changed since review')
    audio.save()
    assert read_file_metadata(str(path))['Tag: TIT2']['text'] == ['Changed since review']


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
