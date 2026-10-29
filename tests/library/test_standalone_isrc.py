"""Standalone ISRC extraction/persistence without the AIFF/WAV metadata branch."""

import struct
import wave
from types import SimpleNamespace

import pytest
from mutagen import File as MutagenFile
from mutagen.id3 import TALB, TIT2, TPE1, TSRC

from core.library.file_tags import read_embedded_tags
from core.soulsync_client import _read_tags, _stable_id


ISRC = 'QZZ792411931'


@pytest.fixture(params=['.flac', '.mp3', '.aiff', '.wav'])
def tagged_audio(tmp_path, request):
    path = tmp_path / ('Track' + request.param)
    if request.param == '.flac':
        streaminfo = bytearray(34)
        streaminfo[0:4] = struct.pack('>HH', 4096, 4096)
        streaminfo[10:18] = ((44100 << 44) | (1 << 41) | (15 << 36) | 4410).to_bytes(8, 'big')
        path.write_bytes(b'fLaC\x80\x00\x00\x22' + streaminfo)
    elif request.param == '.mp3':
        path.write_bytes((b'\xff\xfb\x90\x00' + bytes(413)) * 10)
    elif request.param == '.wav':
        with wave.open(str(path), 'wb') as audio:
            audio.setparams((2, 2, 44100, 0, 'NONE', 'not compressed'))
            audio.writeframes(bytes(4410 * 4))
    else:
        comm = struct.pack('>hIh', 2, 4410, 16) + bytes.fromhex('400eac44000000000000')
        ssnd = struct.pack('>II', 0, 0) + bytes(4410 * 4)
        body = b'AIFFCOMM' + struct.pack('>I', len(comm)) + comm
        body += b'SSND' + struct.pack('>I', len(ssnd)) + ssnd
        path.write_bytes(b'FORM' + struct.pack('>I', len(body)) + body)
    audio = MutagenFile(path)
    if audio.tags is None:
        audio.add_tags()
    if request.param == '.flac':
        audio['title'] = ['Track']
        audio['artist'] = ['Artist']
        audio['album'] = ['Album']
        audio['isrc'] = ['  ' + ISRC.lower() + '  ']
    else:
        for frame in (TIT2(text=['Track']), TPE1(text=['Artist']), TALB(text=['Album']), TSRC(text=['  ' + ISRC.lower() + '  '])):
            audio.tags.add(frame)
    audio.save()
    return path


def test_reads_isrc_from_easy_keys_and_raw_id3(tagged_audio):
    before = tagged_audio.read_bytes()
    assert _read_tags(str(tagged_audio))['isrc'] == ISRC
    assert read_embedded_tags(str(tagged_audio))['tags']['isrc'].strip().upper() == ISRC
    assert tagged_audio.read_bytes() == before


@pytest.mark.parametrize('has_isrc', [True, False])
def test_full_refresh_persists_isrc_or_null(tagged_audio, tmp_path, monkeypatch, has_isrc):
    from api import database_admin as da
    from database.music_database import MusicDatabase

    if not has_isrc:
        audio = MutagenFile(tagged_audio)
        if tagged_audio.suffix == '.flac':
            audio.pop('isrc', None)
        else:
            audio.tags.delall('TSRC')
        audio.save()
        assert _read_tags(str(tagged_audio))['isrc'] == ''

    db = MusicDatabase(str(tmp_path / 'library.db'))
    monkeypatch.setattr(da, 'get_database', lambda: db)
    monkeypatch.setattr(da, 'config_manager', SimpleNamespace(get=lambda *args: str(tmp_path)))
    monkeypatch.setattr(da, 'docker_resolve_path', lambda p: p)
    monkeypatch.setattr(da, '_db_update_phase_callback', lambda *args: None)
    monkeypatch.setattr(da, 'add_activity_item', lambda *args: None)
    monkeypatch.setattr(da, '_db_update_error_callback', pytest.fail)
    finished = []
    monkeypatch.setattr(da, '_db_update_finished_callback', lambda *args: finished.append(args))
    before = tagged_audio.read_bytes()
    expected = ISRC if has_isrc else None
    for _ in range(2):
        da._run_soulsync_full_refresh()
        with db._get_connection() as conn:
            row = conn.execute('SELECT id, isrc FROM tracks').fetchone()
            assert tuple(row) == (_stable_id(str(tagged_audio)) + '::soulsync', expected)
        assert finished[-1] == (1, 1, 1, 1, 0)
        assert tagged_audio.read_bytes() == before
