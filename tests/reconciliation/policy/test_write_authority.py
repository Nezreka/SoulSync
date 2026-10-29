import json
import sqlite3
import threading
from pathlib import Path

import pytest

from core.reconciliation.authority import conflict
from core.reconciliation.choices import assignment_for_file, ensure_schema
from core.reconciliation.settled import record as record_decision
from core.reconciliation.settled import reopen as reopen_decision
from core.repair_worker import RepairWorker


class Database:
    def __init__(self, path):
        self.path = str(path)
        with self._get_connection() as c:
            ensure_schema(c)
            c.execute("CREATE TABLE tracks(id TEXT, album_id TEXT, file_path TEXT)")
            c.execute("INSERT INTO tracks VALUES ('track','album','/music/album/song.flac')")
            c.execute("CREATE TABLE reconcile_album_plans(case_id TEXT, album_id TEXT, album_folder TEXT)")
            c.execute("INSERT INTO reconcile_album_plans VALUES ('case','album','/music/album')")
            c.execute("CREATE TABLE repair_findings(id INTEGER, job_id TEXT, review_case_key TEXT, file_path TEXT, entity_id TEXT, status TEXT, user_action TEXT, details_json TEXT)")
            raw = json.dumps({'changes': {'track_number': {'old': 7, 'new': 2}, 'musicbrainz_recording_id': {'old': '', 'new': 'recording'}}})
            c.execute("INSERT INTO repair_findings VALUES (1,'library_reconciliation','case','/music/album/song.flac','track','pending',NULL,?)", (raw,))
            c.execute("ALTER TABLE repair_findings ADD COLUMN finding_type TEXT NOT NULL DEFAULT 'reconciliation_metadata'")
            c.execute("INSERT INTO reconcile_review_choices(finding_id,case_id,evidence_json,category,choice) VALUES (1,'case',?,'numbering','dismiss')", (raw,))

    def _get_connection(self):
        return sqlite3.connect(self.path)


def test_kept_numbering_blocks_legacy_repair_but_allows_compatible_value(tmp_path):
    db = Database(tmp_path / 'db')
    args = dict(entity_type='track', entity_id='track', file_path='/music/album/song.flac')
    result = conflict(db, 'track_number_mismatch', details={'correct_track_num': 2}, **args)
    assert result['review_case_key'] == 'case'
    assert conflict(db, 'track_number_mismatch', details={'correct_track_num': 7}, **args) is None
    assert conflict(db, 'bpm_backfill', **args) is None


def test_resolved_identity_remains_authoritative_and_obsolete_history_does_not(tmp_path):
    db = Database(tmp_path / 'db')
    with db._get_connection() as c:
        c.execute("UPDATE repair_findings SET status='resolved', user_action='retagged'")
        record_decision(c, 1)
    args = dict(entity_type='track', entity_id='track')
    assert conflict(db, 'mbid_mismatch', **args)['review_conflict']
    assert conflict(db, 'metadata_gap', details={'found_fields': {'musicbrainz_recording_id': 'recording'}}, **args) is None
    with db._get_connection() as c:
        c.execute("UPDATE repair_findings SET user_action='obsolete'")
        # History changes cannot revoke current decisions; reopening is explicit.
        reopen_decision(c, 1)
    assert conflict(db, 'mbid_mismatch', **args) is None


def test_numbering_choice_only_governs_numbering_until_metadata_is_approved(tmp_path):
    db = Database(tmp_path / 'db')
    args = dict(entity_type='track', entity_id='track')
    assert conflict(db, 'mbid_mismatch', **args) is None
    with db._get_connection() as c:
        c.execute("UPDATE reconcile_review_choices SET category='metadata', choice='fix', numbering_choice='dismiss'")
    assert conflict(db, 'mbid_mismatch', **args)['review_conflict']
    assert conflict(db, 'track_number_mismatch', details={'correct_track_num': 2}, **args)['review_conflict']
    assert conflict(db, 'track_number_mismatch', details={'correct_track_num': 7}, **args) is None


def test_changed_unapproved_proposal_does_not_reuse_old_choice(tmp_path):
    db = Database(tmp_path / 'db')
    with db._get_connection() as c:
        c.execute("UPDATE repair_findings SET details_json='{}'")
    assert conflict(db, 'track_number_mismatch', entity_type='track', entity_id='track') is None


def test_active_album_blocks_file_actions_and_completeness_uses_persisted_intent(tmp_path):
    db = Database(tmp_path / 'db')
    for kind in ('duplicate_tracks', 'fake_lossless', 'quality_upgrade', 'corrupt_audio'):
        assert conflict(db, kind, entity_type='track', entity_id='track')['review_conflict']
    with db._get_connection() as c:
        c.execute("UPDATE repair_findings SET status='resolved'")
        c.execute("CREATE TABLE reconcile_album_intent(album_id TEXT, scope TEXT)")
        c.execute("INSERT INTO reconcile_album_intent VALUES ('album','partial')")
        record_decision(c, 1)
    assert conflict(db, 'incomplete_album', entity_type='album', entity_id='album')['review_conflict']


def test_retag_compares_each_track_fields_without_blocking_independent_enrichment(tmp_path):
    db = Database(tmp_path / 'db')
    tracks = [{'file_path': '/music/album/song.flac', 'db_data': {'track_number': 7, 'genre': 'Rock'}}]
    assert conflict(db, 'library_retag', details={'album_id': 'album', 'tracks': tracks}) is None
    tracks[0]['db_data']['track_number'] = 2
    assert conflict(db, 'library_retag', details={'album_id': 'album', 'tracks': tracks})['review_conflict']


def test_review_guard_preserves_callers_connection(tmp_path):
    db = Database(tmp_path / 'db')
    with db._get_connection() as conn:
        blocked = conflict(db, 'incomplete_album', entity_type='album',
                           entity_id='album', connection=conn)
        assert blocked['review_case_key'] == 'case'
        assert conn.execute('SELECT COUNT(*) FROM reconcile_album_plans').fetchone()[0] == 1
        assert conflict(db, 'incomplete_album', entity_type='album',
                        entity_id='other', connection=conn) is None
        assert conn.execute('SELECT COUNT(*) FROM tracks').fetchone()[0] == 1


def test_track_decision_does_not_govern_other_tracks(tmp_path):
    db = Database(tmp_path / 'db')
    with db._get_connection() as c:
        c.execute("INSERT INTO tracks VALUES ('other','album','/music/album/other.flac')")
    assert conflict(db, 'track_number_mismatch', entity_type='track', entity_id='other',
                    details={'correct_track_num': 3}) is None


def test_unique_relocated_assignment_preserves_audio_identity(tmp_path):
    from core.reconciliation.choices import assignment_for_file
    old = str(tmp_path / 'old.flac')
    file = {'path': str(tmp_path / 'new.flac'), 'size_bytes': 100, 'mtime_ns': 2,
            'acoustid': {'fingerprint_key': 'audio'}}
    overrides = {('album','musicbrainz','release',old): ('slot',100,1,'audio')}
    args = (overrides, 'album', 'musicbrainz', 'release', file)
    assert assignment_for_file(*args, [file]) == ('slot',100,2,'audio')
    assert assignment_for_file(*args, [file, dict(file)]) is None
    (tmp_path / 'old.flac').write_bytes(b'existing copy')
    assert assignment_for_file(*args, [file]) is None


def test_mutation_lock_serializes_threads_and_releases_on_failure():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from core.library.maintenance_lock import maintenance_mutation
    entered, release, second = Event(), Event(), Event()

    @maintenance_mutation
    def first():
        entered.set()
        assert release.wait(2)
        raise ValueError('expected failure')

    @maintenance_mutation
    def next_action():
        second.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(first)
        assert entered.wait(2)
        two = pool.submit(next_action)
        assert not second.wait(.05)
        release.set()
        try:
            one.result()
        except ValueError:
            pass
        two.result(timeout=2)
    assert second.is_set()


def test_locked_release_pin_blocks_a_conflicting_resolver_choice(tmp_path):
    db = Database(tmp_path / 'db')
    db.get_album_canonical = lambda _: {'locked': True, 'source': 'musicbrainz', 'album_id': 'selected'}
    assert conflict(db,'canonical_version',entity_type='album',entity_id='album',
                    details={'source':'musicbrainz','album_id':'other'})['review_conflict']
    assert conflict(db,'canonical_version',entity_type='album',entity_id='album',
                    details={'source':'musicbrainz','album_id':'selected'}) is None


def test_accepted_release_id_governs_legacy_auto_resolver_without_explicit_pin(tmp_path):
    db = Database(tmp_path / 'db')
    with db._get_connection() as c:
        raw = json.dumps({'changes':{'musicbrainz_release_id':{'old':'old','new':'selected'}}})
        c.execute("UPDATE repair_findings SET details_json=?, status='resolved', user_action='retagged'", (raw,))
        record_decision(c, 1)
    assert conflict(db,'canonical_version',entity_type='album',entity_id='album',
                    details={'source':'musicbrainz','album_id':'other'})['review_conflict']
    assert conflict(db,'canonical_version',entity_type='album',entity_id='album',
                    details={'source':'musicbrainz','album_id':'selected'}) is None


def test_album_tag_consistency_uses_tag_names_for_accepted_fields(tmp_path):
    db = Database(tmp_path / 'db')
    with db._get_connection() as c:
        raw = json.dumps({'changes':{'musicbrainz_release_id':{'old':'old','new':'selected'}}})
        c.execute("UPDATE repair_findings SET details_json=?,status='resolved',user_action='retagged'", (raw,))
        record_decision(c, 1)
    args = dict(entity_type='album',entity_id='album')
    assert conflict(db,'album_tag_inconsistency',details={'inconsistencies':[
        {'field':'musicbrainz_albumid','canonical':'other'}]},**args)['review_conflict']
    assert conflict(db,'album_tag_inconsistency',details={'inconsistencies':[
        {'field':'musicbrainz_albumid','canonical':'selected'}]},**args) is None


def relocated_assignment_db(tmp_path):
    db = Database(tmp_path / 'db')
    old = str(tmp_path / 'gone' / 'song.flac')
    new = str(tmp_path / 'moved' / 'song.flac')
    with db._get_connection() as conn:
        conn.execute('DELETE FROM repair_findings')
        conn.execute('DELETE FROM reconcile_review_choices')
        conn.execute('UPDATE tracks SET file_path=?', (new,))
        conn.execute('UPDATE reconcile_album_plans SET album_folder=?', (str(Path(new).parent),))
        conn.execute('''CREATE TABLE reconcile_track_assignment (
            album_id TEXT, source TEXT, release_id TEXT, file_path TEXT,
            slot_key TEXT, size_bytes INTEGER, mtime_ns INTEGER, fingerprint_key TEXT,
            PRIMARY KEY(album_id,source,release_id,file_path))''')
        conn.execute('INSERT INTO reconcile_track_assignment VALUES (?,?,?,?,?,?,?,?)',
                     ('album', 'musicbrainz', 'release', old, 'slot', 100, 1, 'audio'))
    # Reconciliation verifies the move before exposing the recovered assignment.
    file = {'path': new, 'size_bytes': 100, 'mtime_ns': 2,
            'acoustid': {'fingerprint_key': 'audio'}}
    overrides = {('album', 'musicbrainz', 'release', old): ('slot', 100, 1, 'audio')}
    assert assignment_for_file(overrides, 'album', 'musicbrainz', 'release',
                               file, [file], db=db)
    return db, old, new


def test_relocated_assignment_can_be_reset(tmp_path):
    db, old, new = relocated_assignment_db(tmp_path)
    worker = RepairWorker.__new__(RepairWorker)
    worker.db = db
    worker._reconciliation_file_guard = lambda *args: None
    worker._reconciliation_release_guard = lambda *args: None
    details = {'_fix_action': 'assign:auto', 'album_id': 'album',
               'source': 'musicbrainz', 'release_id': 'release',
               'size_bytes': 100, 'mtime_ns': 2, 'fingerprint_key': 'audio'}
    assert worker._fix_reconciliation_assignment('track', 'track', new, details)['success']
    with db._get_connection() as conn:
        overrides = {tuple(row[:4]): tuple(row[4:]) for row in
                     conn.execute('SELECT * FROM reconcile_track_assignment')}
    file = {'path': new, 'size_bytes': 100, 'mtime_ns': 2,
            'acoustid': {'fingerprint_key': 'audio'}}
    assert assignment_for_file(overrides, 'album', 'musicbrainz', 'release', file, [file]) is None


def test_relocated_assignment_protects_against_legacy_retag(tmp_path):
    db, old, new = relocated_assignment_db(tmp_path)
    assert conflict(db, 'library_retag', entity_type='track', entity_id='track',
                    file_path=new, details={'db_data': {'title': 'Other song'}})
    with db._get_connection() as conn:
        assert conn.execute('SELECT file_path FROM reconcile_track_assignment').fetchall() == [(new,)]


def test_review_pin_waits_for_legacy_mutation(tmp_path):
    from core.library.maintenance_lock import mutation_lock
    db = Database(tmp_path / 'db')
    db.set_album_canonical = lambda *args, **kwargs: True
    worker = RepairWorker.__new__(RepairWorker)
    worker.db = db
    worker._review_apply_lock = threading.RLock()
    worker.get_review_case = lambda *args, **kwargs: {
        'album_id': 'album', 'folder': '/album', 'album_plan': {
            'source': 'musicbrainz', 'release_id': 'old', 'candidates': [
                {'source': 'musicbrainz', 'release_id': 'new', 'matched': 1, 'clear': 1}]}}
    worker._schedule_review_refresh = lambda *args, **kwargs: True
    started, done = threading.Event(), threading.Event()
    results = []

    def pin():
        started.set()
        results.append(worker.pin_review_release('case', source='musicbrainz',
            release_id='new', expected_source='musicbrainz', expected_release_id='old'))
        done.set()

    with mutation_lock:
        thread = threading.Thread(target=pin)
        thread.start()
        assert started.wait(2)
        completed_while_locked = done.wait(.1)
    thread.join(2)
    assert not completed_while_locked
    assert done.is_set() and results[0]['success']


@pytest.mark.parametrize('old_state,new_state,old_time,new_time,expected', [
    ('published', 'requested', '12:00:00', '11:00:00', 'published'),
    ('published', 'cancelled', '11:00:00', '12:00:00', 'cancelled'),
    ('cancelled', 'requested', '11:00:00', '12:00:00', 'requested'),
    ('published', 'requested', '12:00:00', '12:00:00', 'published'),
    ('published', 'fulfilled', '12:00:00', '12:00:00', 'fulfilled'),
])
def test_relocation_merges_fulfillment_state(tmp_path, old_state, new_state,
                                             old_time, new_time, expected):
    from core.reconciliation.fulfillment import ensure_schema
    from core.reconciliation.redirects import record_relocation
    db = Database(tmp_path / 'db')
    with db._get_connection() as conn:
        conn.execute('DROP TABLE reconcile_album_plans')
        conn.execute('''CREATE TABLE reconcile_album_plans(case_id TEXT, album_id TEXT,
            album_folder TEXT, source TEXT, release_id TEXT, profile_id INTEGER)''')
        conn.executemany('INSERT INTO reconcile_album_plans VALUES (?,?,?,?,?,?)', [
            ('old', 'album', '/old', 'musicbrainz', 'release', 1),
            ('new', 'album', '/new', 'musicbrainz', 'release', 1)])
        ensure_schema(conn)
        conn.executemany('''INSERT INTO reconcile_fulfillment
            (case_id,profile_id,album_id,album_folder,source,release_id,source_track_id,
             state,published_path,updated_at) VALUES (?,1,?,?,?,?,?,?,?,?)''', [
            ('old', 'album', '/old', 'musicbrainz', 'release', 'slot', old_state,
             '/new/song.flac' if old_state == 'published' else '', '2026-10-18 ' + old_time),
            ('new', 'album', '/new', 'musicbrainz', 'release', 'slot', new_state,
             '', '2026-10-18 ' + new_time)])
    record_relocation(db, '/old', 'new')
    with db._get_connection() as conn:
        assert conn.execute('SELECT case_id,state,published_path FROM reconcile_fulfillment').fetchall() == [
            ('new', expected, '/new/song.flac' if expected == 'published' else '')]


def test_full_retag_preserves_identity_and_allows_independent_enrichment(tmp_path, monkeypatch):
    import struct

    from mutagen.flac import FLAC

    from core.metadata import source
    from core.repair_jobs.library_retag import apply_track_plans
    db = Database(tmp_path / 'db')
    path = tmp_path / 'song.flac'
    streaminfo = bytearray(34)
    streaminfo[0:2] = streaminfo[2:4] = struct.pack('>H', 4096)
    streaminfo[10], streaminfo[12] = 0x0A, 0x70
    path.write_bytes(b'fLaC' + bytes([0x80, 0, 0, 34]) + bytes(streaminfo))
    audio = FLAC(str(path))
    audio['musicbrainz_trackid'] = ['recording']
    audio['musicbrainz_albumid'] = ['release']
    audio['date'] = ['2020']
    audio.save()
    with db._get_connection() as conn:
        conn.execute('UPDATE tracks SET file_path=?', (str(path),))
        raw = json.dumps({'changes': {
            'musicbrainz_recording_id': {'old': '', 'new': 'recording'},
            'musicbrainz_release_id': {'old': '', 'new': 'release'},
            'year': {'old': '2010', 'new': '2020'}}})
        conn.execute("UPDATE repair_findings SET file_path=?, details_json=?, status='resolved', user_action='retagged'",
                     (str(path), raw))
        record_decision(conn, 1)
    monkeypatch.setattr(source, 'get_config_manager', lambda: {
        'metadata_enhancement.post_process_order': ['musicbrainz']})

    def provider_result(pp, *args, **kwargs):
        pp['id_tags'].update(MUSICBRAINZ_RECORDING_ID='different-recording',
                            MUSICBRAINZ_RELEASE_ID='different-release', DATE='1999')
        pp['deezer_bpm'] = 120
        pp['release_year'] = '1999'

    monkeypatch.setattr(source, '_process_musicbrainz_source', provider_result)
    plan = {'file_path': str(path), 'db_data': {'genres': ['Rock']},
            'changes': {'genre': {'old': '', 'new': 'Rock'}},
            'full_meta': {'title': 'Song', 'artist': 'Artist', 'album': 'Album'}}
    result = apply_track_plans([plan], full=True, db=db)
    audio = FLAC(str(path))
    assert result['written'] == 1 and result['failed'] == 0
    assert audio['musicbrainz_trackid'] == ['recording']
    assert audio['musicbrainz_albumid'] == ['release']
    assert audio['date'] == ['2020']
    assert audio['genre'] == ['Rock']
    assert audio['bpm'] == ['120']


def test_retag_date_and_count_aliases_respect_accepted_fields(tmp_path):
    db = Database(tmp_path / 'db')
    with db._get_connection() as conn:
        conn.execute("UPDATE repair_findings SET status='resolved', details_json=?",
                     (json.dumps({'changes': {'year': {'old': 1999, 'new': 2020},
                                              'track_total': {'old': 9, 'new': 10}}}),))
        record_decision(conn, 1)
    for values in ({'release_date': '1999-01-01'}, {'track_count': 9}):
        assert conflict(db, 'library_retag', entity_type='track', entity_id='track',
                        details={'db_data': values})['review_conflict']
    assert conflict(db, 'library_retag', entity_type='track', entity_id='track',
                    details={'db_data': {'release_date': '2020-02-01', 'track_count': 10}}) is None


def test_relocation_does_not_resurrect_a_concurrently_removed_assignment(tmp_path):
    db, _, new_path = relocated_assignment_db(tmp_path)
    with db._get_connection() as conn:
        row = conn.execute('''SELECT album_id,source,release_id,file_path,slot_key,
                           size_bytes,mtime_ns,fingerprint_key FROM reconcile_track_assignment''').fetchone()
        conn.execute('DELETE FROM reconcile_track_assignment')
    moved = tmp_path / 'again.flac'
    moved.write_bytes(b'x' * 100)
    stat = moved.stat()
    file = {'path': str(moved), 'size_bytes': stat.st_size, 'mtime_ns': stat.st_mtime_ns,
            'acoustid': {'fingerprint_key': row[7]}}
    overrides = {tuple(row[:4]): tuple(row[4:])}
    assert assignment_for_file(overrides, row[0], row[1], row[2], file, [file], db=db) is None
    with db._get_connection() as conn:
        assert conn.execute('SELECT COUNT(*) FROM reconcile_track_assignment').fetchone()[0] == 0
