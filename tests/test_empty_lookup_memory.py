"""EmptyLookupMemory, and the Cover Art Filler using it (opt-in)."""

import sqlite3
import time
from types import SimpleNamespace

import pytest

from core.repair_jobs.empty_lookup_memory import EmptyLookupMemory
from tests import test_missing_cover_art as base

mca = base.mca


def _db(tmp_path):
    path = str(tmp_path / "music.db")

    def connect():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn

    return SimpleNamespace(_get_connection=connect), connect


# ---- the memory itself -----------------------------------------------------

def test_an_empty_answer_is_trusted_inside_the_window(tmp_path):
    db, _ = _db(tmp_path)
    mem = EmptyLookupMemory.open(db, "job", 7)
    assert not mem.recently_empty("1", "sig")
    mem.remember("1", "sig")
    assert mem.recently_empty("1", "sig")


def test_a_changed_question_asks_again(tmp_path):
    db, _ = _db(tmp_path)
    mem = EmptyLookupMemory.open(db, "job", 7)
    mem.remember("1", "sig")
    assert not mem.recently_empty("1", "other")


def test_an_old_answer_asks_again(tmp_path):
    db, _ = _db(tmp_path)
    mem = EmptyLookupMemory.open(db, "job", 7)
    mem.remember("1", "sig")
    mem._rows["1"] = ("sig", time.time() - 8 * 86400)
    assert not mem.recently_empty("1", "sig")


def test_zero_days_never_trusts_an_answer(tmp_path):
    db, _ = _db(tmp_path)
    mem = EmptyLookupMemory.open(db, "job", 0)
    mem.remember("1", "sig")
    assert not mem.recently_empty("1", "sig")


def test_memory_survives_a_new_run_and_is_per_job(tmp_path):
    db, _ = _db(tmp_path)
    mem = EmptyLookupMemory.open(db, "job", 7)
    mem.remember("1", "sig")
    mem.close()
    again = EmptyLookupMemory.open(db, "job", 7)
    assert again.recently_empty("1", "sig")
    other = EmptyLookupMemory.open(db, "other_job", 7)
    assert not other.recently_empty("1", "sig")


def test_forget_and_prune_are_saved(tmp_path):
    db, connect = _db(tmp_path)
    mem = EmptyLookupMemory.open(db, "job", 7)
    for k in ("1", "2", "3"):
        mem.remember(k, "sig")
    mem.commit()
    mem.forget("1")
    mem.prune(["2"])
    mem.close()
    left = [r[0] for r in connect().execute("SELECT key FROM repair_empty_lookups")]
    assert left == ["2"]


def test_a_broken_database_disables_the_memory_instead_of_failing():
    def boom():
        raise RuntimeError("locked")

    assert EmptyLookupMemory.open(SimpleNamespace(_get_connection=boom), "job", 7) is None


# ---- the Cover Art Filler --------------------------------------------------

def _album_db(tmp_path, album_row=(1, 'Album', 1, '', 'sp-album', None, None, None, None)):
    """The cover-art schema in a file, so every _get_connection is its own
    connection (the scan closes the ones it is given)."""
    seed = base._make_db(album_row)
    path = str(tmp_path / "music.db")
    disk = sqlite3.connect(path)
    seed.backup(disk)
    disk.close()

    def connect():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn

    return SimpleNamespace(_get_connection=connect)


def _ctx(db, **settings):
    cfg = {'repair.jobs.missing_cover_art.settings': settings}
    findings = []
    return SimpleNamespace(
        db=db,
        config_manager=SimpleNamespace(get=lambda key, default=None: cfg.get(key, default)),
        check_stop=lambda: False,
        wait_if_paused=lambda: False,
        update_progress=lambda *a, **k: None,
        report_progress=lambda *a, **k: None,
        create_finding=lambda **kw: (findings.append(kw) or True),
        findings=findings,
    )


class _Empty:
    """A source that answers, and has nothing."""

    def __init__(self):
        self.calls = 0

    def get_album(self, album_id, include_tracks=False):
        self.calls += 1
        return None

    def search_albums(self, query, limit=1):
        self.calls += 1
        return []


class _Broken:
    def __init__(self):
        self.calls = 0

    def get_album(self, *a, **k):
        self.calls += 1
        raise TimeoutError("slow")

    def search_albums(self, *a, **k):
        self.calls += 1
        raise TimeoutError("slow")


@pytest.fixture
def source(monkeypatch):
    holder = SimpleNamespace(client=_Empty())
    monkeypatch.setattr(mca, 'get_primary_source', lambda: 'spotify')
    monkeypatch.setattr(mca, 'get_client_for_source', lambda s: holder.client)
    return holder


def test_default_searches_every_run(tmp_path, source):
    db = _album_db(tmp_path)
    mca.MissingCoverArtJob().scan(_ctx(db))
    first = source.client.calls
    mca.MissingCoverArtJob().scan(_ctx(db))
    assert first > 0
    assert source.client.calls == first * 2


def test_opt_in_does_not_search_an_album_again_after_an_empty_answer(tmp_path, source):
    db = _album_db(tmp_path)
    ctx = lambda: _ctx(db, remember_empty_lookups=True)  # noqa: E731
    mca.MissingCoverArtJob().scan(ctx())
    first = source.client.calls
    assert first > 0
    mca.MissingCoverArtJob().scan(ctx())
    assert source.client.calls == first


def test_a_failed_lookup_is_not_remembered(tmp_path, source):
    source.client = _Broken()
    db = _album_db(tmp_path)
    ctx = lambda: _ctx(db, remember_empty_lookups=True)  # noqa: E731
    mca.MissingCoverArtJob().scan(ctx())
    first = source.client.calls
    mca.MissingCoverArtJob().scan(ctx())
    assert source.client.calls == first * 2


def test_a_rate_limited_lookup_is_not_remembered(tmp_path, source):
    db = _album_db(tmp_path)
    ctx = lambda: _ctx(db, remember_empty_lookups=True)  # noqa: E731
    limited = ctx()
    limited.is_spotify_rate_limited = lambda: True
    mca.MissingCoverArtJob().scan(limited)
    first = source.client.calls
    mca.MissingCoverArtJob().scan(ctx())
    assert source.client.calls == first * 2


def test_a_renamed_album_is_searched_again(tmp_path, source):
    db = _album_db(tmp_path)
    ctx = lambda: _ctx(db, remember_empty_lookups=True)  # noqa: E731
    mca.MissingCoverArtJob().scan(ctx())
    first = source.client.calls
    conn = db._get_connection()
    conn.execute("UPDATE albums SET title = 'Album (Remaster)'")
    conn.commit()
    conn.close()
    mca.MissingCoverArtJob().scan(ctx())
    assert source.client.calls > first


def test_art_found_later_still_makes_a_finding(tmp_path, source):
    db = _album_db(tmp_path)
    ctx = lambda: _ctx(db, remember_empty_lookups=True, recheck_after_days=0)  # noqa: E731
    mca.MissingCoverArtJob().scan(ctx())
    source.client = base._FakeClient(album_image='https://img/x')
    later = ctx()
    result = mca.MissingCoverArtJob().scan(later)
    assert result.findings_created == 1
    assert later.findings[0]['details']['found_artwork_url'] == 'https://img/x'


def test_an_empty_answer_still_produces_the_same_result_when_remembered(tmp_path, source):
    """Skipping the search must read exactly like the search coming back empty."""
    db = _album_db(tmp_path)
    ctx = lambda: _ctx(db, remember_empty_lookups=True)  # noqa: E731
    a = mca.MissingCoverArtJob().scan(ctx())
    b = mca.MissingCoverArtJob().scan(ctx())
    assert (a.scanned, a.skipped, a.findings_created) == (b.scanned, b.skipped, b.findings_created)


# ---- real clients: a failure they swallow is still a failure ---------------

def _reply(status, body=b'{}'):
    import requests
    response = requests.Response()
    response.status_code = status
    response._content = body
    return response


@pytest.fixture
def wire(monkeypatch):
    """What the network answers, under the real clients' own sessions."""
    import core.lookup_failures as elm
    from core import deezer_client, itunes_client

    elm._install()
    state = SimpleNamespace(reply=_reply(200), requests=0)

    def send(self, request, *args, **kwargs):
        state.requests += 1
        if isinstance(state.reply, Exception):
            raise state.reply
        return state.reply

    monkeypatch.setattr(elm, '_send', send)
    quiet = SimpleNamespace(get_search_results=lambda *a, **k: None,
                            store_search_results=lambda *a, **k: None)
    for module in (deezer_client, itunes_client):
        monkeypatch.setattr(module, 'get_metadata_cache', lambda: quiet)
    monkeypatch.setattr('time.sleep', lambda *_: None)
    return state


def _real(source, name, monkeypatch):
    from core.deezer_client import DeezerClient
    from core.itunes_client import iTunesClient
    client = DeezerClient() if name == 'deezer' else iTunesClient()
    monkeypatch.setattr(mca, 'get_primary_source', lambda: name)
    monkeypatch.setattr(mca, 'get_source_priority', lambda primary: [name])
    monkeypatch.setattr(mca, 'get_client_for_source', lambda s: client)
    return client


def _runs(tmp_path):
    db = _album_db(tmp_path)
    ctx = lambda: _ctx(db, remember_empty_lookups=True)  # noqa: E731
    mca.MissingCoverArtJob().scan(ctx())
    return lambda: mca.MissingCoverArtJob().scan(ctx())


@pytest.mark.parametrize('name,failure', [
    ('deezer', _reply(503)),
    ('deezer', _reply(200, b'{"error": {"type": "Exception", "message": "Quota limit exceeded", "code": 4}}')),
    ('deezer', ConnectionError('down')),
    ('itunes', _reply(403)),
    ('itunes', _reply(503)),
])
def test_a_failure_the_real_client_swallows_is_not_remembered(tmp_path, source, wire, monkeypatch, name, failure):
    _real(source, name, monkeypatch)
    wire.reply = failure
    again = _runs(tmp_path)
    first = wire.requests
    assert first > 0
    again()
    assert wire.requests == first * 2


@pytest.mark.parametrize('name,answer', [
    ('deezer', _reply(200, b'{"data": [], "total": 0}')),
    ('deezer', _reply(200, b'{"error": {"type": "DataException", "message": "no data", "code": 800}}')),
    ('itunes', _reply(200, b'{"resultCount": 0, "results": []}')),
    ('itunes', _reply(404)),
])
def test_a_real_empty_answer_is_still_remembered(tmp_path, source, wire, monkeypatch, name, answer):
    _real(source, name, monkeypatch)
    wire.reply = answer
    again = _runs(tmp_path)
    first = wire.requests
    assert first > 0
    again()
    assert wire.requests == first


def test_watching_ignores_other_threads_and_nests(wire):
    import threading
    from core.lookup_failures import watch_lookup_failures
    import requests
    wire.reply = _reply(503)

    def other():
        try:
            requests.Session().get('http://127.0.0.1/x')
        except Exception:  # noqa: BLE001
            pass

    with watch_lookup_failures() as outer:
        with watch_lookup_failures() as inner:
            t = threading.Thread(target=other)
            t.start()
            t.join()
            assert inner.count == 0
            requests.Session().get('http://127.0.0.1/x')
            assert inner.count == 1
        requests.Session().get('http://127.0.0.1/x')
        assert outer.count == 1


def test_the_memory_is_closed_when_the_scan_throws(tmp_path, source, monkeypatch):
    closed = []
    real_close = EmptyLookupMemory.close
    monkeypatch.setattr(EmptyLookupMemory, 'close',
                        lambda self: (closed.append(1), real_close(self))[-1])
    db = _album_db(tmp_path)
    ctx = _ctx(db, remember_empty_lookups=True)
    ctx.check_stop = lambda: (_ for _ in ()).throw(RuntimeError('boom'))
    with pytest.raises(RuntimeError):
        mca.MissingCoverArtJob().scan(ctx)
    assert closed


# ---- Hydrabase talks over a websocket, not requests -------------------------

class _Socket:
    """The shared websocket Hydrabase sends through."""

    def __init__(self, connected=True, reply=None, raises=None):
        self.connected = connected
        self._reply = reply
        self._raises = raises
        self.sent = 0

    def settimeout(self, _):
        pass

    def send(self, _):
        self.sent += 1

    def recv(self):
        if self._raises:
            raise self._raises
        return self._reply


def _hydrabase(socket, monkeypatch):
    import threading
    from core.hydrabase_client import HydrabaseClient
    client = HydrabaseClient(lambda: (socket, threading.Lock()))
    socket.asked = 0
    real = client._send_and_recv

    def counted(*a, **k):
        socket.asked += 1
        return real(*a, **k)

    client._send_and_recv = counted
    monkeypatch.setattr(mca, 'get_primary_source', lambda: 'hydrabase')
    monkeypatch.setattr(mca, 'get_source_priority', lambda primary: ['hydrabase'])
    monkeypatch.setattr(mca, 'get_client_for_source', lambda s: client)
    return client


@pytest.mark.parametrize('socket', [
    _Socket(connected=False),
    _Socket(raises=TimeoutError('slow')),
    _Socket(reply='not json'),
], ids=['disconnected', 'timeout', 'garbage'])
def test_a_hydrabase_failure_is_not_remembered(tmp_path, source, monkeypatch, socket):
    _hydrabase(socket, monkeypatch)
    again = _runs(tmp_path)
    first = socket.asked
    assert first > 0
    again()
    assert socket.asked == first * 2


def test_a_hydrabase_empty_answer_is_remembered(tmp_path, source, monkeypatch):
    socket = _Socket(reply='[]')
    _hydrabase(socket, monkeypatch)
    again = _runs(tmp_path)
    first = socket.asked
    assert first > 0
    again()
    assert socket.asked == first
