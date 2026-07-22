"""Tidal V2 playlist listing must walk EVERY page (#1002 follow-up).

The V2 /playlists endpoint returns ~20 rows per page plus a links.next
cursor. The old code read exactly one page, silently capping users at ~20
playlists (i-byrana: 21 shown, 20+ missing — deleting playlists in Tidal
just rotated different ones into the single page read).
"""

from __future__ import annotations

import core.tidal_client as tc
from core.tidal_client import TidalClient


def _page(ids, next_link=None):
    return {
        "data": [{"id": i, "attributes": {"name": f"pl-{i}", "numberOfTracks": 3},
                  "relationships": {}} for i in ids],
        "links": ({"next": next_link} if next_link else {}),
    }


class _Resp:
    def __init__(self, payload):
        self.status_code = 200
        self._payload = payload
        self.text = ""

    def json(self):
        return self._payload


class _Headers(dict):
    pass


def _client(monkeypatch, pages):
    """TidalClient with auth stubbed and requests.get serving ``pages`` in order."""
    c = TidalClient.__new__(TidalClient)
    c.base_url = "https://openapi.tidal.com/v2"

    class _Session:
        headers = _Headers()

    c.session = _Session()
    monkeypatch.setattr(c, "_ensure_valid_token", lambda: True)
    monkeypatch.setattr(c, "_get_user_id", lambda: ("u1", None))

    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append((url, params))
        page = pages[min(len(calls) - 1, len(pages) - 1)]
        return page if isinstance(page, _Resp) else _Resp(page)

    monkeypatch.setattr(tc.requests, "get", fake_get)
    return c, calls


def test_single_page_needs_one_request(monkeypatch):
    c, calls = _client(monkeypatch, [_page(["a", "b"])])
    got = c.get_user_playlists_metadata_only()
    assert [p.id for p in got] == ["a", "b"]
    assert len(calls) == 1


def test_follows_next_cursor_across_pages(monkeypatch):
    c, calls = _client(monkeypatch, [
        _page(["a", "b"], next_link="/playlists?page%5Bcursor%5D=x2"),
        _page(["c"], next_link="/v2/playlists?page%5Bcursor%5D=x3"),
        _page(["d"]),
    ])
    got = c.get_user_playlists_metadata_only()
    assert [p.id for p in got] == ["a", "b", "c", "d"]
    assert len(calls) == 3
    # relative next links normalize against the API base, both flavors
    assert calls[1][0] == "https://openapi.tidal.com/v2/playlists?page%5Bcursor%5D=x2"
    assert calls[2][0] == "https://openapi.tidal.com/v2/playlists?page%5Bcursor%5D=x3"
    # the cursor URL carries all params — none re-sent
    assert calls[1][1] is None and calls[2][1] is None


def test_mid_walk_error_keeps_earlier_pages(monkeypatch):
    err = _Resp({})
    err.status_code = 500
    c, calls = _client(monkeypatch, [
        _page(["a"], next_link="/playlists?page%5Bcursor%5D=x2"),
        err,
    ])
    got = c.get_user_playlists_metadata_only()
    assert [p.id for p in got] == ["a"]        # first page survives the failure
