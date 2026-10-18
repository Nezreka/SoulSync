import json
import sqlite3

from core.library.reconciliation_authority import conflict
from core.library.reconciliation_choices import ensure_schema


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
    args = dict(entity_type='track', entity_id='track')
    assert conflict(db, 'mbid_mismatch', **args)['review_conflict']
    assert conflict(db, 'metadata_gap', details={'found_fields': {'musicbrainz_recording_id': 'recording'}}, **args) is None
    with db._get_connection() as c:
        c.execute("UPDATE repair_findings SET user_action='obsolete'")
    assert conflict(db, 'mbid_mismatch', **args) is None


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
    assert conflict(db, 'incomplete_album', entity_type='album', entity_id='album')['review_conflict']


def test_retag_compares_each_track_fields_without_blocking_independent_enrichment(tmp_path):
    db = Database(tmp_path / 'db')
    tracks = [{'file_path': '/music/album/song.flac', 'db_data': {'track_number': 7, 'genre': 'Rock'}}]
    assert conflict(db, 'library_retag', details={'album_id': 'album', 'tracks': tracks}) is None
    tracks[0]['db_data']['track_number'] = 2
    assert conflict(db, 'library_retag', details={'album_id': 'album', 'tracks': tracks})['review_conflict']


def test_track_decision_does_not_govern_other_tracks(tmp_path):
    db = Database(tmp_path / 'db')
    with db._get_connection() as c:
        c.execute("INSERT INTO tracks VALUES ('other','album','/music/album/other.flac')")
    assert conflict(db, 'track_number_mismatch', entity_type='track', entity_id='other',
                    details={'correct_track_num': 3}) is None


def test_unique_relocated_assignment_preserves_audio_identity(tmp_path):
    from core.library.reconciliation_choices import assignment_for_file
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
    assert conflict(db,'canonical_version',entity_type='album',entity_id='album',
                    details={'source':'musicbrainz','album_id':'other'})['review_conflict']
    assert conflict(db,'canonical_version',entity_type='album',entity_id='album',
                    details={'source':'musicbrainz','album_id':'selected'}) is None


def test_album_tag_consistency_uses_tag_names_for_accepted_fields(tmp_path):
    db = Database(tmp_path / 'db')
    with db._get_connection() as c:
        raw = json.dumps({'changes':{'musicbrainz_release_id':{'old':'old','new':'selected'}}})
        c.execute("UPDATE repair_findings SET details_json=?,status='resolved',user_action='retagged'", (raw,))
    args = dict(entity_type='album',entity_id='album')
    assert conflict(db,'album_tag_inconsistency',details={'inconsistencies':[
        {'field':'musicbrainz_albumid','canonical':'other'}]},**args)['review_conflict']
    assert conflict(db,'album_tag_inconsistency',details={'inconsistencies':[
        {'field':'musicbrainz_albumid','canonical':'selected'}]},**args) is None
