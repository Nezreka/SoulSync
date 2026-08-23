"""Cross-batch download dedup.

When the same song sits in two concurrently-running batches and the faster
batch has already obtained the file, the slower batch must NOT re-download and
re-import it (which produced a confusing duplicate Completed row with no
AcoustID badge — the second copy imports as "already owned"). Instead the
slower task short-circuits to ``already_owned`` before searching, inheriting the
owner's verification so its row stays consistent.

We only short-circuit against a sibling that has already SUCCEEDED and is
terminal (completed / already_owned) with a file to show for it — never one
still in flight and never one in post-processing, whose integrity/quality/
AcoustID gates can still fail it, so a failed peer can never strand this track
undownloaded (L2-003).
"""

from __future__ import annotations

import threading

import pytest

from core.downloads import task_worker as tw
from core.downloads import lifecycle as lc
from core.runtime_state import download_batches, download_tasks


@pytest.fixture(autouse=True)
def reset_state():
    download_tasks.clear()
    download_batches.clear()
    yield
    download_tasks.clear()
    download_batches.clear()


# ---------------------------------------------------------------------------
# Identity / sibling-finder unit tests
# ---------------------------------------------------------------------------

_TI = {'id': 'sp-1', 'name': 'Money', 'artists': ['Pink Floyd'],
       'album': 'DSOTM', 'duration_ms': 383000}


def _track():
    from core.spotify_client import Track as SpotifyTrack
    return SpotifyTrack(id='sp-1', name='Money', artists=['Pink Floyd'],
                        album='DSOTM', duration_ms=383000, popularity=0)


def test_finds_completed_sibling_with_same_identity():
    download_tasks['owner'] = {'status': 'completed', 'track_info': _TI,
                               'filename': 'Money.flac',
                               'verification_status': 'verified', 'batch_id': 'bA'}
    download_tasks['dup'] = {'status': 'pending', 'track_info': _TI, 'batch_id': 'bB'}
    owner_id, owner = tw._find_owning_sibling('dup', _track())
    assert owner_id == 'owner'
    assert owner['verification_status'] == 'verified'


def test_ignores_sibling_still_in_flight():
    # A sibling that is only searching/downloading hasn't obtained the file yet —
    # skipping against it could strand this track if that sibling later fails.
    download_tasks['owner'] = {'status': 'searching', 'track_info': _TI,
                               'batch_id': 'bA'}
    download_tasks['dup'] = {'status': 'pending', 'track_info': _TI,
                             'batch_id': 'bB'}
    owner_id, owner = tw._find_owning_sibling('dup', _track())
    assert owner_id is None


def test_ignores_different_track():
    other = dict(_TI, name='Time')
    download_tasks['owner'] = {'status': 'completed', 'track_info': other,
                               'filename': 'Time.flac', 'batch_id': 'bA'}
    download_tasks['dup'] = {'status': 'pending', 'track_info': _TI,
                             'batch_id': 'bB'}
    owner_id, _ = tw._find_owning_sibling('dup', _track())
    assert owner_id is None


def test_excludes_self():
    download_tasks['dup'] = {'status': 'completed', 'track_info': _TI,
                             'filename': 'Money.flac', 'batch_id': 'bB'}
    owner_id, _ = tw._find_owning_sibling('dup', _track())
    assert owner_id is None


def test_ignores_a_twin_inside_the_same_batch():
    """One batch's queue is the caller's own list — a repeat in it is their
    choice, and the batch's own bookkeeping owns that decision."""
    download_tasks['owner'] = {'status': 'completed', 'track_info': _TI,
                               'filename': 'Money.flac', 'batch_id': 'bA'}
    download_tasks['dup'] = {'status': 'pending', 'track_info': _TI,
                             'batch_id': 'bA'}
    owner_id, _ = tw._find_owning_sibling('dup', _track())
    assert owner_id is None


def test_a_hand_started_download_is_never_deduped():
    """No batch means the user asked for this one directly (a re-download for a
    better rip, say). Answering that with "you already have it" would undo an
    action they deliberately took."""
    download_tasks['owner'] = {'status': 'completed', 'track_info': _TI,
                               'filename': 'Money.flac', 'batch_id': 'bA'}
    download_tasks['dup'] = {'status': 'pending', 'track_info': _TI}
    owner_id, _ = tw._find_owning_sibling('dup', _track())
    assert owner_id is None


# ---------------------------------------------------------------------------
# L2-003: what may NOT be deduped away
# ---------------------------------------------------------------------------


def test_ignores_sibling_still_in_post_processing():
    """The file is on disk but the import gates have not run. Integrity,
    quality and AcoustID can still quarantine, requeue or fail that owner —
    a task that stood down against it would be left with nothing."""
    download_tasks['owner'] = {'status': 'post_processing', 'track_info': _TI,
                               'filename': 'Money.flac', 'batch_id': 'bA'}
    download_tasks['dup'] = {'status': 'pending', 'track_info': _TI, 'batch_id': 'bB'}
    owner_id, _ = tw._find_owning_sibling('dup', _track())
    assert owner_id is None


def test_ignores_terminal_sibling_with_no_file():
    download_tasks['owner'] = {'status': 'completed', 'track_info': _TI,
                               'batch_id': 'bA'}
    download_tasks['dup'] = {'status': 'pending', 'track_info': _TI, 'batch_id': 'bB'}
    owner_id, _ = tw._find_owning_sibling('dup', _track())
    assert owner_id is None


def test_collaboration_credits_still_dedup():
    """The requesting side used to be built from the FIRST artist only while
    the sibling side joined all of them, so every collaboration was a false
    negative and got downloaded twice."""
    collab = {'id': 'sp-9', 'name': 'Under Pressure',
              'artists': ['Queen', 'David Bowie'], 'album': 'Hot Space',
              'duration_ms': 248000}
    download_tasks['owner'] = {'status': 'completed', 'track_info': collab,
                               'filename': 'up.flac', 'batch_id': 'bA'}
    download_tasks['dup'] = {'status': 'pending', 'track_info': collab, 'batch_id': 'bB'}
    from core.spotify_client import Track as SpotifyTrack
    track = SpotifyTrack(id='sp-9', name='Under Pressure',
                         artists=['Queen', 'David Bowie'], album='Hot Space',
                         duration_ms=248000, popularity=0)
    owner_id, _ = tw._find_owning_sibling('dup', track)
    assert owner_id == 'owner'


def test_same_metadata_different_provider_ids_is_not_the_same_recording():
    """Remaster vs original: identical title/artist/album, different ids in the
    same namespace. The id is authoritative in both directions."""
    mine = {'id': 'dz-1', 'source': 'deezer', 'name': 'Money',
            'artists': ['Pink Floyd'], 'album': 'DSOTM'}
    theirs = dict(mine, id='dz-2')
    download_tasks['owner'] = {'status': 'completed', 'track_info': theirs,
                               'filename': 'money.flac', 'batch_id': 'bA'}
    download_tasks['dup'] = {'status': 'pending', 'track_info': mine, 'batch_id': 'bB'}
    from core.spotify_client import Track as SpotifyTrack
    track = SpotifyTrack(id='dz-1', name='Money', artists=['Pink Floyd'],
                         album='DSOTM', duration_ms=0, popularity=0)
    assert tw._find_owning_sibling('dup', track)[0] is None
    # Same id in the same namespace still dedups.
    download_tasks['owner']['track_info'] = dict(mine)
    assert tw._find_owning_sibling('dup', track)[0] == 'owner'


def test_alternate_take_with_a_very_different_duration_is_not_deduped():
    live = dict(_TI, duration_ms=_TI['duration_ms'] + 60000)
    live.pop('id')
    mine = dict(_TI)
    mine.pop('id')
    download_tasks['owner'] = {'status': 'completed', 'track_info': live,
                               'filename': 'money-live.flac', 'batch_id': 'bA'}
    download_tasks['dup'] = {'status': 'pending', 'track_info': mine, 'batch_id': 'bB'}
    assert tw._find_owning_sibling('dup', _track())[0] is None


def test_higher_quality_profile_request_is_not_deduped_against_a_lower_one():
    """A wishlist upgrade carries its own quality_profile_id. Standing it down
    against a copy fetched under a different profile silently cancels the very
    upgrade the user asked for."""
    low = dict(_TI, quality_profile_id=1)
    high = dict(_TI, quality_profile_id=2)
    download_tasks['owner'] = {'status': 'completed', 'track_info': low,
                               'filename': 'money.mp3', 'batch_id': 'bA'}
    download_tasks['dup'] = {'status': 'pending', 'track_info': high, 'batch_id': 'bB'}
    assert tw._find_owning_sibling('dup', _track())[0] is None
    # Same profile on both sides: dedup as before.
    download_tasks['owner']['track_info'] = dict(high)
    assert tw._find_owning_sibling('dup', _track())[0] == 'owner'


# ---------------------------------------------------------------------------
# Worker integration: dedup short-circuit
# ---------------------------------------------------------------------------

class _Rec:
    def __init__(self):
        self.calls = []

    def __call__(self, name):
        def _inner(*a, **kw):
            self.calls.append((name, a, kw))
        return _inner


class _FakeClient:
    def __init__(self):
        self.mode = 'soulseek'
        self.search_calls = []

    def client(self, name):
        return None

    async def search(self, query, timeout=30, exclude_sources=None,
                     progress_callback=None, search_mode=None):
        self.search_calls.append(query)
        return ([], None)


def _deps(rec):
    return tw.TaskWorkerDeps(
        download_orchestrator=_FakeClient(),
        matching_engine=type('M', (), {'generate_download_queries': lambda self, t: []})(),
        run_async=lambda coro: coro.close(),
        try_source_reuse=lambda *a, **kw: False,
        store_batch_source=rec('store_batch_source'),
        try_staging_match=lambda *a, **kw: False,
        get_valid_candidates=lambda *a, **kw: [],
        attempt_download_with_candidates=lambda *a, **kw: False,
        on_download_completed=rec('on_download_completed'),
        recover_worker_slot=rec('recover_worker_slot'),
    )


def test_worker_skips_redownload_when_sibling_already_owns():
    download_tasks['owner'] = {'status': 'completed', 'track_info': _TI,
                               'verification_status': 'verified', 'quality': 'FLAC',
                               'filename': 'Money.flac', 'batch_id': 'bA'}
    download_tasks['dup'] = {'status': 'pending', 'track_info': _TI, 'batch_id': 'bB'}
    rec = _Rec()
    deps = _deps(rec)
    tw.download_track_worker('dup', 'bB', deps)

    assert download_tasks['dup']['status'] == 'already_owned'
    assert download_tasks['dup']['verification_status'] == 'verified'  # inherited
    assert download_tasks['dup']['quality'] == 'FLAC'
    assert download_tasks['dup']['_dedup_owned_by'] == 'owner'
    # Completion signalled as success, and NO download/search attempted.
    assert ('on_download_completed', ('bB', 'dup', True), {}) in rec.calls
    assert deps.download_orchestrator.search_calls == []


# ---------------------------------------------------------------------------
# Lifecycle: already_owned must count toward batch completion
# ---------------------------------------------------------------------------

class _FakeConfig:
    def get(self, key, default=None):
        return default


class _FakeMonitor:
    def __getattr__(self, name):
        return lambda *a, **kw: None


def _lc_deps():
    rec = _Rec()
    return lc.LifecycleDeps(
        config_manager=_FakeConfig(),
        automation_engine=None,
        download_monitor=_FakeMonitor(),
        repair_worker=None,
        mb_worker=None,
        is_shutting_down=lambda: False,
        get_batch_lock=lambda bid: threading.Lock(),
        submit_download_track_worker=rec('submit_dl'),
        submit_failed_to_wishlist=rec('sf'),
        submit_failed_to_wishlist_with_auto_completion=rec('sfa'),
        process_failed_to_wishlist=rec('pf'),
        process_failed_to_wishlist_with_auto_completion=rec('pfa'),
        ensure_wishlist_track_format=lambda t: t,
        get_track_artist_name=lambda t: 'Artist',
        check_and_remove_from_wishlist=rec('cw'),
        regenerate_batch_m3u=rec('regen'),
        youtube_playlist_states={},
        tidal_discovery_states={},
        deezer_discovery_states={},
        spotify_public_discovery_states={},
    )


def test_already_owned_task_counts_as_finished_and_completes_batch():
    download_tasks['t1'] = {'status': 'already_owned', 'track_info': {'name': 'X'}}
    download_batches['b1'] = {
        'queue': ['t1'], 'queue_index': 1, 'active_count': 1,
        'max_concurrent': 1, 'permanently_failed_tracks': [],
        'cancelled_tracks': set(), 'playlist_name': 'P',
    }
    lc.on_download_completed('b1', 't1', True, _lc_deps())
    # Batch must reach 'complete' — before the fix, already_owned wasn't counted
    # as finished so the batch hung forever.
    assert download_batches['b1'].get('phase') == 'complete'
