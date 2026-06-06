"""#800 — Write Tags must never overwrite a real file value with a placeholder.

A mis-grouped track sits under a 'Various Artists' / '[Unknown Album]' record,
while the file itself is correctly tagged. Previously Write Tags stamped that
junk over the good file (data loss). These pin the guard at both seams: the
preview (build_tag_diff) and the actual write (write_tags_to_file).
"""

from __future__ import annotations

import os
import tempfile

import pytest
from mutagen.flac import FLAC

from core.tag_writer import (
    build_tag_diff,
    guard_placeholder_overwrite,
    is_placeholder_meta,
    write_tags_to_file,
)


# ---------------------------------------------------------------------------
# pure helpers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('val', [
    None, '', '   ', 'Various Artists', 'various artists', 'VARIOUS ARTIST',
    'Unknown Artist', 'Unknown Album', '[Unknown Album]', 'Unknown', 'Untitled Album',
])
def test_placeholder_values_detected(val):
    assert is_placeholder_meta(val) is True


@pytest.mark.parametrize('val', ['OneRepublic', 'Native (Deluxe)', 'Counting Stars', 'VA Movement'])
def test_real_values_not_placeholder(val):
    assert is_placeholder_meta(val) is False


def test_guard_blocks_placeholder_over_real():
    assert guard_placeholder_overwrite('Various Artists', 'OneRepublic') is None
    assert guard_placeholder_overwrite('[Unknown Album]', 'Native (Deluxe)') is None


def test_guard_allows_real_over_anything():
    assert guard_placeholder_overwrite('OneRepublic', 'Old Wrong Name') == 'OneRepublic'
    assert guard_placeholder_overwrite('OneRepublic', '') == 'OneRepublic'


def test_guard_allows_placeholder_when_file_has_no_real_value():
    # Legit compilation: file album-artist empty → 'Various Artists' still writes.
    assert guard_placeholder_overwrite('Various Artists', '') == 'Various Artists'
    assert guard_placeholder_overwrite('Various Artists', 'Various Artists') == 'Various Artists'


# ---------------------------------------------------------------------------
# build_tag_diff — the preview (screenshot #2 scenario)
# ---------------------------------------------------------------------------

def test_diff_protects_placeholder_over_real():
    file_tags = {'title': 'Counting Stars', 'artist': 'OneRepublic',
                 'album': 'Native (Deluxe)', 'album_artist': 'OneRepublic'}
    db_data = {'title': 'Counting Stars', 'artist_name': 'Various Artists',
               'album_title': '[Unknown Album]'}
    diff = {d['field']: d for d in build_tag_diff(file_tags, db_data)}

    assert diff['Title']['changed'] is False          # already equal
    for f in ('Artist', 'Album', 'Album Artist'):
        assert diff[f]['changed'] is False, f          # held back, not a wrong overwrite
        assert diff[f]['protected'] is True, f
    # Nothing real to write → no changes overall.
    assert not any(d['changed'] for d in build_tag_diff(file_tags, db_data))


def test_diff_real_db_value_still_changes():
    file_tags = {'artist': 'Old Wrong'}
    db_data = {'artist_name': 'OneRepublic'}
    diff = {d['field']: d for d in build_tag_diff(file_tags, db_data)}
    assert diff['Artist']['changed'] is True
    assert diff['Artist']['protected'] is False


def test_diff_compilation_va_writes_when_file_empty():
    file_tags = {'album_artist': ''}  # no real value to protect
    db_data = {'artist_name': 'Various Artists'}
    diff = {d['field']: d for d in build_tag_diff(file_tags, db_data)}
    assert diff['Album Artist']['changed'] is True
    assert diff['Album Artist']['protected'] is False


# ---------------------------------------------------------------------------
# write_tags_to_file — end-to-end on a real FLAC
# ---------------------------------------------------------------------------

def _make_minimal_flac(path):
    minimal = (
        b'fLaC' + b'\x80\x00\x00\x22' + b'\x00\x10\x00\x10'
        + b'\x00\x00\x00\x00\x00\x00' + b'\x0a\xc4\x42\xf0\x00\x00\x00\x00' + b'\x00' * 16
    )
    with open(path, 'wb') as f:
        f.write(minimal)


@pytest.fixture
def flac_path():
    fd, path = tempfile.mkstemp(suffix='.flac')
    os.close(fd)
    _make_minimal_flac(path)
    yield path
    try:
        os.remove(path)
    except OSError:
        pass


def test_write_preserves_real_value_against_placeholder(flac_path):
    # 1) lay down correct tags (the file is correctly tagged)
    write_tags_to_file(flac_path, {
        'title': 'Counting Stars', 'artist_name': 'OneRepublic',
        'album_title': 'Native (Deluxe)',
    }, embed_cover=False)

    # 2) attempt to write the mis-grouped DB junk over it
    result = write_tags_to_file(flac_path, {
        'title': 'Counting Stars', 'artist_name': 'Various Artists',
        'album_title': '[Unknown Album]',
    }, embed_cover=False)
    assert result['success'] is True

    audio = FLAC(flac_path)
    assert audio.get('artist') == ['OneRepublic']          # preserved, not clobbered
    assert audio.get('album') == ['Native (Deluxe)']       # preserved
    assert audio.get('albumartist') == ['OneRepublic']     # preserved


def test_write_real_value_still_overwrites(flac_path):
    write_tags_to_file(flac_path, {'artist_name': 'OneRepublic'}, embed_cover=False)
    # A real (non-placeholder) new value must still overwrite normally.
    result = write_tags_to_file(flac_path, {'artist_name': 'Coldplay'}, embed_cover=False)
    assert result['success'] is True
    assert FLAC(flac_path).get('artist') == ['Coldplay']
