"""Exercise recovery boundaries exposed by the October review feedback."""
import json
import time
from types import SimpleNamespace

import pytest

from core.library import playlist_membership
from core.library.reconciliation_fulfillment import record_request, published_wishlist_track
from core.library.reconciliation_organization import approve_organization, approved_organization, organization_context
from core.repair_review_queue import classify_case, list_cases
from core.repair_worker import RepairWorker
from tests.reconciliation.support.acquisition import Database
from tests.reconciliation.support.acceptance import build_album, scan_case
from tests.reconciliation.support.queue import _Database


def test_blocked_apply_distinguishes_blocker_from_unattempted_dependent_actions(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'supported')
    case = scan_case(album)
    attempted = []
    def blocked(fid, **kwargs):
        attempted.append(fid)
        return {'success': False, 'blocked': True, 'error': 'This copy is in a playlist'}
    monkeypatch.setattr(album.worker, '_apply_reviewed_finding', blocked)
    result = album.worker.start_review_safe(case_id=case['case_id'])
    assert result['started'], result
    deadline = time.monotonic() + 5
    while album.worker._bulk_fix_state['running'] and time.monotonic() < deadline:
        time.sleep(.01)
    state = album.worker.get_bulk_fix_status()
    assert not state['running']
    assert state['failed'] == 0 and state['fixed'] == 0
    assert state['blocked'] == 1 and state['not_attempted'] == len(case['items']) - 1
    assert len(attempted) == 1


def test_search_uses_names_and_ranks_album_matches_without_playlist_noise():
    db = _Database()
    for fid, album, artist, extra in (
        (1, 'North', 'Matchbox Twenty', {'playlist_tracks': [{'title': 'Bittersweet Electronic'}]}),
        (2, 'TRON: Legacy', 'Daft Punk', {}),
        (3, 'Other album', 'Artist', {'expected_title': 'Tron theme'}),
        (4, 'Études, No. 5', 'Philip Glass', {}),
        (5, 'Forty Licks', 'Rolling Stones', {'expected_title': 'Love Is Strong'}),
    ):
        db.add(fid, f'/music/{fid}/track.flac', {'album_id': fid, 'album_title': album, 'artist_name': artist, **extra})
    result = list_cases(db, q='tron', limit=1)
    assert result['total'] == 2
    assert result['items'][0]['album'] == 'TRON: Legacy'
    assert len(result['case_ids']) == 2
    assert list_cases(db, q='tron legacy daft')['total'] == 1
    assert list_cases(db, q='etudes no. 5')['items'][0]['album'] == 'Études, No. 5'
    assert list_cases(db, q='bittersweet')['total'] == 0


def test_saved_failed_removal_is_an_execution_blocker_not_an_audio_decision():
    plan = classify_case([{'id': 1, 'finding_type': 'reconciliation_extra',
        'last_error': 'This copy is in a playlist; review that reference first',
        'details': {'plan_approved': True, 'audio_conflict': 'different_recording',
                    'planned_choice': {'action': 'fix', 'fix_action': 'quarantine'}}}])
    assert not plan['can_apply_ready']
    assert plan['decisions'][0]['category'] == 'apply_blockers'
    assert plan['decisions'][0]['label'] == 'Resolve blocked actions'


def test_review_organization_handoff_is_bound_to_completed_actions_and_current_inventory(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'supported')
    case = scan_case(album)
    context = organization_context(album.db, '10', [row['details'] for row in case['items']])
    with pytest.raises(ValueError, match='remaining album actions'):
        approve_organization(album.db, '10', context)
    for row in case['items']:
        assert album.worker._apply_reviewed_finding(row['id'])['success']
    album.worker._create_finding(job_id='library_reconciliation', finding_type='reconciliation_missing',
        severity='info', entity_type='album_slot', entity_id='wanted', file_path=None,
        title='Waiting for a bonus track', description='Requested',
        details={**case['items'][0]['details'], 'fulfillment_state': 'wishlisted', 'missing_tracks': []})
    from core.library.reconciliation_authority import conflict
    assert conflict(album.db, 'path_mismatch', entity_type='album', entity_id='10')
    context = approve_organization(album.db, '10', context)
    item = SimpleNamespace(album_id='10', rename_only=True, metadata_source='tags', review_context=context)
    assert approved_organization(album.db, item)
    item.album_id = 'other'
    assert not approved_organization(album.db, item)

    item.album_id = '10'
    item.review_context = {**context, 'album_title': 'Unapproved name'}
    assert not approved_organization(album.db, item)
    item.review_context = context
    with album.db._get_connection() as conn:
        conn.execute("UPDATE tracks SET file_path=file_path || '.changed' WHERE album_id='10'")
    assert not approved_organization(album.db, item)


def test_completed_case_does_not_restore_historical_request_state(tmp_path, monkeypatch):
    album = build_album(tmp_path, monkeypatch, 'supported')
    case = scan_case(album)
    for row in case['items']:
        assert album.worker._apply_reviewed_finding(row['id'])['success']
    import core.repair_review_queue as queue
    original = queue.get_case
    def history(*args, **kwargs):
        result = original(*args, **kwargs)
        if result:
            result['album_plan']['assignments'][0]['fulfillment_state'] = 'downloaded'
        return result
    monkeypatch.setattr(queue, 'get_case', history)
    terminal = album.worker.get_review_case(case['case_id'])
    assert terminal['status'] == 'completed'
    assert not any(row.get('fulfillment_state') for row in terminal['album_plan']['assignments'])


def test_new_replacement_of_delivered_file_resets_receipt_but_retry_does_not(tmp_path):
    db = Database(tmp_path / 'request.db')
    path = tmp_path / 'delivered.flac'; path.write_bytes(b'audio')
    details = {'album_id': 2, 'profile_id': 1, 'album_folder': str(tmp_path),
               'source': 'musicbrainz', 'release_id': 'release', 'replacement_request': True}
    slot = {'source_track_id': 'slot'}
    record_request(db, details, slot)
    with db._get_connection() as conn:
        conn.execute('INSERT INTO wishlist_tracks VALUES (1, ?, ?)', ('slot', json.dumps({'id': 'slot', 'provider': 'musicbrainz', 'album': {'id': 'release'}})))
    published_wishlist_track(db, 'slot', [1], str(path))
    record_request(db, details, slot)
    with db._get_connection() as conn:
        assert conn.execute('SELECT state FROM reconcile_fulfillment').fetchone()[0] == 'published'
    record_request(db, {**details, 'replacement_for_path': str(path)}, slot)
    with db._get_connection() as conn:
        assert conn.execute('SELECT state,published_path FROM reconcile_fulfillment').fetchone() == ('requested', '')


def test_playlist_references_have_real_ids_and_recheck_keeps_saved_choice(monkeypatch):
    playlist_membership.clear_cache()
    tracks = [SimpleNamespace(ratingKey='track')]
    client = SimpleNamespace(base_url='https://music.example', ensure_connection=lambda: True,
        _make_request=lambda path, params=None: {'playlists': {'playlist': [{'id': 'list-1', 'name': '88 Keys'}]}}
        if path == 'getPlaylists' else {'playlist': {'entry': [{'id': track.ratingKey} for track in tracks]}})
    monkeypatch.setattr(playlist_membership, '_active_server_and_client', lambda: ('navidrome', client))
    db = _Database()
    db.add(1, '/music/file.flac', {'track_id': 'track'})
    worker = RepairWorker.__new__(RepairWorker); worker.db = db
    worker.get_review_case = lambda *a, **k: {'items': [{'id': 1, 'file_path': '/music/file.flac', 'details': {'track_id': 'track'}, 'last_error': 'This copy is in a playlist'}]}
    refs = worker.review_playlist_references('case', '/music/file.flac')['playlists']
    assert refs == [{'id': 'list-1', 'title': '88 Keys', 'server': 'navidrome', 'url': 'https://music.example/app/#/playlist/list-1/show'}]
    raw = db.conn.execute('SELECT details_json FROM repair_findings').fetchone()[0]
    db.conn.execute("UPDATE repair_findings SET last_error='This copy is in a playlist'")
    tracks.clear()
    assert worker.review_playlist_references('case', '/music/file.flac', refresh=True)['playlists'] == []
    assert db.conn.execute('SELECT details_json,last_error FROM repair_findings').fetchone() == (raw, None)
    with pytest.raises(ValueError):
        worker.review_playlist_references('case', '/elsewhere.flac', refresh=True)
    playlist_membership.clear_cache()


@pytest.mark.parametrize('failed_endpoint', ['getPlaylists', 'getPlaylist'])
def test_failed_playlist_recheck_does_not_erase_cached_membership(failed_endpoint):
    playlist_membership.clear_cache()
    client = SimpleNamespace(base_url='https://music.example', ensure_connection=lambda: True,
        _make_request=lambda path, params=None: None if path == failed_endpoint else
        {'playlists': {'playlist': [{'id': 'list-1', 'name': '88 Keys'}]}})
    playlist_membership._cache.update(at=time.monotonic(), server='navidrome', map={'track': ['88 Keys']})
    with pytest.raises(ValueError, match='complete playlist information'):
        playlist_membership.server_playlist_references(refresh=True, resolve=lambda: ('navidrome', client))
    assert playlist_membership._cache['map'] == {'track': ['88 Keys']}
    playlist_membership.clear_cache()


def test_jellyfin_reference_check_uses_playlist_entries_and_reads_all_pages():
    calls = []
    def request(path, params):
        calls.append((path, params))
        if path == '/Users/user/Items':
            return {'Items': [{'Id': 'list-1', 'Name': 'Long playlist'}]}
        assert path == '/Playlists/list-1/Items'
        assert params['UserId'] == 'user'
        return {'Items': [{'Id': str(i)} for i in range(1000)] if params['StartIndex'] == 0 else [{'Id': 'last'}]}
    client = SimpleNamespace(user_id='user', ensure_connection=lambda: True, _make_request=request)
    rows = list(playlist_membership._read_reference_rows('jellyfin', client))
    assert rows[0][:2] == ('list-1', 'Long playlist')
    assert len(rows[0][2]) == 1001 and rows[0][2][-1] == 'last'
    assert len(calls) == 3
