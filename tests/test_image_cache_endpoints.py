"""The artwork-cache HTTP surface: status, clear, prune, and ?v= thumbnails.

Thumbnails are OPT-IN and off by default, so `?v=grid` must be inert until
someone turns them on in Settings -> Advanced. The cache itself keeps its
existing default (on) — it has been on for every install since it shipped, and
turning it off would silently slow down everyone already benefiting.
"""

from __future__ import annotations

import io
import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-imgcache-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'imgcache.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')
pytest.importorskip("PIL")

from core.image_cache import VARIANT_MAX_WIDTH, ImageCache  # noqa: E402

URL = "https://cdn.example.test/endpoint-cover.jpg"


def _jpeg(width, height):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (10, 90, 200)).save(buf, format="JPEG")
    return buf.getvalue()


BIG = _jpeg(1200, 1200)


class FakeResponse:
    def __init__(self, body):
        self.body, self.status_code = body, 200
        self.headers = {"Content-Type": "image/jpeg", "Content-Length": str(len(body))}

    def iter_content(self, chunk_size=65536):
        yield self.body

    def close(self):
        pass


@pytest.fixture
def client():
    return web_server.app.test_client()


@pytest.fixture
def cache(tmp_path, monkeypatch):
    """A throwaway cache wired into the endpoints, never the real one."""
    c = ImageCache(tmp_path, fetcher=lambda url, **kw: FakeResponse(BIG))
    monkeypatch.setattr("core.image_cache.get_image_cache", lambda: c)
    return c


def _thumbnails(monkeypatch, on):
    monkeypatch.setattr("core.image_cache.thumbnails_enabled", lambda: on)


# ── status ───────────────────────────────────────────────────────────────────

def test_status_reports_the_cache_contents(client, cache):
    cache.get_url(URL)

    body = client.get('/api/image-cache/status').get_json()

    assert body['success'] is True
    assert body['entries'] == 1
    assert body['bytes'] > 0
    assert 'thumbnails' in body and 'enabled' in body


def test_status_is_not_swallowed_by_the_key_route(client, cache):
    """/status and /<cache_key> share a prefix. Flask prefers the static rule,
    but that is worth pinning rather than trusting — if it ever flipped, status
    would 404 as a malformed cache key and the Settings panel would go blank."""
    r = client.get('/api/image-cache/status')

    assert r.status_code == 200
    assert r.get_json()['success'] is True


# ── clear + prune ────────────────────────────────────────────────────────────

def test_clear_empties_the_cache(client, cache):
    cache.get_url(URL)
    assert cache.stats()['entries'] == 1

    body = client.post('/api/image-cache/clear').get_json()

    assert body['success'] is True and body['removed'] == 1
    assert cache.stats()['entries'] == 0


def test_prune_runs_on_demand(client, cache):
    cache.get_url(URL)

    body = client.post('/api/image-cache/prune').get_json()

    assert body['success'] is True
    assert 'expired' in body and 'evicted' in body


# ── the ?v= thumbnail gate ───────────────────────────────────────────────────

def _key(cache, url=URL):
    return cache.cache_url_for(url).rsplit('/', 1)[-1]


def test_a_variant_is_ignored_while_thumbnails_are_off(client, cache, monkeypatch):
    """Off by default: the request must serve the original, not resize it."""
    _thumbnails(monkeypatch, False)
    cache.get_url(URL)

    r = client.get(f'/api/image-cache/{_key(cache)}?v=grid')

    assert r.status_code == 200
    assert len(r.data) == len(BIG), "the image was resized despite thumbnails being off"


def test_a_variant_is_served_when_thumbnails_are_on(client, cache, monkeypatch):
    _thumbnails(monkeypatch, True)
    cache.get_url(URL)

    r = client.get(f'/api/image-cache/{_key(cache)}?v=grid')

    assert r.status_code == 200
    assert len(r.data) < len(BIG), "the grid variant was not smaller than the original"
    from PIL import Image
    with Image.open(io.BytesIO(r.data)) as img:
        assert img.width == VARIANT_MAX_WIDTH['grid']


def test_no_variant_still_serves_the_original(client, cache, monkeypatch):
    _thumbnails(monkeypatch, True)
    cache.get_url(URL)

    r = client.get(f'/api/image-cache/{_key(cache)}')

    assert r.status_code == 200
    assert len(r.data) == len(BIG)


def test_an_unknown_variant_serves_the_original_rather_than_failing(client, cache, monkeypatch):
    _thumbnails(monkeypatch, True)
    cache.get_url(URL)

    r = client.get(f'/api/image-cache/{_key(cache)}?v=gigantic')

    assert r.status_code == 200
    assert len(r.data) == len(BIG)


def test_a_malformed_key_is_still_404(client, cache):
    assert client.get('/api/image-cache/not-a-real-key').status_code == 404


# ── the guarantees that had no test until a mutation survived ────────────────

def test_thumbnails_are_off_unless_someone_turns_them_on():
    """Boulder's explicit requirement, and it was unpinned: a mutation that made
    thumbnails default to ON passed all 43 tests. Server-side resizing costs CPU
    on first sight of every image, so it must never switch itself on."""
    from core.image_cache import thumbnails_enabled

    class _Cfg:
        def __init__(self, value): self.value = value
        def get(self, key, default=None):
            return self.value if key == "image_cache.thumbnails" else default

    import core.image_cache as mod
    original = mod.config_manager
    try:
        mod.config_manager = _Cfg(None)          # key absent → the real default
        assert thumbnails_enabled() is False, "thumbnails defaulted to ON"
        mod.config_manager = _Cfg(False)
        assert thumbnails_enabled() is False
        mod.config_manager = _Cfg(True)          # only an explicit opt-in enables
        assert thumbnails_enabled() is True
    finally:
        mod.config_manager = original


def test_image_cache_settings_actually_persist():
    """The save handler filters by a WHITELIST of sections. image_cache was not
    on it, so the Settings panel would have saved nothing at all — silently.
    A mutation removing it again passed every test, so this pins the round trip
    rather than the list itself."""
    import web_server

    r = web_server.app.test_client().post('/api/settings', json={
        'image_cache': {'thumbnails': True, 'max_cache_mb': 512},
    })
    assert r.status_code == 200, r.get_data(as_text=True)

    from core.settings import config_manager
    assert config_manager.get('image_cache.thumbnails') is True, \
        "the thumbnails toggle did not persist — the section is not whitelisted"
    assert int(config_manager.get('image_cache.max_cache_mb')) == 512

    config_manager.set('image_cache.thumbnails', False)   # leave it off again
