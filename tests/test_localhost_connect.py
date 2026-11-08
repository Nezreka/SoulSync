"""localhost connects try the loopback address that answers.

python tries ::1 before 127.0.0.1, and windows takes ~2s to give up on a
refused ::1 connect, so every new connection to an IPv4-only server (jellyfin
at localhost:8096 on Boulder's install) waited two seconds (Nov 4 2026).
"""

from __future__ import annotations

import pytest

from utils import localhost_connect as lc


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(lc, "_preferred", {})


def _fake(listening):
    calls = []

    def create_connection(address, *args, **kwargs):
        calls.append(address)
        if address[0] in listening or address[0] not in ("127.0.0.1", "::1"):
            return ("sock", address, args, kwargs)
        raise ConnectionRefusedError(address)

    return create_connection, calls


def test_localhost_tries_ipv4_first():
    raw, calls = _fake({"127.0.0.1"})
    sock = lc.wrap(raw)(("localhost", 8096), 8, socket_options=[1])
    assert sock == ("sock", ("127.0.0.1", 8096), (8,), {"socket_options": [1]})
    assert calls == [("127.0.0.1", 8096)]


def test_an_ipv6_only_server_is_remembered_after_one_miss():
    raw, calls = _fake({"::1"})
    connect = lc.wrap(raw)
    connect(("localhost", 4533))
    connect(("localhost", 4533))
    assert calls == [("127.0.0.1", 4533), ("::1", 4533), ("::1", 4533)]


def test_ports_are_remembered_apart():
    raw, calls = _fake({"::1", "127.0.0.1"})
    connect = lc.wrap(raw)
    lc._preferred[4533] = "::1"
    connect(("localhost", 8096))
    connect(("localhost", 4533))
    assert calls == [("127.0.0.1", 8096), ("::1", 4533)]


def test_nothing_listening_raises_the_last_error():
    raw, calls = _fake(set())
    with pytest.raises(ConnectionRefusedError):
        lc.wrap(raw)(("LOCALHOST", 9))
    assert calls == [("127.0.0.1", 9), ("::1", 9)]


@pytest.mark.parametrize("host", ["192.168.86.36", "plex.lan", "127.0.0.1", "::1"])
def test_every_other_host_passes_through(host):
    raw, calls = _fake({host})
    assert lc.wrap(raw)((host, 32400))[1] == (host, 32400)
    assert calls == [(host, 32400)]


def test_web_server_installs_it_into_urllib3():
    import web_server  # noqa: F401 - installs at import
    from urllib3.util import connection

    assert getattr(connection.create_connection, "__wrapped__", None) is not None
