"""Multi-value genre tags through the retag path.

A Jellyfin user saw "Alternative Rock, Art Pop, Classical Crossover, Pop,
Singer-Songwriter" as ONE genre. The download pipeline already writes one
value per genre when "Write multi-value ARTISTS and GENRE tags" is on, but the
retag writer always wrote the joined string, and every reader took only the
FIRST value of a multi-value genre. So a retag check saw a correct multi-value
file as narrower than the catalogue and folded it back into one string.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from mutagen.flac import FLAC
from mutagen.id3 import ID3, TCON

from core.tag_writer import (
    _genre_read,
    build_tag_diff,
    read_file_tags,
    read_tag_field,
    set_tag_fields,
    write_tags_to_file,
)

GENRES = ['Alternative Rock', 'Art Pop', 'Pop']


def _make_minimal_flac(path):
    minimal = (
        b'fLaC' + b'\x80\x00\x00\x22' + b'\x00\x10\x00\x10'
        + b'\x00\x00\x00\x00\x00\x00' + b'\x0a\xc4\x42\xf0\x00\x00\x00\x00' + b'\x00' * 16
    )
    with open(path, 'wb') as f:
        f.write(minimal)


@pytest.fixture
def flac_path(tmp_path):
    path = tmp_path / 'song.flac'
    _make_minimal_flac(path)
    return str(path)


def _multi(on: bool):
    return patch('core.tag_writer._multi_artist_write_enabled', return_value=on)


def _genre_field(diffs):
    return next(d for d in diffs if d['file_key'] == 'genre')


def test_a_multi_value_genre_reads_whole(flac_path):
    audio = FLAC(flac_path)
    audio['genre'] = GENRES
    audio.save()
    tags = read_file_tags(flac_path)
    assert tags['genre'] == 'Alternative Rock, Art Pop, Pop'
    assert tags['genre_count'] == 3
    assert read_tag_field(FLAC(flac_path), 'genre') == 'Alternative Rock, Art Pop, Pop'


def test_an_id3_multi_value_genre_reads_whole():
    tags = ID3()
    tags.add(TCON(encoding=3, text=GENRES))
    values = [t for f in tags.getall('TCON') for t in f.text]
    assert _genre_read(values) == ('Alternative Rock, Art Pop, Pop', 3)


def test_the_retag_writes_one_value_per_genre_when_multi_is_on(flac_path):
    with _multi(True):
        write_tags_to_file(flac_path, {'title': 'Song', 'genres': GENRES})
    assert FLAC(flac_path)['genre'] == GENRES


def test_the_retag_keeps_one_joined_value_when_multi_is_off(flac_path):
    with _multi(False):
        write_tags_to_file(flac_path, {'title': 'Song', 'genres': GENRES})
    assert FLAC(flac_path)['genre'] == ['Alternative Rock, Art Pop, Pop']


def test_a_selected_field_genre_write_splits_when_multi_is_on(flac_path):
    audio = FLAC(flac_path)
    with _multi(True):
        set_tag_fields(audio, {'genre': 'Alternative Rock, Art Pop, Pop'})
    assert audio['genre'] == GENRES


def test_a_correct_multi_value_file_is_not_flagged(flac_path):
    # it read as "Alternative Rock" alone before, so it always looked changed
    audio = FLAC(flac_path)
    audio['genre'] = GENRES
    audio.save()
    with _multi(True):
        diffs = build_tag_diff(read_file_tags(flac_path), {'genres': GENRES})
    assert _genre_field(diffs)['changed'] is False


def test_a_joined_value_needs_a_retag_once_multi_is_on(flac_path):
    audio = FLAC(flac_path)
    audio['genre'] = ['Alternative Rock, Art Pop, Pop']
    audio.save()
    tags = read_file_tags(flac_path)
    with _multi(True):
        assert _genre_field(build_tag_diff(tags, {'genres': GENRES}))['changed'] is True
    with _multi(False):
        assert _genre_field(build_tag_diff(tags, {'genres': GENRES}))['changed'] is False


def test_a_single_genre_is_never_a_format_change(flac_path):
    audio = FLAC(flac_path)
    audio['genre'] = ['Pop']
    audio.save()
    with _multi(True):
        diffs = build_tag_diff(read_file_tags(flac_path), {'genres': ['Pop']})
    assert _genre_field(diffs)['changed'] is False
