"""When every request thread is held, the log says by what (SeadogsBooty: the
whole ui and api hung at 2% cpu with nothing in the log)."""

import pytest

from core import request_watchdog as rw


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(rw, "_ensure_watchdog", lambda: None)
    rw._active.clear()
    yield
    rw._active.clear()


class _Closable(list):
    closed = False

    def close(self):
        self.closed = True


def _call(app, path="/", query=""):
    return app({"PATH_INFO": path, "QUERY_STRING": query, "REQUEST_METHOD": "GET"},
               lambda *a: None)


def test_a_streaming_response_holds_its_place_until_closed():
    body = _Closable([b"data"])
    app = rw.track_requests(lambda env, start: body)
    result = _call(app, "/socket.io/", "EIO=4&transport=websocket&sid=x")
    assert result is body  # the same object: gunicorn's sendfile check still holds
    assert [r["path"] for r in rw.snapshot()] == ["/socket.io/?transport=websocket"]
    result.close()
    assert body.closed and rw.snapshot() == []


def test_a_plain_list_response_is_done_at_once():
    app = rw.track_requests(lambda env, start: [b"ok"])
    _call(app, "/api/x")
    assert rw.snapshot() == []


def test_a_request_that_raises_leaves_the_list():
    def boom(env, start):
        raise RuntimeError("x")
    with pytest.raises(RuntimeError):
        _call(rw.track_requests(boom), "/api/x")
    assert rw.snapshot() == []


def test_it_reports_only_after_a_full_minute_and_then_waits(monkeypatch):
    monkeypatch.setenv("SOULSYNC_REQUEST_THREADS", "2")
    app = rw.track_requests(lambda env, start: _Closable([b""]))
    held = [_call(app, "/socket.io/", "transport=websocket") for _ in range(2)]
    said = []
    state = {"full_since": None, "reported": None}

    assert not rw.check(1000, state, said.append)          # just filled
    assert not rw.check(1030, state, said.append)          # 30s full
    assert rw.check(1061, state, said.append)              # a minute: report
    assert "2 x GET /socket.io/?transport=websocket" in said[0]
    assert "stack" in said[0]
    assert not rw.check(1100, state, said.append)          # cooldown
    held[0].close()
    assert not rw.check(1500, state, said.append)          # a thread is free again
    assert len(said) == 1
