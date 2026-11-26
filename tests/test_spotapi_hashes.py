"""core.spotapi_hashes: one web-player hash fetch for every spotapi client."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

spotapi_client = pytest.importorskip("spotapi.client")

from core import spotapi_hashes  # noqa: E402


class FakeHttp:
    def __init__(self):
        self.urls = []

    def get(self, url):
        self.urls.append(url)
        body = 'PACK "queryArtistOverview","query","h-artist" ' if url == 'pack' else f'[{url[-2:]} ü]'
        return SimpleNamespace(fail=False, response=body, error=None)


@pytest.fixture
def patched(monkeypatch):
    base_cls = spotapi_client.BaseClient
    monkeypatch.setattr(base_cls, 'get_sha256_hash', base_cls.get_sha256_hash)
    monkeypatch.setattr(base_cls, 'part_hash', base_cls.part_hash)
    monkeypatch.setattr(spotapi_client, 'extract_mappings', lambda text: ({}, {}))
    monkeypatch.setattr(spotapi_client, 'combine_chunks', lambda h, s: ['c1', 'c2', 'c3'])
    monkeypatch.setattr(spotapi_hashes, '_installed', False)
    spotapi_hashes.reset_for_tests()
    assert spotapi_hashes.install()
    yield base_cls
    spotapi_hashes.reset_for_tests()


def _client(http):
    base = spotapi_client.BaseClient.__new__(spotapi_client.BaseClient)
    base.client = http
    base.js_pack = 'pack'
    return base


def test_every_client_shares_one_fetch(patched):
    """SpotipyFree builds a spotapi object per call, and each fetched the
    JS pack and every chunk again (6-7 s, ~1.4 GB of RSS per fetch)."""
    http = FakeHttp()
    first, second = _client(http), _client(http)
    first.get_sha256_hash()
    second.get_sha256_hash()
    assert http.urls == ['pack'] + [spotapi_hashes._WEB_PLAYER.format(c) for c in ('c1', 'c2', 'c3')]
    assert first.raw_hashes is second.raw_hashes
    assert first.raw_hashes.endswith('[c1 ü][c2 ü][c3 ü]')


def test_part_hash_is_read_once(patched):
    http = FakeHttp()
    base = _client(http)
    assert base.part_hash('queryArtistOverview') == 'h-artist'
    assert spotapi_hashes._shared[2] == {'queryArtistOverview': 'h-artist'}
    assert _client(http).part_hash('queryArtistOverview') == 'h-artist'
    assert len(http.urls) == 4
    with pytest.raises(IndexError):
        base.part_hash('missingOperation')


def test_stale_hashes_are_fetched_again(patched, monkeypatch):
    http = FakeHttp()
    clock = iter([0.0, 10.0, spotapi_hashes.TTL_SECONDS + 1, spotapi_hashes.TTL_SECONDS + 2])
    monkeypatch.setattr(spotapi_hashes.time, 'monotonic', lambda: next(clock))
    _client(http).get_sha256_hash()   # fetch at 0
    _client(http).get_sha256_hash()   # fresh at 10
    _client(http).get_sha256_hash()   # stale: fetched again
    assert http.urls.count('pack') == 2


def test_a_failed_fetch_is_not_cached(patched):
    class Failing(FakeHttp):
        def get(self, url):
            self.urls.append(url)
            return SimpleNamespace(fail=True, response=None, error=SimpleNamespace(string='boom'))

    http = Failing()
    with pytest.raises(spotapi_client.BaseClientError):
        _client(http).get_sha256_hash()
    with pytest.raises(spotapi_client.BaseClientError):
        _client(http).get_sha256_hash()
    assert http.urls == ['pack', 'pack']


def test_an_unknown_spotapi_is_left_alone(monkeypatch):
    monkeypatch.setattr(spotapi_hashes, '_installed', False)
    monkeypatch.delattr(spotapi_client, 'combine_chunks')
    original = spotapi_client.BaseClient.get_sha256_hash
    assert spotapi_hashes.install() is False
    assert spotapi_client.BaseClient.get_sha256_hash is original
