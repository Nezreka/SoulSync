"""AIFF/WAVE's embedded ID3 save accepts no MP3-style v1 argument."""

import wave

import pytest
from mutagen.aiff import AIFF
from mutagen.wave import WAVE
from mutagen.id3 import APIC, TIT2, TXXX

from core.metadata import common
from tests.metadata.test_aiff_tags_1919 import _aiff


@pytest.mark.parametrize('in_place', [False, True])
@pytest.mark.parametrize('format', ['aiff', 'wav'])
def test_embedded_id3_save_preserves_art_custom_tags_and_audio(tmp_path, monkeypatch, in_place, format):
    path = tmp_path / f'track.{format}'
    if format == 'aiff':
        _aiff(path)
        cls = AIFF
    else:
        with wave.open(str(path), 'wb') as output:
            output.setparams((1, 2, 44100, 0, 'NONE', 'not compressed'))
            output.writeframes(b'\0\0' * 4410)
        cls = WAVE
    audio = cls(path)
    audio.add_tags()
    audio.tags.add(TIT2(text=['Before']))
    audio.tags.add(APIC(mime='image/png', type=3, data=b'cover'))
    audio.tags.add(TXXX(desc='CUSTOM', text=['preserved']))
    audio.save()
    before = path.read_bytes()
    chunk = b'SSND' if format == 'aiff' else b'data'

    def pcm(data):
        offset = data.index(chunk)
        size = int.from_bytes(data[offset + 4:offset + 8], 'big' if format == 'aiff' else 'little')
        return data[offset + 8:offset + 8 + size]

    audio = cls(path)
    audio.tags.add(TIT2(text=['After']))
    if in_place:
        def copy_fails(*args, **kwargs):
            raise PermissionError('exercise in-place fallback')
        monkeypatch.setattr(common.shutil, 'copy2', copy_fails)
    assert common.save_audio_file(audio, common.get_mutagen_symbols()) is True
    saved = cls(path)
    assert saved.tags['TIT2'].text == ['After']
    assert saved.tags['TXXX:CUSTOM'].text == ['preserved']
    assert saved.tags.getall('APIC')[0].data == b'cover'
    assert pcm(path.read_bytes()) == pcm(before)
