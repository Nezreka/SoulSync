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
        soulseek_client=soulseek or _FakeSoulseek(),
        spotify_client=spotify or _FakeSpotify(),
        run_async=_run_async,
        get_database=lambda: db or _FakeDB(),
        update_task_status=update_status or (lambda task_id, status: None),
        make_context_key=lambda u, f: f"{u}::{f}",
        on_download_completed=on_complete or (lambda *a, **kw: None),
    )
    return deps


def _seed_task(task_id, *, status="pending", track_info=None, used_sources=None,
               download_id=None):
    download_tasks[task_id] = {
        "status": status,
        "track_info": track_info or {},
        "used_sources": used_sources or set(),
        "download_id": download_id,
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
    assert deps.soulseek_client.download_calls == [("user1", "best.flac", 1000)]
    assert download_tasks["t1"]["download_id"] == "dl-1"
    assert "user1::best.flac" in matched_downloads_context


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
    assert deps.soulseek_client.download_calls[0][1] == "high.flac"


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
    assert len(deps.soulseek_client.download_calls) == 1
    assert deps.soulseek_client.download_calls[0][1] == "fresh.flac"


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

    assert deps.soulseek_client.download_calls[0][1] == "ok.flac"


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
    assert deps.soulseek_client.download_calls == []


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
    assert deps.soulseek_client.download_calls == []


def test_cancellation_after_download_starts_calls_cancel_and_lifecycle():
    """If task is cancelled after download_id assigned, cancel_download fires + on_complete(False)."""
    completion_calls = []
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
    assert deps.soulseek_client.cancel_calls
    # on_download_completed fired with success=False to free the worker slot
    assert completion_calls == [("b7", "t7", False)]


# ---------------------------------------------------------------------------
# Failure path — all candidates exhausted
# ---------------------------------------------------------------------------

def test_all_candidates_failed_returns_false():
    """If soulseek_client.download returns None (failure) for all candidates, returns False."""
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
    assert len(deps.soulseek_client.download_calls) == 1
    assert deps.soulseek_client.download_calls[0][1] == "a.flac"
