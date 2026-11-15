"""#2127: Search > Releases only searched two trackers.

the torrent half of the tab asked two named public trackers (the pirate bay
and 1337x) and nothing else in prowlarr. it now asks every enabled prowlarr
torrent indexer (no indexer filter) and, being a release search, in every
category: the movie categories alone dropped a tv release.
"""

from __future__ import annotations

import pytest

import core.video.prowlarr_search as ps
from api.video.downloads import _torrent_lane_hits


class _Client:
    def __init__(self):
        self.calls = []

    def is_configured(self):
        return True

    def _search_sync(self, q, cats, ids, limit, search_type="search", extra_params=None,
                     max_wait_seconds=None):
        self.calls.append({"cats": cats, "ids": ids})
        return []


@pytest.fixture()
def client(monkeypatch):
    c = _Client()
    monkeypatch.setattr(ps, "_client", lambda: c)
    monkeypatch.setattr(ps, "_indexer_ids", lambda: [])
    import core.video.extto_search as ex
    monkeypatch.setattr(ex, "extto_search", lambda q, **k: {"configured": True, "hits": []})
    return c


def test_releases_tab_asks_every_indexer_in_every_category(client):
    _torrent_lane_hits({"title": "Primal", "basic": True, "indexer": None},
                       "movie", "Primal", None, None, "torrent")
    assert client.calls and all(c["cats"] == [] and c["ids"] == [] for c in client.calls)


def test_a_title_search_keeps_its_categories(client):
    _torrent_lane_hits({"title": "Primal"}, "movie", "Primal", None, None, "torrent")
    assert client.calls and all(2000 in c["cats"] for c in client.calls)
