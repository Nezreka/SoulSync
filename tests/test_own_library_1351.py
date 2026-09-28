"""Regression tests for SoulSync issue #1351 — own-library downloads landing in
the shared folder.

Covers all three fixes:
1. ``post_process_matched_download_with_verification`` stamps the batch's
   profile_id into the context BEFORE popping batch_id, so the inner
   pipeline's ``import_profile_id()`` still resolves (it used to return None
   and ``build_final_path_for_track`` fell back to the shared folder). The
   staging route (``try_staging_match``) never puts batch_id in its context,
   so the wrapper's batch_id argument is used as the fallback source.
2. Automatic wishlist processing stamps each fetched track with its owning
   profile id. The batching engine groups tracks by track-level profile_id
   (H10) — but nothing ever stamped it, so every auto batch fell back to
   the runtime profile (1) and other profiles' tracks downloaded into the
   wrong library. The per-(track, owner) dedupe now also keeps both owners
   of a shared track.
3. Jellyfin's ``trigger_library_scan()`` refreshes EVERY music library on the
   default call, not just the first match.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

import core.imports.pipeline as import_pipeline
import core.runtime_state as runtime_state
import core.wishlist.processing as wishlist_processing
from core.jellyfin_client import JellyfinClient


# ---------------------------------------------------------------------------
# Fix 1: profile_id survives the batch_id pop in the post-processing wrapper
# ---------------------------------------------------------------------------

def _run_wrapper(monkeypatch, context, task_id, batch_id, batch_row):
    """Run the wrapper with the inner pipeline stubbed; capture the context
    the inner pipeline saw."""
    runtime_state.download_batches.clear()
    if batch_row is not None:
        runtime_state.download_batches[batch_id] = dict(batch_row)

    seen = {}

    def fake_inner(context_key, context, file_path, runtime, metadata_runtime=None):
        seen['profile_id'] = context.get('profile_id')
        seen['batch_id'] = context.get('batch_id')
        seen['task_id'] = context.get('task_id')

    monkeypatch.setattr(import_pipeline, 'post_process_matched_download', fake_inner)
    # Keep the #999 atomic-publish redirect off so no extra context keys appear.
    monkeypatch.setattr(import_pipeline.config_manager, 'get', lambda key, default=None: False)

    runtime = SimpleNamespace()  # no on_download_completed -> notify is a no-op
    import_pipeline.post_process_matched_download_with_verification(
        'ctx-key', context, '/tmp/fake.flac', task_id, batch_id, runtime,
    )
    return seen


def test_wrapper_stamps_profile_id_from_batch_before_inner_pipeline(monkeypatch):
    """Batched path: context carries batch_id; the inner pipeline must see
    the batch's profile and must NOT see batch_id/task_id (pop semantics)."""
    context = {'batch_id': 'b1', 'task_id': 't1'}
    seen = _run_wrapper(monkeypatch, context, 't1', 'b1', {'profile_id': 7})

    assert seen['profile_id'] == 7
    assert seen['batch_id'] is None
    assert seen['task_id'] is None
    # The wrapper restores both ids after the inner run.
    assert context['batch_id'] == 'b1'
    assert context['task_id'] == 't1'


def test_wrapper_stamps_profile_id_for_staging_context_without_batch_id(monkeypatch):
    """Staging path (try_staging_match): the context never had batch_id; the
    wrapper's batch_id argument is the only link to the profile."""
    context = {'staging_source': True}
    seen = _run_wrapper(monkeypatch, context, 't9', 'b2', {'profile_id': 9})

    assert seen['profile_id'] == 9


def test_wrapper_keeps_explicit_profile_id_over_batch(monkeypatch):
    """An explicit profile_id stamp (imports from the page) wins over the
    batch lookup."""
    context = {'profile_id': 3, 'batch_id': 'b1', 'task_id': 't1'}
    seen = _run_wrapper(monkeypatch, context, 't1', 'b1', {'profile_id': 7})

    assert seen['profile_id'] == 3


def test_wrapper_leaves_profile_unset_when_batch_has_none(monkeypatch):
    """No profile anywhere -> no stamp; behavior identical to before the fix
    (shared-folder fallback)."""
    context = {'batch_id': 'b1', 'task_id': 't1'}
    seen = _run_wrapper(monkeypatch, context, 't1', 'b1', {'phase': 'queued'})

    assert seen['profile_id'] is None


# ---------------------------------------------------------------------------
# End to end: the stamped profile routes the final path to the own library
# ---------------------------------------------------------------------------

def test_stamped_context_routes_transfer_root_to_own_library(monkeypatch):
    """The reporter's symptom, closed end to end: a context carrying the
    batch's profile (what the wrapper now stamps) resolves
    transfer_root_for_context to the profile's own-library root instead of
    the shared folder."""
    from core.imports import paths as import_paths

    fake_db = SimpleNamespace(
        get_profile_library=lambda pid: (
            {'mode': 'own', 'root': '/media/own-lib'}
            if int(pid) == 7 else {'mode': 'shared', 'root': None}
        ),
    )
    monkeypatch.setattr('database.music_database.get_database', lambda: fake_db)
    monkeypatch.setattr('core.library_scope.own_library_supported', lambda: True)
    monkeypatch.setattr(import_paths, 'config_root_path', lambda p, *a: p)
    monkeypatch.setattr(import_paths, '_get_config_manager',
                        lambda: SimpleNamespace(get=lambda k, d=None: '/media/shared'))

    # What the wrapper stamps for a profile-7 batch:
    assert import_paths.transfer_root_for_context({'profile_id': 7}) == '/media/own-lib'
    # No profile anywhere (the old broken state): shared fallback, unchanged.
    assert import_paths.transfer_root_for_context({}) == '/media/shared'


# ---------------------------------------------------------------------------
# Fix 2: automatic wishlist processing keeps each track's owning profile
# ---------------------------------------------------------------------------
# The auto flow fetches one wishlist per profile and merges them. The
# batching engine (_run_wishlist_cycle, H10) groups tracks by track-level
# profile_id — but nothing ever stamped it, so every auto batch fell back
# to the runtime profile (1) and other profiles' tracks downloaded into
# the wrong library. The fix stamps each fetched track with its owning
# profile id; these tests pin the stamp and its effect on batching.

def _auto_runtime(monkeypatch):
    """A minimal WishlistAutoProcessingRuntime-shaped namespace."""
    runtime = SimpleNamespace(
        logger=SimpleNamespace(
            info=lambda *a, **k: None,
            warning=lambda *a, **k: None,
            error=lambda *a, **k: None,
            debug=lambda *a, **k: None,
            exception=lambda *a, **k: None,
        ),
        is_actually_processing=lambda: False,
        processing_guard=lambda: _true_context(),
        app_context_factory=lambda: _true_context(),
        get_profiles_database=lambda: SimpleNamespace(
            get_all_profiles=lambda: [{'id': 1}, {'id': 2}]),
        get_music_database=lambda: SimpleNamespace(
            remove_wishlist_duplicates=lambda profile_id: 0,
            reset_wishlist_retry_backoff=lambda ids, profile_id=None: 0,
        ),
        get_active_server=lambda: 'test-server',
        download_batches={},
        tasks_lock=threading.Lock(),
        update_automation_progress=lambda *a, **k: None,
        current_time_fn=lambda: 0.0,
        profile_id=1,
    )
    return runtime


@contextmanager
def _true_context():
    yield True


def _run_auto_flow(monkeypatch, tracks_by_profile):
    """Run process_wishlist_automatically with collaborators stubbed; return
    the tracks handed to _run_wishlist_cycle."""
    captured = {}

    service = SimpleNamespace(
        get_wishlist_count=lambda profile_id, approved_only=False: len(
            tracks_by_profile.get(profile_id, [])),
        get_wishlist_tracks_for_download=lambda profile_id, approved_only=False: [
            dict(t) for t in tracks_by_profile.get(profile_id, [])],
    )
    monkeypatch.setattr(wishlist_processing, 'get_wishlist_service', lambda: service)
    monkeypatch.setattr('core.permissions.profile_can_download', lambda p: True)
    monkeypatch.setattr(wishlist_processing, 'remove_tracks_already_in_library',
                        lambda *a, **k: 0)
    # Real sanitize: proves the per-(track, owner) dedupe keeps both copies.
    monkeypatch.setattr('core.metadata.release_dates.split_released_unreleased',
                        lambda tracks: (tracks, []))
    monkeypatch.setattr(wishlist_processing, 'filter_wishlist_tracks_by_category',
                        lambda tracks, cycle: (tracks, []))
    monkeypatch.setattr(wishlist_processing, 'get_wishlist_cycle', lambda db_factory: 'singles')
    monkeypatch.setattr(wishlist_processing, 'set_wishlist_cycle', lambda db_factory, cycle: None)

    def fake_cycle(runtime, **kwargs):
        captured['tracks'] = kwargs['tracks']
        captured['auto_initiated'] = kwargs['auto_initiated']
        return {'submitted': [], 'album_batches': 0,
                'residual_count': len(kwargs['tracks'])}

    monkeypatch.setattr(wishlist_processing, '_run_wishlist_cycle', fake_cycle)

    runtime = _auto_runtime(monkeypatch)
    wishlist_processing.process_wishlist_automatically(runtime)
    return captured


def test_auto_flow_stamps_each_track_with_its_owning_profile(monkeypatch):
    """The #1351 regression: tracks fetched per profile must reach the
    batching engine carrying their owner's id (before the fix they carried
    nothing and every batch fell back to profile 1)."""
    tracks = {
        1: [{'spotify_track_id': 's1', 'name': 'Track One'}],
        2: [{'spotify_track_id': 's2', 'name': 'Track Two'}],
    }
    captured = _run_auto_flow(monkeypatch, tracks)

    by_track = {t['spotify_track_id']: t['profile_id'] for t in captured['tracks']}
    assert by_track == {'s1': 1, 's2': 2}
    assert captured['auto_initiated'] is True


def test_auto_flow_keeps_both_owners_of_a_shared_track(monkeypatch):
    """The same track wishlisted by two profiles is two owned requests: the
    per-(track, owner) dedupe keeps both, each stamped with its owner."""
    tracks = {
        1: [{'spotify_track_id': 'shared', 'name': 'Shared Track'}],
        2: [{'spotify_track_id': 'shared', 'name': 'Shared Track'}],
    }
    captured = _run_auto_flow(monkeypatch, tracks)

    owners = sorted(t['profile_id'] for t in captured['tracks']
                    if t['spotify_track_id'] == 'shared')
    assert owners == [1, 2]


def _cycle_runtime(batches):
    return SimpleNamespace(
        logger=SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None,
                               error=lambda *a, **k: None, debug=lambda *a, **k: None),
        tasks_lock=threading.Lock(),
        download_batches=batches,
        get_batch_max_concurrent=lambda: 2,
        missing_download_executor=SimpleNamespace(submit=lambda fn, *a: None),
        album_bundle_executor=None,
        run_full_missing_tracks_process=lambda *a: None,
        current_time_fn=lambda: 0.0,
        profile_id=1,
    )


def _spy_batch_rows(monkeypatch):
    made_rows = []
    real_make_row = wishlist_processing.make_wishlist_batch_row

    def spy_make_row(**kwargs):
        row = real_make_row(**kwargs)
        made_rows.append(row)
        return row

    monkeypatch.setattr(wishlist_processing, 'make_wishlist_batch_row', spy_make_row)
    return made_rows


def test_stamped_tracks_batch_under_their_owning_profile(monkeypatch):
    """End to end through the batching engine: tracks stamped by the auto
    flow produce batches under each owner's profile, not the runtime's."""
    made_rows = _spy_batch_rows(monkeypatch)
    batches = {}
    runtime = _cycle_runtime(batches)
    tracks = [
        {'spotify_track_id': 's1', 'name': 'One', 'profile_id': 1},
        {'spotify_track_id': 's2', 'name': 'Two', 'profile_id': 2},
    ]

    wishlist_processing._run_wishlist_cycle(
        runtime, playlist_id='wishlist', cycle='singles', tracks=tracks,
        run_id='run-1', auto_initiated=True)

    assert made_rows, "expected at least one batch row"
    assert {r['profile_id'] for r in made_rows} == {1, 2}


def test_unstamped_tracks_fall_back_to_runtime_profile(monkeypatch):
    """Tracks without a stamp (the manual flow) keep the old behavior:
    everything batches under the runtime's profile."""
    made_rows = _spy_batch_rows(monkeypatch)
    batches = {}
    runtime = _cycle_runtime(batches)
    runtime.profile_id = 4
    tracks = [{'spotify_track_id': 's1', 'name': 'Solo'}]

    wishlist_processing._run_wishlist_cycle(
        runtime, playlist_id='wishlist', cycle='singles', tracks=tracks,
        run_id='run-1', auto_initiated=False)

    assert made_rows
    assert {r['profile_id'] for r in made_rows} == {4}


# ---------------------------------------------------------------------------
# Fix 3: Jellyfin scan refreshes every music library
# ---------------------------------------------------------------------------

def _jellyfin_client(monkeypatch, views):
    client = JellyfinClient()
    client.base_url = 'http://jellyfin:8096'
    client.api_key = 'fake-key'
    client.user_id = 'user-1'
    monkeypatch.setattr(client, 'ensure_connection', lambda: True)
    monkeypatch.setattr(client, '_make_request',
                        lambda path: {'Items': views} if path.endswith('/Views') else None)
    posted = []

    class _Resp:
        def raise_for_status(self):
            pass

    def fake_post(url, headers=None, params=None, timeout=None):
        posted.append(url)
        return _Resp()

    monkeypatch.setattr('requests.post', fake_post)
    return client, posted


_MUSIC_VIEWS = [
    {'Id': 'lib-a', 'Name': 'Music', 'CollectionType': 'music'},
    {'Id': 'lib-b', 'Name': 'Profile Two Music', 'CollectionType': 'music'},
    {'Id': 'lib-c', 'Name': 'Movies', 'CollectionType': 'movies'},
]


def test_trigger_library_scan_refreshes_all_music_libraries(monkeypatch):
    """The default post-download scan hits every music library, not just the
    first match."""
    client, posted = _jellyfin_client(monkeypatch, _MUSIC_VIEWS)

    assert client.trigger_library_scan() is True

    assert sorted(posted) == [
        'http://jellyfin:8096/Items/lib-a/Refresh',
        'http://jellyfin:8096/Items/lib-b/Refresh',
    ]


def test_trigger_library_scan_explicit_name_targets_one_library(monkeypatch):
    """An explicit non-default name keeps single-library behavior."""
    client, posted = _jellyfin_client(monkeypatch, _MUSIC_VIEWS)

    assert client.trigger_library_scan('Profile Two') is True

    assert posted == ['http://jellyfin:8096/Items/lib-b/Refresh']


def test_trigger_library_scan_no_music_library_returns_false(monkeypatch):
    client, posted = _jellyfin_client(monkeypatch, [
        {'Id': 'lib-c', 'Name': 'Movies', 'CollectionType': 'movies'},
    ])
    client.music_library_id = None

    assert client.trigger_library_scan() is False
    assert posted == []
