"""a re-tag finding shows the tag in the file beside the one it would write,
keeps what it changed, and can put it back."""

import json
from contextlib import closing

from mutagen.flac import FLAC

from core.library2 import retag
from core.repair_worker import RepairWorker, attach_retag_preview
from core.tag_writer import read_number_pair, restore_tag_fields
from tests.library2.test_metadata_consistency import (  # noqa: F401 - fixtures
    _no_cover_art_artist_name_search, library, observe,
)


def _drift(path):
    audio = FLAC(path)
    audio['genre'], audio['date'] = ['Krautrock'], ['1999']
    audio.save()


def _item(db):
    with closing(db._get_connection()) as conn:
        row = dict(conn.execute('SELECT * FROM repair_findings').fetchone())
    row['details'] = json.loads(row.pop('details_json'))
    return row


def _worker(db, config):
    worker = object.__new__(RepairWorker)
    worker.db, worker._config_manager, worker.transfer_folder = db, config, None
    return worker


def test_preview_puts_the_file_value_beside_the_library_value(library):
    db, path, _ = library
    _drift(path)
    observe(library)
    item = _item(db)
    with closing(db._get_connection()) as conn:
        attach_retag_preview(conn, [item])
    rows = {r['key']: (r['before'], r['after']) for r in item['preview']}
    # only what the finding flagged, never the rest of the tag set
    assert rows == {'year': ('1999', '2026'), 'genre': ('Krautrock', 'Rock')}


def test_preview_is_left_off_what_is_not_an_open_retag_finding(library):
    db, path, _ = library
    _drift(path)
    observe(library)
    closed, other = {**_item(db), 'status': 'resolved'}, {**_item(db), 'finding_type': 'missing_lyrics'}
    with closing(db._get_connection()) as conn:
        attach_retag_preview(conn, [closed, other])
    assert 'preview' not in closed and 'preview' not in other


def test_a_write_records_before_and_after_only_when_asked(library):
    db, path, _ = library
    _drift(path)
    options = retag.scan_finding_options({'validation': {'checks': {'year': 'mismatch'}}})
    stats = retag.write_tags(db, [7], embed_cover=False, options=options, record_changes=True)
    assert stats['written'] == 1, stats
    (change,) = stats['changes']
    assert change['file_id'] == 1 and change['cover'] is None
    assert change['fields'] == [{'key': 'year', 'label': 'Year', 'before': '1999', 'after': '2026'}]
    _drift(path)
    assert 'changes' not in retag.write_tags(db, [7], embed_cover=False, options=options)


def test_undo_puts_the_old_tags_back_and_reopens_the_finding(library):
    db, path, config = library
    _drift(path)
    observe(library)
    finding_id = _item(db)['id']
    options = retag.scan_finding_options(_item(db)['details'])
    stats = retag.write_tags(db, [7], embed_cover=False, options=options, record_changes=True)
    assert FLAC(path)['date'] == ['2026'] and FLAC(path)['genre'] == ['Rock']
    worker = _worker(db, config)
    worker._save_applied(finding_id, stats['changes'])
    with closing(db._get_connection()) as conn, conn:
        conn.execute("UPDATE repair_findings SET status='resolved', user_action='applied_tags' WHERE id=?", (finding_id,))
    assert {f['key'] for f in _item(db)['details']['applied']['changes'][0]['fields']} == {'year', 'genre'}

    result = worker.undo_finding(finding_id)

    assert result['success'] is True, result
    after = FLAC(path)
    assert after['date'] == ['1999'] and after['genre'] == ['Krautrock']
    assert after['title'] == ['Track 7']
    item = _item(db)
    assert item['status'] == 'pending' and item['user_action'] is None
    assert 'applied' not in item['details'] and item['details']['undone']['kept'] == []
    # one row, reopened: the re-read did not file a second finding for the file
    with closing(db._get_connection()) as conn:
        assert conn.execute('SELECT COUNT(*) FROM repair_findings').fetchone()[0] == 1


def test_undo_refuses_a_finding_with_nothing_recorded(library):
    db, path, config = library
    _drift(path)
    observe(library)
    worker = _worker(db, config)
    # open, and nothing applied: there is no before value to go back to
    assert worker.undo_finding(_item(db)['id'])['success'] is False
    assert worker.undo_finding(9999)['success'] is False
    assert FLAC(path)['date'] == ['1999']


def test_restore_removes_a_tag_that_was_empty_before(library):
    _, path, _ = library
    result = restore_tag_fields(str(path), {'genre': '', 'total_tracks': '', 'title': 'Old title',
                                            'spotify_track_id': ''})
    assert result['success'] is True, result
    audio = FLAC(path)
    assert 'genre' not in audio and audio['title'] == ['Old title']
    assert read_number_pair(audio, 'track') == (7, None)
    # a source id has no clean removal here, so it is named rather than guessed at
    assert result['kept_fields'] == ['spotify_track_id']


# ── #2043: an unknown edition holds the numbers, not the whole fix ───────────

def _retag_details(checks):
    return {'validation': {'file_id': 1, 'track_id': 7, 'album_id': 1, 'source': 'scan',
                           'checks': {**checks, 'edition': 'unknown'}}}


def test_an_unknown_edition_still_writes_the_tags_that_do_not_depend_on_it(library):
    """the fix refused outright ("Select the release edition in Re-identify
    before writing track numbers") for a finding that also wanted the album
    tag, and Re-identify was nowhere on the page it pointed at."""
    db, path, config = library
    audio = FLAC(path)
    audio['genre'], audio['tracknumber'] = ['Krautrock'], ['3']
    audio.save()
    worker = _worker(db, config)
    result = worker._fix_library_retag('track', 'lib2:7', str(path), _retag_details(
        {'genre': 'mismatch', 'track_number': 'mismatch'}))
    assert result['success'] is True, result
    after = FLAC(path)
    assert after['genre'] == ['Rock']
    # the number is left exactly as it was, and the answer says so
    assert after['tracknumber'] == ['3']
    assert 'Re-identify' in result['message']
    assert {f['key'] for f in result['changes'][0]['fields']} == {'genre'}


def test_a_finding_about_nothing_but_the_numbers_waits_and_says_what_to_do(library):
    from core.repair_worker import RETAG_NEEDS_EDITION
    db, path, config = library
    audio = FLAC(path)
    audio['tracknumber'] = ['3']
    audio.save()
    result = _worker(db, config)._fix_library_retag('track', 'lib2:7', str(path), _retag_details(
        {'track_number': 'mismatch', 'total_tracks': 'missing'}))
    assert result == {'success': False, 'error': RETAG_NEEDS_EDITION}
    assert 'Re-identify' in RETAG_NEEDS_EDITION
    assert FLAC(path)['tracknumber'] == ['3']


def test_a_known_edition_writes_the_numbers_as_before(library):
    db, path, config = library
    audio = FLAC(path)
    audio['tracknumber'] = ['3']
    audio.save()
    details = _retag_details({'track_number': 'mismatch'})
    details['validation']['checks']['edition'] = 'correct'
    result = _worker(db, config)._fix_library_retag('track', 'lib2:7', str(path), details)
    assert result['success'] is True, result
    assert FLAC(path)['tracknumber'] == ['7']
