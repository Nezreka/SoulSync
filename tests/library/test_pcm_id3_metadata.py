"""Exercise real AIFF/WAV containers through Standalone, Import and maintenance readers."""

import struct
import wave
from types import SimpleNamespace

import pytest
from mutagen import File as MutagenFile
from mutagen.aiff import AIFF
from mutagen.id3 import TALB, TCON, TDRC, TIT2, TPE1, TPE2, TPOS, TRCK, TSRC, TXXX, UFID
from mutagen.wave import WAVE

from core.auto_import_worker import _read_file_tags
from core.imports.staging import _read_staging_audio_tags, read_staging_file_metadata
from core.imports.track_number import read_embedded_album, read_embedded_album_artist, read_embedded_track_number
from core.soulsync_client import _read_tags, _stable_id
from core.tag_writer import read_file_tags


ARTIST = 'A Hundred Drums, VEIL'
TITLE = 'Shockwave'
ISRC = 'QZZ792411931'
RECORDING_ID = '8a89a04f-7eba-4c0c-bf0c-5c9d7d54df54'


@pytest.fixture(params=['.aif', '.aiff', '.AIFF', '.wav', '.WAV'])
def audio_path(tmp_path, request):
    """A tenth of a second of PCM with genuine embedded ID3, no binary fixture."""
    path = tmp_path / f'{ARTIST} - {TITLE}{request.param}'
    if request.param.lower() == '.wav':
        with wave.open(str(path), 'wb') as pcm:
            pcm.setparams((2, 2, 44100, 0, 'NONE', 'not compressed'))
            pcm.writeframes(bytes(4410 * 4))
        audio = WAVE(path)
    else:
        comm = struct.pack('>hIh', 2, 4410, 16) + bytes.fromhex('400eac44000000000000')
        ssnd = struct.pack('>II', 0, 0) + bytes(4410 * 4)
        body = b'AIFFCOMM' + struct.pack('>I', len(comm)) + comm
        body += b'SSND' + struct.pack('>I', len(ssnd)) + ssnd
        path.write_bytes(b'FORM' + struct.pack('>I', len(body)) + body)
        audio = AIFF(path)
    audio.add_tags()
    for frame in (
        TIT2(text=[TITLE]), TPE1(text=[ARTIST]), TALB(text=['Shockwave EP']),
        TDRC(text=['2024-06-21']), TSRC(text=[ISRC]),
        TRCK(text=['2/5']), TPOS(text=['1/2']), TCON(text=['Electronic', 'Bass']),
        TXXX(desc='MusicBrainz Album Id', text=['release-id']),
        TXXX(desc='MusicBrainz Album Comment', text=['deluxe']),
        UFID(owner='http://musicbrainz.org', data=RECORDING_ID.encode('ascii')),
    ):
        audio.tags.add(frame)
    audio.save()
    return path


def test_scan_and_import_read_embedded_id3_metadata(audio_path):
    before = audio_path.read_bytes()
    for reader in (_read_tags, _read_file_tags):
        tags = reader(str(audio_path))
        assert tags['artist'] == ARTIST
        assert tags['title'] == TITLE
        assert tags['album'] == 'Shockwave EP'
        assert tags['year'] == '2024'
        assert tags['track_number'] == 2
        assert tags['disc_number'] == 1
        assert tags['duration_ms'] == 100
    assert _read_tags(str(audio_path))['bitrate'] > 0
    assert _read_tags(str(audio_path))['musicbrainz_albumid'] == 'release-id'
    assert _read_tags(str(audio_path))['musicbrainz_albumcomment'] == 'deluxe'
    assert _read_file_tags(str(audio_path))['mbid'] == RECORDING_ID
    assert _read_file_tags(str(audio_path))['isrc'] == ISRC
    assert _read_file_tags(str(audio_path))['genres'] == ['Electronic', 'Bass']
    assert audio_path.read_bytes() == before


def test_staging_and_tag_reader_support_id3_containers(audio_path):
    from core.library.file_tags import read_embedded_tags

    before = audio_path.read_bytes()
    tags = read_staging_file_metadata(str(audio_path))
    assert (tags['artist'], tags['title'], tags['album']) == (ARTIST, TITLE, 'Shockwave EP')
    assert tags['duration_ms'] == 100
    assert tags['track_number'] == 2
    assert _read_staging_audio_tags(str(audio_path)) == ('Shockwave EP', ARTIST)
    assert read_embedded_album(str(audio_path)) == 'Shockwave EP'
    assert read_embedded_track_number(str(audio_path)) == 2
    assert read_file_tags(str(audio_path))['artist'] == ARTIST
    assert read_file_tags(str(audio_path))['error'] is None
    assert read_embedded_tags(str(audio_path))['tags']['artist'] == ARTIST
    assert audio_path.read_bytes() == before

    audio = MutagenFile(audio_path)
    audio.tags.add(TPE2(text=['Album Artist']))
    audio.save()
    before = audio_path.read_bytes()
    assert read_embedded_album_artist(str(audio_path)) == 'Album Artist'
    assert _read_tags(str(audio_path))['album_artist'] == 'Album Artist'
    assert _read_file_tags(str(audio_path))['artist'] == 'Album Artist'
    assert audio_path.read_bytes() == before


def test_partial_and_bad_tags_preserve_usable_metadata(audio_path):
    audio = MutagenFile(audio_path)
    audio.tags.clear()
    audio.tags.add(TPE1(text=[ARTIST]))
    audio.tags.add(TIT2(text=[TITLE]))
    audio.tags.add(TRCK(text=['invalid']))
    audio.tags.add(TPOS(text=['invalid']))
    audio.tags.add(TDRC(text=['invalid']))
    audio.save()
    for reader in (_read_tags, _read_file_tags):
        tags = reader(str(audio_path))
        assert (tags['artist'], tags['title']) == (ARTIST, TITLE)
        assert tags['album'] == tags['year'] == ''
        assert tags['track_number'] == 0
        assert tags['disc_number'] == 1
        assert tags['duration_ms'] == 100


def test_untagged_and_corrupt_audio_fall_back_gracefully(audio_path):
    audio = MutagenFile(audio_path)
    audio.delete()
    assert _read_tags(str(audio_path))['title'] == f'{ARTIST} - {TITLE}'
    assert _read_tags(str(audio_path))['artist'] == ''
    assert _read_tags(str(audio_path))['duration_ms'] == 100
    assert _read_file_tags(str(audio_path))['duration_ms'] == 100
    audio_path.write_bytes(b'not an audio file')
    assert _read_tags(str(audio_path))['artist'] == ''
    assert _read_file_tags(str(audio_path))['title'] == ''


def test_unknown_artist_job_resolves_id3_containers_from_tags(audio_path, monkeypatch):
    from core.repair_jobs import unknown_artist_fixer as fixer

    monkeypatch.setattr(fixer, 'get_primary_source', lambda: 'spotify')
    monkeypatch.setattr(fixer, 'get_client_for_source', lambda source: None)
    context = SimpleNamespace(config_manager=SimpleNamespace(get=lambda *args: {}))
    corrected = fixer.UnknownArtistFixerJob()._resolve_metadata(
        context, {'title': f'{ARTIST} - {TITLE}', 'album_title': 'Unknown Album'}, str(audio_path))
    assert corrected['artist'] == ARTIST
    assert corrected['title'] == TITLE
    assert corrected['source'] == 'file_tags'


def test_full_refresh_replaces_unknown_rows_from_real_id3_containers(audio_path, tmp_path, monkeypatch):
    from api import database_admin as da
    from database.music_database import MusicDatabase

    db = MusicDatabase(str(tmp_path / 'library.db'))
    track_id = _stable_id(str(audio_path)) + '::soulsync'
    with db._get_connection() as conn:
        conn.execute("INSERT INTO artists (id, name, server_source) VALUES ('unknown', 'Unknown Artist', 'soulsync')")
        conn.execute("INSERT INTO albums (id, artist_id, title, server_source) VALUES ('unknown', 'unknown', 'Unknown Album', 'soulsync')")
        conn.execute("INSERT INTO tracks (id, album_id, artist_id, title, file_path, server_source) VALUES (?, 'unknown', 'unknown', ?, ?, 'soulsync')",
                     (track_id, audio_path.stem, str(audio_path)))
        conn.commit()

    monkeypatch.setattr(da, 'get_database', lambda: db)
    monkeypatch.setattr(da, 'config_manager', SimpleNamespace(get=lambda *args: str(tmp_path)))
    monkeypatch.setattr(da, 'docker_resolve_path', lambda p: p)
    monkeypatch.setattr(da, '_db_update_phase_callback', lambda *args: None)
    monkeypatch.setattr(da, 'add_activity_item', lambda *args: None)
    monkeypatch.setattr(da, '_db_update_error_callback', pytest.fail)
    finished = []
    monkeypatch.setattr(da, '_db_update_finished_callback', lambda *args: finished.append(args))
    before = audio_path.read_bytes()

    # Run twice: existing rows must be rebuilt, not skipped as already indexed.
    for _ in range(2):
        da._run_soulsync_full_refresh()
        with db._get_connection() as conn:
            row = conn.execute('SELECT t.id, t.title, ar.name, al.title FROM tracks t '
                               'JOIN artists ar ON ar.id=t.artist_id JOIN albums al ON al.id=t.album_id').fetchone()
            assert tuple(row) == (track_id, TITLE, ARTIST, 'Shockwave EP')
            assert conn.execute("SELECT COUNT(*) FROM artists WHERE name='Unknown Artist'").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM albums WHERE title='Unknown Album'").fetchone()[0] == 0
        assert finished[-1] == (1, 1, 1, 1, 0)
        assert audio_path.read_bytes() == before
