"""Re-tag compares the multi-value ARTISTS list like any other field.

A credit healed into two artists ("Camellia, Toby Fox" -> Camellia + Toby Fox)
keeps the display string "Camellia, Toby Fox" with the default separator, so
a display-only compare never told the Re-tag job the files were behind. The
list is a preview row now, selected with the artist field, written by the
same field writer and read back the same way, so a written file is clean.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

import core.tag_writer as tw
from core.library2.retag import _policy_diff


@pytest.fixture
def multi(monkeypatch):
    monkeypatch.setattr(tw, '_multi_artist_write_enabled', lambda: True)


DB = {'title': 'Song', 'artist_name': 'Camellia', 'track_artist': 'Camellia, Toby Fox',
      'artists_list': ['Camellia', 'Toby Fox']}


def _row(diff, key):
    return next((d for d in diff if d['file_key'] == key), None)


def test_a_missing_list_is_a_change_even_with_the_same_display(multi):
    diff = tw.build_tag_diff({'title': 'Song', 'artist': 'Camellia, Toby Fox'}, DB)
    assert _row(diff, 'artist')['changed'] is False
    assert _row(diff, 'artists')['changed'] is True
    assert _row(diff, 'artists')['db_value'] == 'Camellia; Toby Fox'


def test_the_same_list_is_no_change(multi):
    tags = {'title': 'Song', 'artist': 'Camellia, Toby Fox', 'artists': ['camellia', 'Toby Fox']}
    assert _row(tw.build_tag_diff(tags, DB), 'artists')['changed'] is False


def test_no_row_when_the_user_does_not_write_the_list(monkeypatch):
    monkeypatch.setattr(tw, '_multi_artist_write_enabled', lambda: False)
    assert _row(tw.build_tag_diff({'artist': 'Camellia, Toby Fox'}, DB), 'artists') is None


def test_no_row_for_a_single_artist(multi):
    assert _row(tw.build_tag_diff({'artist': 'Camellia'}, {**DB, 'artists_list': ['Camellia']}),
                'artists') is None


def test_the_list_is_selected_with_the_artist_field(multi):
    tags = {'title': 'Song', 'artist': 'Camellia, Toby Fox'}
    policy = {'fields': ['artist'], 'cover_art': 'skip', 'lyrics': 'skip', 'mode': 'overwrite'}
    assert _row(_policy_diff(tags, DB, policy), 'artists')['changed'] is True
    policy['fields'] = ['title']
    assert _row(_policy_diff(tags, DB, policy), 'artists')['changed'] is False


def _flac(path):
    import struct
    from mutagen.flac import FLAC
    si = bytearray(34)
    si[0:2] = struct.pack(">H", 4096)
    si[2:4] = struct.pack(">H", 4096)
    si[10] = 0x0A
    si[12] = 0x70
    path.write_bytes(b"fLaC" + bytes([0x80, 0x00, 0x00, 0x22]) + bytes(si) + bytes(range(256)) * 8)
    audio = FLAC(str(path))
    audio['artist'] = ['Camellia, Toby Fox']
    audio.save()


def _ffmpeg(path, codec):
    subprocess.run(['ffmpeg', '-y', '-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=mono', '-t', '0.2',
                    '-c:a', codec, str(path)], check=True, capture_output=True)


@pytest.mark.parametrize("ext,codec", [("flac", None), ("mp3", "libmp3lame"), ("m4a", "aac"),
                                       ("ogg", "libvorbis")])
def test_written_list_reads_back_as_no_change(tmp_path, multi, ext, codec):
    """write and read agree on the frame (TXXX:Artists, the iTunes ARTISTS
    atom, the ARTISTS comment), or Re-tag would rewrite the file forever"""
    path = tmp_path / f"song.{ext}"
    if codec is None:
        _flac(path)
    elif shutil.which('ffmpeg'):
        _ffmpeg(path, codec)
    else:
        pytest.skip('ffmpeg not installed')
    result = tw.write_tag_fields(str(path), {'artist': 'Camellia, Toby Fox',
                                             'artists': ['Camellia', 'Toby Fox']})
    assert result['success'], result
    tags = tw.read_file_tags(str(path))
    assert tags['artists'] == ['Camellia', 'Toby Fox']
    assert _row(tw.build_tag_diff(tags, DB), 'artists')['changed'] is False
    # and a second write changes nothing again
    tw.write_tag_fields(str(path), {'artists': ['Camellia', 'Toby Fox']})
    assert tw.read_file_tags(str(path))['artists'] == ['Camellia', 'Toby Fox']


@pytest.mark.parametrize('partial', [True, False])
def test_picards_artists_frame_is_read_and_kept_one(tmp_path, multi, partial):
    """Nezreka's review: Picard writes TXXX:ARTISTS; only TXXX:Artists was
    read, so its files always showed a change and got a second frame"""
    if not shutil.which('ffmpeg'):
        pytest.skip('ffmpeg not installed')
    from mutagen.id3 import ID3, TXXX
    path = tmp_path / "picard.mp3"
    _ffmpeg(path, 'libmp3lame')
    tags = ID3(str(path))
    tags.add(TXXX(encoding=3, desc='ARTISTS', text=['Camellia', 'Toby Fox']))
    tags.save(str(path))
    read = tw.read_file_tags(str(path))
    assert read['artists'] == ['Camellia', 'Toby Fox']
    assert _row(tw.build_tag_diff(read, DB), 'artists')['changed'] is False
    artists = ['Camellia', 'Toby Fox', 'Guest']
    if partial:
        result = tw.write_tag_fields(str(path), {'artists': artists})
    else:
        result = tw.write_tags_to_file(str(path), {**DB, 'artists_list': artists})
    assert result['success'], result
    frames = [k for k in ID3(str(path)).keys() if k.upper() == 'TXXX:ARTISTS']
    assert frames == ['TXXX:ARTISTS']
    assert tw.read_file_tags(str(path))['artists'] == ['Camellia', 'Toby Fox', 'Guest']
