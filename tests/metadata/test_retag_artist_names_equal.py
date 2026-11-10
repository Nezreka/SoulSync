"""the library re-tag job reported 'artist: mismatch' for files that name the
same artists, spelled or joined differently (Storm): 20k of 24k artist
mismatches on one library were only that. a real difference still counts."""

import pytest

from core.tag_writer import build_tag_diff


def _changed(field, file_val, db):
    diff = {d['file_key']: d for d in build_tag_diff({field: file_val}, db)}
    return diff[field]['changed']


@pytest.mark.parametrize('file_val, db_val', [
    ('8ruki, Jolagreen23', '8ruki; Jolagreen23'),
    ('7L & Esoteric', '7L; 7L & Esoteric'),
    ('Tōru Takemitsu', 'Toru Takemitsu'),
    ('Hiroyuki Sawano <Vocal: Eliana>', 'Hiroyuki Sawano'),
])
def test_the_same_artists_are_not_a_mismatch(file_val, db_val):
    assert not _changed('artist', file_val, {'track_artist': db_val, 'artist_name': db_val})


def test_a_curly_apostrophe_album_artist_is_the_same_artist():
    assert not _changed('album_artist', 'Jim O’Rourke', {'artist_name': "Jim O'Rourke"})


@pytest.mark.parametrize('file_val, db_val', [
    ('7L', '7L; NAYSSA'),
    ('Akon', '21 Savage'),
])
def test_a_different_set_of_artists_still_is(file_val, db_val):
    assert _changed('artist', file_val, {'track_artist': db_val, 'artist_name': db_val})
