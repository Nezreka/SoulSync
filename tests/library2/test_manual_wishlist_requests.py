"""An admin working in the library of a profile that asks first (E-12) and
clicking "download" on its wishlist gets the approved requests only: the rest
waits on the Requests page, same rule as the scheduled run (upstream #requests).
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from core.wishlist import processing as P


class _Service:
    def __init__(self, total=0):
        self.calls = []
        self.total = total

    def get_wishlist_count(self, profile_id=1, approved_only=False):
        return self.total

    def get_wishlist_tracks_for_download(self, profile_id=1, approved_only=False, limit=None):
        self.calls.append((profile_id, approved_only))
        return []


class _DB:
    def __init__(self, profiles):
        self.profiles = profiles

    def remove_wishlist_duplicates(self, profile_id=1):
        return 0

    def get_profile(self, pid):
        return self.profiles.get(pid)


def _run(monkeypatch, profile_id, profiles, total=0, batches=None):
    service = _Service(total)
    monkeypatch.setattr(P, "get_wishlist_service", lambda: service)
    batches = batches if batches is not None else {"b1": {"phase": "analysis"}}
    runtime = SimpleNamespace(
        get_music_database=lambda: _DB(profiles), download_batches=batches,
        tasks_lock=threading.Lock(), profile_id=profile_id, logger=P.logger,
        missing_download_executor=None, run_full_missing_tracks_process=None,
        get_batch_max_concurrent=lambda: 1, add_activity_item=lambda *a: None,
        active_server="plex", album_bundle_executor=None)
    P._prepare_and_run_manual_wishlist_batch(runtime, "b1", None, None)
    return service.calls


@pytest.mark.parametrize("profile_id,profiles,approved_only", [
    (5, {5: {"id": 5, "can_download": 0}}, True),     # asks first
    (6, {6: {"id": 6, "can_download": 1}}, False),    # downloads
    (1, {}, False),                                   # the admin's own list
])
def test_manual_run_reads_approved_rows_for_a_profile_that_asks_first(
        monkeypatch, profile_id, profiles, approved_only):
    assert _run(monkeypatch, profile_id, profiles) == [(profile_id, approved_only)]


def test_an_admin_sees_why_nothing_started_for_a_requester(monkeypatch):
    """the run said "No tracks in wishlist" over a list of requests nobody had
    approved yet. it says they wait on the Requests page now."""
    batches = {"b1": {"phase": "analysis"}}
    _run(monkeypatch, 5, {5: {"id": 5, "can_download": 0}}, total=3, batches=batches)
    assert batches["b1"]["error"] == "3 requests waiting for approval on the Requests page"


def test_an_empty_wishlist_still_says_empty(monkeypatch):
    batches = {"b1": {"phase": "analysis"}}
    _run(monkeypatch, 6, {6: {"id": 6, "can_download": 1}}, total=0, batches=batches)
    assert batches["b1"]["error"] == "No tracks in wishlist"
