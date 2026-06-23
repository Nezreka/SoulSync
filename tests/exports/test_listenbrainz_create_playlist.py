"""ListenBrainz create_playlist client method (#903).

Pins the create -> batched item/add flow against a mocked network: large playlists are
added in <=100 batches (LB's MAX_RECORDINGS_PER_ADD), the new playlist MBID/URL are
returned, and failures are reported (never raised) so the export job can surface them.
"""

from __future__ import annotations

from core.listenbrainz_client import ListenBrainzClient


class _Resp:
    def __init__(self, status, body=None):
        self.status_code = status
        self._body = body or {}

    def json(self):
        return self._body


def _client():
    # token='' (not None) skips the network token-validation in __init__; then fake auth.
    c = ListenBrainzClient(token="")
    c.token = "tok"
    c.username = "user"
    return c


def _tracks(n):
    return [{"identifier": f"https://musicbrainz.org/recording/{i:08d}-0000-0000-0000-000000000000"}
            for i in range(n)]


def test_create_then_batched_add(monkeypatch):
    c = _client()
    calls = []

    def fake_req(method, url, **kw):
        calls.append(url)
        if url.endswith("/playlist/create"):
            return _Resp(200, {"status": "ok", "playlist_mbid": "PL-MBID"})
        return _Resp(200, {"status": "ok"})

    monkeypatch.setattr(c, "_make_request_with_retry", fake_req)
    res = c.create_playlist("My Playlist", _tracks(250))

    assert res["success"] is True
    assert res["playlist_mbid"] == "PL-MBID"
    assert res["playlist_url"] == "https://listenbrainz.org/playlist/PL-MBID"
    assert res["added"] == 250
    # 1 create + 3 add batches (100 + 100 + 50)
    assert sum(1 for u in calls if u.endswith("/playlist/create")) == 1
    assert sum(1 for u in calls if "/item/add" in u) == 3


def test_not_authenticated_is_reported_not_raised():
    c = ListenBrainzClient(token="")  # no username -> not authenticated
    res = c.create_playlist("x", _tracks(1))
    assert res["success"] is False
    assert "authenticated" in (res["error"] or "")


def test_create_failure_reported(monkeypatch):
    c = _client()
    monkeypatch.setattr(c, "_make_request_with_retry", lambda *a, **k: _Resp(400, {}))
    res = c.create_playlist("x", _tracks(5))
    assert res["success"] is False
    assert res["playlist_mbid"] is None
    assert "400" in (res["error"] or "")


def test_create_ok_but_partial_add_failure(monkeypatch):
    c = _client()

    def fake_req(method, url, **kw):
        if url.endswith("/playlist/create"):
            return _Resp(200, {"playlist_mbid": "PL"})
        return _Resp(500, {})  # every add fails

    monkeypatch.setattr(c, "_make_request_with_retry", fake_req)
    res = c.create_playlist("x", _tracks(10))
    # Playlist WAS created -> success True, but added=0 (honest partial report)
    assert res["success"] is True
    assert res["playlist_mbid"] == "PL"
    assert res["added"] == 0
