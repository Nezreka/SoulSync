import json
import sqlite3

from core.library.reconciliation_fulfillment import (
    outstanding, published_wishlist_track, record_request, settle_assignments,
)
from core.repair_review_identity import review_case_key
from core.repair_worker import RepairWorker


class Database:
    def __init__(self, path):
        self.path = str(path)
        with self._get_connection() as conn:
            conn.execute('CREATE TABLE wishlist_tracks(profile_id INTEGER, spotify_track_id TEXT, spotify_data TEXT)')

    def _get_connection(self):
        return sqlite3.connect(self.path)


def test_published_request_survives_wishlist_removal_until_correct_assignment(tmp_path):
    db = Database(tmp_path / 'state.db')
    details = {'album_id': 2, 'profile_id': 1, 'album_folder': str(tmp_path),
               'source': 'musicbrainz', 'release_id': 'release'}
    slot = {'source_track_id': 'slot'}
    record_request(db, details, slot)
    record_request(db, {**details, 'profile_id': 2}, slot)
    payload = json.dumps({'id': 'slot', 'provider': 'musicbrainz', 'album': {'id': 'release'}})
    with db._get_connection() as conn:
        conn.executemany('INSERT INTO wishlist_tracks VALUES (?, ?, ?)',
                         [(1, 'slot', payload), (2, 'slot', payload)])
    # A transfer/staging path that has not been published is not completion.
    published_wishlist_track(db, 'slot', [1], str(tmp_path / 'absent.flac'))
    with db._get_connection() as conn:
        assert all(row[3] == 'requested' for row in outstanding(conn))
    audio = tmp_path / '01.flac'
    audio.write_bytes(b'published audio')
    published_wishlist_track(db, 'slot', [1], str(audio))
    with db._get_connection() as conn:
        conn.execute('DELETE FROM wishlist_tracks WHERE profile_id=1')
        assert {(row[0], row[3]) for row in outstanding(conn)} == {(1, 'published'), (2, 'requested')}
    # Coalesce multiple completed files into a single refresh of the album.
    worker = RepairWorker.__new__(RepairWorker)
    worker.db = db
    calls = []
    worker._schedule_album_refresh = lambda *args, **kwargs: calls.append((args, kwargs)) or True
    worker._refresh_published_review_albums()
    worker._refresh_published_review_albums()
    assert len(calls) == 1 and calls[0][1] == {'scope': 'discovery'}
    key = review_case_key(details, None)
    settle_assignments(db, key, [{'source_track_id': 'slot', 'status': 'uncertain', 'file_path': str(audio)}], 'release', 'musicbrainz')
    with db._get_connection() as conn:
        assert len(outstanding(conn)) == 2
    settle_assignments(db, key, [{'source_track_id': 'slot', 'status': 'confirmed', 'file_path': str(audio)}], 'other-release', 'musicbrainz')
    with db._get_connection() as conn:
        assert len(outstanding(conn)) == 2
    settle_assignments(db, key, [{'source_track_id': 'slot', 'status': 'confirmed', 'file_path': str(audio)}], 'release', 'musicbrainz')
    with db._get_connection() as conn:
        assert [(row[0], row[3]) for row in outstanding(conn)] == [(2, 'requested')]


def test_request_identity_includes_provider_when_numeric_ids_overlap(tmp_path):
    from core.repair_jobs.base import JobContext
    from core.repair_jobs.library_reconciliation import LibraryReconciliationJob

    db = Database(tmp_path / 'providers.db')
    details = {'album_id': 2, 'profile_id': 1, 'album_folder': str(tmp_path),
               'source': 'deezer', 'release_id': '123'}
    record_request(db, details, {'source_track_id': '456'})
    job = LibraryReconciliationJob()
    context = JobContext(db=db, transfer_folder=str(tmp_path), config_manager=None)
    assert job._wishlisted_slots(context)[1] == {('deezer', '456', '123')}
    key = review_case_key(details, None)
    assignment = [{'source_track_id': '456', 'status': 'confirmed', 'file_path': '/audio.flac'}]
    settle_assignments(db, key, assignment, '123', 'itunes')
    with db._get_connection() as conn:
        assert len(outstanding(conn)) == 1
    settle_assignments(db, key, assignment, '123', 'deezer')
    with db._get_connection() as conn:
        assert outstanding(conn) == []


def test_legacy_wishlist_history_does_not_resurrect_completed_or_cancelled_requests(tmp_path):
    from core.library.reconciliation_fulfillment import backfill_requests

    db = Database(tmp_path / 'legacy.db')
    details = {'album_id': 2, 'profile_id': 1, 'album_folder': str(tmp_path),
               'source': 'musicbrainz', 'release_id': 'release',
               'missing_tracks': [{'source_track_id': 'slot'}]}
    key = review_case_key(details, None)
    with db._get_connection() as conn:
        conn.execute('CREATE TABLE repair_findings(review_case_key TEXT, details_json TEXT, finding_type TEXT, user_action TEXT, status TEXT)')
        conn.execute('INSERT INTO repair_findings VALUES (?,?,?,?,?)',
                     (key, json.dumps(details), 'reconciliation_missing', 'wishlisted', 'resolved'))
        backfill_requests(conn)
        assert outstanding(conn) == []
    # Correct an older migration's inferred request, without asserting delivery.
    record_request(db, details, {'source_track_id': 'slot'})
    with db._get_connection() as conn:
        backfill_requests(conn)
        assert outstanding(conn) == []
        assert conn.execute('SELECT state FROM reconcile_fulfillment').fetchone()[0] == 'untracked'
        payload = {'provider': 'musicbrainz', 'id': 'slot', 'album': {'id': 'release'}}
        conn.execute('INSERT INTO wishlist_tracks VALUES (?,?,?)', (1, 'slot', json.dumps(payload)))
        backfill_requests(conn)
        assert len(outstanding(conn)) == 1
