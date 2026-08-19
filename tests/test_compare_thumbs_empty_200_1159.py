"""Compare-view images: 200 with an empty body (#1159, AfonsoG6).

    "No images loading. All requests for the images return a 200 response but
    with an empty body."

The compare endpoint handed the BROWSER raw Jellyfin URLs:

    {jf_base}/Items/{album_id}/Images/Primary?maxHeight=100

Two things wrong with that, and his network tab shows the second:

  - ``jf_base`` is the address SOULSYNC uses to reach Jellyfin, which is not
    necessarily one the browser can use at all (docker-internal hostnames);
  - the URL carries NO api_key — and Jellyfin answers unauthenticated image
    requests with **200 and an empty body**. That exact behaviour is why
    ``core/server_activity._fetch_jellyfin_image`` guards on
    ``r.status_code == 200 and r.content`` — the codebase had already met it.

So the fix is not a new mechanism: the compare's Jellyfin thumbs go through
the same ``/api/server-activity/image?path=jf:<id>`` proxy the activity view
uses, which attaches the api_key server-side (the token never reaches the
browser — the same reason Navidrome thumbs go through /api/navidrome/cover/).
Plex compare thumbs are untouched here: they work today, though they embed
X-Plex-Token in the page — noted, not changed.
"""

from types import SimpleNamespace

import pytest


def read(path):
    with open(path, encoding='utf-8') as handle:
        return handle.read()


# ── the endpoint stops leaking raw jellyfin URLs ────────────────────────────

def test_compare_no_longer_hands_the_browser_a_raw_jellyfin_url():
    src = read('web_server.py')
    assert '/Images/Primary?maxHeight=100' not in src, (
        "the unauthenticated direct-URL form is the bug")


def test_compare_thumbs_go_through_the_token_safe_proxy():
    src = read('web_server.py')
    assert 'thumb = f"/api/server-activity/image?path=jf:{album_id}" if album_id else \'\'' in src


# ── the proxy really refuses to forward an empty 200 ────────────────────────
#
# The heart of the symptom: Jellyfin's 200-empty must become a 404 from the
# proxy, never another 200-empty. Driven through the real fetch function.

def _fetch(monkeypatch, status=200, content=b'', api_key='k'):
    import core.server_activity as sa
    monkeypatch.setattr(sa, '_jellyfin_config',
                        lambda db=None: {'base_url': 'http://jf:8096', 'api_key': api_key})
    seen = {}

    def fake_get(url, params=None, timeout=None):
        seen['url'] = url
        seen['params'] = params or {}
        return SimpleNamespace(status_code=status, content=content,
                               headers={'Content-Type': 'image/jpeg'})

    import requests as real_requests
    monkeypatch.setattr(real_requests, 'get', fake_get)
    return sa._fetch_jellyfin_image('abc123'), seen


def test_an_empty_200_from_jellyfin_is_not_forwarded(monkeypatch):
    got, _ = _fetch(monkeypatch, status=200, content=b'')
    assert got is None, "200-empty upstream must become a proxy 404, not another 200-empty"


def test_a_real_image_passes_through(monkeypatch):
    got, _ = _fetch(monkeypatch, status=200, content=b'\xff\xd8jpegbytes')
    assert got is not None
    content, ctype = got
    assert content.startswith(b'\xff\xd8') and ctype == 'image/jpeg'


def test_the_proxy_authenticates_server_side(monkeypatch):
    """The whole point: the api_key rides on the SERVER's request and never
    appears in anything the browser sees."""
    _, seen = _fetch(monkeypatch, content=b'x')
    assert seen['params'].get('api_key') == 'k'


def test_no_api_key_means_no_fetch_rather_than_an_unauthenticated_one(monkeypatch):
    got, seen = _fetch(monkeypatch, content=b'x', api_key='')
    assert got is None
    assert 'url' not in seen, "must not fire the request Jellyfin will 200-empty"
