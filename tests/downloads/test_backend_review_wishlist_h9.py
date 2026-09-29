"""H9: the wishlist AUTO cycle must drop already-owned rows BEFORE the batch
is built (force_download_all skips the per-track ownership check, so without
a cycle-start cleanup owned tracks re-download every unattended cycle).

The MANUAL flow deliberately keeps its force semantics (no sweep): the user
explicitly asked for those tracks — see
test_start_manual_wishlist_download_batch_does_not_run_library_cleanup."""
import contextlib
import logging
import threading
from types import SimpleNamespace

import core.wishlist.processing as wl_processing

logger = logging.getLogger("tests")


class _FakeProfilesDB:
    def get_all_profiles(self):
        return [{"id": 1}]

    def remove_wishlist_duplicates(self, profile_id=None):
        return 0


class _FakeMusicDB:
    def remove_wishlist_duplicates(self, profile_id=None):
        return 0


def _make_fake_wishlist_service(order):
    svc = SimpleNamespace()
    svc.get_wishlist_count = lambda profile_id=None, approved_only=False: 1
    def _tracks(profile_id=None, approved_only=False):
        order.append("fetch")
        return []
    svc.get_wishlist_tracks_for_download = _tracks
    return svc


def _auto_runtime(order):
    return SimpleNamespace(
        logger=logger,
        is_actually_processing=lambda: False,
        processing_guard=lambda: contextlib.contextmanager(lambda: (yield True))(),
        app_context_factory=lambda: contextlib.contextmanager(lambda: (yield None))(),
        get_profiles_database=lambda: _FakeProfilesDB(),
        get_music_database=lambda: _FakeMusicDB(),
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


def test_h9_auto_cycle_runs_owned_track_cleanup_first(monkeypatch):
    """The auto wishlist cycle must call remove_tracks_already_in_library
    BEFORE fetching tracks for the batch — owned rows must never enter a
    force_download_all batch."""
    order = []
    monkeypatch.setattr(
        wl_processing, "get_wishlist_service", lambda: _make_fake_wishlist_service(order)
    )
    cleanup_calls = []
    def _spy(wishlist_service, profiles_database, music_database, active_server, **kwargs):
        cleanup_calls.append((profiles_database, active_server))
        order.append("cleanup")
        return 0
    monkeypatch.setattr(wl_processing, "remove_tracks_already_in_library", _spy)

    wl_processing.process_wishlist_automatically(_auto_runtime(order))

    assert cleanup_calls, "auto cycle never ran remove_tracks_already_in_library (H9)"
    assert order.index("cleanup") < order.index("fetch"), (
        f"cleanup must run before the track fetch, got order {order}"
    )
