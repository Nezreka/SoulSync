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


# ── update-in-place + create-or-update (#903 no-duplicate re-export) ──────────

def _route(existing_count=None):
    """Build a fake _make_request_with_retry; records calls. existing_count=None -> GET 404."""
    calls = []
    def fake(method, url, **kw):
        calls.append((method, url, kw.get("json")))
        if method == "GET" and "/playlist/" in url:
            if existing_count is None:
                return _Resp(404, {})
            return _Resp(200, {"playlist": {"track": [{} for _ in range(existing_count)]}})
        if url.endswith("/playlist/create"):
            return _Resp(200, {"playlist_mbid": "NEW-MBID"})
        return _Resp(200, {})  # item/add, item/delete, edit, delete
    return fake, calls


def test_update_playlist_clears_then_re_adds_same_mbid(monkeypatch):
    c = _client()
    fake, calls = _route(existing_count=3)
    monkeypatch.setattr(c, "_make_request_with_retry", fake)
    res = c.update_playlist("EXIST-MBID", "New Title", _tracks(5))
    assert res["success"] is True and res["updated"] is True
    assert res["playlist_mbid"] == "EXIST-MBID"          # stable URL/MBID
    assert res["added"] == 5
    urls = [u for _, u, _ in calls]
    assert any("/item/delete" in u for u in urls)        # cleared existing
    assert any("/item/add" in u for u in urls)           # re-added
    assert any("/playlist/edit/EXIST-MBID" in u for u in urls)  # title refreshed


def test_update_playlist_reports_gone(monkeypatch):
    c = _client()
    fake, _ = _route(existing_count=None)                # GET 404 -> gone
    monkeypatch.setattr(c, "_make_request_with_retry", fake)
    res = c.update_playlist("DEAD-MBID", "T", _tracks(2))
    assert res["success"] is False
    assert res["gone"] is True


def test_create_or_update_updates_when_existing(monkeypatch):
    c = _client()
    fake, _ = _route(existing_count=2)
    monkeypatch.setattr(c, "_make_request_with_retry", fake)
    res = c.create_or_update_playlist("T", _tracks(4), existing_mbid="EXIST")
    assert res["updated"] is True and res["playlist_mbid"] == "EXIST"


def test_create_or_update_falls_back_to_create_when_gone(monkeypatch):
    c = _client()
    fake, _ = _route(existing_count=None)                # remembered playlist deleted on LB
    monkeypatch.setattr(c, "_make_request_with_retry", fake)
    res = c.create_or_update_playlist("T", _tracks(4), existing_mbid="DEAD")
    assert res["success"] is True
    assert res["updated"] is False                       # fell back to create
    assert res["playlist_mbid"] == "NEW-MBID"


def test_create_or_update_creates_when_no_existing(monkeypatch):
    c = _client()
    fake, _ = _route()
    monkeypatch.setattr(c, "_make_request_with_retry", fake)
    res = c.create_or_update_playlist("T", _tracks(3), existing_mbid=None)
    assert res["playlist_mbid"] == "NEW-MBID" and res["updated"] is False


def test_delete_playlist(monkeypatch):
    c = _client()
    monkeypatch.setattr(c, "_make_request_with_retry", lambda *a, **k: _Resp(200, {}))
    assert c.delete_playlist("MBID") is True
    monkeypatch.setattr(c, "_make_request_with_retry", lambda *a, **k: _Resp(404, {}))
    assert c.delete_playlist("MBID") is False
