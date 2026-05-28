"""Tests for the lifted, source-agnostic discovery route helpers in
``core.discovery.endpoints``.

These pin the exact behavior the per-source ``convert_<source>_results_to_spotify_tracks``
functions had in web_server.py, so the lift is provably 1:1. Each input shape
the originals handled is exercised here.
"""

from __future__ import annotations

import threading

from core.discovery.endpoints import (
    convert_results_to_spotify_tracks,
    cancel_sync,
    delete_playlist_state,
    get_sync_status,
    playlist_name_strict as _pl_name_strict,
    playlist_name_safe as _pl_name_safe,
)


class _FakeFuture:
    def __init__(self):
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


def _cancel_infra():
    """Fresh sync infra (lock + the two shared dicts) for cancel_sync tests."""
    return {
        'sync_lock': threading.Lock(),
        'sync_states': {},
        'active_sync_workers': {},
    }


# ---------------------------------------------------------------------------
# spotify_data (manual-fix) shape
# ---------------------------------------------------------------------------

def test_spotify_data_shape_basic():
    results = [{
        'spotify_data': {
            'id': 'sp1', 'name': 'Song', 'artists': ['A'], 'album': 'Alb',
            'duration_ms': 1234,
        }
    }]
    assert convert_results_to_spotify_tracks(results, 'Tidal') == [{
        'id': 'sp1', 'name': 'Song', 'artists': ['A'], 'album': 'Alb',
        'duration_ms': 1234,
    }]


def test_spotify_data_duration_defaults_to_zero():
    results = [{'spotify_data': {'id': 'x', 'name': 'n', 'artists': [], 'album': 'a'}}]
    out = convert_results_to_spotify_tracks(results, 'Deezer')
    assert out[0]['duration_ms'] == 0


def test_spotify_data_includes_track_and_disc_number_when_present():
    results = [{'spotify_data': {
        'id': 'x', 'name': 'n', 'artists': [], 'album': 'a',
        'track_number': 5, 'disc_number': 2,
    }}]
    out = convert_results_to_spotify_tracks(results, 'Qobuz')
    assert out[0]['track_number'] == 5
    assert out[0]['disc_number'] == 2


def test_spotify_data_omits_track_disc_when_absent_or_falsy():
    # track_number/disc_number of 0 are falsy -> omitted, matching original.
    results = [{'spotify_data': {
        'id': 'x', 'name': 'n', 'artists': [], 'album': 'a',
        'track_number': 0, 'disc_number': 0,
    }}]
    out = convert_results_to_spotify_tracks(results, 'YouTube')
    assert 'track_number' not in out[0]
    assert 'disc_number' not in out[0]


# ---------------------------------------------------------------------------
# spotify_track + status_class == 'found' (auto-discovery) shape
# ---------------------------------------------------------------------------

def test_auto_discovery_shape_full():
    results = [{
        'spotify_track': 'Track', 'status_class': 'found',
        'spotify_id': 'id9', 'spotify_artist': 'Artist', 'spotify_album': 'Album',
    }]
    assert convert_results_to_spotify_tracks(results, 'ListenBrainz') == [{
        'id': 'id9', 'name': 'Track', 'artists': ['Artist'], 'album': 'Album',
        'duration_ms': 0,
    }]


def test_auto_discovery_defaults_when_fields_missing():
    results = [{'spotify_track': 'T', 'status_class': 'found'}]
    out = convert_results_to_spotify_tracks(results, 'Spotify Public')
    assert out == [{
        'id': 'unknown', 'name': 'T', 'artists': ['Unknown Artist'],
        'album': 'Unknown Album', 'duration_ms': 0,
    }]


def test_auto_discovery_empty_artist_yields_unknown_artist():
    results = [{
        'spotify_track': 'T', 'status_class': 'found', 'spotify_artist': '',
    }]
    out = convert_results_to_spotify_tracks(results, 'Tidal')
    assert out[0]['artists'] == ['Unknown Artist']


# ---------------------------------------------------------------------------
# skip / mixed / empty
# ---------------------------------------------------------------------------

def test_auto_discovery_requires_found_status():
    # spotify_track present but status_class != 'found' -> skipped.
    results = [{'spotify_track': 'T', 'status_class': 'not_found'}]
    assert convert_results_to_spotify_tracks(results, 'Tidal') == []


def test_result_matching_neither_shape_is_skipped():
    results = [{'irrelevant': True}, {'spotify_track': 'T'}]  # 2nd has no status_class
    assert convert_results_to_spotify_tracks(results, 'Tidal') == []


def test_mixed_results_preserve_order():
    results = [
        {'spotify_data': {'id': '1', 'name': 'a', 'artists': [], 'album': ''}},
        {'irrelevant': True},
        {'spotify_track': 'b', 'status_class': 'found', 'spotify_id': '2'},
    ]
    out = convert_results_to_spotify_tracks(results, 'Tidal')
    assert [t['id'] for t in out] == ['1', '2']


def test_empty_input():
    assert convert_results_to_spotify_tracks([], 'Tidal') == []


def test_spotify_data_takes_precedence_over_auto_fields():
    # A result carrying both shapes uses spotify_data (the if-branch wins),
    # matching the original if/elif ordering.
    results = [{
        'spotify_data': {'id': 'D', 'name': 'd', 'artists': [], 'album': ''},
        'spotify_track': 'IGNORED', 'status_class': 'found', 'spotify_id': 'A',
    }]
    out = convert_results_to_spotify_tracks(results, 'Tidal')
    assert out[0]['id'] == 'D'


# ---------------------------------------------------------------------------
# cancel_sync
# ---------------------------------------------------------------------------

def test_cancel_sync_not_found_returns_404():
    body, code = cancel_sync({}, 'missing', label='Tidal',
                             not_found_message='Tidal playlist not found', **_cancel_infra())
    assert code == 404
    assert body == {"error": "Tidal playlist not found"}


def test_cancel_sync_cancels_active_worker_and_reverts_state():
    infra = _cancel_infra()
    infra['sync_states']['sp1'] = {"status": "running"}
    infra['active_sync_workers']['sp1'] = _FakeFuture()
    states = {'pl': {'phase': 'syncing', 'sync_playlist_id': 'sp1',
                     'sync_progress': {'done': 1}, 'last_accessed': 0}}

    body, code = cancel_sync(states, 'pl', label='Tidal',
                             not_found_message='nf', **infra)

    assert code == 200
    assert body == {"success": True, "message": "Tidal sync cancelled"}
    # sync marked cancelled, worker removed
    assert infra['sync_states']['sp1'] == {"status": "cancelled"}
    assert 'sp1' not in infra['active_sync_workers']
    # state reverted
    assert states['pl']['phase'] == 'discovered'
    assert states['pl']['sync_playlist_id'] is None
    assert states['pl']['sync_progress'] == {}
    assert states['pl']['last_accessed'] != 0  # touched


def test_cancel_sync_worker_absent_from_active_map_is_safe():
    infra = _cancel_infra()  # active_sync_workers empty
    states = {'pl': {'phase': 'syncing', 'sync_playlist_id': 'sp1'}}
    body, code = cancel_sync(states, 'pl', label='Deezer', not_found_message='nf', **infra)
    assert code == 200
    assert infra['sync_states']['sp1'] == {"status": "cancelled"}


def test_cancel_sync_no_sync_in_progress_still_reverts():
    infra = _cancel_infra()
    states = {'pl': {'phase': 'discovered'}}  # no sync_playlist_id
    body, code = cancel_sync(states, 'pl', label='Qobuz', not_found_message='nf', **infra)
    assert code == 200
    assert states['pl']['sync_playlist_id'] is None
    assert states['pl']['sync_progress'] == {}
    assert infra['sync_states'] == {}  # nothing cancelled


def test_cancel_sync_label_in_message():
    infra = _cancel_infra()
    states = {'pl': {}}
    body, _ = cancel_sync(states, 'pl', label='iTunes Link', not_found_message='nf', **infra)
    assert body["message"] == "iTunes Link sync cancelled"


def test_cancel_sync_exception_returns_500():
    infra = _cancel_infra()
    states = {'pl': object()}  # not subscriptable -> raises inside try
    body, code = cancel_sync(states, 'pl', label='Tidal', not_found_message='nf', **infra)
    assert code == 500
    assert "error" in body


# ---------------------------------------------------------------------------
# delete_playlist_state
# ---------------------------------------------------------------------------

def test_delete_not_found_returns_404():
    body, code = delete_playlist_state({}, 'missing', label='Tidal',
                                       not_found_message='Tidal playlist not found')
    assert code == 404
    assert body == {"error": "Tidal playlist not found"}


def test_delete_cancels_discovery_future_and_removes_state():
    fut = _FakeFuture()
    states = {'pl': {'discovery_future': fut}}
    body, code = delete_playlist_state(states, 'pl', label='Tidal', not_found_message='nf')
    assert code == 200
    assert body == {"success": True, "message": "Playlist deleted"}
    assert fut.cancelled is True
    assert 'pl' not in states


def test_delete_without_discovery_future_still_removes():
    states = {'pl': {'phase': 'discovered'}}  # no discovery_future
    body, code = delete_playlist_state(states, 'pl', label='Deezer', not_found_message='nf')
    assert code == 200
    assert 'pl' not in states


def test_delete_falsy_discovery_future_not_cancelled():
    states = {'pl': {'discovery_future': None}}
    body, code = delete_playlist_state(states, 'pl', label='Qobuz', not_found_message='nf')
    assert code == 200
    assert 'pl' not in states


def test_delete_exception_returns_500():
    fut = object()  # no .cancel() -> AttributeError inside try
    states = {'pl': {'discovery_future': fut}}
    body, code = delete_playlist_state(states, 'pl', label='Tidal', not_found_message='nf')
    assert code == 500
    assert "error" in body
    # state NOT deleted because the exception fired before del
    assert 'pl' in states


# ---------------------------------------------------------------------------
# playlist-name accessors
# ---------------------------------------------------------------------------

class _Obj:
    def __init__(self, name):
        self.name = name


def test_name_attr_or_unknown():
    from core.discovery.endpoints import playlist_name_attr_or_unknown as g
    assert g({'playlist': _Obj('My PL')}) == 'My PL'
    assert g({'playlist': {'name': 'dict'}}) == 'Unknown Playlist'  # dict has no .name attr
    assert g({}) == 'Unknown Playlist'
    assert g({'playlist': None}) == 'Unknown Playlist'


def test_name_strict():
    from core.discovery.endpoints import playlist_name_strict as g
    assert g({'playlist': {'name': 'X'}}) == 'X'
    import pytest
    with pytest.raises(KeyError):
        g({})  # strict -> raises, matching originals


def test_name_safe():
    from core.discovery.endpoints import playlist_name_safe as g
    assert g({'playlist': {'name': 'X'}}) == 'X'
    assert g({}) == 'Unknown Playlist'
    assert g({'playlist': {}}) == 'Unknown Playlist'


# ---------------------------------------------------------------------------
# get_sync_status
# ---------------------------------------------------------------------------

def _activity_recorder():
    calls = []

    def add_activity_item(user, action, desc, when):
        calls.append((user, action, desc, when))

    return calls, add_activity_item


def _status_kwargs(infra, add_activity_item, *, not_found_message='nf',
                   error_label='Tidal', activity_subject='Tidal playlist',
                   name_getter=None):
    return dict(
        not_found_message=not_found_message, error_label=error_label,
        activity_subject=activity_subject,
        playlist_name_getter=name_getter or _pl_name_safe,
        add_activity_item=add_activity_item,
        sync_lock=infra['sync_lock'], sync_states=infra['sync_states'],
    )


def test_status_not_found():
    infra = _cancel_infra()
    calls, add = _activity_recorder()
    body, code = get_sync_status({}, 'missing',
                                 **_status_kwargs(infra, add, not_found_message='Tidal playlist not found'))
    assert code == 404 and body == {"error": "Tidal playlist not found"}
    assert calls == []


def test_status_no_sync_in_progress():
    infra = _cancel_infra()
    calls, add = _activity_recorder()
    states = {'pl': {'phase': 'discovered'}}  # no sync_playlist_id
    body, code = get_sync_status(states, 'pl', **_status_kwargs(infra, add))
    assert code == 404 and body == {"error": "No sync in progress"}


def test_status_running_returns_shape_without_mutation_or_activity():
    infra = _cancel_infra()
    infra['sync_states']['sp1'] = {"status": "running", "progress": {"n": 2}}
    calls, add = _activity_recorder()
    states = {'pl': {'phase': 'syncing', 'sync_playlist_id': 'sp1'}}
    body, code = get_sync_status(states, 'pl', **_status_kwargs(infra, add))
    assert code == 200
    assert body == {
        'phase': 'syncing', 'sync_status': 'running',
        'progress': {"n": 2}, 'complete': False, 'error': None,
    }
    assert states['pl']['phase'] == 'syncing'  # unchanged
    assert calls == []


def test_status_finished_sets_complete_and_posts_activity():
    infra = _cancel_infra()
    infra['sync_states']['sp1'] = {"status": "finished", "progress": {"done": 9}}
    calls, add = _activity_recorder()
    states = {'pl': {'phase': 'syncing', 'sync_playlist_id': 'sp1',
                     'playlist': {'name': 'Mix'}}}
    body, code = get_sync_status(states, 'pl', **_status_kwargs(
        infra, add, activity_subject='Spotify Link playlist',
        name_getter=_pl_name_strict))
    assert code == 200
    assert body['complete'] is True
    assert states['pl']['phase'] == 'sync_complete'
    assert states['pl']['sync_progress'] == {"done": 9}
    assert calls == [("", "Sync Complete", "Spotify Link playlist 'Mix' synced successfully", "Now")]


def test_status_error_reverts_and_posts_failed_activity():
    infra = _cancel_infra()
    infra['sync_states']['sp1'] = {"status": "error", "error": "boom"}
    calls, add = _activity_recorder()
    states = {'pl': {'phase': 'syncing', 'sync_playlist_id': 'sp1',
                     'playlist': {'name': 'Mix'}}}
    body, code = get_sync_status(states, 'pl', **_status_kwargs(
        infra, add, activity_subject='YouTube playlist', name_getter=_pl_name_safe))
    assert code == 200
    assert body['error'] == "boom"
    assert states['pl']['phase'] == 'discovered'
    assert calls == [("", "Sync Failed", "YouTube playlist 'Mix' sync failed", "Now")]


def test_status_strict_getter_missing_playlist_raises_500_after_partial_mutation():
    infra = _cancel_infra()
    infra['sync_states']['sp1'] = {"status": "finished", "progress": {}}
    calls, add = _activity_recorder()
    states = {'pl': {'phase': 'syncing', 'sync_playlist_id': 'sp1'}}  # no 'playlist'
    body, code = get_sync_status(states, 'pl', **_status_kwargs(
        infra, add, error_label='Deezer', name_getter=_pl_name_strict))
    assert code == 500 and "error" in body
    # phase was set to sync_complete BEFORE the strict getter raised (1:1).
    assert states['pl']['phase'] == 'sync_complete'
    assert calls == []  # activity never posted
