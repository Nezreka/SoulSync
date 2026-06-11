"""#702: a mirrored playlist (e.g. a ListenBrainz weekly) whose in-memory
discovery state was wiped by a restart must still cancel/reset cleanly instead of
404-ing into a permanent wedge. cancel_sync is the shared core for YouTube +
ListenBrainz cancel, so its idempotency is the fix."""

from __future__ import annotations

import threading

from core.discovery.endpoints import cancel_sync


def _lock():
    return threading.Lock()


def test_cancel_missing_key_is_idempotent_success_not_404():
    body, code = cancel_sync(
        {}, 'state_was_wiped', label='YouTube',
        not_found_message='YouTube playlist not found',
        sync_lock=_lock(), sync_states={}, active_sync_workers={})
    assert code == 200
    assert body.get('success') is True
    assert 'not found' not in str(body).lower()   # the wedge message must be gone


def test_cancel_present_key_cancels_and_clears_worker():
    states = {'h': {'phase': 'syncing', 'sync_playlist_id': 'sp1'}}
    sync_states, workers = {}, {'sp1': 'worker'}
    body, code = cancel_sync(
        states, 'h', label='YouTube', not_found_message='x',
        sync_lock=_lock(), sync_states=sync_states, active_sync_workers=workers)
    assert code == 200 and body['success'] is True
    assert sync_states['sp1'] == {'status': 'cancelled'}
    assert 'sp1' not in workers
    assert states['h']['phase'] == 'discovered'
    assert states['h']['sync_playlist_id'] is None


def test_cancel_present_with_no_active_sync_still_succeeds():
    states = {'h': {'phase': 'discovered', 'sync_playlist_id': None}}
    body, code = cancel_sync(
        states, 'h', label='YouTube', not_found_message='x',
        sync_lock=_lock(), sync_states={}, active_sync_workers={})
    assert code == 200 and body['success'] is True
