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


def test_writing_aiff_is_still_not_offered(tagged_aiff):
    """reading AIFF is the fix; writing it is a separate change."""
    from core.tag_writer import write_tag_fields

    result = write_tag_fields(str(tagged_aiff), {'title': 'Other'})

    assert result.get('success') is not True
