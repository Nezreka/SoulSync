"""a finding the tag scan files writes what it flagged, and nothing else.

one library had 183k of these, and applying them took the Write Tags path:
every core tag and the embedded cover rewritten for "total_discs: missing".
"""

import json
from contextlib import closing

import pytest
from mutagen.flac import FLAC

from core.library2 import retag
from core.library2.validation import settle_scan_findings
from core.tag_writer import build_tag_diff, keeps_various_album_artist, total_is_settled
from tests.library2.test_metadata_consistency import (  # noqa: F401 - fixtures
    _no_cover_art_artist_name_search, library, observe,
)


@pytest.fixture(autouse=True)
def _genre_and_year_differences_are_flagged(monkeypatch):
    """these tests use a changed genre and year as their example of a tag that
    differs. those two are only filed when the setting asks (off by default,
    tests/library2/test_retag_fill_only.py), so here it asks."""
    monkeypatch.setattr('core.library2.validation.quiet_differences', lambda config: frozenset())


def _finding(db, checks, **details):
    payload = {'validation': {'file_id': 1, 'track_id': 7, 'album_id': 1, 'checks': checks,
                              'source': 'scan'}, **details}
    with closing(db._get_connection()) as conn, conn:
        conn.execute(
            "INSERT INTO repair_findings(job_id,finding_type,severity,entity_type,entity_id,file_path,title,"
            "description,details_json) VALUES('library_retag','library_retag','info','track','lib2:7',?,"
            "'Metadata issues','x',?)",
            (conn.execute('SELECT path FROM lib2_track_files WHERE id=1').fetchone()[0], json.dumps(payload)))


def test_scan_finding_options_are_the_flagged_fields():
    options = retag.scan_finding_options({'validation': {'checks': {
        'genre': 'mismatch', 'albumartist': 'missing', 'title': 'correct', 'edition': 'correct',
        'artwork_sidecar': 'missing'}}})
    assert options['fields'] == ['album_artist', 'cover_art', 'genre']
    assert options['cover_art'] == 'fill_missing'


def test_scan_finding_with_nothing_flagged_writes_nothing():
    # an empty list, never None: None is "every field" to the writer
    options = retag.scan_finding_options({'validation': {'checks': {'title': 'correct'}}})
    assert options['fields'] == []
    assert retag.scan_finding_options({}) is None


def test_a_scan_finding_fix_leaves_unflagged_tags_alone(library):
    """the file's genre and artist differ from the library's, the finding is
    about the year: only the year is written"""
    db, path, _ = library
    audio = FLAC(path)
    audio['genre'], audio['date'], audio['title'] = ['Krautrock'], ['1999'], ['track 7']
    audio.save()
    options = retag.scan_finding_options({'validation': {'checks': {'year': 'mismatch'}}})
    stats = retag.write_tags(db, [7], embed_cover=False, options=options)
    assert stats['written'] == 1, stats
    after = FLAC(path)
    assert after['date'] == ['2026']
    assert after['genre'] == ['Krautrock']
    assert after['title'] == ['track 7']


def test_no_disc_total_on_one_disc_is_not_a_change():
    data = {'total_discs': 1, 'disc_number': 1, 'track_count': 5, 'track_number': 2}
    rows = {d['file_key']: d for d in build_tag_diff({'disc_number': 1, 'track_number': 2, 'total_tracks': 5}, data)}
    assert rows['total_discs']['changed'] is False
    # two discs and no total is still filled
    rows = {d['file_key']: d for d in build_tag_diff(
        {'disc_number': 1, 'track_number': 2, 'total_tracks': 5}, {**data, 'total_discs': 2})}
    assert rows['total_discs']['changed'] is True


def test_a_file_counting_the_whole_album_keeps_its_total():
    data = {'track_count': 16, 'album_track_total': 32, 'track_number': 3, 'disc_number': 1, 'total_discs': 2}
    tags = {'track_number': 3, 'disc_number': 1, 'total_tracks': 32, 'total_discs': 2}
    rows = {d['file_key']: d for d in build_tag_diff(tags, data)}
    assert rows['total_tracks']['changed'] is False
    assert total_is_settled('total_tracks', 31, 16, data) is False


def test_various_artists_is_kept_on_a_track_by_someone_else():
    assert keeps_various_album_artist('Various Artists', 'Official Soundtrack', 'Michael Giacchino')
    # a one-artist album mis-tagged as various is still corrected
    assert not keeps_various_album_artist('Various Artists', 'Akon', 'Akon, Snoop Dogg')
    assert not keeps_various_album_artist('Akon', 'Official Soundtrack', 'Michael Giacchino')
    rows = {d['file_key']: d for d in build_tag_diff(
        {'album_artist': 'Various Artists', 'artist': 'Michael Giacchino'},
        {'artist_name': 'Official Soundtrack', 'track_artist': 'Michael Giacchino'})}
    assert (rows['album_artist']['changed'], rows['album_artist']['protected']) == (False, True)


def test_the_album_artist_stays_out_of_a_track_it_does_not_perform():
    credits = ['Official Soundtrack', 'Michael Giacchino']
    assert retag._performers(credits, 'Official Soundtrack', 'Michael Giacchino') == ['Michael Giacchino']
    # the track's own artist names the album artist: the full credit stands
    assert retag._performers(['Drake', 'Wizkid'], 'Drake', 'Drake, Wizkid') == ['Drake', 'Wizkid']
    # no artist of its own to go by
    assert retag._performers(credits, 'Official Soundtrack', None) == credits


def test_a_release_date_is_written_without_a_time():
    assert retag._date_only('2019-02-22T08:00:00Z') == '2019-02-22'
    assert retag._date_only('2019-02-22') == '2019-02-22'
    assert retag._date_only(None) is None


def test_settle_closes_a_finding_that_is_no_longer_an_issue(library):
    db, path, cfg = library
    audio = FLAC(path)
    del audio['disctotal']
    audio.save()
    observe(library)
    _finding(db, {'total_discs': 'missing'})
    stats = settle_scan_findings(db, cfg)
    assert stats['closed'] == 1
    with closing(db._get_connection()) as conn:
        assert conn.execute('SELECT status, user_action FROM repair_findings').fetchone()[:2] == ('resolved', 'obsolete')


def test_settle_keeps_a_finding_that_still_stands(library):
    db, path, cfg = library
    audio = FLAC(path)
    audio['date'] = ['1999']
    del audio['disctotal']
    audio.save()
    observe(library)
    with closing(db._get_connection()) as conn, conn:
        conn.execute('DELETE FROM repair_findings')  # the scan filed its own; use the old shape
    _finding(db, {'total_discs': 'missing', 'year': 'mismatch'})
    stats = settle_scan_findings(db, cfg)
    assert (stats['closed'], stats['updated']) == (0, 1)
    with closing(db._get_connection()) as conn:
        status, description, details = conn.execute(
            'SELECT status, description, details_json FROM repair_findings').fetchone()[:3]
    assert status == 'pending'
    assert description == 'year: mismatch'
    assert json.loads(details)['required_fields'] == ['year']


def test_settle_leaves_a_joined_genre_finding_for_the_next_read(library):
    """the cache does not record how many values the genre tag holds, so a
    "several genres in one value" finding cannot be judged from it"""
    db, path, cfg = library
    observe(library)
    _finding(db, {'genre': 'mismatch'})
    stats = settle_scan_findings(db, cfg)
    assert stats['closed'] == 0
    with closing(db._get_connection()) as conn:
        assert conn.execute('SELECT status FROM repair_findings').fetchone()[0] == 'pending'
