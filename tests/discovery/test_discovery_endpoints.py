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


# ---------------------------------------------------------------------------
# get_discovery_status
# ---------------------------------------------------------------------------

def test_discovery_status_not_found():
    from core.discovery.endpoints import get_discovery_status
    body, code = get_discovery_status({}, 'missing',
                                      not_found_message='Tidal discovery not found',
                                      error_label='Tidal')
    assert code == 404 and body == {"error": "Tidal discovery not found"}


def test_discovery_status_builds_response_and_bumps_access():
    from core.discovery.endpoints import get_discovery_status
    state = {
        'phase': 'discovered', 'status': 'done', 'discovery_progress': 100,
        'spotify_matches': 8, 'spotify_total': 10,
        'discovery_results': [{'x': 1}], 'last_accessed': 0,
    }
    states = {'pl': state}
    body, code = get_discovery_status(states, 'pl',
                                      not_found_message='nf', error_label='Tidal')
    assert code == 200
    assert body == {
        'phase': 'discovered', 'status': 'done', 'progress': 100,
        'spotify_matches': 8, 'spotify_total': 10,
        'results': [{'x': 1}], 'complete': True,
    }
    assert state['last_accessed'] != 0


def test_discovery_status_complete_false_when_not_discovered():
    from core.discovery.endpoints import get_discovery_status
    state = {
        'phase': 'discovering', 'status': 'running', 'discovery_progress': 40,
        'spotify_matches': 2, 'spotify_total': 10, 'discovery_results': [],
    }
    body, code = get_discovery_status({'pl': state}, 'pl',
                                      not_found_message='nf', error_label='Deezer')
    assert code == 200 and body['complete'] is False


def test_discovery_status_missing_field_raises_500():
    from core.discovery.endpoints import get_discovery_status
    # state missing 'status' -> strict access raises -> 500 (matches original)
    states = {'pl': {'phase': 'x'}}
    body, code = get_discovery_status(states, 'pl',
                                      not_found_message='nf', error_label='Beatport')
    assert code == 500 and "error" in body


# ---------------------------------------------------------------------------
# reset_playlist
# ---------------------------------------------------------------------------

def test_reset_not_found():
    from core.discovery.endpoints import reset_playlist
    body, code = reset_playlist({}, 'missing', label='Tidal',
                                not_found_message='Tidal playlist not found')
    assert code == 404 and body == {"error": "Tidal playlist not found"}


def test_reset_clears_state_preserving_playlist_and_cancels_future():
    from core.discovery.endpoints import reset_playlist
    fut = _FakeFuture()
    state = {
        'playlist': {'name': 'keep me'},
        'phase': 'discovered', 'status': 'done',
        'discovery_results': [{'x': 1}], 'discovery_progress': 100,
        'spotify_matches': 5, 'sync_playlist_id': 'sp', 'last_accessed': 0,
        'converted_spotify_playlist_id': 'cv', 'download_process_id': 'dp',
        'sync_progress': {'n': 1}, 'discovery_future': fut,
    }
    states = {'pl': state}
    body, code = reset_playlist(states, 'pl', label='Tidal', not_found_message='nf')

    assert code == 200
    assert body == {"success": True, "message": "Playlist reset to fresh phase"}
    assert fut.cancelled is True
    # cleared
    assert state['phase'] == 'fresh'
    assert state['status'] == 'fresh'
    assert state['discovery_results'] == []
    assert state['discovery_progress'] == 0
    assert state['spotify_matches'] == 0
    assert state['sync_playlist_id'] is None
    assert state['converted_spotify_playlist_id'] is None
    assert state['download_process_id'] is None
    assert state['sync_progress'] == {}
    assert state['discovery_future'] is None
    assert state['last_accessed'] != 0
    # original playlist payload preserved
    assert state['playlist'] == {'name': 'keep me'}


def test_reset_without_discovery_future():
    from core.discovery.endpoints import reset_playlist
    state = {'phase': 'discovered'}  # no discovery_future key
    body, code = reset_playlist({'pl': state}, 'pl', label='Deezer', not_found_message='nf')
    assert code == 200
    assert state['phase'] == 'fresh'


def test_reset_exception_returns_500():
    from core.discovery.endpoints import reset_playlist
    fut = object()  # .cancel missing -> AttributeError in try
    states = {'pl': {'discovery_future': fut}}
    body, code = reset_playlist(states, 'pl', label='Qobuz', not_found_message='nf')
    assert code == 500 and "error" in body


# ---------------------------------------------------------------------------
# get_playlist_states (bulk hydration list)
# ---------------------------------------------------------------------------

def test_playlist_states_builds_list_and_bumps_access():
    from core.discovery.endpoints import get_playlist_states
    s1 = {'phase': 'discovered', 'status': 'done', 'discovery_progress': 100,
          'spotify_matches': 3, 'spotify_total': 5, 'discovery_results': [{'a': 1}],
          'converted_spotify_playlist_id': 'cv', 'download_process_id': 'dp',
          'last_accessed': 0}
    states = {'k1': s1}
    body, code = get_playlist_states(states, error_label='Tidal', info_log_label='Tidal')
    assert code == 200
    assert body == {"states": [{
        'playlist_id': 'k1', 'phase': 'discovered', 'status': 'done',
        'discovery_progress': 100, 'spotify_matches': 3, 'spotify_total': 5,
        'discovery_results': [{'a': 1}], 'converted_spotify_playlist_id': 'cv',
        'download_process_id': 'dp', 'last_accessed': s1['last_accessed'],
    }]}
    assert s1['last_accessed'] != 0  # bumped


def test_playlist_states_empty():
    from core.discovery.endpoints import get_playlist_states
    body, code = get_playlist_states({}, error_label='Deezer', info_log_label='Deezer')
    assert code == 200 and body == {"states": []}


def test_playlist_states_optional_ids_default_none():
    from core.discovery.endpoints import get_playlist_states
    state = {'phase': 'fresh', 'status': 'fresh', 'discovery_progress': 0,
             'spotify_matches': 0, 'spotify_total': 0, 'discovery_results': []}
    body, _ = get_playlist_states({'k': state}, error_label='iTunes Link')
    assert body['states'][0]['converted_spotify_playlist_id'] is None
    assert body['states'][0]['download_process_id'] is None


def test_playlist_states_missing_required_field_raises_500():
    from core.discovery.endpoints import get_playlist_states
    # state missing 'phase' -> strict access raises -> 500
    body, code = get_playlist_states({'k': {'status': 'x'}}, error_label='Qobuz')
    assert code == 500 and "error" in body


# ---------------------------------------------------------------------------
# start_sync
# ---------------------------------------------------------------------------

def _start_kwargs(infra, *, convert_fn=None, name='PL', image='img',
                  activity_label='Tidal', error_label='Tidal',
                  not_found_message='Tidal playlist not found',
                  not_ready_message='Tidal playlist not ready for sync',
                  sync_id_prefix='tidal'):
    submitted = []

    def submit(sync_playlist_id, playlist_name, tracks, image_url):
        rec = (sync_playlist_id, playlist_name, tracks, image_url)
        submitted.append(rec)
        return f"future:{sync_playlist_id}"

    calls, add = _activity_recorder()
    kw = dict(
        sync_id_prefix=sync_id_prefix, not_found_message=not_found_message,
        not_ready_message=not_ready_message,
        convert_fn=convert_fn or (lambda r: [{'id': 't'}]),
        playlist_name_getter=lambda s: name, playlist_image_getter=lambda s: image,
        activity_label=activity_label, error_label=error_label,
        sync_lock=infra['sync_lock'], sync_states=infra['sync_states'],
        active_sync_workers=infra['active_sync_workers'],
        submit_sync_task=submit, add_activity_item=add,
    )
    return kw, submitted, calls


def test_start_sync_not_found():
    from core.discovery.endpoints import start_sync
    infra = _cancel_infra()
    kw, _, _ = _start_kwargs(infra)
    body, code = start_sync({}, 'missing', **kw)
    assert code == 404 and body == {"error": "Tidal playlist not found"}


def test_start_sync_not_ready_phase():
    from core.discovery.endpoints import start_sync
    infra = _cancel_infra()
    kw, _, _ = _start_kwargs(infra)
    states = {'pl': {'phase': 'discovering'}}
    body, code = start_sync(states, 'pl', **kw)
    assert code == 400 and body == {"error": "Tidal playlist not ready for sync"}


def test_start_sync_no_matches():
    from core.discovery.endpoints import start_sync
    infra = _cancel_infra()
    kw, _, _ = _start_kwargs(infra, convert_fn=lambda r: [])
    states = {'pl': {'phase': 'discovered', 'discovery_results': []}}
    body, code = start_sync(states, 'pl', **kw)
    assert code == 400 and body == {"error": "No Spotify matches found for sync"}


def test_start_sync_happy_path():
    from core.discovery.endpoints import start_sync
    infra = _cancel_infra()
    kw, submitted, calls = _start_kwargs(
        infra, convert_fn=lambda r: [{'id': 'a'}, {'id': 'b'}],
        name='My Mix', image='cover.jpg',
        activity_label='Spotify Link', error_label='Spotify Public',
        sync_id_prefix='spotify_public')
    states = {'h1': {'phase': 'discovered', 'discovery_results': [1, 2]}}
    body, code = start_sync(states, 'h1', **kw)

    assert code == 200
    assert body == {"success": True, "sync_playlist_id": "spotify_public_h1"}
    # state mutated
    assert states['h1']['phase'] == 'syncing'
    assert states['h1']['sync_playlist_id'] == 'spotify_public_h1'
    assert states['h1']['sync_progress'] == {}
    # sync infra seeded + worker registered
    assert infra['sync_states']['spotify_public_h1'] == {"status": "starting", "progress": {}}
    assert infra['active_sync_workers']['spotify_public_h1'] == 'future:spotify_public_h1'
    # submit got name/tracks/image
    assert submitted == [('spotify_public_h1', 'My Mix', [{'id': 'a'}, {'id': 'b'}], 'cover.jpg')]
    # activity uses activity_label (not error_label) + track count
    assert calls == [("", "Spotify Link Sync Started", "'My Mix' - 2 tracks", "Now")]


def test_start_sync_allows_resync_phases():
    from core.discovery.endpoints import start_sync
    for phase in ('sync_complete', 'download_complete'):
        infra = _cancel_infra()
        kw, _, _ = _start_kwargs(infra)
        states = {'pl': {'phase': phase, 'discovery_results': [1]}}
        body, code = start_sync(states, 'pl', **kw)
        assert code == 200, phase


def test_start_sync_exception_returns_500():
    from core.discovery.endpoints import start_sync
    infra = _cancel_infra()
    def boom(r):
        raise RuntimeError("convert blew up")
    kw, _, _ = _start_kwargs(infra, convert_fn=boom)
    states = {'pl': {'phase': 'discovered', 'discovery_results': [1]}}
    body, code = start_sync(states, 'pl', **kw)
    assert code == 500 and "error" in body


# ---------------------------------------------------------------------------
# first_artist extractors
# ---------------------------------------------------------------------------

def test_first_artist_str_or_obj():
    from core.discovery.endpoints import first_artist_str_or_obj as g
    assert g({'artists': ['A', 'B']}) == 'A'
    assert g({'artists': [{'name': 'Obj'}]}) == 'Obj'
    assert g({'artists': []}) == ''
    assert g({}) == ''


def test_first_artist_plain():
    from core.discovery.endpoints import first_artist_plain as g
    assert g({'artists': ['A', 'B']}) == 'A'
    assert g({'artists': []}) == ''
    assert g({}) == ''


# ---------------------------------------------------------------------------
# update_discovery_match
# ---------------------------------------------------------------------------

class _FakeCacheDB:
    def __init__(self):
        self.saved = []

    def save_discovery_cache_match(self, *args):
        self.saved.append(args)


def _update_kwargs(*, json_data, cache_db=None, getter=None):
    from core.discovery.endpoints import first_artist_plain
    db = cache_db or _FakeCacheDB()
    kw = dict(
        source_log_label='tidal', error_label='Tidal',
        original_track_key='tidal_track',
        original_artist_getter=getter or first_artist_plain,
        join_artist_names=lambda arts: ", ".join(arts),
        extract_artist_name=lambda a: str(a),
        build_fix_modal_spotify_data=lambda st: {'built': st['id']},
        get_discovery_cache_key=lambda name, artist: (name.lower(), artist.lower()),
        get_database=lambda: db,
        get_active_discovery_source=lambda: 'spotify',
    )
    return (lambda: json_data), kw, db


def test_update_match_missing_fields():
    from core.discovery.endpoints import update_discovery_match
    gj, kw, _ = _update_kwargs(json_data={'identifier': 'p'})  # missing track_index/spotify_track
    body, code = update_discovery_match({}, gj, **kw)
    assert code == 400 and body == {'error': 'Missing required fields'}


def test_update_match_state_not_found():
    from core.discovery.endpoints import update_discovery_match
    gj, kw, _ = _update_kwargs(json_data={
        'identifier': 'p', 'track_index': 0, 'spotify_track': {'id': 'x'}})
    body, code = update_discovery_match({}, gj, **kw)
    assert code == 404 and body == {'error': 'Discovery state not found'}


def test_update_match_invalid_index():
    from core.discovery.endpoints import update_discovery_match
    gj, kw, _ = _update_kwargs(json_data={
        'identifier': 'p', 'track_index': 5,
        'spotify_track': {'id': 'x', 'name': 'n', 'artists': [], 'album': 'a'}})
    states = {'p': {'discovery_results': []}}
    body, code = update_discovery_match(states, gj, **kw)
    assert code == 400 and body == {'error': 'Invalid track index'}


def test_update_match_happy_path_full():
    from core.discovery.endpoints import update_discovery_match
    sp = {'id': 'sp9', 'name': 'New Song', 'artists': ['Art1', 'Art2'],
          'album': 'Alb', 'duration_ms': 185000, 'image_url': 'cov.jpg'}
    gj, kw, db = _update_kwargs(json_data={
        'identifier': 'p', 'track_index': 0, 'spotify_track': sp})
    result = {'status': 'not_found', 'tidal_track': {'name': 'Orig', 'artists': ['OrigArt']}}
    states = {'p': {'discovery_results': [result], 'spotify_matches': 2}}

    body, code = update_discovery_match(states, gj, **kw)

    assert code == 200 and body['success'] is True
    assert result['status'] == 'Found'
    assert result['status_class'] == 'found'
    assert result['spotify_track'] == 'New Song'
    assert result['spotify_artist'] == 'Art1, Art2'
    assert result['spotify_id'] == 'sp9'
    assert result['duration'] == '3:05'
    assert result['spotify_data'] == {'built': 'sp9'}
    assert result['wing_it_fallback'] is False
    assert result['manual_match'] is True
    assert states['p']['spotify_matches'] == 3  # incremented (was not found)
    # cache saved with normalized key + matched_data carrying image
    assert len(db.saved) == 1
    key0, key1, source, score, matched, oname, oartist = db.saved[0]
    assert (key0, key1) == ('orig', 'origart')
    assert source == 'spotify' and score == 1.0
    assert matched['album'] == {'name': 'Alb', 'image_url': 'cov.jpg', 'images': [{'url': 'cov.jpg'}]}
    assert oname == 'Orig' and oartist == 'OrigArt'


def test_update_match_no_increment_when_already_found():
    from core.discovery.endpoints import update_discovery_match
    sp = {'id': 'x', 'name': 'n', 'artists': ['A'], 'album': 'a', 'duration_ms': 0}
    gj, kw, _ = _update_kwargs(json_data={'identifier': 'p', 'track_index': 0, 'spotify_track': sp})
    result = {'status': 'Found', 'tidal_track': {}}
    states = {'p': {'discovery_results': [result], 'spotify_matches': 5}}
    body, code = update_discovery_match(states, gj, **kw)
    assert code == 200
    assert states['p']['spotify_matches'] == 5  # unchanged
    assert result['duration'] == '0:00'


def test_update_match_cache_error_is_swallowed():
    from core.discovery.endpoints import update_discovery_match
    class _BoomDB:
        def save_discovery_cache_match(self, *a):
            raise RuntimeError("db down")
    sp = {'id': 'x', 'name': 'n', 'artists': ['A'], 'album': 'a', 'duration_ms': 0}
    gj, kw, _ = _update_kwargs(json_data={'identifier': 'p', 'track_index': 0, 'spotify_track': sp},
                               cache_db=_BoomDB())
    states = {'p': {'discovery_results': [{'status': 'x', 'tidal_track': {}}], 'spotify_matches': 0}}
    body, code = update_discovery_match(states, gj, **kw)
    assert code == 200 and body['success'] is True  # cache failure doesn't fail the request


def test_update_match_get_json_raises_returns_500():
    from core.discovery.endpoints import update_discovery_match
    def boom():
        raise ValueError("bad json")
    _, kw, _ = _update_kwargs(json_data={})
    body, code = update_discovery_match({}, boom, **kw)
    assert code == 500 and 'error' in body
