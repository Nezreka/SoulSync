"""M11: every failed track's attempt must be recorded, not just the first 50.

``_record_failed_attempt`` (retry_count + 1, the retry/backoff signal) used
to live inside the 50-capped wishlist-add loop, so a batch with 60 failed
tracks left 10 with retry_count stuck — they never escalated backoff and
burned a full search every cycle forever. The 50-entry add cap may stay (the
add is the expensive part), but the skipped names must be logged at warning.
"""
import logging

import core.downloads.wishlist_failed as wf_mod


def _failed_track(i):
    return {
        "track_name": f"Track {i}",
        "track_data": {"id": f"sp-{i}", "name": f"Track {i}",
                       "artists": [{"name": "Some Artist"}]},
        "spotify_track": {},
        "failure_reason": "no candidates",
    }


class _FakeWishlistService:
    def __init__(self):
        self.stamps = []  # (spotify_track_id, success, profile_id)
        self.adds = []

    def mark_track_download_result(self, spotify_track_id, success,
                                   error_message=None, profile_id=1, **kwargs):
        self.stamps.append((spotify_track_id, success, profile_id))
        return True

    def add_failed_track_from_modal(self, track_info=None, source_type=None,
                                    source_context=None, profile_id=1):
        self.adds.append(track_info)
        return True


def _run_step2(n_tracks, profile_id=2):
    service = _FakeWishlistService()
    batch = {"profile_id": profile_id, "playlist_id": "wishlist",
             "playlist_name": "Wishlist"}
    tracks = [_failed_track(i) for i in range(n_tracks)]
    summary = wf_mod.add_failed_tracks_to_wishlist(batch, tracks, service)
    return service, summary


def test_m11_every_failed_track_gets_attempt_stamp():
    service, summary = _run_step2(55)
    stamped = [s for s in service.stamps if s[1] is False]
    assert len(stamped) == 55, (
        f"only {len(stamped)}/55 failed tracks got their attempt stamped")
    assert {s[0] for s in stamped} == {f"sp-{i}" for i in range(55)}
    # the stamp lands on the OWNER's row, not the default profile
    assert {s[2] for s in stamped} == {2}
    assert summary["total_failed"] == 55


def test_m11_wishlist_add_cap_stays_at_50():
    service, summary = _run_step2(55)
    assert len(service.adds) == 50
    assert summary["tracks_added"] == 50
    assert summary["wishlist_capped_skips"] == 5


def test_m11_skipped_names_logged_at_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="downloads.wishlist_failed"):
        _run_step2(53)
    warnings = [r.getMessage() for r in caplog.records
                if r.levelno >= logging.WARNING]
    assert any("Track 50" in m and "Track 52" in m for m in warnings), (
        f"skipped names not logged at warning: {warnings}")


def test_m11_small_batch_stamps_and_adds_everything(caplog):
    service, summary = _run_step2(3)
    assert len([s for s in service.stamps if s[1] is False]) == 3
    assert len(service.adds) == 3
    assert summary["wishlist_capped_skips"] == 0
    warnings = [r.getMessage() for r in caplog.records
                if r.levelno >= logging.WARNING and "cap" in r.getMessage()]
    assert not warnings, f"unexpected cap warning for a small batch: {warnings}"
