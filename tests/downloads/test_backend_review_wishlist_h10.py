"""H10: a track wishlisted by profile 2 must download into profile 2's library.

Current bug: the auto flow concatenates wishlist tracks across ALL profiles,
``sanitize_and_dedupe_wishlist_tracks`` dedupes by track id keeping the first
profile's copy, and the batch is stamped ``profile_id=1`` (the runtime's
hardcoded profile). The master worker then scopes the whole run — analysis,
destination root — to profile 1, so profile 2's row survives every cycle
(the scoped success DELETE only clears owners of the published path) and the
track re-downloads into profile 1's root forever.

Fix: formatted tracks carry their owner ``profile_id``; dedupe keeps one
entry per (track, owner); ``_run_wishlist_cycle`` builds one batch per owner
profile; success/failure updates then scope to the actual owner via the
batch's profile_id.
"""
import threading
from types import SimpleNamespace

from core.wishlist.processing import _run_wishlist_cycle
from core.wishlist.selection import sanitize_and_dedupe_wishlist_tracks
from core.wishlist.service import WishlistService


def _fmt(track_id, profile_id):
    return {
        "track_id": track_id,
        "spotify_track_id": track_id,
        "track_name": "Lonely Track",
        "name": "Lonely Track",
        "artists": [{"name": "Some Artist"}],
        "profile_id": profile_id,
    }


def _make_runtime():
    submitted = []

    class _Exec:
        def submit(self, fn, *a, **k):
            submitted.append((fn, a, k))

    return SimpleNamespace(
        processing_guard=None,
        is_actually_processing=lambda: False,
        app_context_factory=None,
        get_profiles_database=lambda: None,
        get_music_database=lambda: None,
        download_batches={},
        tasks_lock=threading.Lock(),
        update_automation_progress=lambda *a, **k: None,
        automation_engine=None,
        missing_download_executor=_Exec(),
        run_full_missing_tracks_process=lambda *a, **k: None,
        get_batch_max_concurrent=lambda: 3,
        get_active_server=lambda: "test",
        current_time_fn=__import__("time").time,
        profile_id=1,  # the auto runtime hardcodes 1 — the bug's stamp
        logger=SimpleNamespace(
            info=lambda *a, **k: None,
            warning=lambda *a, **k: None,
            error=lambda *a, **k: None,
            debug=lambda *a, **k: None,
        ),
        album_bundle_executor=None,
        _submitted=submitted,
    )


def test_h10_formatted_tracks_carry_owner_profile_id():
    """get_wishlist_tracks_for_download stamps the profile it queried."""
    service = WishlistService.__new__(WishlistService)
    service._database = SimpleNamespace(
        get_wishlist_tracks=lambda **k: [{
            "id": 7,
            "spotify_track_id": "sp-X",
            "spotify_data": {"name": "Lonely Track",
                             "artists": [{"name": "Some Artist"}],
                             "album": {"name": "Some Album"}},
            "failure_reason": "",
            "retry_count": 0,
            "last_attempted": None,
            "date_added": "2026-01-01",
            "source_type": "manual",
            "source_info": {},
            "quality_profile_id": None,
        }],
    )
    tracks = service.get_wishlist_tracks_for_download(profile_id=2)
    assert len(tracks) == 1
    assert tracks[0]["profile_id"] == 2, "owner profile_id lost on formatting"


def test_h10_dedupe_keeps_one_entry_per_owner():
    raw = [_fmt("sp-X", 1), _fmt("sp-X", 2)]
    tracks, dupes = sanitize_and_dedupe_wishlist_tracks(raw)
    assert len(tracks) == 2, "same track wanted by 2 profiles must survive as 2 owned entries"
    assert {t["profile_id"] for t in tracks} == {1, 2}
    assert dupes == 0


def test_h10_same_profile_dupes_still_deduped():
    raw = [_fmt("sp-X", 2), _fmt("sp-X", 2)]
    tracks, dupes = sanitize_and_dedupe_wishlist_tracks(raw)
    assert len(tracks) == 1
    assert dupes == 1


def test_h10_cycle_batches_profile2_track_under_profile2():
    """The repro's exact shape: only profile 2 wants the track."""
    runtime = _make_runtime()
    result = _run_wishlist_cycle(
        runtime, playlist_id="wishlist", cycle="singles",
        tracks=[_fmt("sp-X", 2)], run_id="run-1", auto_initiated=True,
    )
    batches = list(runtime.download_batches.values())
    assert len(batches) == 1
    assert batches[0]["profile_id"] == 2, (
        f"profile 2's track batched under profile {batches[0]['profile_id']} — "
        "worker would download into the wrong library"
    )
    assert result["submitted"] == list(runtime.download_batches.keys())


def test_h10_cycle_groups_mixed_owners_into_separate_batches():
    runtime = _make_runtime()
    tracks = [_fmt("sp-A", 1), _fmt("sp-X", 2), _fmt("sp-B", 2)]
    _run_wishlist_cycle(
        runtime, playlist_id="wishlist", cycle="singles",
        tracks=tracks, run_id="run-1", auto_initiated=True,
    )
    by_owner = {}
    for batch in runtime.download_batches.values():
        by_owner.setdefault(batch["profile_id"], 0)
        by_owner[batch["profile_id"]] += batch["analysis_total"]
    assert by_owner == {1: 1, 2: 2}, f"tracks not grouped per owner: {by_owner}"


def test_h10_cycle_falls_back_to_runtime_profile_without_owner():
    """Tracks with no owner tag keep the old behavior (runtime profile)."""
    runtime = _make_runtime()
    track = _fmt("sp-X", 2)
    del track["profile_id"]
    _run_wishlist_cycle(
        runtime, playlist_id="wishlist", cycle="singles",
        tracks=[track], run_id="run-1", auto_initiated=True,
    )
    batches = list(runtime.download_batches.values())
    assert len(batches) == 1
    assert batches[0]["profile_id"] == 1
