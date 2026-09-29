"""S13: a manual "Process Wishlist Now" run must clear retry backoff only for
the tracks the run is about to attempt, scoped to each track's owning
profile.

The pre-fix code called ``reset_wishlist_retry_backoff(ids)`` with
``profile_id=None`` on the whole pre-cycle wishlist: a user click cleared
the retry clock for every profile's rows, including tracks in categories
this cycle never touches.
"""
import contextlib
import logging
import threading
from types import SimpleNamespace

import core.wishlist.processing as wl_processing

logger = logging.getLogger("tests")


class _FakeProfilesDB:
    def get_all_profiles(self):
        return [{"id": 1}, {"id": 2}]

    def remove_wishlist_duplicates(self, profile_id=None):
        return 0


class _FakeMusicDB:
    def __init__(self):
        self.backoff_clears = []  # (tuple(ids), profile_id)

    def remove_wishlist_duplicates(self, profile_id=None):
        return 0

    def reset_wishlist_retry_backoff(self, spotify_track_ids=None, profile_id=None):
        ids = [str(t) for t in (spotify_track_ids or []) if t]
        self.backoff_clears.append((tuple(ids), profile_id))
        return len(ids)


def _track(tid, profile_id, category):
    return {
        "spotify_track_id": tid,
        "track_id": tid,
        "id": tid,
        "name": f"Song {tid}",
        "profile_id": profile_id,
        "test_category": category,
    }


def _make_service():
    svc = SimpleNamespace()
    svc.get_wishlist_count = lambda profile_id=None, approved_only=False: 1

    def _tracks(profile_id=None, approved_only=False):
        if profile_id == 1:
            return [_track("X", 1, "singles"), _track("Y", 1, "albums")]
        return [_track("X", 2, "singles")]

    svc.get_wishlist_tracks_for_download = _tracks
    return svc


def _runtime(music_db):
    return SimpleNamespace(
        logger=logger,
        is_actually_processing=lambda: False,
        processing_guard=lambda: contextlib.contextmanager(lambda: (yield True))(),
        app_context_factory=lambda: contextlib.contextmanager(lambda: (yield None))(),
        get_profiles_database=lambda: _FakeProfilesDB(),
        get_music_database=lambda: music_db,
        download_batches={},
        tasks_lock=threading.Lock(),
        update_automation_progress=lambda *a, **k: None,
        automation_engine=None,
        missing_download_executor=None,
        run_full_missing_tracks_process=lambda *a, **k: None,
        get_batch_max_concurrent=lambda: 3,
        get_active_server=lambda: "soulseek",
        current_time_fn=lambda: 0.0,
        profile_id=1,
        album_bundle_executor=None,
    )


def _patch_common(monkeypatch, music_db):
    monkeypatch.setattr(wl_processing, "get_wishlist_service", _make_service)
    monkeypatch.setattr(wl_processing, "remove_tracks_already_in_library",
                        lambda *a, **k: 0)
    monkeypatch.setattr(wl_processing, "get_wishlist_cycle", lambda db_fn: "singles")
    monkeypatch.setattr(wl_processing, "set_wishlist_cycle", lambda db_fn, c: None)

    def _fake_filter(tracks, category, **kwargs):
        matched = [t for t in tracks if t.get("test_category") == category]
        return matched, len(matched)

    monkeypatch.setattr(wl_processing, "filter_wishlist_tracks_by_category", _fake_filter)
    monkeypatch.setattr("core.metadata.release_dates.split_released_unreleased",
                        lambda tracks: (tracks, []))
    monkeypatch.setattr(wl_processing, "_run_wishlist_cycle",
                        lambda *a, **k: {"album_batches": 0, "residual_count": 0})
    # sanitize is real; make sure it keeps our marker + owner keys
    return music_db


def test_s13_manual_run_clears_only_cycle_tracks_per_owner(monkeypatch):
    """The 'singles' cycle attempts X (profiles 1 and 2). Y sits in the
    'albums' category and is not attempted — its backoff must survive, for
    every profile."""
    music_db = _FakeMusicDB()
    _patch_common(monkeypatch, music_db)

    wl_processing.process_wishlist_automatically(_runtime(music_db))

    assert music_db.backoff_clears, "manual run never cleared retry backoff (S13)"
    cleared_ids = set()
    for ids, pid in music_db.backoff_clears:
        assert pid is not None, (
            f"backoff clear must be owner-scoped, got profile_id=None for {ids} (S13)")
        cleared_ids.update((i, pid) for i in ids)
    assert ("X", 1) in cleared_ids, "cycle track X (owner 1) must be cleared"
    assert ("X", 2) in cleared_ids, "cycle track X (owner 2) must be cleared"
    assert not any(i == "Y" for i, _ in cleared_ids), (
        f"non-cycle track Y must keep its backoff, cleared: {cleared_ids}")
