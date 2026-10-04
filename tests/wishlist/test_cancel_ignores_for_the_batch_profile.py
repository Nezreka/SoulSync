"""A cancelled download is ignored on the wishlist it was for (#874, #1199).

The cancel handler used the profile of whoever pressed cancel. An admin
stopping another profile's download wrote the ignore entry and the removal
into the admin's wishlist, so the other profile's row stayed and its
auto-processor fetched the track again.
"""

from __future__ import annotations

import pytest

import web_server


class _Database:
    def __init__(self, calls):
        self.calls = calls

    def add_to_wishlist_ignore(self, track_id, **kwargs):
        self.calls.append(("ignore", track_id, kwargs["profile_id"]))


class _Wishlist:
    def __init__(self):
        self.calls = []
        self.database = _Database(self.calls)

    def remove_track_from_wishlist(self, track_id, profile_id=None):
        self.calls.append(("remove", track_id, profile_id))


@pytest.fixture
def wishlist(monkeypatch):
    service = _Wishlist()
    monkeypatch.setattr("core.wishlist_service.get_wishlist_service", lambda: service)
    monkeypatch.setattr(web_server, "get_current_profile_id", lambda: 1)
    return service


def test_the_batch_profile_owns_the_ignore(wishlist, monkeypatch):
    monkeypatch.setitem(web_server.download_batches, "b-kim", {"profile_id": 3})
    web_server._add_cancelled_task_to_wishlist(
        {"batch_id": "b-kim", "track_info": {"id": "t1", "name": "Song"}})
    assert wishlist.calls == [("ignore", "t1", 3), ("remove", "t1", 3)]


def test_without_a_batch_the_canceller_owns_it(wishlist):
    web_server._add_cancelled_task_to_wishlist({"track_info": {"id": "t2", "name": "Song"}})
    assert wishlist.calls == [("ignore", "t2", 1), ("remove", "t2", 1)]
