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
    diff_has_actionable_change,
    guard_placeholder_overwrite,
    is_placeholder_meta,
    write_tags_to_file,
)


# ---------------------------------------------------------------------------
# #1052 — write only affected files: diff_has_actionable_change
# ---------------------------------------------------------------------------

def _diff(changed_field=None, cover_changed=False):
    """Minimal diff list: one metadata row + the cover row."""
    return [
        {'file_key': 'title', 'field': 'Title', 'changed': changed_field == 'title'},
        {'file_key': 'genre', 'field': 'Genre', 'changed': changed_field == 'genre'},
        {'file_key': 'cover_art', 'field': 'Cover Art', 'changed': cover_changed},
    ]


def test_no_change_is_not_actionable():
    assert diff_has_actionable_change(_diff()) is False
    assert diff_has_actionable_change([]) is False


def test_metadata_change_is_actionable():
    assert diff_has_actionable_change(_diff(changed_field='genre')) is True
    # ...regardless of the cover-embed setting
    assert diff_has_actionable_change(_diff(changed_field='genre'), embed_cover=False) is True


def test_cover_only_change_respects_embed_flag():
    # cover missing + embedding ON → worth writing
    assert diff_has_actionable_change(_diff(cover_changed=True), embed_cover=True) is True
    # cover missing + embedding OFF → the writer wouldn't touch it, so skip
    assert diff_has_actionable_change(_diff(cover_changed=True), embed_cover=False) is False


def test_matches_preview_has_changes_via_build_tag_diff():
    # An unchanged file (tags already match the DB) must be non-actionable, so the
    # batch write skips exactly what the preview marks unchanged.
    file_tags = {'title': 'Song', 'artist': 'Art', 'album': 'Alb',
                 'album_artist': 'Art', 'has_cover_art': True}
    db_data = {'title': 'Song', 'artist_name': 'Art', 'album_title': 'Alb',
               'thumb_url': 'http://x/c.jpg'}
    diff = build_tag_diff(file_tags, db_data)
    assert any(d['changed'] for d in diff) is False
    assert diff_has_actionable_change(diff, embed_cover=True) is False


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


# ---------------------------------------------------------------------------
# #824 — full release dates must not be downgraded to just the year
# ---------------------------------------------------------------------------

def test_diff_full_date_same_year_not_flagged():
    # File has the full date 2023-11-03; DB has year 2023. Same year → the writer
    # keeps the full date, so it must NOT show as a change.
    diff = {d['field']: d for d in build_tag_diff({'year': '2023-11-03'}, {'year': 2023})}
    assert diff['Year']['changed'] is False


def test_diff_different_year_still_flagged():
    # A genuinely different year is still a real change.
    diff = {d['field']: d for d in build_tag_diff({'year': '2022-11-03'}, {'year': 2023})}
    assert diff['Year']['changed'] is True


def test_write_preserves_full_date_when_year_matches(flac_path):
    audio = FLAC(flac_path)
    audio['date'] = ['2023-11-03']      # file already has the full release date
    audio.save()

    write_tags_to_file(flac_path, {'year': 2023}, embed_cover=False)   # DB knows only the year

    assert FLAC(flac_path).get('date') == ['2023-11-03']   # full date preserved, NOT downgraded


def test_write_corrects_year_when_it_actually_differs(flac_path):
    audio = FLAC(flac_path)
    audio['date'] = ['2022']
    audio.save()

    write_tags_to_file(flac_path, {'year': 2023}, embed_cover=False)

    assert FLAC(flac_path).get('date') == ['2023']         # wrong year still corrected


# ---------------------------------------------------------------------------
# #824 Part 2 — DB release_date (full date) is written, and wins over year
# ---------------------------------------------------------------------------

def test_write_uses_db_release_date_over_year(flac_path):
    write_tags_to_file(flac_path, {'year': 2023, 'release_date': '2023-09-01'},
                       embed_cover=False)
    assert FLAC(flac_path).get('date') == ['2023-09-01']   # full DB date written, not the year


def test_write_db_release_date_overrides_existing_file_date(flac_path):
    audio = FLAC(flac_path)
    audio['date'] = ['2023-11-03']        # file has a different (but same-year) date
    audio.save()
    write_tags_to_file(flac_path, {'year': 2023, 'release_date': '2023-09-01'},
                       embed_cover=False)
    # The DB's explicit full date is authoritative — it replaces the file's date.
    assert FLAC(flac_path).get('date') == ['2023-09-01']


def test_write_falls_back_to_year_when_no_release_date(flac_path):
    write_tags_to_file(flac_path, {'year': 2023, 'release_date': None}, embed_cover=False)
    assert FLAC(flac_path).get('date') == ['2023']


def test_diff_uses_release_date_when_present():
    # File has a full date that differs from the DB's release_date → real change.
    diff = {d['field']: d for d in build_tag_diff(
        {'year': '2023-11-03'}, {'year': 2023, 'release_date': '2023-09-01'})}
    assert diff['Year']['changed'] is True
    assert diff['Year']['db_value'] == '2023-09-01'

    # File already equals the DB release_date → no change.
    diff = {d['field']: d for d in build_tag_diff(
        {'year': '2023-09-01'}, {'year': 2023, 'release_date': '2023-09-01'})}
    assert diff['Year']['changed'] is False
