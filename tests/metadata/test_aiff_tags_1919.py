"""#1919: AIFF files scanned as Unknown Artist with the filename as the title.

AIFF carries the same ID3 frames as an MP3, but mutagen's easy view has no
mapping for them, and the catalogue reader refused the extension outright.
"""

import struct

import pytest

from core.metadata.easy_tags import open_easy


def _aiff(path, *, frames=4410):
    """A minimal mono 16-bit AIFF of silence (no aifc: gone in 3.13)."""
    rate = b'\x40\x0e\xac\x44\x00\x00\x00\x00\x00\x00'  # 44100 as an 80-bit float
    comm = struct.pack('>hIh', 1, frames, 16) + rate
    ssnd = struct.pack('>II', 0, 0) + b'\x00\x00' * frames
    body = b'AIFF' + b'COMM' + struct.pack('>I', len(comm)) + comm \
        + b'SSND' + struct.pack('>I', len(ssnd)) + ssnd
    path.write_bytes(b'FORM' + struct.pack('>I', len(body)) + body)


@pytest.fixture
def tagged_aiff(tmp_path):
    from mutagen.aiff import AIFF
    from mutagen.id3 import TALB, TIT2, TPE1, TPE2, TRCK, TSRC, TXXX

    path = tmp_path / 'A Hundred Drums, VEIL - Shockwave.aiff'
    _aiff(path)
    audio = AIFF(path)
    audio.add_tags()
    for frame in (TIT2(encoding=3, text='Shockwave'), TPE1(encoding=3, text='A Hundred Drums, VEIL'),
                  TPE2(encoding=3, text='A Hundred Drums'), TALB(encoding=3, text='Shockwave EP'),
                  TRCK(encoding=3, text='2/4'), TSRC(encoding=3, text='QZZ792411931'),
                  TXXX(encoding=3, desc='MusicBrainz Album Id', text='mb-album-1')):
        audio.tags.add(frame)
    audio.save()
    return path


def test_the_standalone_scan_reads_aiff_tags(tagged_aiff):
    from core.soulsync_client import _read_tags

    tags = _read_tags(str(tagged_aiff))

    assert tags['title'] == 'Shockwave'
    assert tags['artist'] == 'A Hundred Drums, VEIL'
    assert tags['album_artist'] == 'A Hundred Drums'
    assert tags['album'] == 'Shockwave EP'
    assert tags['track_number'] == 2
    assert tags['musicbrainz_albumid'] == 'mb-album-1'


def test_the_catalogue_reader_reads_aiff_tags(tagged_aiff):
    from core.tag_writer import read_file_tags

    tags = read_file_tags(str(tagged_aiff))

    assert tags['error'] is None
    assert (tags['title'], tags['artist'], tags['album']) == (
        'Shockwave', 'A Hundred Drums, VEIL', 'Shockwave EP')
    assert tags['isrc'] == 'QZZ792411931'


def test_the_easy_view_matches_mutagens_keys(tagged_aiff):
    tags = open_easy(str(tagged_aiff))

    assert tags.get('title') == ['Shockwave']
    assert tags.get('isrc') == ['QZZ792411931']
    assert tags.get('missing', ['']) == ['']


def test_selected_aiff_tag_write_preserves_other_tags(tagged_aiff):
    from core.tag_writer import write_tag_fields

    result = write_tag_fields(str(tagged_aiff), {'title': 'Other'})

    assert result['success'] is True
    tags = open_easy(str(tagged_aiff))
    assert tags.get('title') == ['Other']
    assert tags.get('artist') == ['A Hundred Drums, VEIL']
    assert tags.get('album') == ['Shockwave EP']


def _sound_chunk(path):
    data = path.read_bytes()
    offset = 12
    while offset + 8 <= len(data):
        size = int.from_bytes(data[offset + 4:offset + 8], 'big')
        if data[offset:offset + 4] == b'SSND':
            return data[offset + 8:offset + 8 + size]
        offset += 8 + size + size % 2
    raise AssertionError('AIFF sound chunk missing')


@pytest.mark.parametrize('extension', ['aiff', 'aif', 'AIFF', 'AIF'])
@pytest.mark.parametrize('tagged', [False, True])
def test_retag_aiff_preserves_audio_and_unrelated_tags(tmp_path, extension, tagged):
    from mutagen.aiff import AIFF
    from mutagen.id3 import TXXX
    from core.tag_writer import read_file_tags, write_tags_to_file

    path = tmp_path / f'track.{extension}'
    _aiff(path)
    if tagged:
        audio = AIFF(path)
        audio.add_tags()
        audio.tags.add(TXXX(desc='CUSTOM', text=['preserved']))
        audio.save()
    before = _sound_chunk(path)

    result = write_tags_to_file(str(path), {
        'title': 'Reviewed', 'track_artist': 'Phaxe, Zora',
        'artist_name': 'Phaxe', 'album_title': 'Aventura',
        'release_date': '2019-08-30', 'genres': ['Electronic'],
        'track_number': 2, 'track_count': 4, 'disc_number': 1,
        'total_discs': 2, 'bpm': 138, 'isrc': 'DEKC41900062',
    }, cover_data=(b'cover', 'image/png'))

    assert result['success'] is True, result
    tags = read_file_tags(str(path))
    assert (tags['title'], tags['artist'], tags['album_artist'], tags['album']) == (
        'Reviewed', 'Phaxe, Zora', 'Phaxe', 'Aventura')
    assert tags['year'] == '2019-08-30'
    assert tags['genre'] == 'Electronic'
    assert (tags['track_number'], tags['total_tracks']) == (2, 4)
    assert (tags['disc_number'], tags['total_discs']) == (1, 2)
    assert tags['bpm'] == 138
    assert tags['isrc'] == 'DEKC41900062'
    assert AIFF(path).tags.getall('APIC')[0].data == b'cover'
    if tagged:
        assert AIFF(path).tags['TXXX:CUSTOM'].text == ['preserved']
    assert _sound_chunk(path) == before
    assert not list(tmp_path.glob('*.sstmp'))
