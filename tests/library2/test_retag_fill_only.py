"""the re-tag tool never throws a tag away in bulk.

83,574 findings on one library, simulated against its cached tags: Fix All
would have replaced the genre on 49,057 files (45,702 losing a genre the file
had: "Electro" for "Electronic", "Pop" for "R&B"), moved 13,143 to another
year, and rewritten 8,755 artist credits, some to "Ama Lou, Ama". The same
pile also held 32,817 blank BPMs and 11,015 blank disc totals, which lose
nothing by being filled.

so: a genre or a year that differs is shown and not filed as a fault unless
asked; a bulk run fills blank tags and replaces none; what a partial fill
wrote is kept and can be undone.
"""

import json
from contextlib import closing
from types import SimpleNamespace

from mutagen.flac import FLAC

from core.library2 import retag
from core.library2.validation import quiet_differences, settle_scan_findings
from core.repair_worker import (
    RETAG_DIFFERS_REVIEW, RETAG_REPLACE_ACTION, RepairWorker,
)
from tests.library2.test_metadata_consistency import (  # noqa: F401 - fixtures
    _no_cover_art_artist_name_search, library, observe,
)


def _findings(db):
    with closing(db._get_connection()) as conn:
        rows = [dict(r) for r in conn.execute('SELECT * FROM repair_findings ORDER BY id')]
    for row in rows:
        row['details'] = json.loads(row.pop('details_json') or '{}')
    return rows


def _worker(db, config):
    worker = object.__new__(RepairWorker)
    worker.db, worker._config_manager, worker.transfer_folder = db, config, None
    return worker


def _retag(path, **tags):
    audio = FLAC(path)
    for key, value in tags.items():
        if value is None:
            audio.pop(key, None)
        else:
            audio[key] = [value]
    audio.save()


def _flagging(library, **flags):
    """the same library, with the settings asking for these differences"""
    db, path, config = library
    settings = {f'flag_{name}_differences': on for name, on in flags.items()}
    asked = SimpleNamespace(get=lambda key, default=None: (
        settings if key == 'repair.jobs.library_retag.settings' else config.get(key, default)))
    return db, path, asked


# ── which differences are faults ─────────────────────────────────────────────

def test_genre_and_year_differences_are_quiet_unless_asked():
    off = SimpleNamespace(get=lambda key, default=None: default)
    assert quiet_differences(off) == {'genre', 'year'}
    genre_on = SimpleNamespace(get=lambda key, default=None: {'flag_genre_differences': True})
    assert quiet_differences(genre_on) == {'year'}
    # only True turns one on: a stray string from a hand-edited config does not
    odd = SimpleNamespace(get=lambda key, default=None: {'flag_year_differences': 'yes'})
    assert quiet_differences(odd) == {'genre', 'year'}
    broken = SimpleNamespace(get=lambda key, default=None: 1 / 0)
    assert quiet_differences(broken) == {'genre', 'year'}


def test_a_different_genre_and_year_file_no_finding(library):
    db, path, _ = library
    _retag(path, genre='Krautrock', date='1999')
    file = observe(library)
    checks = json.loads(file['tags_json'])['_validation']['checks']
    # seen, and on the record as a difference: not something to fix
    assert checks['genre'] == 'differs' and checks['year'] == 'differs'
    assert json.loads(file['tags_json'])['_validation']['status'] != 'issues'
    assert _findings(db) == []


def test_they_are_filed_when_the_setting_asks(library):
    asked = _flagging(library, genre=True, year=True)
    db, path, _ = asked
    _retag(path, genre='Krautrock', date='1999')
    file = observe(asked)
    checks = json.loads(file['tags_json'])['_validation']['checks']
    assert checks['genre'] == 'mismatch' and checks['year'] == 'mismatch'
    (finding,) = _findings(db)
    assert finding['finding_type'] == 'library_retag'


def test_a_blank_genre_is_always_filed(library):
    db, path, _ = library
    _retag(path, genre=None)
    file = observe(library)
    assert json.loads(file['tags_json'])['_validation']['checks']['genre'] == 'missing'
    assert len(_findings(db)) == 1


def test_any_other_difference_is_still_a_finding(library):
    db, path, _ = library
    _retag(path, album='Albun')
    observe(library)
    (finding,) = _findings(db)
    assert finding['details']['validation']['checks']['album'] == 'mismatch'


def test_the_recheck_closes_what_is_no_longer_a_fault(library):
    """the 49k genre and 13k year findings already filed go on the next start,
    without opening a file"""
    asked = _flagging(library, genre=True, year=True)
    db, path, _ = asked
    _retag(path, genre='Krautrock', date='1999')
    observe(asked)
    assert len(_findings(db)) == 1
    stats = settle_scan_findings(db, config=library[2])
    assert stats['closed'] == 1
    (finding,) = _findings(db)
    assert finding['status'] == 'resolved' and finding['user_action'] == 'obsolete'
    # the file was not touched
    assert FLAC(path)['genre'] == ['Krautrock'] and FLAC(path)['date'] == ['1999']


def test_the_recheck_keeps_the_part_that_still_stands(library):
    asked = _flagging(library, genre=True)
    db, path, _ = asked
    _retag(path, genre='Krautrock', album='Albun')
    observe(asked)
    settle_scan_findings(db, config=library[2])
    (finding,) = _findings(db)
    assert finding['status'] == 'pending'
    checks = finding['details']['validation']['checks']
    assert checks['album'] == 'mismatch' and checks['genre'] == 'differs'
    assert finding['description'] == 'album: mismatch'
    # and applying it writes the album, never the genre it once listed
    assert retag.scan_finding_options(finding['details'])['fields'] == ['album']


# ── what a write would do: fill or replace ───────────────────────────────────

def test_a_finding_splits_into_what_is_filled_and_what_is_replaced():
    details = {'validation': {'checks': {
        'genre': 'missing', 'total_discs': 'missing', 'album': 'mismatch', 'albumartist': 'mismatch',
        'title': 'correct', 'year': 'differs', 'cover': 'missing', 'edition': 'correct'}}}
    assert retag.scan_finding_parts(details) == {
        'fill': ['cover_art', 'genre', 'total_discs'], 'replace': ['album', 'album_artist']}
    fill = retag.scan_finding_options(details, fill_only=True)
    assert fill['fields'] == ['cover_art', 'genre', 'total_discs']
    # and never over a value that turned up since the scan
    assert fill['mode'] == 'fill_missing'
    everything = retag.scan_finding_options(details)
    assert everything['fields'] == ['album', 'album_artist', 'cover_art', 'genre', 'total_discs']
    assert everything['mode'] == 'overwrite'


def test_a_fill_only_write_ignores_what_an_older_scan_asked_for():
    # required_fields remembers every field the finding ever needed
    details = {'required_fields': ['album', 'genre'],
               'validation': {'checks': {'album': 'mismatch', 'genre': 'missing'}}}
    assert retag.scan_finding_options(details, fill_only=True)['fields'] == ['genre']
    assert retag.scan_finding_options({}, fill_only=True) is None


# ── a bulk run fills, a person replaces ──────────────────────────────────────

def _mixed(library):
    """a blank genre (a fill) and a misspelt album (a replacement), filed"""
    db, path, config = library
    _retag(path, genre=None, album='Albun')
    observe(library)
    (finding,) = _findings(db)
    return db, path, config, finding


def test_a_bulk_fix_fills_the_blank_and_leaves_the_difference(library):
    db, path, config, finding = _mixed(library)
    result = _worker(db, config).fix_finding(finding['id'])
    assert result['success'] is True and result['partial'] is True, result
    after = FLAC(path)
    assert after['genre'] == ['Rock']
    assert after['album'] == ['Albun']          # the file's own value, kept
    assert result['left_for_review'] == ['album']
    assert 'left for you to review' in result['message']
    (row,) = _findings(db)
    # still open for the album, with nothing on it that reads as a failure
    assert row['status'] == 'pending' and row['last_error'] is None
    assert row['fix_claimed_at'] is None
    assert row['details']['validation']['checks']['genre'] == 'correct'
    assert row['details']['validation']['checks']['album'] == 'mismatch'
    # what was written is on the row, as for any other fix
    (change,) = row['details']['applied']['changes']
    assert change['fields'] == [{'key': 'genre', 'label': 'Genre', 'before': '', 'after': 'Rock'}]


def test_the_fill_can_be_undone_while_the_finding_is_still_open(library):
    db, path, config, finding = _mixed(library)
    worker = _worker(db, config)
    worker.fix_finding(finding['id'])
    result = worker.undo_finding(finding['id'])
    assert result['success'] is True, result
    after = FLAC(path)
    assert 'genre' not in after and after['album'] == ['Albun']
    (row,) = _findings(db)
    assert row['status'] == 'pending' and 'applied' not in row['details']


def test_a_bulk_fix_does_not_touch_a_file_that_only_differs(library):
    db, path, config = library
    _retag(path, album='Albun')
    observe(library)
    (finding,) = _findings(db)
    before = FLAC(path).tags.pprint()
    result = _worker(db, config).fix_finding(finding['id'])
    assert result['success'] is False and result['skipped_by_design'] is True
    assert result['error'] == RETAG_DIFFERS_REVIEW
    assert FLAC(path).tags.pprint() == before
    assert _findings(db)[0]['status'] == 'pending'


def test_the_person_looking_at_the_finding_replaces(library):
    db, path, config, finding = _mixed(library)
    result = _worker(db, config).fix_finding(finding['id'], explicit=True)
    assert result['success'] is True and not result.get('partial'), result
    after = FLAC(path)
    assert after['genre'] == ['Rock'] and after['album'] == ['Album']
    assert _findings(db)[0]['status'] == 'resolved'


def test_a_bulk_run_replaces_only_when_it_asks_by_name(library):
    db, path, config, finding = _mixed(library)
    result = _worker(db, config).fix_finding(finding['id'], fix_action=RETAG_REPLACE_ACTION)
    assert result['success'] is True and not result.get('partial'), result
    assert FLAC(path)['album'] == ['Album']
    assert _findings(db)[0]['status'] == 'resolved'


def test_releasing_hand_set_fields_is_not_a_licence_to_replace(library):
    # 'overwrite_manual' is another bulk answer; it does not mean "replace"
    db, path, config, finding = _mixed(library)
    result = _worker(db, config).fix_finding(finding['id'], fix_action='overwrite_manual')
    assert result.get('partial') is True, result
    assert FLAC(path)['album'] == ['Albun']


# ── what a bulk run picks up ─────────────────────────────────────────────────

def _seed(db, path, checks, **details):
    payload = {'validation': {'file_id': 1, 'track_id': 7, 'album_id': 1, 'checks': checks,
                              'source': 'scan'}, **details}
    with closing(db._get_connection()) as conn, conn:
        return conn.execute(
            "INSERT INTO repair_findings(job_id,finding_type,severity,entity_type,entity_id,file_path,title,"
            "description,details_json) VALUES('library_retag','library_retag','info','track','lib2:7',?,"
            "'Metadata issues','x',?)", (str(path), json.dumps(payload))).lastrowid


def test_a_bulk_run_leaves_out_the_rows_it_could_only_refuse(library, monkeypatch):
    db, path, config = library
    monkeypatch.setattr('core.repair_worker._findings_library_clause', lambda: None)
    only_differs = _seed(db, path, {'album': 'mismatch', 'title': 'correct'})
    has_a_blank = _seed(db, path, {'album': 'mismatch', 'genre': 'missing'})
    only_blank = _seed(db, path, {'total_discs': 'missing'})
    # a re-tag job's own finding carries its reviewed diff and is not judged here
    job_made = _seed(db, path, {'album': 'mismatch'}, diff=[{'file_key': 'album', 'changed': True}])
    with_options = _seed(db, path, {'album': 'mismatch'}, retag_options={'mode': 'overwrite'})
    worker = _worker(db, config)
    monkeypatch.setattr(worker, '_fix_handlers', lambda: {'library_retag': None})
    picked = set(worker._pending_fixable_ids(finding_type='library_retag'))
    assert picked == {has_a_blank, only_blank, job_made, with_options}
    assert only_differs not in picked
    # asked for by name, it is in
    assert only_differs in set(worker._pending_fixable_ids(
        finding_type='library_retag', replace_tags=True))
    # and a row somebody ticked is judged the same way
    assert worker._pending_fixable_ids(finding_ids=[only_differs]) == []


# ── changing what counts as a fault re-checks what is open ───────────────────

def test_changing_a_difference_switch_rechecks_the_open_findings(library, monkeypatch):
    db, path, config = library
    store = {'repair.jobs.library_retag.settings': {'flag_genre_differences': True}}
    manager = SimpleNamespace(get=lambda key, default=None: store.get(key, default),
                              set=lambda key, value: store.__setitem__(key, value))
    worker = _worker(db, manager)
    worker._jobs, worker._ensure_jobs_loaded = {}, lambda: None
    rechecks = []
    monkeypatch.setattr(worker, '_recheck_retag_findings', lambda: rechecks.append(1))
    # a setting that is not one of the two: nothing to re-check
    worker.set_job_settings('library_retag', settings={'cover_art': 'skip'})
    assert rechecks == []
    worker.set_job_settings('library_retag', settings={'flag_genre_differences': False})
    assert rechecks == [1]
    assert store['repair.jobs.library_retag.settings']['flag_genre_differences'] is False
    # saved again with the same answer: not again
    worker.set_job_settings('library_retag', settings={'flag_genre_differences': False})
    assert rechecks == [1]
    # and another job's settings never trigger it
    worker.set_job_settings('genre_cleanup', settings={'flag_genre_differences': True})
    assert rechecks == [1]


def test_the_job_carries_both_switches_off_and_its_options_pass_them_through():
    from core.repair_jobs.library_retag import LibraryRetagJob
    defaults = LibraryRetagJob.default_settings
    assert defaults['flag_genre_differences'] is False and defaults['flag_year_differences'] is False
    # the worker rebuilds the job's settings from these options, key by key
    options = retag.retag_options({'flag_year_differences': True})
    assert all(key in options for key in defaults)
    assert options['flag_year_differences'] is True and options['flag_genre_differences'] is False
