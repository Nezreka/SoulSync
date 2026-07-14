"""Tests for core/downloads/candidates.py — candidate fallback download logic."""

from __future__ import annotations

import threading
from dataclasses import dataclass

import pytest

from core.downloads import candidates as dc
from core.runtime_state import (
    download_tasks,
    matched_context_lock,
    matched_downloads_context,
    tasks_lock,
)


# ---------------------------------------------------------------------------
# Fixtures + fakes
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_state():
    download_tasks.clear()
    matched_downloads_context.clear()
    yield
    download_tasks.clear()
    matched_downloads_context.clear()


@dataclass
class _Candidate:
    username: str = "user1"
    filename: str = "song.flac"
    confidence: float = 0.9
    size: int = 1000
    title: str = "Song"
    artist: str = "Artist"
    album: str = "Album"
    quality_score: float = 0.0
    upload_speed: int = 0
    queue_length: int = 0
    free_upload_slots: int = 0


@dataclass
class _Track:
    name: str = "Song Title"
    album: str = "Album Name"
    artists: list = None
    id: str = "spt-1"

    def __post_init__(self):
        if self.artists is None:
            self.artists = ["Artist Name"]


class _FakeSoulseek:
    def __init__(self, download_id="dl-1"):
        self._download_id = download_id
        self.download_calls = []
        self.cancel_calls = []

    async def download(self, username, filename, size):
        self.download_calls.append((username, filename, size))
        return self._download_id

    async def cancel_download(self, download_id, username, remove=True):
        self.cancel_calls.append((download_id, username, remove))


class _FakeSpotify:
    def __init__(self, track_details=None):
        self._track_details = track_details

    def get_track_details(self, track_id):
        return self._track_details


class _FakeDB:
    def __init__(self, blacklisted=None):
        self._blacklisted = blacklisted or set()

    def is_blacklisted(self, username, filename):
        return (username, filename) in self._blacklisted


def _run_async(coro):
    """Drive async functions synchronously for tests."""
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _build_deps(
    *,
    soulseek=None,
    spotify=None,
    db=None,
    update_status=None,
    on_complete=None,
):
    deps = dc.CandidatesDeps(
        download_orchestrator=soulseek or _FakeSoulseek(),
        spotify_client=spotify or _FakeSpotify(),
        run_async=_run_async,
        get_database=lambda: db or _FakeDB(),
        update_task_status=update_status or (lambda task_id, status: None),
        make_context_key=lambda u, f: f"{u}::{f}",
        on_download_completed=on_complete or (lambda *a, **kw: None),
    )
    return deps


def _seed_task(task_id, *, status="pending", track_info=None, used_sources=None,
               download_id=None, profile_id=1):
    download_tasks[task_id] = {
        "status": status,
        "track_info": track_info or {},
        "used_sources": used_sources or set(),
        "download_id": download_id,
        "profile_id": profile_id,
    }


# ---------------------------------------------------------------------------
# Happy path — first candidate succeeds
# ---------------------------------------------------------------------------

def test_first_candidate_starts_download_and_returns_true():
    """High-confidence candidate accepts → download_id stored, context populated, returns True."""
    deps = _build_deps()
    _seed_task("t1")
    candidates = [_Candidate(filename="best.flac", confidence=0.95)]
    track = _Track()

    result = dc.attempt_download_with_candidates("t1", candidates, track, batch_id="b1", deps=deps)

    assert result is True
    assert deps.download_orchestrator.download_calls == [("user1", "best.flac", 1000)]
    assert download_tasks["t1"]["download_id"] == "dl-1"
    assert "user1::best.flac" in matched_downloads_context


def test_retry_context_preserves_exact_library_entity_from_track_info():
    deps = _build_deps()
    entity = {"track_id": 42, "album_id": 7, "quality_profile_id": 3}
    _seed_task("t_lib2", track_info={
        "name": "Song Title",
        "artists": [{"name": "Artist Name"}],
        "album": {"name": "Album Name"},
        "lib2_entity": entity,
        "_acquisition_import_id": "aim1-test",
    })

    result = dc.attempt_download_with_candidates(
        "t_lib2",
        [_Candidate(filename="retry.flac")],
        _Track(),
        deps=deps,
    )

    assert result is True
    context = matched_downloads_context["user1::retry.flac"]
    assert context["track_info"]["lib2_entity"] == entity
    assert context["track_info"]["_acquisition_import_id"] == "aim1-test"


def test_candidates_tried_in_confidence_order():
    """Multiple candidates → tried highest-confidence first."""
    deps = _build_deps()
    _seed_task("t2")
    candidates = [
        _Candidate(filename="low.flac", confidence=0.5),
        _Candidate(filename="high.flac", confidence=0.95),
        _Candidate(filename="mid.flac", confidence=0.7),
    ]
    track = _Track()

    dc.attempt_download_with_candidates("t2", candidates, track, batch_id=None, deps=deps)

    # First call should be the highest-confidence one
    assert deps.download_orchestrator.download_calls[0][1] == "high.flac"


# ---------------------------------------------------------------------------
# used_sources dedupe
# ---------------------------------------------------------------------------

def test_already_tried_source_skipped():
    """Source in used_sources is skipped (no duplicate download attempt)."""
    deps = _build_deps()
    _seed_task("t3", used_sources={"user1_already.flac"})
    candidates = [
        _Candidate(filename="already.flac", confidence=0.9),
        _Candidate(filename="fresh.flac", confidence=0.85),
    ]
    track = _Track()

    dc.attempt_download_with_candidates("t3", candidates, track, batch_id=None, deps=deps)

    # First candidate skipped (already used), second one tried
    assert len(deps.download_orchestrator.download_calls) == 1
    assert deps.download_orchestrator.download_calls[0][1] == "fresh.flac"


# ---------------------------------------------------------------------------
# Blacklist
# ---------------------------------------------------------------------------

def test_blacklisted_source_skipped():
    """Blacklisted candidate is skipped."""
    db = _FakeDB(blacklisted={("user1", "blacklisted.flac")})
    deps = _build_deps(db=db)
    _seed_task("t4")
    candidates = [
        _Candidate(filename="blacklisted.flac", confidence=0.95),
        _Candidate(filename="ok.flac", confidence=0.85),
    ]
    track = _Track()

    dc.attempt_download_with_candidates("t4", candidates, track, batch_id=None, deps=deps)

    assert deps.download_orchestrator.download_calls[0][1] == "ok.flac"


# ---------------------------------------------------------------------------
# Cancellation paths
# ---------------------------------------------------------------------------

def test_cancellation_before_attempt_returns_false():
    """status=cancelled at top of loop → return False, no download attempted."""
    deps = _build_deps()
    _seed_task("t5", status="cancelled")
    candidates = [_Candidate()]
    track = _Track()

    result = dc.attempt_download_with_candidates("t5", candidates, track, batch_id=None, deps=deps)

    assert result is False
    assert deps.download_orchestrator.download_calls == []


def test_task_deleted_returns_false():
    """Task removed from download_tasks mid-loop → return False."""
    deps = _build_deps()
    # Task NOT seeded — looks deleted
    candidates = [_Candidate()]
    track = _Track()

    result = dc.attempt_download_with_candidates("missing", candidates, track, batch_id=None, deps=deps)

    assert result is False


def test_active_download_id_skips_new_download():
    """If task already has download_id, candidate skipped (race protection)."""
    deps = _build_deps()
    _seed_task("t6", download_id="existing-dl")
    candidates = [_Candidate(), _Candidate(filename="other.flac")]
    track = _Track()

    dc.attempt_download_with_candidates("t6", candidates, track, batch_id=None, deps=deps)

    # Both candidates skipped (download_id already present)
    assert deps.download_orchestrator.download_calls == []


def test_cancellation_after_download_starts_calls_cancel_and_lifecycle(monkeypatch):
    """If task is cancelled after download_id assigned, cancel_download fires + on_complete(False)."""
    completion_calls = []
    acquisition_cancels = []
    from core.acquisition import pipeline_callback
    monkeypatch.setattr(
        pipeline_callback,
        "notify_correlated_grab_cancelled",
        lambda download_id: acquisition_cancels.append(download_id),
    )
    deps = _build_deps(on_complete=lambda batch_id, task_id, success=None: completion_calls.append((batch_id, task_id, success)))
    _seed_task("t7")
    candidates = [_Candidate()]
    track = _Track()

    # Simulate cancel happening between download_id assignment and final lock check.
    # update_task_status is the callback that runs RIGHT after the download starts.
    # We use it to flip status to cancelled.
    def cancel_mid_flight(task_id, status):
        if status == "downloading":
            with tasks_lock:
                if task_id in download_tasks:
                    pass  # status set legitimately
            # No-op here; we'll cancel via the lock directly below
            download_tasks[task_id]["status"] = "cancelled"

    deps.update_task_status = cancel_mid_flight

    result = dc.attempt_download_with_candidates("t7", candidates, track, batch_id="b7", deps=deps)

    assert result is False
    # cancel_download was called for the in-flight transfer
    assert deps.download_orchestrator.cancel_calls
    assert acquisition_cancels == ["dl-1"]
    # on_download_completed fired with success=False to free the worker slot
    assert completion_calls == [("b7", "t7", False)]


def test_cancel_after_download_frees_slot_outside_tasks_lock():
    """Regression: the mid-download-cancel completion callback MUST run outside
    tasks_lock. on_download_completed re-acquires it and tasks_lock is
    non-reentrant, so an in-lock call deadlocked the worker WHILE HOLDING the
    global lock, freezing all downloads. Prove the lock is free when the callback
    fires by having it acquire the lock (times out on the buggy in-lock version)."""
    acquired = []

    def _cb(batch_id, task_id, success=None):
        got = tasks_lock.acquire(timeout=2)
        acquired.append(got)
        if got:
            tasks_lock.release()

    deps = _build_deps(on_complete=_cb)
    _seed_task("t8")

    def cancel_mid_flight(task_id, status):
        if status == "downloading":
            download_tasks[task_id]["status"] = "cancelled"

    deps.update_task_status = cancel_mid_flight

    result = dc.attempt_download_with_candidates("t8", [_Candidate()], _Track(), batch_id="b8", deps=deps)
    assert result is False
    assert acquired == [True]   # lock was free when the callback ran


# ---------------------------------------------------------------------------
# Failure path — all candidates exhausted
# ---------------------------------------------------------------------------

def test_all_candidates_failed_returns_false():
    """If download_orchestrator.download returns None (failure) for all candidates, returns False."""
    soulseek = _FakeSoulseek(download_id=None)
    deps = _build_deps(soulseek=soulseek)
    _seed_task("t8")
    candidates = [_Candidate(filename="c1.flac"), _Candidate(filename="c2.flac")]
    track = _Track()

    result = dc.attempt_download_with_candidates("t8", candidates, track, batch_id=None, deps=deps)

    assert result is False
    # Both candidates were tried
    assert len(soulseek.download_calls) == 2


def test_exception_during_download_continues_to_next_candidate():
    """An exception on one candidate → continue to the next."""
    call_count = [0]

    class _FlakySoulseek(_FakeSoulseek):
        async def download(self, username, filename, size):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("network blip")
            return "dl-2"

    soulseek = _FlakySoulseek()
    deps = _build_deps(soulseek=soulseek)
    _seed_task("t9")
    candidates = [_Candidate(filename="c1.flac"), _Candidate(filename="c2.flac")]
    track = _Track()

    result = dc.attempt_download_with_candidates("t9", candidates, track, batch_id=None, deps=deps)

    assert result is True
    assert download_tasks["t9"]["download_id"] == "dl-2"


# ---------------------------------------------------------------------------
# Context payload
# ---------------------------------------------------------------------------

def test_explicit_album_context_uses_real_album_data():
    """track_info with _is_explicit_album_download=True copies real album/artist context."""
    deps = _build_deps()
    explicit_album = {
        "id": "alb-real",
        "name": "Real Album",
        "release_date": "2024-05-05",
        "total_tracks": 12,
        "total_discs": 2,
        "album_type": "album",
        "image_url": "http://img/a.jpg",
    }
    explicit_artist = {"id": "art-real", "name": "Real Artist"}
    _seed_task("t10", track_info={
        "_is_explicit_album_download": True,
        "_explicit_album_context": explicit_album,
        "_explicit_artist_context": explicit_artist,
        "track_number": 5,
        "disc_number": 2,
    })
    candidates = [_Candidate(filename="explicit.flac")]
    track = _Track(album="Real Album", artists=["Real Artist"])

    dc.attempt_download_with_candidates("t10", candidates, track, batch_id=None, deps=deps)

    ctx = matched_downloads_context["user1::explicit.flac"]
    assert ctx["spotify_album"]["id"] == "alb-real"
    assert ctx["spotify_album"]["total_discs"] == 2
    assert ctx["spotify_artist"]["id"] == "art-real"
    assert ctx["is_album_download"] is True


def test_track_number_from_track_info_preferred_over_api():
    """track_number from track_info wins over track object and API."""
    api_track = {"track_number": 99, "disc_number": 9, "album": {}}
    deps = _build_deps(spotify=_FakeSpotify(track_details=api_track))
    _seed_task("t11", track_info={"track_number": 5, "disc_number": 1})
    candidates = [_Candidate()]
    track = _Track()

    dc.attempt_download_with_candidates("t11", candidates, track, batch_id=None, deps=deps)

    enhanced = matched_downloads_context["user1::song.flac"]["original_search_result"]
    assert enhanced["track_number"] == 5
    assert enhanced["disc_number"] == 1


def test_api_backfills_album_context_when_missing():
    """When local album context is incomplete, Spotify API backfills release_date / album_type."""
    api_track = {
        "track_number": 7,
        "disc_number": 1,
        "album": {
            "id": "alb-from-api",
            "release_date": "2025-01-01",
            "album_type": "album",
            "total_tracks": 10,
            "images": [{"url": "http://api/img.jpg"}],
        },
    }
    deps = _build_deps(spotify=_FakeSpotify(track_details=api_track))
    # No track_info track_number → triggers API fallback
    _seed_task("t12")
    candidates = [_Candidate()]
    track = _Track(album="Album Name")  # truthy but no detailed metadata locally

    dc.attempt_download_with_candidates("t12", candidates, track, batch_id=None, deps=deps)

    ctx = matched_downloads_context["user1::song.flac"]
    # release_date defaults to '' in the fallback context, so backfill fires.
    assert ctx["spotify_album"]["release_date"] == "2025-01-01"
    # Note: id stays "from_sync_modal" because the fallback assigns a non-empty
    # placeholder, and the backfill only fires when `not spotify_album_context.get('id')`.
    # The current behavior is what production does — assertion documents that.
    assert ctx["spotify_album"]["id"] == "from_sync_modal"


# ---------------------------------------------------------------------------
# Sort by confidence is stable for equal scores
# ---------------------------------------------------------------------------

def test_candidates_with_equal_confidence_both_tried():
    """Equal-confidence candidates are tried in their existing order."""
    deps = _build_deps()
    _seed_task("t13")
    candidates = [
        _Candidate(filename="a.flac", confidence=0.9),
        _Candidate(filename="b.flac", confidence=0.9),
    ]
    track = _Track()

    dc.attempt_download_with_candidates("t13", candidates, track, batch_id=None, deps=deps)

    # First one wins — second never tried because download succeeded
    assert len(deps.download_orchestrator.download_calls) == 1
    assert deps.download_orchestrator.download_calls[0][1] == "a.flac"


def test_user_manual_pick_injects_acoustid_bypass_into_post_process_context():
    """Issue #701: when the user picks a specific candidate via the
    candidates modal, the download_selected_candidate endpoint sets
    `_user_manual_pick=True` on the task. The candidates helper must
    propagate that into the stored post-process context as
    `_skip_quarantine_check='acoustid'`; without it the manual pick
    loops straight back into quarantine whenever AcoustID disagrees
    with the user's selection."""
    deps = _build_deps()
    _seed_task("t_manual_pick")
    download_tasks["t_manual_pick"]["_user_manual_pick"] = True

    candidates = [_Candidate(filename="picked.flac", confidence=0.99)]
    track = _Track()

    result = dc.attempt_download_with_candidates("t_manual_pick", candidates, track, batch_id="b1", deps=deps)

    assert result is True
    ctx = matched_downloads_context["user1::picked.flac"]
    assert ctx["_skip_quarantine_check"] == "acoustid"
    assert ctx["_user_manual_pick"] is True


def test_auto_search_pick_does_not_inject_acoustid_bypass():
    """The bypass is ONLY for user-initiated manual picks. Auto-search
    candidate picks (which run during the normal download flow) must
    still get AcoustID verification — they're the canonical guard
    against the wrong-file leak that quarantine exists to catch."""
    deps = _build_deps()
    _seed_task("t_auto_pick")  # No _user_manual_pick flag set

    candidates = [_Candidate(filename="auto.flac", confidence=0.99)]
    track = _Track()

    result = dc.attempt_download_with_candidates("t_auto_pick", candidates, track, batch_id="b1", deps=deps)

    assert result is True
    ctx = matched_downloads_context["user1::auto.flac"]
    assert "_skip_quarantine_check" not in ctx
    assert "_user_manual_pick" not in ctx


def test_skip_acoustid_track_flag_injects_bypass():
    """Issue #797: when the album-download request had the per-request
    'Skip AcoustID verification' toggle on, the master worker stamps
    `_skip_acoustid=True` onto each track's track_info. The candidates
    helper must propagate that into the post-process context as
    `_skip_quarantine_check='acoustid'` so AcoustID never quarantines
    this request's files (e.g. correct downloads of non-English artists
    whose native-script metadata AcoustID can't reconcile)."""
    deps = _build_deps()
    _seed_task("t_skip_aid", track_info={"_skip_acoustid": True})

    candidates = [_Candidate(filename="skip.flac", confidence=0.99)]
    track = _Track()

    result = dc.attempt_download_with_candidates("t_skip_aid", candidates, track, batch_id="b1", deps=deps)

    assert result is True
    ctx = matched_downloads_context["user1::skip.flac"]
    assert ctx["_skip_quarantine_check"] == "acoustid"
    # It's the toggle path, NOT a manual pick.
    assert "_user_manual_pick" not in ctx


def test_no_skip_acoustid_flag_keeps_verification():
    """Without the toggle (no `_skip_acoustid` on track_info), AcoustID
    verification must still run — the bypass is opt-in per request."""
    deps = _build_deps()
    _seed_task("t_no_skip_aid", track_info={})  # no _skip_acoustid

    candidates = [_Candidate(filename="verify.flac", confidence=0.99)]
    track = _Track()

    result = dc.attempt_download_with_candidates("t_no_skip_aid", candidates, track, batch_id="b1", deps=deps)

    assert result is True
    ctx = matched_downloads_context["user1::verify.flac"]
    assert "_skip_quarantine_check" not in ctx


# ---------------------------------------------------------------------------
# Scheduled acquisition correlation (roadmap 3 slice 2): a wishlist-worker
# dispatch whose track_info rides lib2 mirror context correlates into the
# acquisition contract and stamps the grab marker into the post-process
# context. Strictly observational — a failing correlation must never touch
# the download itself.
# ---------------------------------------------------------------------------

_LIB2_SOURCE_INFO = {
    "source": "library_v2",
    "lib2_track_id": 42,
    "lib2_album_id": 7,
    "quality_profile_id": 3,
}


def _capture_scheduled_correlation(monkeypatch, markers=None, order=None):
    calls = []

    def _fake(**kwargs):
        if order is not None:
            order.append("prepare")
        calls.append(kwargs)
        return markers

    from core.acquisition import manual_grab
    monkeypatch.setattr(manual_grab, "try_prepare_scheduled_grab", _fake)
    return calls


def test_wishlist_lib2_dispatch_correlates_and_stamps_grab_marker(monkeypatch):
    order = []
    calls = _capture_scheduled_correlation(
        monkeypatch,
        markers={"download_id": "scheduled-x", "request_id": "arq1-x"},
        order=order,
    )

    class _OrderedSoulseek(_FakeSoulseek):
        async def download(self, username, filename, size):
            order.append("dispatch")
            return await super().download(username, filename, size)

    from core.acquisition import manual_grab
    monkeypatch.setattr(
        manual_grab,
        "bind_correlated_grab_transfer",
        lambda markers, transfer_id: order.append(
            ("bind", markers["download_id"], transfer_id)),
    )
    deps = _build_deps(soulseek=_OrderedSoulseek())
    _seed_task("t_wl", track_info={"source_info": dict(_LIB2_SOURCE_INFO)})

    result = dc.attempt_download_with_candidates(
        "t_wl", [_Candidate(filename="wl.flac")], _Track(), batch_id="b_wl", deps=deps)

    assert result is True
    assert len(calls) == 1
    assert calls[0]["lib2_context"] == {
        "track_id": 42, "album_id": 7, "quality_profile_id": 3}
    assert calls[0]["task_id"] == "t_wl"
    assert calls[0]["batch_id"] == "b_wl"
    assert calls[0]["source"] == "soulseek"
    assert calls[0]["search_result"]["filename"] == "wl.flac"
    assert order == [
        "prepare", "dispatch", ("bind", "scheduled-x", "dl-1")]
    ctx = matched_downloads_context["user1::wl.flac"]
    assert ctx["_acquisition_grab_download_id"] == "scheduled-x"


def test_wishlist_source_info_json_string_is_parsed(monkeypatch):
    import json
    calls = _capture_scheduled_correlation(
        monkeypatch, markers={"download_id": "scheduled-y", "request_id": "arq1-y"})
    deps = _build_deps()
    _seed_task("t_wl_json", track_info={
        "source_info": json.dumps(_LIB2_SOURCE_INFO)})

    dc.attempt_download_with_candidates(
        "t_wl_json", [_Candidate(filename="wlj.flac")], _Track(), deps=deps)

    assert len(calls) == 1
    assert calls[0]["lib2_context"]["track_id"] == 42


def test_native_acquisition_dispatch_is_not_double_correlated(monkeypatch):
    calls = _capture_scheduled_correlation(monkeypatch)
    deps = _build_deps()
    _seed_task("t_native", track_info={
        "source_info": dict(_LIB2_SOURCE_INFO),
        "_acquisition_import_id": "aim1-test",
    })

    dc.attempt_download_with_candidates(
        "t_native", [_Candidate(filename="native.flac")], _Track(), deps=deps)

    assert calls == []
    assert "_acquisition_grab_download_id" not in (
        matched_downloads_context["user1::native.flac"])


def test_user_manual_pick_is_not_scheduled_correlated(monkeypatch):
    calls = _capture_scheduled_correlation(monkeypatch)
    deps = _build_deps()
    _seed_task("t_pick", track_info={"source_info": dict(_LIB2_SOURCE_INFO)})
    download_tasks["t_pick"]["_user_manual_pick"] = True

    dc.attempt_download_with_candidates(
        "t_pick", [_Candidate(filename="pick.flac")], _Track(), deps=deps)

    assert calls == []


def test_wishlist_without_lib2_context_uses_legacy_shadow_correlation(monkeypatch):
    calls = _capture_scheduled_correlation(
        monkeypatch, markers={"download_id": "scheduled-shadow", "request_id": "arq1-z"})
    deps = _build_deps()
    _seed_task("t_pl", track_info={
        "id": "spotify-track-123",
        "name": "Song Title",
        "artists": [{"name": "Artist Name"}],
        "source_info": {"playlist_name": "My Playlist"},
    })

    dc.attempt_download_with_candidates(
        "t_pl", [_Candidate(filename="pl.flac")], _Track(), deps=deps)

    assert len(calls) == 1
    assert calls[0]["lib2_context"] is None
    assert calls[0]["target_context"]["id"] == "spotify-track-123"
    assert matched_downloads_context[
        "user1::pl.flac"]["_acquisition_grab_download_id"] == "scheduled-shadow"


def test_nonadmin_wishlist_dispatch_stays_outside_admin_acquisition(monkeypatch):
    calls = _capture_scheduled_correlation(monkeypatch)
    from core.acquisition import manual_grab
    monkeypatch.setattr(
        manual_grab, "correlation_enforcement_enabled", lambda: True)
    deps = _build_deps()
    _seed_task(
        "t_other_profile",
        profile_id=2,
        track_info={"id": "spotify-track-123", "name": "Song Title"},
    )

    result = dc.attempt_download_with_candidates(
        "t_other_profile", [_Candidate(filename="other.flac")], _Track(), deps=deps)

    assert result is True
    assert calls == []


def test_enforcement_blocks_scheduled_dispatch_without_preparation(monkeypatch):
    _capture_scheduled_correlation(monkeypatch, markers=None)
    from core.acquisition import manual_grab
    monkeypatch.setattr(
        manual_grab, "correlation_enforcement_enabled", lambda: True)
    outcomes = []
    from core.acquisition import correlation_coverage
    monkeypatch.setattr(
        correlation_coverage,
        "record_correlation_outcome_fail_open",
        lambda consumer, outcome: outcomes.append((consumer, outcome)),
    )
    soulseek = _FakeSoulseek()
    deps = _build_deps(soulseek=soulseek)
    _seed_task(
        "t_enforced",
        track_info={"id": "spotify-track-e", "name": "Song Title"},
    )

    result = dc.attempt_download_with_candidates(
        "t_enforced", [_Candidate(filename="enforced.flac")], _Track(), deps=deps)

    assert result is False
    assert soulseek.download_calls == []
    assert outcomes == [("scheduled", "blocked")]


def test_failed_correlation_never_blocks_the_download(monkeypatch):
    def _boom(**kwargs):
        raise RuntimeError("correlation exploded")

    from core.acquisition import manual_grab
    monkeypatch.setattr(manual_grab, "try_prepare_scheduled_grab", _boom)
    outcomes = []
    from core.acquisition import correlation_coverage
    monkeypatch.setattr(
        correlation_coverage,
        "record_correlation_outcome_fail_open",
        lambda consumer, outcome: outcomes.append((consumer, outcome)),
    )
    deps = _build_deps()
    _seed_task("t_boom", track_info={"source_info": dict(_LIB2_SOURCE_INFO)})

    result = dc.attempt_download_with_candidates(
        "t_boom", [_Candidate(filename="boom.flac")], _Track(), deps=deps)

    assert result is True
    ctx = matched_downloads_context["user1::boom.flac"]
    assert "_acquisition_grab_download_id" not in ctx
    assert outcomes == [("scheduled", "unprepared_dispatched")]


def test_rejected_scheduled_dispatch_closes_prepared_correlation(monkeypatch):
    markers = {"download_id": "scheduled-rejected", "request_id": "arq1-r"}
    _capture_scheduled_correlation(monkeypatch, markers=markers)
    failures = []
    from core.acquisition import manual_grab
    monkeypatch.setattr(
        manual_grab,
        "fail_prepared_correlated_grab",
        lambda prepared, error: failures.append((prepared, error)),
    )
    deps = _build_deps(soulseek=_FakeSoulseek(download_id=None))
    _seed_task(
        "t_rejected",
        track_info={"id": "spotify-track-r", "name": "Song Title"},
    )

    result = dc.attempt_download_with_candidates(
        "t_rejected", [_Candidate(filename="rejected.flac")], _Track(), deps=deps)

    assert result is False
    assert failures == [(markers, "legacy client rejected the dispatch")]


def test_equal_confidence_candidates_prefer_better_peer_quality():
    """Equal-confidence Soulseek candidates use peer quality as the tiebreaker."""
    deps = _build_deps()
    _seed_task("t14")
    candidates = [
        _Candidate(filename="slow.flac", confidence=0.9, quality_score=0.8,
                   upload_speed=100_000, queue_length=0, free_upload_slots=1),
        _Candidate(filename="fast.flac", confidence=0.9, quality_score=1.0,
                   upload_speed=5_000_000, queue_length=0, free_upload_slots=1),
    ]
    track = _Track()

    dc.attempt_download_with_candidates("t14", candidates, track, batch_id=None, deps=deps)

    assert deps.download_orchestrator.download_calls[0][1] == "fast.flac"
